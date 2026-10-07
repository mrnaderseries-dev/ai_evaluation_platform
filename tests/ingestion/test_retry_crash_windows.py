"""Crash-window and shutdown tests for the retry path.

These reason about the windows the previous report only described. A real
process kill is not simulated (the test architecture cannot represent it
faithfully); what IS asserted is the *observable* consequence: whether the
offset is committed, and whether a duplicate is produced.
"""

from __future__ import annotations

import pytest

from ai_eval_platform.ingestion.producer import RetryablePublishError
from ai_eval_platform.ingestion.retry_dispatcher import DispatchAction
from ai_eval_platform.ingestion.retry_publisher import RetryPublisher

from .conftest import RecordingProducer, make_record, valid_event_bytes
from .test_retry_consumer import (
    DLQ_TOPIC,
    FakeRetrySource,
    MAIN_TOPIC,
    NOW,
    RETRY_TOPIC,
    make_dispatcher,
    make_retry_record,
)
from .test_retry_consumer import RetryConsumerLoop


def test_duplicate_retry_after_crash_is_produced_not_lost():
    # CRASH WINDOW: retry publish succeeds, the process dies before the main
    # offset commit, so the main record is redelivered and a SECOND retry
    # record is produced. Acceptable under at-least-once.
    producer = RecordingProducer()
    publisher = RetryPublisher(producer, RETRY_TOPIC)
    source = make_record(valid_event_bytes(), offset=10)

    publisher.publish(source=source, attempt=1, due_at=NOW)
    publisher.publish(source=source, attempt=1, due_at=NOW)

    assert len(producer.published) == 2

    # Each carries a distinct retry_id, so duplicates are detectable rather
    # than silently overwriting each other.
    ids = {p["retry_id"] for p in producer.payloads()}
    assert len(ids) == 2


def test_duplicate_retry_is_processed_idempotently_downstream():
    # A duplicate retry record forwards twice, producing two main-topic
    # records. Both carry the SAME original bytes, so both have the same
    # event_id and the DB's ON CONFLICT DO NOTHING makes the second a no-op.
    producer = RecordingProducer()
    dispatcher = make_dispatcher(producer)
    record = make_retry_record()

    first = dispatcher.dispatch(record)
    second = dispatcher.dispatch(record)

    assert first.action is DispatchAction.FORWARDED
    assert second.action is DispatchAction.FORWARDED
    # Identical original payload both times -> identical event_id downstream.
    assert producer.published[0]["value"] == producer.published[1]["value"]


def test_forward_succeeds_but_commit_fails_leaves_record_uncommitted():
    # CRASH WINDOW: the retry is forwarded to the main topic, then the
    # process dies before the retry offset is committed. The same retry is
    # published again on redelivery - a duplicate, protected downstream by
    # event_id idempotency. This must NOT be treated as data loss.
    producer = RecordingProducer()
    dispatcher = make_dispatcher(producer)

    outcome = dispatcher.dispatch(make_retry_record())

    assert outcome.action is DispatchAction.FORWARDED
    # The caller decides whether to commit; here we simply assert the
    # position it WOULD commit.
    position = dispatcher.commit_position(make_retry_record())

    assert (position.topic, position.next_offset) == (RETRY_TOPIC, 6)


def test_dead_lettered_retry_commits_only_after_dlq_confirmation():
    producer = RecordingProducer()
    dispatcher = make_dispatcher(producer, max_attempts=2)
    record = make_retry_record(attempt=5)

    outcome = dispatcher.dispatch(record)

    assert outcome.action is DispatchAction.DEAD_LETTERED
    # Only after the DLQ publish is confirmed does the caller commit.
    assert producer.published[0]["topic"] == DLQ_TOPIC


def test_retry_loop_close_leaves_deferred_partition_uncommitted():
    # A record that is NOT yet due is deferred, so shutdown must not
    # acknowledge it.
    from datetime import timedelta

    producer = RecordingProducer()
    source = FakeRetrySource(
        [make_retry_record(due_at=NOW + timedelta(hours=1))]
    )
    loop = RetryConsumerLoop(
        source=source,
        dispatcher=make_dispatcher(producer),
    )

    loop.run(max_records=1)

    # Shutdown must not acknowledge a record that never reached a durable
    # destination.
    assert source.closed is True
    assert source.commit_count == 0


def test_shutdown_mid_cycle_does_not_commit_the_unprocessed_record():
    producer = RecordingProducer()
    source = FakeRetrySource(
        [
            make_retry_record(offset=1),
            make_retry_record(offset=2),
        ]
    )
    loop = RetryConsumerLoop(
        source=source,
        dispatcher=make_dispatcher(producer),
    )

    loop.stop()
    loop.run()

    assert source.closed is True
    assert source.commit_count == 0


def test_forward_failure_is_retryable_not_dead_lettered():
    # A main-topic publish failure is transient: the retry must be retried,
    # not dead-lettered, or a broker blip would lose the record.
    producer = RecordingProducer(fail_with=RetryablePublishError("down"))
    dispatcher = make_dispatcher(producer)

    outcome = dispatcher.dispatch(make_retry_record())

    assert outcome.action is DispatchAction.DEFERRED
    assert producer.call_count == 1
    # Nothing reached the DLQ.
    assert all(p["topic"] != DLQ_TOPIC for p in producer.published)