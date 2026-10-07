"""Shared fakes for Kafka ingestion tests.

These test the APPLICATION layer, which is transport-agnostic. They do NOT
prove broker semantics: a fake producer that returns None mimics a
successful publish, and real ``confluent_kafka`` delivery-report behaviour
is only exercised by infrastructure tests against a real broker. See the
"Remaining Risks" section of ``Kafka_analysis.md``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest

from ai_eval_platform.ingestion.dlq import DeadLetterQueue
from ai_eval_platform.ingestion.kafka_record import KafkaRecord
from ai_eval_platform.ingestion.producer import PublishError
from ai_eval_platform.ingestion.retry_publisher import RetryPublisher


class RecordingProducer:
    """A ConfirmedProducer double that records confirmed publications."""

    def __init__(self, fail_with: Exception | None = None) -> None:
        self.published: list[dict[str, Any]] = []
        self.fail_with = fail_with
        self.call_count = 0

    def publish(
        self,
        *,
        topic: str,
        value: bytes,
        key: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        self.call_count += 1

        if self.fail_with is not None:
            raise self.fail_with

        self.published.append(
            {
                "topic": topic,
                "value": value,
                "key": key,
                "headers": headers or {},
            }
        )

    def payloads(self) -> list[dict[str, Any]]:
        return [json.loads(p["value"]) for p in self.published]


class FakeConsumer:
    """A RecordSource double that records explicit commit positions.

    ``committed`` holds the actual ``TopicPosition`` values passed in, so a
    test can assert *which* partition/offset was acknowledged rather than
    just that "a commit happened".
    """

    def __init__(self, records: list[KafkaRecord] | None = None) -> None:
        self._records = list(records or [])
        self.commit_count = 0
        self.committed: list[list] = []
        self.closed = False
        self.paused: list[KafkaRecord] = []
        self.resumed: list[KafkaRecord] = []

    def poll(self, timeout_s: float) -> list[KafkaRecord]:
        if not self._records:
            return []

        return [self._records.pop(0)]

    def commit(self, positions) -> None:
        self.commit_count += 1
        self.committed.append(list(positions))

    def committed_positions(self) -> list[tuple[str, int, int]]:
        """Flattened (topic, partition, next_offset) actually acknowledged."""
        return [
            (p.topic, p.partition, p.next_offset)
            for batch in self.committed
            for p in batch
        ]

    def pause(self, record: KafkaRecord) -> None:
        self.paused.append(record)

    def resume(self, record: KafkaRecord) -> None:
        self.resumed.append(record)

    def close(self) -> None:
        self.closed = True


def make_record(
    value: bytes | dict[str, Any] | None = None,
    *,
    topic: str = "events",
    partition: int = 0,
    offset: int = 0,
    key: str | None = "run-1",
    headers: dict[str, str] | None = None,
) -> KafkaRecord:
    if value is None:
        value = valid_event_bytes()
    elif isinstance(value, dict):
        value = json.dumps(value).encode("utf-8")

    return KafkaRecord(
        topic=topic,
        partition=partition,
        offset=offset,
        value=value,
        key=key,
        timestamp_ms=1_700_000_000_000,
        headers=headers or {},
    )


def valid_event_dict(
    *,
    event_id: str = "evt-1",
    run_id: str = "run-1",
    event_type: str = "model_called",
    **overrides: Any,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "event_id": event_id,
        "schema_version": "1.0",
        "system_id": "gaia-agent",
        "system_version": "1.0.0",
        "run_id": run_id,
        "event_type": event_type,
        "timestamp": "2026-10-04T00:00:00+00:00",
        "payload": {
            "model_id": "qwen2.5:3b",
            "output_id": "out-1",
            "output_text": "hello",
        },
    }
    event.update(overrides)
    return event


def valid_event_bytes(**overrides: Any) -> bytes:
    return json.dumps(valid_event_dict(**overrides)).encode("utf-8")


def fixed_clock() -> Any:
    return lambda: datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def producer() -> RecordingProducer:
    return RecordingProducer()


@pytest.fixture()
def dlq(producer: RecordingProducer) -> DeadLetterQueue:
    return DeadLetterQueue(
        producer,
        "events.dlq",
        clock=fixed_clock(),
    )


@pytest.fixture()
def retry_publisher(producer: RecordingProducer) -> RetryPublisher:
    return RetryPublisher(producer, "events.retry", clock=fixed_clock())