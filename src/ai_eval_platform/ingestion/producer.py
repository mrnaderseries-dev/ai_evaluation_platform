"""Application-level publish contract.

The critical property of this module is that ``publish()`` returning
successfully means *the broker has acknowledged the record*. This is the
contract that makes "publish to DLQ, then commit the Kafka offset" safe.

The naive shape ``producer.produce(...)`` followed by ``None`` return is
WRONG for ``confluent-kafka``: ``produce()`` only enqueues into a local
buffer and raises on almost nothing. Broker-side rejections (message too
large, unknown topic, not-leader, timeout) are reported asynchronously
through delivery reports, which are drained by ``poll()``/``flush()``.

See ``infrastructure/kafka/producer.py`` for the real implementation. A mock
producer that returns ``None`` does NOT prove broker semantics; see the
"Remaining Risks" section of ``Kafka_analysis.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class PublishError(Exception):
    """The record was not confirmed by the broker.

    Raised for synchronous enqueue failures *and* asynchronous delivery
    failures. Callers must treat this as "destination not durable" and must
    NOT commit the source offset.
    """


class MessageTooLargeError(PublishError):
    """The serialized record exceeded the broker/topic size limit."""


class RetryablePublishError(PublishError):
    """A transient publish failure (broker unavailable, timeout, not-leader)."""


class PermanentPublishError(PublishError):
    """A non-transient publish failure (unknown topic, auth, bad config)."""


@runtime_checkable
class ConfirmedProducer(Protocol):
    """A producer whose ``publish`` resolves only after broker delivery.

    Contract:

    * ``publish`` returns normally -> the broker acknowledged the record
      (delivery report received, no error).
    * ``publish`` raises :class:`PublishError` -> the record is NOT durable.

    Implementations must drain delivery reports; they must never return
    before the delivery report for this record has been observed.
    """

    def publish(
        self,
        *,
        topic: str,
        value: bytes,
        key: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        """Publish ``value`` and block until the broker confirms delivery."""
        ...


@runtime_checkable
class RecordSource(Protocol):
    """Minimal consumer-side read contract (poll + explicit commit)."""

    def poll(self, timeout_s: float) -> list: ...

    def commit(self, positions: list["TopicPosition"]) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class TopicPosition:
    """An explicit ``(topic, partition, next_offset)`` commit position.

    ``next_offset`` is the offset to resume from - i.e. ``record.offset + 1``,
    which is Kafka's "commit the next record to read" convention. Callers must
    pass this rather than relying on the client's implicit current position.

    Why this is explicit
    --------------------
    ``confluent_kafka.Consumer.commit()`` with **no** offsets argument commits
    the current position of *every assigned partition*. In a consumer that
    processes partitions independently, that silently acknowledges records
    that were never made durable: if partition 0's record failed to reach the
    DLQ but partition 1's succeeded, the bare ``commit()`` advances partition
    0 too and the failed record is lost.

    Commiting explicit per-partition positions keeps the guarantee "only the
    records that reached a durable destination are acknowledged".
    """

    topic: str
    partition: int
    next_offset: int

    def __post_init__(self) -> None:
        if self.next_offset < 0:
            raise ValueError("next_offset must not be negative")