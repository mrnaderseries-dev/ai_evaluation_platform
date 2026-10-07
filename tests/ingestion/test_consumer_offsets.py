"""Offset-commit safety tests - the core guarantee of the pipeline.

Every test asserts the SAME invariant from a different angle:

    the source Kafka offset is committed ONLY after the record's durable
    destination (DB, retry topic, or DLQ) is CONFIRMED.

Test doubles stand in for the database and the broker. They verify
orchestration and ordering, NOT broker semantics: a fake producer that
returns None mimics a confirmed publish, and real delivery-report
behaviour needs an integration test against a broker. See
``Kafka_analysis.md`` ("Remaining Risks").
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from ai_eval_platform.ingestion.delivery_classifier import DeliveryClassifier
from ai_eval_platform.ingestion.dlq import DeadLetterQueue
from ai_eval_platform.ingestion.event_validator import EventValidator
from ai_eval_platform.ingestion.handler import HandlerError
from ai_eval_platform.ingestion.kafka_consumer import KafkaEventConsumer
from ai_eval_platform.ingestion.producer import RetryablePublishError
from ai_eval_platform.ingestion.retry_policy import RetryConfig, RetryPolicy
from ai_eval_platform.ingestion.retry_publisher import RetryPublisher

from .conftest import (
    FakeConsumer,
    RecordingProducer,
    make_record,
    valid_event_bytes,
)


class RaisingHandler:
    """A router that fails the way a handler/DB failure would."""

    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.calls = 0

    def route(self, event: Any) -> None:
        self.calls += 1

        if self.error is not None:
            raise self.error


class NoopRouter:
    def route(self, event: Any) -> None:
        return None


class TransientDBError(Exception):
    """Stands in for OperationalError / DBAPIError."""


@contextmanager
def fake_session_scope():
    yield object()


def build_consumer(
    *,
    consumer: FakeConsumer,
    producer: RecordingProducer,
    router: Any = None,
    retry_config: RetryConfig | None = None,
) -> KafkaEventConsumer:
    router = router or NoopRouter()

    return KafkaEventConsumer(
        consumer=consumer,
        validator=EventValidator(),
        router=router,
        session_factory=fake_session_scope,
        handler_factory=lambda _session: router,
        classifier=DeliveryClassifier(),
        retry_policy=RetryPolicy(retry_config or RetryConfig(max_attempts=3)),
        retry_publisher=RetryPublisher(producer, "events.retry"),
        dlq=DeadLetterQueue(producer, "events.dlq"),
    )


# ------------------------------------------------------------- happy paths


def test_valid_event_persists_and_commits():
    consumer = FakeConsumer()
    producer = RecordingProducer()
    sut = build_consumer(consumer=consumer, producer=producer)

    outcome = sut.process(make_record(valid_event_bytes()))

    assert outcome.action.value == "accept"
    assert outcome.committed is True
    assert consumer.commit_count == 1
    assert producer.call_count == 0


def test_duplicate_event_is_accepted_without_dlq():
    duplicate = IntegrityError(
        "INSERT",
        {},
        Exception(
            "duplicate key value violates unique constraint "
            '"events_pkey" DETAIL: Key (event_id)=(evt-1) already exists.'
        ),
    )
    consumer = FakeConsumer()
    producer = RecordingProducer()
    sut = build_consumer(
        consumer=consumer,
        producer=producer,
        router=RaisingHandler(duplicate),
    )

    outcome = sut.process(make_record())

    # Duplicate event_id is a safe no-op, so it is acknowledged.
    assert outcome.action.value == "accept"
    assert outcome.committed is True
    assert producer.call_count == 0
# -------------------------------------------------- validation -> DLQ


def test_invalid_utf8_goes_to_dlq_then_commits():
    consumer = FakeConsumer()
    producer = RecordingProducer()
    sut = build_consumer(consumer=consumer, producer=producer)

    outcome = sut.process(make_record(b"\xff\xfe\x00"))

    assert outcome.action.value == "dlq"
    assert outcome.committed is True
    assert consumer.commit_count == 1
    assert len(producer.published) == 1

    payload = producer.payloads()[0]
    assert payload["failure"]["code"] == "INVALID_UTF8"
    assert payload["failure"]["stage"] == "validation"
    assert payload["source"]["offset"] == 0


def test_invalid_json_goes_to_dlq_then_commits():
    consumer = FakeConsumer()
    producer = RecordingProducer()
    sut = build_consumer(consumer=consumer, producer=producer)

    outcome = sut.process(make_record(b"{not json"))

    assert outcome.action.value == "dlq"
    assert outcome.committed is True
    assert producer.payloads()[0]["failure"]["code"] == "INVALID_JSON"


def test_oversized_payload_is_dead_lettered_not_looped():
    # An oversize record must reach the DLQ rather than spin forever.
    consumer = FakeConsumer()
    producer = RecordingProducer()
    sut = build_consumer(consumer=consumer, producer=producer)

    outcome = sut.process(make_record(b"x" * 700_000))

    assert outcome.action.value == "dlq"
    assert outcome.committed is True
    assert producer.payloads()[0]["failure"]["code"] == "MESSAGE_TOO_LARGE"


# ------------------------------------------------ publish-failure windows


def test_dlq_publish_failure_does_not_commit():
    # The single most important assertion in the suite.
    producer = RecordingProducer(fail_with=RetryablePublishError("broker down"))
    consumer = FakeConsumer()
    sut = build_consumer(consumer=consumer, producer=producer)

    outcome = sut.process(make_record(b"\xff\xfe"))

    assert outcome.action.value == "dlq"
    assert outcome.committed is False
    assert consumer.commit_count == 0, (
        "offset must NOT be committed when the DLQ publish fails"
    )


def test_permanent_handler_failure_goes_to_dlq_then_commits():
    consumer = FakeConsumer()
    producer = RecordingProducer()
    sut = build_consumer(
        consumer=consumer,
        producer=producer,
        router=RaisingHandler(HandlerError("permanent")),
    )

    outcome = sut.process(make_record())

    assert outcome.action.value == "dlq"
    assert outcome.committed is True
    assert consumer.commit_count == 1

    payload = producer.payloads()[0]
    assert payload["failure"]["stage"] == "handler"
    # A validated event reaches the DLQ directly, never re-parsed from bytes.
    assert payload["event"]["event_id"] == "evt-1"


def test_unknown_error_is_dead_lettered_on_exhaustion_not_acknowledged():
    consumer = FakeConsumer()
    producer = RecordingProducer()
    sut = build_consumer(
        consumer=consumer,
        producer=producer,
        router=RaisingHandler(RuntimeError("unexpected")),
        retry_config=RetryConfig(max_attempts=1),
    )

    outcome = sut.process(make_record())

    # Unknown failures fail safely into the DLQ, never silently acknowledged.
    assert outcome.action.value == "dlq"
    assert outcome.committed is True
    assert producer.payloads()[0]["failure"]["stage"] == "retry_exhausted"
# ------------------------------------------------------------------ retry


def test_transient_failure_goes_to_retry_topic_then_commits():
    consumer = FakeConsumer()
    producer = RecordingProducer()
    sut = build_consumer(
        consumer=consumer,
        producer=producer,
        router=RaisingHandler(TransientDBError("db down")),
    )

    outcome = sut.process(make_record())

    assert outcome.action.value == "retry"
    assert outcome.committed is True
    assert consumer.commit_count == 1
    assert producer.published[0]["topic"] == "events.retry"

    payload = producer.payloads()[0]
    assert payload["attempt"] == 1
    assert "due_at" in payload


def test_retry_publish_failure_does_not_commit():
    producer = RecordingProducer(fail_with=RetryablePublishError("down"))
    consumer = FakeConsumer()
    sut = build_consumer(
        consumer=consumer,
        producer=producer,
        router=RaisingHandler(TransientDBError("db down")),
    )

    outcome = sut.process(make_record())

    assert outcome.action.value == "retry"
    assert outcome.committed is False
    assert consumer.commit_count == 0


def test_retry_attempt_header_increments_attempts():
    # Attempts survive a restart because they ride the header.
    consumer = FakeConsumer()
    producer = RecordingProducer()
    sut = build_consumer(
        consumer=consumer,
        producer=producer,
        router=RaisingHandler(TransientDBError("db down")),
        retry_config=RetryConfig(max_attempts=5),
    )

    outcome = sut.process(make_record(headers={"attempt": "3"}))

    assert outcome.attempt == 4
    assert producer.payloads()[0]["attempt"] == 4


def test_max_attempt_dead_letters_instead_of_looping():
    consumer = FakeConsumer()
    producer = RecordingProducer()
    sut = build_consumer(
        consumer=consumer,
        producer=producer,
        router=RaisingHandler(TransientDBError("db down")),
        retry_config=RetryConfig(max_attempts=3),
    )

    outcome = sut.process(make_record(headers={"attempt": "3"}))

    assert outcome.action.value == "dlq"
    assert producer.payloads()[0]["failure"]["stage"] == "retry_exhausted"


def test_malformed_attempt_header_degrades_to_zero():
    consumer = FakeConsumer()
    producer = RecordingProducer()
    sut = build_consumer(
        consumer=consumer,
        producer=producer,
        router=RaisingHandler(TransientDBError("db down")),
    )

    outcome = sut.process(make_record(headers={"attempt": "not-a-number"}))

    # Degrades safely rather than crashing the consumer loop.
    assert outcome.attempt == 1


# ------------------------------------------------------- crash-window view


def test_redelivery_after_uncommitted_dlq_failure_is_idempotent():
    # Documented at-least-once behaviour. A DLQ publish that is not
    # confirmed leaves the offset uncommitted, so redelivery repeats it. The
    # alternative - committing anyway - would lose the record entirely.
    producer = RecordingProducer()
    consumer = FakeConsumer()
    sut = build_consumer(consumer=consumer, producer=producer)

    producer.fail_with = RetryablePublishError("down")
    first = sut.process(make_record(b"\xff\xfe"))
    assert first.committed is False

    producer.fail_with = None
    second = sut.process(make_record(b"\xff\xfe"))

    assert second.committed is True
    assert len(producer.published) == 1


def test_duplicate_dlq_after_crash_is_distinguishable():
    # A crash between a confirmed DLQ publish and the offset commit causes a
    # second DLQ record. It is a distinct record (fresh dlq_id), not a silent
    # overwrite: the duplicate is detectable, which is what replay tooling
    # needs.
    producer = RecordingProducer()
    consumer = FakeConsumer()
    sut = build_consumer(consumer=consumer, producer=producer)

    first = sut.process(make_record(b"\xff\xfe", offset=5))
    second = sut.process(make_record(b"\xff\xfe", offset=5))

    payloads = producer.payloads()
    assert payloads[0]["dlq_id"] != payloads[1]["dlq_id"]
    assert payloads[0]["source"]["offset"] == 5


def test_processing_never_sleeps_on_retry_backoff():
    # The loop must not block; backoff is deferred via the retry topic.
    import time

    consumer = FakeConsumer()
    producer = RecordingProducer()
    sut = build_consumer(
        consumer=consumer,
        producer=producer,
        router=RaisingHandler(TransientDBError("db down")),
        retry_config=RetryConfig(base_delay=30.0, max_attempts=5),
    )

    started = time.monotonic()
    outcome = sut.process(make_record())

    assert outcome.action.value == "retry"
    assert time.monotonic() - started < 1.0


# ------------------------------------------------------------- shutdown


def test_commit_is_explicit_per_partition_not_bare():
    # Regression: a bare confluent commit() advances EVERY assigned
    # partition. If partition 0's record is not durable while partition 1's
    # is, a bare commit would silently acknowledge partition 0 and lose the
    # record. Commits must carry an explicit position.
    consumer = FakeConsumer()
    producer = RecordingProducer()
    sut = build_consumer(consumer=consumer, producer=producer)

    sut.process(make_record(valid_event_bytes(), partition=1, offset=41))

    assert consumer.committed_positions() == [("events", 1, 42)], (
        "commit must name the partition and next offset explicitly"
    )


def test_failed_record_partition_is_not_committed_by_another_record():
    # Two records on DIFFERENT partitions. The first (partition 0) fails to
    # dead-letter; the second (partition 1) succeeds. Partition 0 must not
    # appear in any commit.
    producer = RecordingProducer(fail_with=RetryablePublishError("down"))
    consumer = FakeConsumer()
    sut = build_consumer(consumer=consumer, producer=producer)

    failed = sut.process(make_record(b"\xff\xfe", partition=0, offset=10))
    assert failed.committed is False

    producer.fail_with = None
    ok = sut.process(make_record(valid_event_bytes(), partition=1, offset=20))
    assert ok.committed is True

    committed = consumer.committed_positions()
    assert committed == [("events", 1, 21)], (
        "only the durable record's partition may be committed"
    )


def test_run_closes_consumer_without_committing_unprocessed():
    consumer = FakeConsumer([])
    sut = build_consumer(consumer=consumer, producer=RecordingProducer())

    # idle_poll_limit bounds the loop so an empty source cannot busy-spin.
    outcomes = sut.run(idle_poll_limit=3)

    # Nothing to process; shutdown must not commit anything.
    assert outcomes == []
    assert consumer.closed is True
    assert consumer.commit_count == 0


def test_run_stops_when_requested():
    consumer = FakeConsumer([])
    sut = build_consumer(consumer=consumer, producer=RecordingProducer())

    sut.stop()
    outcomes = sut.run()

    assert outcomes == []
    assert consumer.closed is True
    assert consumer.commit_count == 0


def test_run_processes_batch_and_commits_each():
    consumer = FakeConsumer(
        [
            make_record(valid_event_bytes(), offset=1),
            make_record(valid_event_bytes(), offset=2),
        ]
    )
    producer = RecordingProducer()
    sut = build_consumer(consumer=consumer, producer=producer)

    outcomes = sut.run(max_records=2)

    assert len(outcomes) == 2
    assert all(o.committed for o in outcomes)
    assert consumer.commit_count == 2
    assert consumer.closed is True


def test_idle_loop_does_not_busy_spin():
    # A poll timeout paces the loop; an empty source must not spin on
    # process() and must terminate when bounded.
    import time

    consumer = FakeConsumer([])
    sut = build_consumer(consumer=consumer, producer=RecordingProducer())

    started = time.monotonic()
    outcomes = sut.run(idle_poll_limit=5)

    assert outcomes == []
    assert time.monotonic() - started < 2.0