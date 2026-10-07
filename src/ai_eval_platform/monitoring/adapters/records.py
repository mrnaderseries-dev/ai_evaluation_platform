from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: float | None = Field(default=None, ge=0)
    trace_id: str | None = None


class ToolCallRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    call_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    latency_ms: float | None = Field(default=None, ge=0)
    success: bool | None = None


class LatencyRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1)
    metric_name: str = Field(min_length=1)
    observed_value: float
    expected_value: float
    baseline_value: float
    baseline_window: str = Field(min_length=1)
    threshold: float = Field(ge=0)
    sample_count: int = Field(default=1, ge=1)


class ConfidenceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1)
    metric: str = Field(min_length=1)
    previous_value: float
    current_value: float
    baseline: float


class EvaluationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1)
    evaluation_run_id: str = Field(min_length=1)
    metric_name: str = Field(min_length=1)
    observed_value: float
    expected_value: float
    baseline_value: float
    baseline_window: str = Field(min_length=1)
    threshold: float = Field(ge=0)
    sample_count: int = Field(default=1, ge=1)


class WindowRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1)
    window_start: datetime
    window_end: datetime


class AdapterUnavailable(Exception):
    pass
