"""End-to-end ingestion tests against a real SQLAlchemy session.

These use in-memory SQLite through the project's own ``session_scope``
semantics (commit on success, rollback on exception) and the real
``EventRepository``, so at-least-once idempotency is verified against actual
persistence rather than a mock.

They do NOT use PostgreSQL. Dialect-specific ``ON CONFLICT`` behaviour is
covered by ``tests/storage/test_postgres_integration.py`` (which self-skips
without ``DATABASE_URL``).
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from ai_eval_platform.ingestion.delivery_classifier import DeliveryClassifier
from ai_eval_platform.ingestion.dlq import DeadLetterQueue
from ai_eval_platform.ingestion.event_router import build_event_router
from ai_eval_platform.ingestion.event_validator import EventValidator
from ai_eval_platform.ingestion.handler import (
    AgentPolicyEventHandler,
    BehaviorEventHandler,
    ContextEventHandler,
    EvaluationEventHandler,
    ExecutionEventHandler,
)
from ai_eval_platform.ingestion.kafka_consumer import KafkaEventConsumer
from ai_eval_platform.ingestion.retry_policy import RetryConfig, RetryPolicy
from ai_eval_platform.ingestion.retry_publisher import RetryPublisher
from ai_eval_platform.storage.models import (
    Agent,
    AgentVersion,
    Base,
    Event,
    Run,
)
from ai_eval_platform.storage.repositories import EventRepository

from .conftest import (
    FakeConsumer,
    RecordingProducer,
    make_record,
    valid_event_bytes,
)


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture()
def seeded(engine):
    with Session(engine, expire_on_commit=False, autoflush=False) as db:
        db.add(Agent(agent_id="agent-1", name="agent-1"))
        db.add(
            AgentVersion(
                agent_version_id="av-1",
                agent_id="agent-1",
                version="1.0.0",
            )
        )
        db.add(
            Run(
                run_id="run-1",
                agent_id="agent-1",
                agent_version_id="av-1",
                status="running",
            )
        )
        db.commit()


def make_session_scope(engine):
    """A session_scope equivalent: commit on success, rollback on failure."""

    @contextmanager
    def scope():
        session = Session(engine, expire_on_commit=False, autoflush=False)
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return scope
def build_db_consumer(
    engine,
    consumer,
    producer,
    *,
    session_factory=None,
):
    scope = session_factory or make_session_scope(engine)

    def handler_factory(session):
        repository = EventRepository(session)
        return build_event_router(
            execution_handler=ExecutionEventHandler(repository),
            context_handler=ContextEventHandler(repository),
            behavior_handler=BehaviorEventHandler(repository),
            evaluation_handler=EvaluationEventHandler(repository),
            agent_policy_handler=AgentPolicyEventHandler(repository),
        )

    return KafkaEventConsumer(
        consumer=consumer,
        validator=EventValidator(),
        router=None,
        session_factory=scope,
        handler_factory=handler_factory,
        classifier=DeliveryClassifier(),
        retry_policy=RetryPolicy(RetryConfig(max_attempts=3)),
        retry_publisher=RetryPublisher(producer, "events.retry"),
        dlq=DeadLetterQueue(producer, "events.dlq"),
    )


def count_events(engine) -> int:
    with Session(engine) as db:
        return db.scalar(select(func.count()).select_from(Event))


def test_valid_event_is_persisted_and_committed(engine, seeded):
    consumer = FakeConsumer()
    producer = RecordingProducer()
    sut = build_db_consumer(engine, consumer, producer)

    outcome = sut.process(make_record(valid_event_bytes()))

    assert outcome.action.value == "accept"
    assert outcome.committed is True
    assert consumer.commit_count == 1
    assert count_events(engine) == 1
    assert producer.call_count == 0


def test_redelivery_of_same_event_id_is_idempotent(engine, seeded):
    # AREA 5: a crash after DB commit but before the offset commit causes
    # redelivery. event_id idempotency must make that safe.
    producer = RecordingProducer()

    build_db_consumer(engine, FakeConsumer(), producer).process(
        make_record(valid_event_bytes())
    )

    # Same record delivered again, as after a restart.
    outcome = build_db_consumer(
        engine, FakeConsumer(), producer
    ).process(make_record(valid_event_bytes()))

    assert outcome.action.value == "accept"
    assert outcome.committed is True
    assert count_events(engine) == 1, "duplicate must not create a second row"
    assert producer.call_count == 0, "duplicate must not be dead-lettered"


def test_event_identity_is_persisted(engine, seeded):
    sut = build_db_consumer(engine, FakeConsumer(), RecordingProducer())
    sut.process(make_record(valid_event_bytes(event_id="evt-42")))

    with Session(engine) as db:
        row = db.get(Event, "evt-42")
        assert row is not None
        assert row.run_id == "run-1"
        assert row.event_type == "model_called"


def test_db_failure_rolls_back_and_does_not_commit(engine, seeded):
    from ai_eval_platform.ingestion.producer import RetryablePublishError

    @contextmanager
    def exploding_scope():
        raise RuntimeError("db down")
        yield  # pragma: no cover - unreachable

    consumer = FakeConsumer()
    producer = RecordingProducer(fail_with=RetryablePublishError("down"))
    sut = build_db_consumer(
        engine,
        consumer,
        producer,
        session_factory=exploding_scope,
    )

    outcome = sut.process(make_record(valid_event_bytes()))

    # Nothing durable happened, so the offset stays uncommitted.
    assert outcome.committed is False
    assert consumer.commit_count == 0
    assert count_events(engine) == 0


def test_malformed_record_is_dead_lettered_without_touching_db(
    engine, seeded
):
    consumer = FakeConsumer()
    producer = RecordingProducer()
    sut = build_db_consumer(engine, consumer, producer)

    outcome = sut.process(make_record(b"\xff\xfe"))

    assert outcome.action.value == "dlq"
    assert outcome.committed is True
    assert count_events(engine) == 0

    payload = producer.payloads()[0]
    assert payload["raw"] is not None
    assert payload["failure"]["code"] == "INVALID_UTF8"


def test_duplicate_dlq_after_crash_creates_a_second_distinct_record(
    engine, seeded
):
    # AREA 5: duplicate DLQ delivery is expected under at-least-once. The
    # records are distinguishable, which is what replay tooling needs.
    producer = RecordingProducer()
    sut = build_db_consumer(engine, FakeConsumer(), producer)

    sut.process(make_record(b"{bad", offset=7))
    sut.process(make_record(b"{bad", offset=7))

    payloads = producer.payloads()
    assert len(payloads) == 2
    assert payloads[0]["dlq_id"] != payloads[1]["dlq_id"]
    assert payloads[0]["source"]["offset"] == 7