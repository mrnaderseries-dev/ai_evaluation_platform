"""Durable failure record for records the pipeline could not process.

Responsibilities (deliberately narrow):

* Build a versioned, replayable DLQ envelope.
* Publish it through a :class:`ConfirmedProducer`, whose contract is that a
  normal return means *the broker acknowledged the record*.

It does NOT classify failures (:mod:`delivery_classifier` decides WHY) and
does NOT decide retry timing (:mod:`retry_policy` decides WHEN). It only
records and durably publishes.

At-least-once: if the consumer publishes to the DLQ and then crashes before
committing the source offset, the same record may be dead-lettered twice.
That is expected and acceptable; see ``Kafka_analysis.md``. Nothing here
claims exactly-once.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field

from schemas.events.common import AIEvent

from .event_validator import DLQ_ENVELOPE_BUDGET, ValidationFailure
from .kafka_record import KafkaRecord
from .producer import ConfirmedProducer, MessageTooLargeError, PublishError

DLQ_SCHEMA_VERSION = "1.0"

# Bounded so a pathological exception message cannot dominate the envelope
# or push it past the broker record-size limit.
MAX_ERROR_MESSAGE_CHARS = 2_000

# Final serialized envelope ceiling, shared with the validator so the
# accepted-input limit and the DLQ budget cannot drift apart. Must stay
# under the broker default (1_000_000 bytes) so a DLQ record is always
# publishable. Exceeding it raises rather than silently truncating
# provenance.
MAX_ENVELOPE_BYTES = DLQ_ENVELOPE_BUDGET

assert MAX_ENVELOPE_BYTES == DLQ_ENVELOPE_BUDGET, (
    "DLQ envelope budget must match the validator's budget"
)


class FailureStage(str, Enum):
    """Where in the pipeline the record failed."""

    VALIDATION = "validation"
    ROUTING = "routing"
    HANDLER = "handler"
    PERSISTENCE = "persistence"
    RETRY_EXHAUSTED = "retry_exhausted"
    UNKNOWN = "unknown"


class DlqPublishError(PublishError):
    """The DLQ record was not confirmed by the broker.

    Subclasses :class:`PublishError` so the consumer treats "DLQ failed" and
    "retry publish failed" identically: do not commit the source offset.
    """


class SourceMetadata(BaseModel):
    """Where the failed record came from in Kafka."""

    model_config = ConfigDict(extra="forbid")

    topic: str
    partition: int = Field(ge=0)
    offset: int = Field(ge=0)
    key: str | None = None
    record_timestamp_ms: int | None = None


class OriginalEvent(BaseModel):
    """A record that parsed into a domain envelope.

    ``event`` holds the JSON-mode dump so every value is serializable
    (enums -> values, datetimes -> ISO-8601). ``event_id``/``run_id``/
    ``event_type`` are denormalised for indexing and replay ordering.
    """

    model_config = ConfigDict(extra="forbid")

    event_id: str
    run_id: str
    event_type: str
    schema_version: str
    event: dict[str, Any]
    sequence_number: int | None = None
    correlation_id: str | None = None


class OriginalRaw(BaseModel):
    """A record that never became a domain envelope.

    Arbitrary bytes are stored base64-encoded so invalid UTF-8 survives a
    JSON round-trip intact. Replay sends these bytes back verbatim.
    """

    model_config = ConfigDict(extra="forbid")

    raw_payload_base64: str
    byte_length: int
    encoding: str = "base64"


class FailureInfo(BaseModel):
    """Machine-readable failure metadata.

    No stack traces are stored: they are large, leak local paths and
    variables, and are useless to a replayer running later on different
    infrastructure. They belong in consumer logs keyed by ``dlq_id``.
    """

    model_config = ConfigDict(extra="forbid")

    stage: FailureStage
    code: str
    error_type: str
    message: str = Field(max_length=MAX_ERROR_MESSAGE_CHARS)
    attempt: int = Field(ge=0)


class DlqRecord(BaseModel):
    """The versioned DLQ envelope.

    Either ``event`` or ``raw`` is populated, never both - a record is only
    "raw" when validation never produced an envelope.
    """

    model_config = ConfigDict(extra="forbid")

    dlq_schema_version: str = DLQ_SCHEMA_VERSION
    dlq_id: str
    created_at: datetime
    source: SourceMetadata
    event: OriginalEvent | None = None
    raw: OriginalRaw | None = None
    failure: FailureInfo

    def serialized(self) -> bytes:
        """Canonical UTF-8 JSON bytes, used for publishing and sizing."""
        return json.dumps(
            self.model_dump(mode="json"),
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")

    def serialized_size(self) -> int:
        return len(self.serialized())

    def replay_bytes(self) -> bytes:
        """The material to replay onto the source topic.

        For a parsed event this re-serializes the stored JSON; for a raw
        record it returns the original bytes verbatim. Preserving the exact
        original bytes is what makes replay of a poison record faithful.
        """
        if self.raw is not None:
            return base64.b64decode(self.raw.raw_payload_base64)

        assert self.event is not None
        return json.dumps(self.event.event, separators=(",", ":")).encode(
            "utf-8"
        )
def _truncate(message: str) -> str:
    if len(message) <= MAX_ERROR_MESSAGE_CHARS:
        return message

    return message[: MAX_ERROR_MESSAGE_CHARS - 15] + "...[truncated]"


class DeadLetterQueue:
    """Builds and durably publishes DLQ envelopes.

    ``publish`` returns only after the broker confirms the record, or raises
    :class:`DlqPublishError`. Callers must not commit the source Kafka offset
    when it raises.
    """

    def __init__(
        self,
        producer: ConfirmedProducer,
        topic: str,
        *,
        max_envelope_bytes: int = MAX_ENVELOPE_BYTES,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not topic:
            raise ValueError("DLQ topic must not be empty.")

        if max_envelope_bytes <= 0:
            raise ValueError("max_envelope_bytes must be positive")

        self._producer = producer
        self._topic = topic
        self._max_envelope_bytes = max_envelope_bytes
        self._clock = clock

    def build_failure_info(
        self,
        *,
        stage: FailureStage,
        code: str,
        error: BaseException,
        attempt: int,
    ) -> FailureInfo:
        """Build bounded, scrubbed failure metadata.

        Centralising this keeps message scrubbing (truncation and SQL/bound
        -parameter stripping) inside the DLQ, so no caller can accidentally
        bypass it by constructing :class:`FailureInfo` directly.
        """
        return FailureInfo(
            stage=stage,
            code=code,
            error_type=type(error).__name__,
            message=self._safe_message(error),
            attempt=attempt,
        )

    def build_record(
        self,
        *,
        record: KafkaRecord,
        failure: FailureInfo,
        event: AIEvent | None = None,
        validation_failure: ValidationFailure | None = None,
    ) -> DlqRecord:
        """Assemble the envelope for one failed record.

        Exactly one of ``event``/``validation_failure`` should be supplied:
        a validated event for routing/handler/persistence failures, or a
        validation failure (with preserved raw bytes) for decode/parse
        failures. Passing neither falls back to the record's raw bytes.
        """
        source = SourceMetadata(
            topic=record.topic,
            partition=record.partition,
            offset=record.offset,
            key=record.key,
            record_timestamp_ms=record.timestamp_ms,
        )

        original_event: OriginalEvent | None = None
        original_raw: OriginalRaw | None = None

        if event is not None:
            original_event = self._event_from_envelope(event)
        else:
            raw_bytes = (
                validation_failure.raw_payload
                if validation_failure is not None
                else record.value
            )

            original_raw = OriginalRaw(
                raw_payload_base64=base64.b64encode(raw_bytes).decode(
                    "ascii"
                ),
                byte_length=len(raw_bytes),
            )

        if validation_failure is not None:
            failure = failure.model_copy(
                update={
                    "code": validation_failure.code.value,
                    "message": _truncate(validation_failure.message),
                }
            )

        return DlqRecord(
            dlq_id=str(uuid.uuid4()),
            created_at=self._now(),
            source=source,
            event=original_event,
            raw=original_raw,
            failure=failure,
        )

    def publish(self, envelope: DlqRecord) -> None:
        """Publish the envelope, confirming broker delivery before returning.

        Raises :class:`DlqPublishError` when the record is not durable.
        """
        payload = envelope.serialized()

        if len(payload) > self._max_envelope_bytes:
            # Oversize is reported, never silently truncated: dropping
            # provenance would make the record unreplayable.
            raise DlqPublishError(
                f"DLQ envelope of {len(payload)} bytes exceeds the maximum "
                f"of {self._max_envelope_bytes} bytes."
            )

        # Keyed by run_id so a run's DLQ records stay on one partition and
        # replay preserves per-run ordering, matching the main topic. Raw
        # records have no run_id, so they fall back to the record key.
        key_value = self._routing_key(envelope)

        try:
            self._producer.publish(
                topic=self._topic,
                value=payload,
                key=key_value.encode("utf-8") if key_value else None,
                headers={
                    "dlq_schema_version": envelope.dlq_schema_version,
                    "dlq_id": envelope.dlq_id,
                    "failure_stage": envelope.failure.stage.value,
                    "failure_code": envelope.failure.code,
                    "source_topic": envelope.source.topic,
                },
            )
        except MessageTooLargeError as exc:
            raise DlqPublishError(
                f"DLQ record rejected as too large by the broker: {exc}"
            ) from exc
        except PublishError as exc:
            raise DlqPublishError(
                f"Failed to publish DLQ record {envelope.dlq_id}: {exc}"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            # Any producer fault must surface as DlqPublishError, otherwise a
            # consumer catching only PublishError would miss it and commit.
            raise DlqPublishError(
                f"Unexpected producer failure for DLQ record "
                f"{envelope.dlq_id}: {exc}"
            ) from exc
    def publish_failure(
        self,
        *,
        record: KafkaRecord,
        error: BaseException,
        stage: FailureStage,
        code: str,
        attempt: int,
        event: AIEvent | None = None,
        validation_failure: ValidationFailure | None = None,
    ) -> DlqRecord:
        """Build then publish; returns the confirmed envelope."""
        failure = FailureInfo(
            stage=stage,
            code=code,
            error_type=type(error).__name__,
            message=self._safe_message(error),
            attempt=attempt,
        )

        envelope = self.build_record(
            record=record,
            failure=failure,
            event=event,
            validation_failure=validation_failure,
        )

        self.publish(envelope)
        return envelope

    @staticmethod
    def _routing_key(envelope: DlqRecord) -> str | None:
        if envelope.event is not None:
            return envelope.event.run_id

        return envelope.source.key

    @staticmethod
    def _event_from_envelope(event: AIEvent) -> OriginalEvent:
        return OriginalEvent(
            event_id=event.event_id,
            run_id=event.run_id,
            event_type=event.event_type.value,
            schema_version=event.schema_version,
            event=json.loads(event.model_dump_json()),
            sequence_number=event.sequence_number,
            correlation_id=event.correlation_id,
        )

    @staticmethod
    def _safe_message(error: BaseException) -> str:
        """Exception text scrubbed of SQL statements and bound parameters.

        SQLAlchemy's ``DBAPIError.__str__`` embeds the compiled statement and
        the bound parameter tuple, which would copy payload values into the
        DLQ. ``str(exc.orig)`` gives the driver message only.
        """
        try:
            from sqlalchemy.exc import DBAPIError

            if isinstance(error, DBAPIError):
                return _truncate(str(error.orig))
        except ImportError:  # pragma: no cover
            pass

        return _truncate(str(error) or type(error).__name__)

    def _now(self) -> datetime:
        if self._clock is not None:
            return self._clock()

        return datetime.now(timezone.utc)


def decode_raw_payload(raw: OriginalRaw) -> bytes:
    """Decode a stored raw payload; raises on a corrupted envelope."""
    try:
        return base64.b64decode(raw.raw_payload_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(
            "DLQ record has a corrupt base64 raw payload"
        ) from exc
