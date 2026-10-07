"""Retry policy and retry-envelope tests.

Covers required test case 12 (retry attempt propagation) and the retry
exhaustion bound (test case 8's policy half).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ai_eval_platform.ingestion.producer import RetryablePublishError
from ai_eval_platform.ingestion.retry_policy import (
    RetryConfig,
    RetryDecision,
    RetryPolicy,
)
from ai_eval_platform.ingestion.retry_publisher import (
    RetryPublisher,
    RetryPublishError,
)

from .conftest import RecordingProducer, make_record


def due_at(seconds: float = 1.0) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def test_retry_policy_allries_until_max_attempts():
    policy = RetryPolicy(RetryConfig(max_attempts=3))

    assert policy.evaluate(1).should_retry is True
    assert policy.evaluate(2).should_retry is True
    assert policy.evaluate(3).should_retry is False


def test_retry_policy_reports_exhausted_action():
    policy = RetryPolicy(RetryConfig(max_attempts=2))

    decision = policy.evaluate(2)

    assert decision.action.value == "exhausted"
    assert decision.delay_seconds == 0.0


def test_retry_delay_is_exponential_and_capped():
    policy = RetryPolicy(
        RetryConfig(
            base_delay=1.0, max_delay=4.0, jitter=False, max_attempts=10
        )
    )

    assert policy.evaluate(1).delay_seconds == 1.0
    assert policy.evaluate(2).delay_seconds == 2.0
    assert policy.evaluate(3).delay_seconds == 4.0
    # Capped, not growing unbounded.
    assert policy.evaluate(4).delay_seconds == 4.0


def test_retry_jitter_stays_within_bounds():
    policy = RetryPolicy(
        RetryConfig(base_delay=1.0, max_delay=10.0, jitter=True)
    )

    for attempt in range(1, 6):
        delay = policy.evaluate(attempt).delay_seconds
        assert 0.0 <= delay <= 10.0


def test_retry_policy_rejects_invalid_attempt():
    with pytest.raises(ValueError):
        RetryPolicy().evaluate(0)


def test_retry_config_rejects_incoherent_values():
    with pytest.raises(ValueError):
        RetryConfig(base_delay=10.0, max_delay=1.0)

    with pytest.raises(ValueError):
        RetryConfig(max_attempts=0)


def test_retry_record_carries_attempt_and_provenance(retry_publisher):
    record = make_record(offset=42, key="run-7")

    envelope = retry_publisher.publish(
        source=record,
        attempt=3,
        due_at=due_at(),
    )

    # The attempt survives publication, so a restarted consumer resumes
    # counting instead of restarting the retry count at 1.
    assert envelope.attempt == 3
    assert envelope.source_topic == "events"
    assert envelope.source_partition == 0
    assert envelope.source_offset == 42
    assert envelope.original_key == "run-7"
    assert envelope.original_bytes() == record.value


def test_retry_record_round_trips_original_payload(retry_publisher):
    raw = b"\xff\xfe not utf8 at all"

    envelope = retry_publisher.publish(
        source=make_record(raw),
        attempt=1,
        due_at=due_at(),
    )

    # Arbitrary bytes survive the retry hop intact.
    assert envelope.original_bytes() == raw


def test_retry_publish_failure_raises(retry_publisher, producer):
    producer.fail_with = RetryablePublishError("down")

    with pytest.raises(RetryPublishError):
        retry_publisher.publish(
            source=make_record(),
            attempt=1,
            due_at=due_at(),
        )


def test_retry_publish_maps_unexpected_fault(retry_publisher, producer):
    producer.fail_with = RuntimeError("unexpected")

    with pytest.raises(RetryPublishError):
        retry_publisher.publish(
            source=make_record(),
            attempt=1,
            due_at=due_at(),
        )


def test_retry_uses_original_key_to_preserve_partition(
    retry_publisher, producer
):
    retry_publisher.publish(
        source=make_record(key="run-9"),
        attempt=1,
        due_at=due_at(),
    )

    assert producer.published[0]["key"] == b"run-9"


def test_retry_rejects_empty_topic():
    with pytest.raises(ValueError):
        RetryPublisher(RecordingProducer(), "")


def test_retry_publisher_does_not_sleep(retry_publisher):
    # The retry path must never block the consumer loop; the delay lives in
    # the envelope's due_at and is honoured by the retry consumer instead.
    import time

    started = time.monotonic()

    retry_publisher.publish(
        source=make_record(),
        attempt=1,
        due_at=due_at(30.0),
    )

    assert time.monotonic() - started < 1.0