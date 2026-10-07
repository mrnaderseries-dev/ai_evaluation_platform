"""``confluent_kafka`` consumer adapter.

Offset discipline
-----------------
The broker is configured with ``enable.auto.commit=False`` (enforced by
:class:`KafkaSettings`). Offsets are committed explicitly by the application
*only after* the record's durable destination is confirmed.

Why explicit positions
----------------------
``confluent_kafka.Consumer.commit()`` called with **no** offsets argument
commits the current position of *every assigned partition*. That is unsafe
for this consumer: if partition 0's record failed to reach the DLQ while
partition 1's succeeded, the bare commit would advance partition 0 as well
and the undurable record would be silently skipped.

This client therefore requires the caller to pass explicit
:class:`TopicPosition` values and translates them into ``TopicPartition``
offsets. Only records the application has confirmed durable are ever
included, which is what preserves at-least-once delivery.

``next_offset`` follows Kafka's convention: committing ``N`` means "the next
record to read is N", i.e. ``record.offset + 1``.
"""

from __future__ import annotations

import logging
from typing import Any

from ai_eval_platform.ingestion.kafka_record import KafkaRecord
from ai_eval_platform.ingestion.producer import TopicPosition

from .config import KafkaSettings
from .errors import require_confluent_kafka

logger = logging.getLogger(__name__)


class KafkaConsumerClient:
    """A :class:`RecordSource` backed by ``confluent_kafka.Consumer``."""

    def __init__(
        self,
        settings: KafkaSettings,
        *,
        topics: list[str] | None = None,
    ) -> None:
        client = require_confluent_kafka()

        self._client = client
        self._settings = settings
        self._consumer = client.Consumer(settings.consumer_config())

        self._topics = topics or [settings.input_topic]
        self._pending: list[Any] = []
        self._closed = False
        self._subscribed = False

    def subscribe(self) -> None:
        if not self._subscribed:
            self._consumer.subscribe(self._topics)
            self._subscribed = True

    def poll(self, timeout_s: float) -> list[KafkaRecord]:
        """Return records available within ``timeout_s`` (possibly empty)."""
        if self._closed:
            return []

        self.subscribe()

        message = self._consumer.poll(timeout_s)

        if message is None:
            return []

        error = message.error()

        if error is not None:
            # A per-message error is surfaced, not swallowed: silently
            # dropping it would skip the record without dead-lettering it.
            raise self._map_poll_error(error)

        self._pending.append(message)

        return [self._to_record(message)]

    def commit(self, positions: list[TopicPosition]) -> None:
        """Commit the given explicit per-partition positions.

        Synchronous: a failed commit raises, so the application learns that
        the offsets were not stored and the records will be redelivered.
        Committing an empty list is a no-op.
        """
        if self._closed:
            raise RuntimeError("Cannot commit on a closed consumer")

        if not positions:
            return

        offsets = [
            self._client.TopicPartition(
                position.topic,
                position.partition,
                position.next_offset,
            )
            for position in positions
        ]

        # synchronous=True: surface commit failure to the caller rather than
        # silently losing offsets.
        self._consumer.commit(offsets=offsets, asynchronous=False)

    def pause(self, topic: str, partition: int) -> None:
        """Stop fetching from a partition without revoking the assignment.

        Used by the retry loop to park a partition whose head record is not
        yet due. Best-effort: pausing must never raise into the poll loop.
        """
        try:
            self._consumer.pause(
                [self._client.TopicPartition(topic, partition)]
            )
        except Exception:  # noqa: BLE001 - pausing is best-effort
            logger.debug("pause failed for %s[%s]", topic, partition)

    def resume(self, topic: str, partition: int) -> None:
        """Resume fetching a paused partition."""
        try:
            self._consumer.resume(
                [self._client.TopicPartition(topic, partition)]
            )
        except Exception:  # noqa: BLE001 - resuming is best-effort
            logger.debug("resume failed for %s[%s]", topic, partition)

    def close(self) -> None:
        """Close cleanly, leaving uncommitted offsets uncommitted.

        Anything already polled but not committed is intentionally left for
        redelivery: committing here would acknowledge records whose durable
        destination was never confirmed.
        """
        if self._closed:
            return

        self._closed = True
        self._pending.clear()

        try:
            self._consumer.close()
        except Exception:  # noqa: BLE001 - shutdown must not raise
            pass

    def _to_record(self, message: Any) -> KafkaRecord:
        key = message.key()

        headers = {
            name: (value.decode("utf-8", errors="replace") if value else "")
            for name, value in (message.headers() or [])
        }

        timestamp_type, timestamp_ms = message.timestamp()

        if timestamp_type == self._client.TIMESTAMP_NOT_AVAILABLE:
            timestamp_ms = None

        return KafkaRecord(
            topic=message.topic(),
            partition=message.partition(),
            offset=message.offset(),
            value=message.value() or b"",
            key=key.decode("utf-8", errors="replace") if key else None,
            timestamp_ms=timestamp_ms,
            headers=headers,
        )

    @staticmethod
    def _map_poll_error(error: object) -> RuntimeError:
        return RuntimeError(f"Kafka poll error: {error}")