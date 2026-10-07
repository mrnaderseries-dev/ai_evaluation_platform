from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EventType(str, Enum):
    MODEL_CALLED = "model_called"
    MODEL_OUTPUT = "model_output"

    TOOL_CALLED = "tool_called"
    TOOL_COMPLETED = "tool_completed"

    RETRIEVAL_STARTED = "retrieval_started"
    RETRIEVAL_COMPLETED = "retrieval_completed"

    ANSWER_GENERATED = "answer_generated"

    ERROR = "error"
    RECOVERY_ATTEMPTED = "recovery_attempted"
    RECOVERY_COMPLETED = "recovery_completed"

    CONTEXT_ASSEMBLED = "context_assembled"
    CONTEXT_RESOURCE_USED = "context_resource_used"
    CONTEXT_UPDATED = "context_updated"
    EVALUATION_COMPLETED = "evaluation_completed"
    POLICY_CHECKED = "policy_checked"
    POLICY_ALLOWED = "policy_allowed"
    POLICY_BLOCKED = "policy_blocked"
    BEHAVIOR_RULE_TRIGGERED = "behavior_rule_triggered"

    HUMAN_REVIEW_REQUESTED = "human_review_requested"
    HUMAN_REVIEW_COMPLETED = "human_review_completed"

    AGENT_ACTION = "agent_action"
    VERIFICATION_ATTEMPTED = "verification_attempted"
    COUNTER_EVIDENCE_REQUESTED = "counter_evidence_requested"
    ANSWER_REVISED = "answer_revised"
    ABSTENTION = "abstention"

    BEHAVIOR_PATTERN_DETECTED = "behavior_pattern_detected"


class AIEvent(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
    )

    event_id: str = Field(..., min_length=1)
    schema_version: str = Field(..., pattern=r"^\d+\.\d+$")
    system_id: str = Field(..., min_length=1)
    system_version: str = Field(..., min_length=1)
    run_id: str = Field(..., min_length=1)
    event_type: EventType
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    payload: dict[str, Any] = Field(default_factory=dict)
    sequence_number: int | None = Field(default=None, ge=0)
    parent_event_id: str | None = Field(default=None, min_length=1)
    correlation_id: str | None = Field(default=None, min_length=1)
    causation_id: str | None = Field(default=None, min_length=1)
    step_id: str | None = Field(default=None, min_length=1)

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_timezone_aware(
        cls,
        value: datetime,
    ) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value