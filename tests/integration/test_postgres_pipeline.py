"""Integration tests against a REAL PostgreSQL database.

These validate what the SQLite unit tests structurally cannot:

* the real PostgreSQL dialect for ``ON CONFLICT (event_id) DO NOTHING``
* the ``uq_event_run_sequence`` constraint under concurrent inserts
* the ``session_scope()`` commit / rollback boundary against a real server

Skipped when ``DATABASE_URL`` is not set. Run with::

    DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/ai_eval_storage_test
        python -m pytest tests/integration -q

The database is schema-managed by these tests (drop_all/create_all); point
them at a scratch database only.

This suite contains NO Kafka broker: it validates the persistence and
transaction half of the pipeline. Broker behaviour is covered separately in
``test_real_kafka_integration.py``, which skips unless a broker is
reachable.
"""

from __future__ import annotations

import os
from contextlib import contextmanager

import pytest

pytest.importorskip(
    "psycopg2",
    reason="psycopg2 is required for PostgreSQL integration tests",
)

from sqlalchemy import create_engine, func, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from ai_eval_platform.ingestion.delivery_classifier import (  # noqa: E402
    DeliveryClassifier,
)
from ai_eval_platform.ingestion.dlq import DeadLetterQueue  # noqa: E402
from ai_eval_platform.ingestion.event_router import build_event_router  # noqa: E402
from ai_eval_platform.ingestion.event_validator import EventValidator  # noqa: E402
from ai_eval_platform.ingestion.handler import (  # noqa: E402
    AgentPolicyEventHandler,
    BehaviorEventHandler,
    ContextEventHandler,
    EvaluationEventHandler,
    ExecutionEventHandler,
)
from ai_eval_platform.ingestion.kafka_consumer import (  # noqa: E402
    KafkaEventConsumer,
)
from ai_eval_platform.ingestion.kafka_record import KafkaRecord  # noqa: E402
from ai_eval_platform.ingestion.retry_policy import (  # noqa: E402
    RetryConfig,
    RetryPolicy,
)
from ai_eval_platform.ingestion.retry_publisher import (  # noqa: E402
    RetryPublisher,
)
from ai_eval_platform.storage.models import (  # noqa: E402
    Agent,
    AgentVersion,
    Base,
    Event,
    Run,
)
from ai_eval_platform.storage.repositories import EventRepository  # noqa: E402

from tests.ingestion.conftest import (  # noqa: E402
    FakeConsumer,
    RecordingProducer,
    valid_event_bytes,
)

DATABASE_URL_ENV = "DATABASE_URL"

pytestmark = pytest.mark.skipif(
    not os.getenv(DATABASE_URL_ENV),
    reason=(
        "DATABASE_URL not configured; PostgreSQL integration tests skipped"
    ),
)


@pytest.fixture()
def engine():
    from ai_eval_platform.storage.postgres import get_database_url

    schema = f"it_ingestion_{os.getpid()}"

    eng = create_engine(get_database_url(), future=True)

    with eng.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))

    eng = eng.execution_options(
        connect_args={"options": f"-csearch_path={schema}"}
    )
    Base.metadata.create_all(eng)

    try:
        yield eng
    finally:
        Base.metadata.drop_all(eng)

        with eng.begin() as connection:
            connection.execute(
                text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            )

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


def count_events(engine) -> int:
    with Session(engine) as db:
        return db.scalar(select(func.count()).select_from(Event))


def build_consumer(engine, consumer, producer):
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
        retry_publisher=RetryPublisher(producer, "ai.events.retry"),
        dlq=DeadLetterQueue(producer, "ai.events.dlq"),
    )


def main_record(offset: int = 0, value: bytes | None = None, **overrides) -> KafkaRecord:
    return KafkaRecord(
        topic="ai.events",
        partition=0,
        offset=offset,
        value=value if value is not None else valid_event_bytes(**overrides),
        key="run-1",
    )


def test_valid_event_persists_and_commits(engine, seeded):
    consumer = FakeConsumer()
    producer = RecordingProducer()
    sut = build_consumer(engine, consumer, producer)

    outcome = sut.process(main_record())

    assert outcome.action.value == "accept"
    assert outcome.committed is True
    assert count_events(engine) == 1
    assert consumer.committed_positions() == [("ai.events", 0, 1)]


def test_committed_position_is_offset_plus_one(engine, seeded):
    consumer = FakeConsumer()
    sut = build_consumer(engine, consumer, RecordingProducer())

    sut.process(main_record(offset=41))

    assert consumer.committed_positions() == [("ai.events", 0, 42)]


def test_duplicate_delivery_creates_no_second_row_on_postgres(engine, seeded):
    consumer_a = FakeConsumer()
    consumer_b = FakeConsumer()
    producer = RecordingProducer()

    build_consumer(engine, consumer_a, producer).process(main_record(offset=0))
    outcome = build_consumer(engine, consumer_b, producer).process(
        main_record(offset=1)
    )

    assert outcome.action.value == "accept"
    assert count_events(engine) == 1, (
        "ON CONFLICT (event_id) DO NOTHING must absorb redelivery on the "
        "real PostgreSQL dialect"
    )
    assert producer.call_count == 0, "a duplicate must not be dead-lettered"


def test_ten_redeliveries_yield_exactly_one_row(engine, seeded):
    producer = RecordingProducer()

    for offset in range(10):
        build_consumer(engine, FakeConsumer(), producer).process(
            main_record(offset=offset)
        )

    assert count_events(engine) == 1
    assert producer.call_count == 0


def test_distinct_events_of_one_run_both_persist(engine, seeded):
    producer = RecordingProducer()
    sut = build_consumer(engine, FakeConsumer(), producer)

    sut.process(main_record(offset=0, event_id="evt-1"))
    sut.process(main_record(offset=1, event_id="evt-2"))

    assert count_events(engine) == 2


def test_per_run_sequence_is_assigned_in_arrival_order(engine, seeded):
    producer = RecordingProducer()
    sut = build_consumer(engine, FakeConsumer(), producer)

    for offset in range(5):
        sut.process(main_record(offset=offset, event_id=f"evt-{offset}"))

    with Session(engine) as db:
        rows = db.scalars(
            select(Event).order_by(Event.sequence_number.asc())
        ).all()
        sequence_numbers = [row.sequence_number for row in rows]

    assert sequence_numbers == sorted(sequence_numbers)
    assert len(set(sequence_numbers)) == 5


def test_validation_failure_reaches_dlq_without_touching_database(
    engine, seeded
):
    consumer = FakeConsumer()
    producer = RecordingProducer()
    sut = build_consumer(engine, consumer, producer)

    outcome = sut.process(main_record(value=b"\xff\xfe"))

    assert outcome.action.value == "dlq"
    assert outcome.committed is True
    assert count_events(engine) == 0
    assert producer.payloads()[0]["failure"]["code"] == "INVALID_UTF8"


def test_dlq_publish_failure_leaves_offset_uncommitted(engine, seeded):
    from ai_eval_platform.ingestion.producer import RetryablePublishError

    consumer = FakeConsumer()
    producer = RecordingProducer(fail_with=RetryablePublishError("down"))
    sut = build_consumer(engine, consumer, producer)

    outcome = sut.process(main_record(value=b"\xff\xfe"))

    assert outcome.committed is False
    assert consumer.commit_count == 0
    assert count_events(engine) == 0


def test_failed_partition_is_never_advanced_by_another_record(engine, seeded):
    from ai_eval_platform.ingestion.producer import RetryablePublishError

    producer = RecordingProducer(fail_with=RetryablePublishError("down"))
    consumer = FakeConsumer()
    sut = build_consumer(engine, consumer, producer)

    failed = KafkaRecord(
        topic="ai.events",
        partition=0,
        offset=10,
        value=b"\xff\xfe",
        key="run-1",
    )
    assert sut.process(failed).committed is False

    producer.fail_with = None
    ok = KafkaRecord(
        topic="ai.events",
        partition=1,
        offset=20,
        value=valid_event_bytes(),
        key="run-1",
    )
    assert sut.process(ok).committed is True

    assert consumer.committed_positions() == [("ai.events", 1, 21)]