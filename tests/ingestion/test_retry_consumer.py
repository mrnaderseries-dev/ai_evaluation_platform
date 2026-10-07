"""Retry dispatcher and retry consumer loop tests.

Unit-level: these prove the application's due-time decisions, attempt
semantics, and commit/no-commit behaviour. They use a producer double and
therefore do NOT prove broker semantics.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest

from ai_eval_platform.ingestion.kafka_record import KafkaRecord
from ai_eval_platform.ingestion.producer import RetryablePublishError
from ai_eval_platform.ingestion.retry_consumer import RetryConsumerLoop
from ai_eval_platform.ingestion.retry_dispatcher import (
    DispatchAction,
    MalformedRetryError,
    RetryDispatcher,
)
from ai_eval_platform.ingestion.retry_publisher import RetryRecord

from .conftest import RecordingProducer, make_record, valid_event_bytes

NOW = datetime(2026, 10, 4, 15, 10, tzinfo=timezone.utc)
MAIN_TOPIC = "ai.events"
DLQ_TOPIC = "ai.events.dlq"
RETRY_TOPIC = "ai.events.retry"


class FakeRetrySource:
    """A retry-topic consumer double recording commits and pauses."""

    def __init__(self, records: list[KafkaRecord] | None = None) -> None:
        self._records = list(records or [])
        self.commit_count = 0
        self.committed: list[list] = []
        self.paused: list[tuple[str, int]] = []
        self.resumed: list[tuple[str, int]] = []
        self.closed = False

    def poll(self, timeout_s: float) -> list[KafkaRecord]:
        if not self._records:
            return []

        return [self._records.pop(0)]

    def commit(self, positions) -> None:
        self.commit_count += 1
        self.committed.append(list(positions))

    def committed_positions(self) -> list[tuple[str, int, int]]:
        return [
            (p.topic, p.partition, p.next_offset)
            for batch in self.committed
            for p in batch
        ]

    def pause(self, topic: str, partition: int) -> None:
        self.paused.append((topic, partition))

    def resume(self, topic: str, partition: int) -> None:
        self.resumed.append((topic, partition))

    def close(self) -> None:
        self.closed = True


def make_retry_record(
    *,
    attempt: int = 1,
    due_at: datetime | None = None,
    payload: bytes | None = None,
    key: str | None = "run-1",
    topic: str = RETRY_TOPIC,
    partition: int = 0,
    offset: int = 5,
) -> KafkaRecord:
    envelope = RetryRecord(
        retry_id="r-1",
        attempt=attempt,
        due_at=due_at if due_at is not None else NOW - timedelta(seconds=1),
        source_topic=MAIN_TOPIC,
        source_partition=0,
        source_offset=42,
        original_key=key,
        original_value_base64=base64.b64encode(
            payload if payload is not None else valid_event_bytes()
        ).decode("ascii"),
    )

    return KafkaRecord(
        topic=topic,
        partition=partition,
        offset=offset,
        value=envelope.serialized(),
        key=key,
    )


def make_dispatcher(
    producer: RecordingProducer,
    *,
    max_attempts: int = 5,
) -> RetryDispatcher:
    return RetryDispatcher(
        producer=producer,
        main_topic=MAIN_TOPIC,
        dlq_topic=DLQ_TOPIC,
        max_attempts=max_attempts,
        clock=lambda: NOW,
    )
# ------------------------------------------------------------------ parsing


def test_retry_envelope_has_everything_needed_to_replay():
    envelope = RetryRecord.model_validate(
        json.loads(make_retry_record().value.decode("utf-8"))
    )

    # Original payload, key, topic, partition, offset, attempt, due_at.
    assert envelope.original_bytes() == valid_event_bytes()
    assert envelope.original_key == "run-1"
    assert envelope.source_topic == MAIN_TOPIC
    assert envelope.source_partition == 0
    assert envelope.source_offset == 42
    assert envelope.attempt == 1
    assert envelope.due_at is not None


def test_malformed_retry_raises_parse_error():
    with pytest.raises(MalformedRetryError):
        make_dispatcher(RecordingProducer()).parse(
            make_record(b"not json at all", topic=RETRY_TOPIC)
        )


def test_retry_record_missing_due_at_is_malformed():
    payload = {
        "retry_id": "r-1",
        "attempt": 1,
        "source_topic": "t",
        "source_partition": 0,
        "source_offset": 0,
        "original_value_base64": "eA==",
    }

    record = KafkaRecord(
        topic=RETRY_TOPIC,
        partition=0,
        offset=0,
        value=json.dumps(payload).encode("utf-8"),
    )

    with pytest.raises(MalformedRetryError):
        make_dispatcher(RecordingProducer()).parse(record)


# ---------------------------------------------------------------- due_at


def test_due_record_is_forwarded_to_main_topic():
    producer = RecordingProducer()

    outcome = make_dispatcher(producer).dispatch(make_retry_record())

    assert outcome.action is DispatchAction.FORWARDED
    published = producer.published[0]
    assert published["topic"] == MAIN_TOPIC
    # The ORIGINAL payload is republished, not the retry envelope.
    assert json.loads(published["value"])["event_id"] == "evt-1"


def test_future_record_is_deferred_not_forwarded():
    producer = RecordingProducer()

    outcome = make_dispatcher(producer).dispatch(
        make_retry_record(due_at=NOW + timedelta(minutes=20))
    )

    assert outcome.action is DispatchAction.DEFERRED
    assert producer.call_count == 0, "a future record must not be published"


def test_forward_preserves_original_key_for_run_ordering():
    producer = RecordingProducer()

    make_dispatcher(producer).dispatch(make_retry_record(key="run-42"))

    # Keyed by run_id so the record returns to its original partition.
    assert producer.published[0]["key"] == b"run-42"


def test_forward_carries_attempt_header_forward():
    producer = RecordingProducer()

    make_dispatcher(producer).dispatch(make_retry_record(attempt=2))

    # The main consumer's retry bound must survive the round-trip.
    assert producer.published[0]["headers"]["attempt"] == "2"


def test_forward_does_not_reuse_retry_partition_number():
    # Partition numbers are per-topic; the key, not the number, decides the
    # destination partition.
    producer = RecordingProducer()

    make_dispatcher(producer).dispatch(
        make_retry_record(partition=7, key="run-9")
    )

    assert producer.published[0]["key"] == b"run-9"
# ------------------------------------------------------- attempt semantics


def test_record_at_max_attempts_is_dead_lettered():
    producer = RecordingProducer()

    outcome = make_dispatcher(producer, max_attempts=3).dispatch(
        make_retry_record(attempt=3)
    )

    assert outcome.action is DispatchAction.DEAD_LETTERED
    assert producer.published[0]["topic"] == DLQ_TOPIC
    assert producer.payloads()[0]["failure"]["code"] == "RETRY_EXHAUSTED"


def test_excessive_attempt_cannot_loop_forever():
    # A wildly inflated attempt must terminate at the DLQ, not retry again.
    producer = RecordingProducer()

    outcome = make_dispatcher(producer, max_attempts=5).dispatch(
        make_retry_record(attempt=10_000)
    )

    assert outcome.action is DispatchAction.DEAD_LETTERED
    assert producer.published[0]["topic"] == DLQ_TOPIC


def test_attempt_below_max_is_forwarded():
    producer = RecordingProducer()

    outcome = make_dispatcher(producer, max_attempts=5).dispatch(
        make_retry_record(attempt=4)
    )

    assert outcome.action is DispatchAction.FORWARDED


@pytest.mark.parametrize("bad_attempt", [0, -3])
def test_invalid_attempt_is_dead_lettered_not_looped(bad_attempt):
    payload = json.loads(make_retry_record().value.decode("utf-8"))
    payload["attempt"] = bad_attempt

    record = KafkaRecord(
        topic=RETRY_TOPIC,
        partition=0,
        offset=0,
        value=json.dumps(payload).encode("utf-8"),
    )

    producer = RecordingProducer()
    outcome = make_dispatcher(producer).dispatch(record)

    assert outcome.action is DispatchAction.DEAD_LETTERED
    assert producer.payloads()[0]["failure"]["code"] == (
        "MALFORMED_RETRY_RECORD"
    )


# --------------------------------------------------------- poison records


def test_malformed_retry_goes_to_dlq():
    producer = RecordingProducer()

    outcome = make_dispatcher(producer).dispatch(
        make_record(b"{ broken", topic=RETRY_TOPIC)
    )

    assert outcome.action is DispatchAction.DEAD_LETTERED
    payload = producer.payloads()[0]
    assert payload["failure"]["code"] == "MALFORMED_RETRY_RECORD"
    assert payload["failure"]["stage"] == "retry_dispatch"


def test_malformed_retry_never_creates_an_infinite_loop():
    # A malformed record cannot be scheduled (due_at unknown), so retrying it
    # would loop forever. The DLQ is the deterministic terminal state.
    producer = RecordingProducer()

    outcome = make_dispatcher(producer).dispatch(
        make_record(b"{ broken", topic=RETRY_TOPIC)
    )

    assert outcome.action is not DispatchAction.DEFERRED
    assert producer.published[0]["topic"] == DLQ_TOPIC


def test_malformed_retry_dlq_preserves_raw_payload():
    producer = RecordingProducer()
    raw = b"\x00\xff broken"

    make_dispatcher(producer).dispatch(make_record(raw, topic=RETRY_TOPIC))

    assert producer.payloads()[0]["raw"]["byte_length"] == len(raw)


# ------------------------------------------------- publish failure paths


def test_forward_publish_failure_does_not_report_success():
    producer = RecordingProducer(fail_with=RetryablePublishError("down"))

    outcome = make_dispatcher(producer).dispatch(make_retry_record())

    # DEFERRED, never FORWARDED: the caller must not commit.
    assert outcome.action is DispatchAction.DEFERRED


def test_dlq_publish_failure_defers_instead_of_dropping():
    producer = RecordingProducer(fail_with=RetryablePublishError("down"))

    outcome = make_dispatcher(producer).dispatch(
        make_record(b"{ broken", topic=RETRY_TOPIC)
    )

    # The retry record must NOT disappear; it is retried.
    assert outcome.action is DispatchAction.DEFERRED
# ------------------------------------------------- consumer loop semantics


def test_loop_commits_after_forwarding():
    source = FakeRetrySource([make_retry_record()])
    producer = RecordingProducer()
    loop = RetryConsumerLoop(
        source=source,
        dispatcher=make_dispatcher(producer),
    )

    outcomes = loop.run(max_records=1)

    assert outcomes[0].action is DispatchAction.FORWARDED
    assert source.committed_positions() == [(RETRY_TOPIC, 0, 6)]


def test_loop_commits_after_dead_lettering():
    source = FakeRetrySource(
        [make_record(b"{ broken", topic=RETRY_TOPIC, offset=3)]
    )
    producer = RecordingProducer()
    loop = RetryConsumerLoop(
        source=source,
        dispatcher=make_dispatcher(producer),
    )

    outcomes = loop.run(max_records=1)

    assert outcomes[0].action is DispatchAction.DEAD_LETTERED
    assert source.committed_positions() == [(RETRY_TOPIC, 0, 4)]


def test_loop_does_not_commit_a_deferred_record():
    # The whole point: a not-yet-due record must not be acknowledged, or it
    # would be lost when its due time arrives.
    source = FakeRetrySource(
        [make_retry_record(due_at=NOW + timedelta(minutes=5))]
    )
    loop = RetryConsumerLoop(
        source=source,
        dispatcher=make_dispatcher(RecordingProducer()),
    )

    outcomes = loop.run(max_records=1)

    assert outcomes[0].action is DispatchAction.DEFERRED
    assert source.commit_count == 0


def test_loop_pauses_the_partition_when_deferring():
    source = FakeRetrySource(
        [make_retry_record(due_at=NOW + timedelta(minutes=5), partition=2)]
    )
    loop = RetryConsumerLoop(
        source=source,
        dispatcher=make_dispatcher(RecordingProducer()),
    )

    loop.run(max_records=1)

    # Pausing (not sleeping) keeps the poll loop alive.
    assert source.paused == [(RETRY_TOPIC, 2)]
    assert loop.paused_partitions == {(RETRY_TOPIC, 2)}


def test_loop_pauses_each_partition_once():
    source = FakeRetrySource(
        [
            make_retry_record(
                due_at=NOW + timedelta(minutes=5), offset=1, partition=0
            ),
            make_retry_record(
                due_at=NOW + timedelta(minutes=6), offset=2, partition=0
            ),
        ]
    )
    loop = RetryConsumerLoop(
        source=source,
        dispatcher=make_dispatcher(RecordingProducer()),
    )

    loop.run(max_records=2)

    # Idempotent: an already-paused partition is not paused again.
    assert source.paused == [(RETRY_TOPIC, 0)]


def test_loop_does_not_sleep_while_deferring():
    import time

    source = FakeRetrySource(
        [make_retry_record(due_at=NOW + timedelta(hours=1))]
    )
    loop = RetryConsumerLoop(
        source=source,
        dispatcher=make_dispatcher(RecordingProducer()),
    )

    started = time.monotonic()
    loop.run(max_records=1)
    elapsed = time.monotonic() - started

    # A record due in an hour must not cost an hour of wall clock.
    assert elapsed < 1.0


def test_loop_processes_due_records_around_a_future_one():
    # Different partitions: a future record on partition 0 must not block a
    # due record on partition 1.
    source = FakeRetrySource(
        [
            make_retry_record(
                due_at=NOW + timedelta(minutes=30),
                partition=0,
                offset=1,
            ),
            make_retry_record(partition=1, offset=1),
        ]
    )
    producer = RecordingProducer()
    loop = RetryConsumerLoop(
        source=source,
        dispatcher=make_dispatcher(producer),
    )

    outcomes = loop.run(max_records=2)

    assert outcomes[0].action is DispatchAction.DEFERRED
    assert outcomes[1].action is DispatchAction.FORWARDED
    # Only the due record was committed.
    assert source.committed_positions() == [(RETRY_TOPIC, 1, 2)]
def test_loop_forward_failure_leaves_retry_offset_uncommitted():
    producer = RecordingProducer(fail_with=RetryablePublishError("down"))
    source = FakeRetrySource([make_retry_record()])
    loop = RetryConsumerLoop(
        source=source,
        dispatcher=make_dispatcher(producer),
    )

    outcomes = loop.run(max_records=1)

    assert outcomes[0].action is DispatchAction.DEFERRED
    assert source.commit_count == 0, (
        "a failed forward must not acknowledge the retry record"
    )


def test_loop_does_not_loop_on_a_malformed_record():
    # A poison retry record terminates at the DLQ in a single pass.
    source = FakeRetrySource(
        [make_record(b"{ broken", topic=RETRY_TOPIC)]
    )
    producer = RecordingProducer()
    loop = RetryConsumerLoop(
        source=source,
        dispatcher=make_dispatcher(producer),
    )

    outcomes = loop.run(max_records=1, idle_poll_limit=3)

    assert outcomes[0].action is DispatchAction.DEAD_LETTERED
    assert source.commit_count == 1


def test_loop_shutdown_closes_source():
    source = FakeRetrySource([])
    loop = RetryConsumerLoop(
        source=source,
        dispatcher=make_dispatcher(RecordingProducer()),
    )

    loop.stop()
    loop.run()

    assert source.closed is True
    assert source.commit_count == 0


def test_loop_summary_counts_outcomes():
    source = FakeRetrySource(
        [
            make_retry_record(offset=1),
            make_retry_record(
                due_at=NOW + timedelta(minutes=5), offset=2, partition=1
            ),
        ]
    )
    loop = RetryConsumerLoop(
        source=source,
        dispatcher=make_dispatcher(RecordingProducer()),
    )

    outcomes = loop.run(max_records=2)
    summary = loop.summary(outcomes)

    assert summary.as_dict() == {
        "forwarded": 1,
        "deferred": 1,
        "dead_lettered": 0,
    }


def test_dispatcher_rejects_invalid_configuration():
    producer = RecordingProducer()

    with pytest.raises(ValueError):
        RetryDispatcher(
            producer=producer,
            main_topic="m",
            dlq_topic="d",
            max_attempts=0,
        )

    with pytest.raises(ValueError):
        RetryDispatcher(
            producer=producer,
            main_topic="",
            dlq_topic="d",
            max_attempts=3,
        )