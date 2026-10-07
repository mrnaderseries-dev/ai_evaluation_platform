"""Deferred-retry transport: publish a failed record with a future due time.

The main loop never sleeps. A transient failure is published to a retry topic
with a ``due_at`` timestamp; a retry consumer forwards it back to the main
topic once it is due. This keeps the partition being polled, so
``max.poll.interval.ms`` is never at risk from retry backoff.

RetryPolicy decides *when* (delay); this module decides *how to transport*.
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field

from .kafka_record import KafkaRecord
from .producer import (
    ConfirmedProducer,
    MessageTooLargeError,
    PublishError,
    RetryablePublishError,
)

RETRY_SCHEMA_VERSION = "1.0"

MAX_RETRY_ENVELOPE_BYTES = 900_000


class RetryPublishError(PublishError):
    """The retry record was not confirmed by the broker.

    The consumer must not commit the source offset when this is raised.
    """


class RetryRecord(BaseModel):
    """Envelope carrying a failed record across the retry topic.

    Carrying ``attempt`` in the message (not just in process memory) is what
    makes the retry bound survive a consumer restart: a redeployed consumer
    resumes counting where the message left off instead of restarting at 1.
    """

    model_config = ConfigDict(extra="forbid")

    retry_schema_version: str = RETRY_SCHEMA_VERSION
    retry_id: str
    attempt: int = Field(ge=1)
    due_at: datetime
    source_topic: str
    source_partition: int = Field(ge=0)
    source_offset: int = Field(ge=0)
    original_key: str | None = None
    original_value_base64: str

    def serialized(self) -> bytes:
        return json.dumps(
            self.model_dump(mode="json"),
            separators=(",", ":"),
        ).encode("utf-8")

    def original_bytes(self) -> bytes:
        return base64.b64decode(self.original_value_base64)


class RetryPublisher:
    """Publishes deferred-retry records, confirming broker delivery."""

    def __init__(
        self,
        producer: ConfirmedProducer,
        topic: str,
        *,
        max_envelope_bytes: int = MAX_RETRY_ENVELOPE_BYTES,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not topic:
            raise ValueError("Retry topic must not be empty.")

        self._producer = producer
        self._topic = topic
        self._max_envelope_bytes = max_envelope_bytes
        self._clock = clock

    def build_record(
        self,
        *,
        source: KafkaRecord,
        attempt: int,
        due_at: datetime,
    ) -> RetryRecord:
        return RetryRecord(
            retry_id=str(uuid.uuid4()),
            attempt=attempt,
            due_at=due_at,
            source_topic=source.topic,
            source_partition=source.partition,
            source_offset=source.offset,
            original_key=source.key,
            original_value_base64=base64.b64encode(source.value).decode(
                "ascii"
            ),
        )

    def publish(
        self,
        *,
        source: KafkaRecord,
        attempt: int,
        due_at: datetime,
    ) -> RetryRecord:
        """Publish to the retry topic; raises if the broker did not confirm."""
        envelope = self.build_record(
            source=source,
            attempt=attempt,
            due_at=due_at,
        )

        payload = envelope.serialized()

        if len(payload) > self._max_envelope_bytes:
            raise RetryPublishError(
                f"Retry envelope of {len(payload)} bytes exceeds the maximum "
                f"of {self._max_envelope_bytes} bytes; the source record is "
                "too large to defer safely and must be dead-lettered instead."
            )

        # Keyed by the original key so a retried record stays on the same
        # partition as the source record, preserving per-run ordering when it
        # is forwarded back to the main topic.
        key_value = source.key

        try:
            self._producer.publish(
                topic=self._topic,
                value=payload,
                key=key_value.encode("utf-8") if key_value else None,
                headers={
                    "retry_schema_version": envelope.retry_schema_version,
                    "retry_id": envelope.retry_id,
                    "attempt": str(envelope.attempt),
                    "source_topic": envelope.source_topic,
                },
            )
        except MessageTooLargeError as exc:
            raise RetryPublishError(
                f"Retry record rejected as too large: {exc}"
            ) from exc
        except PublishError as exc:
            raise RetryPublishError(
                f"Failed to publish retry for {source.position} "
                f"attempt {attempt}: {exc}"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise RetryPublishError(
                f"Unexpected producer failure for retry {source.position}: "
                f"{exc}"
            ) from exc

        return envelope

    @staticmethod
    def is_retryable_publish_error(error: BaseException) -> bool:
        """Whether retrying the *publication* itself could succeed."""
        return isinstance(error, (RetryablePublishError, RetryPublishError))