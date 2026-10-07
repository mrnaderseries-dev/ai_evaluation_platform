from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import ValidationError

#: Hard ceiling for a serialized DLQ envelope. Shared with
#: :mod:`ai_eval_platform.ingestion.dlq` (as ``MAX_ENVELOPE_BYTES``) so the
#: validator's accepted-input limit can be checked against it.
#:
#: Kept below Kafka's default ``max.message.bytes`` (1_000_000) so a DLQ
#: record is always publishable. It is duplicated as a literal here rather
#: than imported so that importing the validator does not pull in the DLQ
#: module (and its producer/record dependencies); the DLQ module imports
#: this constant's owner, not the reverse.
DLQ_ENVELOPE_BUDGET = 900_000

from schemas.events.common import AIEvent, EventType

from schemas.events.execution_events import (
    AnswerGeneratedEvent,
    ErrorEvent,
    ModelCalledEvent,
    ModelOutputEvent,
    RecoveryAttemptedEvent,
    RecoveryCompletedEvent,
    RetrievalCompletedEvent,
    RetrievalStartedEvent,
    ToolCalledEvent,
    ToolCompletedEvent,
)

from schemas.events.context_events import (
    ContextAssembledEvent,
    ContextResourceUsedEvent,
    ContextUpdatedEvent,
)

from schemas.events.evaluation_events import (
    EvaluationCompletedEvent,
)

from schemas.events.policy_events import (
    BehaviorRuleTriggeredEvent,
    HumanReviewCompletedEvent,
    HumanReviewRequestedEvent,
    PolicyAllowedEvent,
    PolicyBlockedEvent,
    PolicyCheckedEvent,
)

from schemas.events.agent_behavior_events import (
    AbstentionEvent,
    AgentActionEvent,
    AnswerRevisedEvent,
    BehaviorPatternDetectedEvent,
    CounterEvidenceRequestedEvent,
    VerificationAttemptedEvent,
)


EVENT_MODELS: dict[EventType, type[AIEvent]] = {
    EventType.MODEL_CALLED: ModelCalledEvent,
    EventType.MODEL_OUTPUT: ModelOutputEvent,
    EventType.TOOL_CALLED: ToolCalledEvent,
    EventType.TOOL_COMPLETED: ToolCompletedEvent,
    EventType.RETRIEVAL_STARTED: RetrievalStartedEvent,
    EventType.RETRIEVAL_COMPLETED: RetrievalCompletedEvent,
    EventType.ANSWER_GENERATED: AnswerGeneratedEvent,
    EventType.ERROR: ErrorEvent,
    EventType.RECOVERY_ATTEMPTED: RecoveryAttemptedEvent,
    EventType.RECOVERY_COMPLETED: RecoveryCompletedEvent,
    EventType.CONTEXT_ASSEMBLED: ContextAssembledEvent,
    EventType.CONTEXT_RESOURCE_USED: ContextResourceUsedEvent,
    EventType.CONTEXT_UPDATED: ContextUpdatedEvent,
    EventType.EVALUATION_COMPLETED: EvaluationCompletedEvent,
    EventType.POLICY_CHECKED: PolicyCheckedEvent,
    EventType.POLICY_ALLOWED: PolicyAllowedEvent,
    EventType.POLICY_BLOCKED: PolicyBlockedEvent,
    EventType.BEHAVIOR_RULE_TRIGGERED: BehaviorRuleTriggeredEvent,
    EventType.HUMAN_REVIEW_REQUESTED: HumanReviewRequestedEvent,
    EventType.HUMAN_REVIEW_COMPLETED: HumanReviewCompletedEvent,
    EventType.AGENT_ACTION: AgentActionEvent,
    EventType.VERIFICATION_ATTEMPTED: VerificationAttemptedEvent,
    EventType.COUNTER_EVIDENCE_REQUESTED: CounterEvidenceRequestedEvent,
    EventType.ANSWER_REVISED: AnswerRevisedEvent,
    EventType.ABSTENTION: AbstentionEvent,
    EventType.BEHAVIOR_PATTERN_DETECTED: BehaviorPatternDetectedEvent,
}
class ValidationStage(str, Enum):
    """Which step of validation rejected the record."""

    DECODE = "decode"
    PARSE = "parse"
    ENVELOPE = "envelope"
    ROUTING = "routing"


class ValidationCode(str, Enum):
    """Machine-readable validation failure codes.

    These are part of the DLQ contract: replay tooling branches on them, so
    they must stay stable across refactors.
    """

    INVALID_UTF8 = "INVALID_UTF8"
    INVALID_JSON = "INVALID_JSON"
    NOT_A_JSON_OBJECT = "NOT_A_JSON_OBJECT"
    INVALID_ENVELOPE = "INVALID_ENVELOPE"
    UNSUPPORTED_SCHEMA_VERSION = "UNSUPPORTED_SCHEMA_VERSION"
    UNKNOWN_EVENT_TYPE = "UNKNOWN_EVENT_TYPE"
    SCHEMA_VALIDATION_ERROR = "SCHEMA_VALIDATION_ERROR"
    MESSAGE_TOO_LARGE = "MESSAGE_TOO_LARGE"
    INVALID_EVENT = "INVALID_EVENT"


@dataclass(frozen=True, slots=True)
class ValidationFailure:
    """A rejected record, with the original bytes preserved verbatim.

    The consumer must never try to reconstruct an event from a failed
    record; the raw payload is what gets dead-lettered and replayed.
    """

    code: ValidationCode
    stage: ValidationStage
    message: str
    raw_payload: bytes


@dataclass(frozen=True, slots=True)
class ValidationSuccess:
    """A validated envelope plus the bytes it was decoded from."""

    event: AIEvent
    raw_payload: bytes


ValidationResult = ValidationSuccess | ValidationFailure
class EventValidator:
    """Turns raw bytes into a typed :class:`AIEvent` or a structured failure.

    The validator owns raw byte handling, decoding, JSON parsing and Pydantic
    validation. It never raises for malformed input: every failure is
    returned as :class:`ValidationFailure` with the raw payload preserved, so
    the DLQ can record and replay bytes the validator could not decode.
    """

    #: Largest raw message accepted.
    #:
    #: This is deliberately well below Kafka's default ``max.message.bytes``
    #: (1_000_000). The limit is driven by the DLQ, not the main topic: a
    #: record that fails validation is stored base64-encoded in the DLQ
    #: envelope, and base64 inflates by 4/3. Combined with JSON overhead the
    #: envelope must still fit under ``dlq.MAX_ENVELOPE_BYTES`` (900_000),
    #: otherwise an oversize-but-accepted record could never be
    #: dead-lettered - and under the no-commit-on-DLQ-failure rule it would
    #: be redelivered forever.
    MAX_MESSAGE_BYTES = 600_000

    def __init__(
        self,
        *,
        max_message_bytes: int | None = None,
        supported_schema_versions: set[str] | None = None,
    ) -> None:
        if max_message_bytes is None:
            max_message_bytes = self.MAX_MESSAGE_BYTES

        if max_message_bytes <= 0:
            raise ValueError("max_message_bytes must be positive")

        # Guard the size-inversion defect: enforce that a maximum-size
        # accepted record still fits in a base64 DLQ envelope. base64
        # expands 3 bytes -> 4 characters, i.e. 4/3, plus a group pad.
        max_envelope = 4 * ((max_message_bytes + 2) // 3)

        if max_envelope > DLQ_ENVELOPE_BUDGET:
            raise ValueError(
                f"max_message_bytes={max_message_bytes} base64-encodes to "
                f"{max_envelope} bytes, which exceeds the DLQ envelope "
                f"budget of {DLQ_ENVELOPE_BUDGET}. Oversize records could "
                "never be dead-lettered and would loop forever."
            )

        self.max_message_bytes = max_message_bytes
        self.supported_schema_versions = supported_schema_versions

    def validate(self, raw_message: bytes | str) -> ValidationResult:
        """Validate ``raw_message``; never raises for malformed input."""
        raw_bytes = self._to_bytes(raw_message)

        too_large = self._check_size(raw_bytes)
        if too_large is not None:
            return too_large

        text = self._decode(raw_bytes)
        if isinstance(text, ValidationFailure):
            return text

        data = self._parse_json(text, raw_bytes)
        if isinstance(data, ValidationFailure):
            return data

        if not isinstance(data, dict):
            return ValidationFailure(
                code=ValidationCode.NOT_A_JSON_OBJECT,
                stage=ValidationStage.PARSE,
                message="Event must be a JSON object.",
                raw_payload=raw_bytes,
            )

        return self._validate_envelope(data, raw_bytes)
    @staticmethod
    def _to_bytes(raw_message: bytes | str) -> bytes:
        if isinstance(raw_message, bytes):
            return raw_message

        if isinstance(raw_message, str):
            try:
                return raw_message.encode("utf-8")
            except UnicodeEncodeError:
                # Not UTF-8 representable; keep the str form so _decode can
                # produce the authoritative INVALID_UTF8 failure with the
                # original text preserved.
                return raw_message.encode("utf-8", errors="surrogatepass")

        raise TypeError("raw_message must be bytes or str")

    def _check_size(self, raw_bytes: bytes) -> ValidationFailure | None:
        size = len(raw_bytes)

        if size > self.max_message_bytes:
            # Truncate what we keep, so the DLQ envelope itself stays
            # publishable. Without this an oversize record could never be
            # dead-lettered and would loop forever.
            return ValidationFailure(
                code=ValidationCode.MESSAGE_TOO_LARGE,
                stage=ValidationStage.DECODE,
                message=(
                    f"Message of {size} bytes exceeds the maximum allowed "
                    f"size of {self.max_message_bytes} bytes."
                ),
                raw_payload=raw_bytes[: self.max_message_bytes],
            )

        return None

    def _decode(self, raw_bytes: bytes) -> str | ValidationFailure:
        try:
            return raw_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            return ValidationFailure(
                code=ValidationCode.INVALID_UTF8,
                stage=ValidationStage.DECODE,
                message=f"Message must be valid UTF-8: {exc}",
                raw_payload=raw_bytes,
            )

    def _parse_json(
        self,
        text: str,
        raw_bytes: bytes,
    ) -> Any | ValidationFailure:
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            return ValidationFailure(
                code=ValidationCode.INVALID_JSON,
                stage=ValidationStage.PARSE,
                message=f"Message is not valid JSON: {exc}",
                raw_payload=raw_bytes,
            )
        except RecursionError:
            return ValidationFailure(
                code=ValidationCode.INVALID_JSON,
                stage=ValidationStage.PARSE,
                message="Message contains excessively nested JSON.",
                raw_payload=raw_bytes,
            )

    def _validate_envelope(
        self,
        data: dict[str, Any],
        raw_bytes: bytes,
    ) -> ValidationResult:
        try:
            envelope = AIEvent.model_validate(data)
        except ValidationError as exc:
            return ValidationFailure(
                code=ValidationCode.INVALID_ENVELOPE,
                stage=ValidationStage.ENVELOPE,
                message=self._summarize_errors(exc),
                raw_payload=raw_bytes,
            )

        if (
            self.supported_schema_versions is not None
            and envelope.schema_version not in self.supported_schema_versions
        ):
            return ValidationFailure(
                code=ValidationCode.UNSUPPORTED_SCHEMA_VERSION,
                stage=ValidationStage.ENVELOPE,
                message=(
                    f"Schema version '{envelope.schema_version}' is not "
                    "supported."
                ),
                raw_payload=raw_bytes,
            )

        event_model = EVENT_MODELS.get(envelope.event_type)

        if event_model is None:
            return ValidationFailure(
                code=ValidationCode.UNKNOWN_EVENT_TYPE,
                stage=ValidationStage.ROUTING,
                message=(
                    "No concrete schema is registered for event type "
                    f"'{envelope.event_type.value}'."
                ),
                raw_payload=raw_bytes,
            )

        try:
            event = event_model.model_validate(data)
        except ValidationError as exc:
            return ValidationFailure(
                code=ValidationCode.SCHEMA_VALIDATION_ERROR,
                stage=ValidationStage.ENVELOPE,
                message=self._summarize_errors(exc),
                raw_payload=raw_bytes,
            )

        return ValidationSuccess(event=event, raw_payload=raw_bytes)

    @staticmethod
    def _summarize_errors(exc: ValidationError) -> str:
        """Summarise Pydantic errors WITHOUT input values.

        ``include_input=False`` is deliberate: this text ends up in the DLQ,
        and input values are frequently the sensitive part of a payload.
        """
        errors = exc.errors(include_input=False)
        details: list[str] = []

        for error in errors[:10]:
            location = ".".join(str(part) for part in error["loc"])
            error_type = str(error["type"])

            if location:
                details.append(f"{location}: {error_type}")
            else:
                details.append(error_type)

        return "; ".join(details) if details else "Schema validation failed."