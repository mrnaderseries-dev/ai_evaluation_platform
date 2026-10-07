"""Full retry lifecycle: main -> retry topic -> dispatcher -> main -> DLQ.

Integration-level within the application layer. It wires the real
KafkaEventConsumer, RetryPublisher, RetryDispatcher and RetryConsumerLoop
together, with a producer double standing in for the broker.

It proves the application's state machine across the whole loop. It does NOT
prove broker semantics (delivery reports, real offsets, rebalances).
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

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
from ai_eval_platform.ingestion.kafka_record import KafkaRecord
from ai_eval_platform.ingestion.producer import RetryablePublishError
from ai_eval_platform.ingestion.retry_consumer import RetryConsumerLoop
from ai_eval_platform.ingestion.retry_dispatcher import DispatchAction, RetryDispatcher
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
from .test_retry_consumer import FakeRetrySource

MAIN_TOPIC = "ai.events"
RETRY_TOPIC = "ai.events.retry"
DLQ_TOPIC = "ai.events.dlq"

T0 = datetime(2026, 10, 4, 15, 0, tzinfo=timezone.utc)


class Clock:
    """A controllable clock so due_at can be advanced deterministically."""

    def __init__(self, start: datetime = T0) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now = self.now + timedelta(**kwargs)


class FlakyRepository:
    """Wraps EventRepository so persistence fails a fixed number of times.

    Injecting this at the REPOSITORY level (rather than replacing the router)
    keeps the real handler + router + database path intact, so a successful
    retry genuinely persists a row.

    ``fail_times`` is consumed from the shared counter rather than reset per
    instance, because each ``process()`` call opens a NEW session and builds a
    NEW handler graph - the failure budget has to span the whole retry cycle.
    """

    def __init__(self, repository: Any, budget: list[int]) -> None:
        self._repository = repository
        self._budget = budget
        self.calls = 0

    def insert_from_envelope(self, envelope: Any, **kwargs: Any) -> int:
        self.calls += 1

        if self._budget[0] > 0:
            self._budget[0] -= 1
            raise OperationalError(
                "SELECT 1",
                {},
                Exception("database is temporarily unavailable"),
            )

        return self._repository.insert_from_envelope(envelope, **kwargs)


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
                agent_version_id="av-1", agent_id="agent-1", version="1.0.0"
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


def build_pipeline(
    engine,
    producer: RecordingProducer,
    fail_times: int,
    clock,
    *,
    max_attempts: int = 3,
):
    """Wire the main consumer and the retry dispatcher together.

    The real router and real handlers are used; the transient failure is
    injected at the repository layer so the whole handler + DB path is
    exercised and a successful retry genuinely persists a row.
    """
    state = {"budget": [fail_times], "calls": 0}

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
        repository = FlakyRepository(
            EventRepository(session), state["budget"]
        )

        class Counting:
            def insert_from_envelope(self, envelope, **kwargs):
                state["calls"] += 1
                return repository.insert_from_envelope(envelope, **kwargs)

        counting = Counting()

        return build_event_router(
            execution_handler=ExecutionEventHandler(counting),
            context_handler=ContextEventHandler(counting),
            behavior_handler=BehaviorEventHandler(counting),
            evaluation_handler=EvaluationEventHandler(counting),
            agent_policy_handler=AgentPolicyEventHandler(counting),
        )

    main_consumer = KafkaEventConsumer(
        consumer=FakeConsumer(),
        validator=EventValidator(),
        router=None,
        session_factory=scope,
        handler_factory=handler_factory,
        classifier=DeliveryClassifier(),
        retry_policy=RetryPolicy(
            RetryConfig(
                base_delay=600.0,
                max_delay=600.0,
                max_attempts=max_attempts,
                jitter=False,
            )
        ),
        retry_publisher=RetryPublisher(
            producer, RETRY_TOPIC, clock=clock
        ),
        dlq=DeadLetterQueue(producer, DLQ_TOPIC, clock=clock),
        # The consumer must use the SAME clock as the publisher, otherwise
        # due_at is computed against real time while the dispatcher reads a
        # test clock.
        clock=clock,
    )

    dispatcher = RetryDispatcher(
        producer=producer,
        main_topic=MAIN_TOPIC,
        dlq_topic=DLQ_TOPIC,
        max_attempts=max_attempts,
        clock=clock,
    )

    return main_consumer, dispatcher, state
def test_transient_failure_deferred_then_forwarded_then_succeeds(
    engine, seeded
):
    # The full loop: main -> RETRY -> (wait) -> dispatcher -> main -> DB.
    clock = Clock()
    producer = RecordingProducer()
    main_consumer, dispatcher, state = build_pipeline(
        engine, producer, 1, clock
    )

    original = KafkaRecord(
        topic=MAIN_TOPIC,
        partition=0,
        offset=10,
        value=valid_event_bytes(),
        key="run-1",
    )

    # 1. Main consumer: transient failure -> retry topic, offset committed.
    first = main_consumer.process(original)
    assert first.committed is True
    retry_published = producer.published[-1]
    assert retry_published["topic"] == RETRY_TOPIC
    assert producer.call_count == 1, "nothing reached the main topic yet"

    # 2. Before due_at the dispatcher defers and does not commit.
    from ai_eval_platform.ingestion.retry_dispatcher import DispatchAction

    retry_record = KafkaRecord(
        topic=RETRY_TOPIC,
        partition=0,
        offset=0,
        value=retry_published["value"],
        key="run-1",
    )

    early = dispatcher.dispatch(retry_record)
    assert early.action is DispatchAction.DEFERRED
    assert producer.call_count == 1

    # 3. After due_at the dispatcher forwards the ORIGINAL payload.
    clock.advance(minutes=30)
    forwarded = dispatcher.dispatch(retry_record)
    assert forwarded.action is DispatchAction.FORWARDED
    assert producer.published[-1]["topic"] == MAIN_TOPIC
    # attempt travelled with the record.
    assert producer.published[-1]["headers"]["attempt"] == "1"

    # 4. Main consumer processes the retried record and persists it.
    retried = KafkaRecord(
        topic=MAIN_TOPIC,
        partition=0,
        offset=11,
        value=producer.published[-1]["value"],
        key="run-1",
        headers={"attempt": "1"},
    )

    second = main_consumer.process(retried)

    assert second.committed is True
    assert count_events(engine) == 1
    assert state['calls'] == 2


def test_retry_exhaustion_reaches_dlq(engine, seeded):
    # A record that always fails transiently must end in the DLQ, not loop.
    clock = Clock()
    producer = RecordingProducer()
    main_consumer, dispatcher, state = build_pipeline(
        engine, producer, 99, clock, max_attempts=2
    )

    original = KafkaRecord(
        topic=MAIN_TOPIC,
        partition=0,
        offset=10,
        value=valid_event_bytes(),
        key="run-1",
    )

    # First failure -> retry attempt 1.
    main_consumer.process(original)
    retry_value = producer.published[-1]["value"]

    # Advance past due and let the dispatcher see attempt 1 (still < 2).
    clock.advance(minutes=30)
    retry_record = KafkaRecord(
        topic=RETRY_TOPIC,
        partition=0,
        offset=0,
        value=retry_value,
        key="run-1",
    )

    outcome = dispatcher.dispatch(retry_record)
    assert outcome.action is DispatchAction.FORWARDED

    # Main consumer reprocesses and fails again: attempt 2 == max_attempts,
    # so RetryPolicy reports EXHAUSTED and the record is dead-lettered.
    retried = KafkaRecord(
        topic=MAIN_TOPIC,
        partition=0,
        offset=11,
        value=producer.published[-1]["value"],
        key="run-1",
        headers={"attempt": "1"},
    )

    final = main_consumer.process(retried)

    assert final.action.value == "dlq"
    assert final.committed is True

    dlq_payload = producer.payloads()[-1]
    assert dlq_payload["failure"]["stage"] == "retry_exhausted"
    assert count_events(engine) == 0


def test_retry_consumer_loop_defers_then_forwards_across_the_cycle(
    engine, seeded
):
    # The loop itself, not just the dispatcher.
    clock = Clock()
    producer = RecordingProducer()
    main_consumer, dispatcher, state = build_pipeline(
        engine, producer, 1, clock
    )

    main_consumer.process(
        KafkaRecord(
            topic=MAIN_TOPIC,
            partition=0,
            offset=10,
            value=valid_event_bytes(),
            key="run-1",
        )
    )

    retry_value = producer.published[-1]["value"]
    record = KafkaRecord(
        topic=RETRY_TOPIC, partition=0, offset=0, value=retry_value, key="run-1"
    )

    source = FakeRetrySource([record])
    loop = RetryConsumerLoop(source=source, dispatcher=dispatcher, clock=clock)

    outcomes = loop.run(max_records=1, idle_poll_limit=1)
    assert outcomes[0].action is DispatchAction.DEFERRED
    assert source.commit_count == 0
    assert source.paused == [(RETRY_TOPIC, 0)]

    # Now that it is due, a fresh poll forwards and commits.
    clock.advance(minutes=30)
    source2 = FakeRetrySource([record])
    loop2 = RetryConsumerLoop(
        source=source2, dispatcher=dispatcher, clock=clock
    )

    outcomes2 = loop2.run(max_records=1, idle_poll_limit=1)
    assert outcomes2[0].action is DispatchAction.FORWARDED
    assert source2.committed_positions() == [(RETRY_TOPIC, 0, 1)]


def test_retry_publish_failure_leaves_original_offset_uncommitted(
    engine, seeded
):
    # The original record must not be acknowledged when its retry could not
    # be published; it is redelivered instead.
    clock = Clock()
    producer = RecordingProducer(fail_with=RetryablePublishError("down"))
    main_consumer, _, state = build_pipeline(
        engine, producer, 1, clock
    )

    outcome = main_consumer.process(
        KafkaRecord(
            topic=MAIN_TOPIC,
            partition=0,
            offset=10,
            value=valid_event_bytes(),
            key="run-1",
        )
    )

    assert outcome.committed is False
    assert count_events(engine) == 0