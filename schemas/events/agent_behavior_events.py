from typing import Any, Literal

from pydantic import BaseModel, Field

from schemas.events.common import AIEvent, EventType


class AgentActionPayload(BaseModel):
    action_id: str = Field(..., min_length=1)
    action_type: str = Field(..., min_length=1)
    target: str | None = None
    step_index: int | None = Field(default=None, ge=0)
    repeated_action_count: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class VerificationAttemptedPayload(BaseModel):
    verification_id: str = Field(..., min_length=1)
    verification_method: str = Field(..., min_length=1)
    result: str | None = None


class CounterEvidenceRequestedPayload(BaseModel):
    verification_id: str = Field(..., min_length=1)
    query: str | None = None
    source_type: str | None = None
    reason_code: str | None = None


class AnswerRevisedPayload(BaseModel):
    answer_id: str = Field(..., min_length=1)
    previous_answer_id: str = Field(..., min_length=1)
    revision_type: str = Field(..., min_length=1)
    trigger_event_id: str | None = None


class AbstentionPayload(BaseModel):
    reason_code: str = Field(..., min_length=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    target_type: str | None = None
    target_id: str | None = None


class BehaviorPatternDetectedPayload(BaseModel):
    pattern_id: str = Field(..., min_length=1)
    pattern_type: str = Field(..., min_length=1)
    window_size: int = Field(..., ge=1)
    occurrence_count: int = Field(..., ge=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    evidence_event_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentActionEvent(AIEvent):
    event_type: Literal[EventType.AGENT_ACTION] = EventType.AGENT_ACTION
    payload: AgentActionPayload


class VerificationAttemptedEvent(AIEvent):
    event_type: Literal[EventType.VERIFICATION_ATTEMPTED] = EventType.VERIFICATION_ATTEMPTED
    payload: VerificationAttemptedPayload


class CounterEvidenceRequestedEvent(AIEvent):
    event_type: Literal[EventType.COUNTER_EVIDENCE_REQUESTED] = EventType.COUNTER_EVIDENCE_REQUESTED
    payload: CounterEvidenceRequestedPayload


class AnswerRevisedEvent(AIEvent):
    event_type: Literal[EventType.ANSWER_REVISED] = EventType.ANSWER_REVISED
    payload: AnswerRevisedPayload


class AbstentionEvent(AIEvent):
    event_type: Literal[EventType.ABSTENTION] = EventType.ABSTENTION
    payload: AbstentionPayload


class BehaviorPatternDetectedEvent(AIEvent):
    event_type: Literal[EventType.BEHAVIOR_PATTERN_DETECTED] = EventType.BEHAVIOR_PATTERN_DETECTED
    payload: BehaviorPatternDetectedPayload