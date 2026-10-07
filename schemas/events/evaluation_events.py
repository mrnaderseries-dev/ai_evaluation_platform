from typing import Any, Literal

from pydantic import BaseModel, Field

from schemas.events.common import AIEvent, EventType


class EvaluationCompletedPayload(BaseModel):
    model_config = {"extra": "forbid"}
    evaluation_run_id: str = Field(..., min_length=1)
    evaluation_case_id: str = Field(..., min_length=1)
    metric: str = Field(..., min_length=1)
    value: float = Field(..., allow_inf_nan=False)
    evaluator_type: Literal[
        "deterministic",
        "llm_judge",
        "human",
        "statistical",
        "custom",
    ]
    evaluator_name: str | None = Field(default=None, min_length=1)
    evaluator_version: str | None = Field(default=None, min_length=1)
    dataset_id: str | None = Field(default=None, min_length=1)
    dataset_version: str | None = Field(default=None, min_length=1)
    threshold: float | None = Field(default=None, allow_inf_nan=False)
    passed: bool | None = None
    failure_reason: str | None = Field(default=None, min_length=1)
    duration_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvaluationCompletedEvent(AIEvent):
    event_type: Literal[EventType.EVALUATION_COMPLETED] = EventType.EVALUATION_COMPLETED
    payload: EvaluationCompletedPayload