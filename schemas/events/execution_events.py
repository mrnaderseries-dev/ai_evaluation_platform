"""Execution-layer events: model, tool, retrieval, answer, error and recovery.

Every concrete event locks its ``event_type`` with ``Literal[EventType.X]`` so a
wrong ``event_type`` is rejected at construction time (Pydantic v2 semantics),
not merely at assignment time.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from schemas.events.common import AIEvent, EventType


class ModelCalledPayload(BaseModel):
    model_id: str = Field(..., min_length=1)
    model_version: str | None = Field(default=None, min_length=1)
    provider: str | None = Field(default=None, min_length=1)

    request_id: str | None = Field(default=None, min_length=1)

    input_tokens: int | None = Field(default=None, ge=0)
    temperature: float | None = Field(default=None, ge=0)

    metadata: dict[str, Any] = Field(default_factory=dict)


class ModelOutputPayload(BaseModel):
    model_id: str = Field(..., min_length=1)
    model_version: str | None = Field(default=None, min_length=1)
    provider: str | None = Field(default=None, min_length=1)

    output_id: str = Field(..., min_length=1)

    output_text: str | None = None

    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)

    latency_ms: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
    )

    finish_reason: str | None = None

    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolCalledPayload(BaseModel):
    tool_id: str = Field(..., min_length=1)
    tool_name: str = Field(..., min_length=1)
    tool_version: str | None = Field(default=None, min_length=1)

    call_id: str = Field(..., min_length=1)

    arguments: dict[str, Any] = Field(default_factory=dict)

    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolCompletedPayload(BaseModel):
    tool_id: str = Field(..., min_length=1)
    tool_name: str = Field(..., min_length=1)
    tool_version: str | None = Field(default=None, min_length=1)

    call_id: str = Field(..., min_length=1)

    success: bool

    result: Any | None = None
    error_message: str | None = None

    latency_ms: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
    )

    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievalStartedPayload(BaseModel):
    retrieval_id: str = Field(..., min_length=1)
    retriever_id: str = Field(..., min_length=1)
    retriever_version: str | None = Field(default=None, min_length=1)

    query: str | None = None

    top_k: int | None = Field(default=None, ge=1)

    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievalCompletedPayload(BaseModel):
    retrieval_id: str = Field(..., min_length=1)
    retriever_id: str = Field(..., min_length=1)
    retriever_version: str | None = Field(default=None, min_length=1)

    result_count: int = Field(..., ge=0)

    results: list[dict[str, Any]] = Field(default_factory=list)

    latency_ms: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
    )

    metadata: dict[str, Any] = Field(default_factory=dict)


class AnswerGeneratedPayload(BaseModel):
    answer_id: str = Field(..., min_length=1)

    answer_type: str = Field(..., min_length=1)

    content: str | None = None

    source_event_ids: list[str] = Field(default_factory=list)

    metadata: dict[str, Any] = Field(default_factory=dict)


class ErrorPayload(BaseModel):
    error_id: str = Field(..., min_length=1)

    error_type: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)

    component: str | None = Field(default=None, min_length=1)

    recoverable: bool = False

    retryable: bool = False

    parent_event_id: str | None = Field(default=None, min_length=1)

    metadata: dict[str, Any] = Field(default_factory=dict)


class RecoveryAttemptedPayload(BaseModel):
    recovery_id: str = Field(..., min_length=1)

    strategy: str = Field(..., min_length=1)

    trigger_event_id: str | None = Field(
        default=None,
        min_length=1,
    )

    attempt_number: int = Field(..., ge=1)

    reason: str | None = None

    metadata: dict[str, Any] = Field(default_factory=dict)


class RecoveryCompletedPayload(BaseModel):
    recovery_id: str = Field(..., min_length=1)

    strategy: str = Field(..., min_length=1)

    success: bool

    attempt_number: int = Field(..., ge=1)

    result: str | None = None

    error_message: str | None = None

    metadata: dict[str, Any] = Field(default_factory=dict)


class ModelCalledEvent(AIEvent):
    event_type: Literal[EventType.MODEL_CALLED] = EventType.MODEL_CALLED
    payload: ModelCalledPayload


class ModelOutputEvent(AIEvent):
    event_type: Literal[EventType.MODEL_OUTPUT] = EventType.MODEL_OUTPUT
    payload: ModelOutputPayload


class ToolCalledEvent(AIEvent):
    event_type: Literal[EventType.TOOL_CALLED] = EventType.TOOL_CALLED
    payload: ToolCalledPayload


class ToolCompletedEvent(AIEvent):
    event_type: Literal[EventType.TOOL_COMPLETED] = EventType.TOOL_COMPLETED
    payload: ToolCompletedPayload


class RetrievalStartedEvent(AIEvent):
    event_type: Literal[EventType.RETRIEVAL_STARTED] = EventType.RETRIEVAL_STARTED
    payload: RetrievalStartedPayload


class RetrievalCompletedEvent(AIEvent):
    event_type: Literal[EventType.RETRIEVAL_COMPLETED] = EventType.RETRIEVAL_COMPLETED
    payload: RetrievalCompletedPayload


class AnswerGeneratedEvent(AIEvent):
    event_type: Literal[EventType.ANSWER_GENERATED] = EventType.ANSWER_GENERATED
    payload: AnswerGeneratedPayload


class ErrorEvent(AIEvent):
    event_type: Literal[EventType.ERROR] = EventType.ERROR
    payload: ErrorPayload


class RecoveryAttemptedEvent(AIEvent):
    event_type: Literal[EventType.RECOVERY_ATTEMPTED] = EventType.RECOVERY_ATTEMPTED
    payload: RecoveryAttemptedPayload


class RecoveryCompletedEvent(AIEvent):
    event_type: Literal[EventType.RECOVERY_COMPLETED] = EventType.RECOVERY_COMPLETED
    payload: RecoveryCompletedPayload