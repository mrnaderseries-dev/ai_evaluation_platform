"""Kafka transport record, kept strictly separate from the domain event.

The ingestion layer receives :class:`KafkaRecord` values and turns them into
:class:`~schemas.events.common.AIEvent` envelopes. Kafka transport metadata
(topic, partition, offset) lives *here* and never inside the domain event.

Rationale: ``schemas/events/common.py`` defines ``AIEvent`` with
``extra="forbid"`` and no Kafka fields. Adding transport metadata to the
domain event would couple the schema to the transport and break the
Event Layer contract, so the separation is deliberate.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class KafkaRecord:
    """One undecoded Kafka record plus its transport coordinates.

    ``value`` is the raw ``bytes`` payload exactly as received. The validator
    owns decoding; this class never parses anything.
    """

    topic: str
    partition: int
    offset: int
    value: bytes
    key: str | None = None
    timestamp_ms: int | None = None
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def position(self) -> str:
        """Human/diagnostic identity of this record, e.g. ``topic[0]@12``."""
        return f"{self.topic}[{self.partition}]@{self.offset}"