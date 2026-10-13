from datetime import datetime

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SignalScope(str, Enum):
    RUN = "run"
    EVALUATION = "evaluation"
    WINDOW = "window"


class SignalType(str, Enum):
    EVENT_OBSERVATION = "event_observation"
    LATENCY_ANOMALY = "latency_anomaly"
    ROUTE_CHANGE = "route_change"
    CONFIDENCE_DROP = "confidence_drop"
    REPEATED_TOOL_USAGE = "repeated_tool_usage"
    TOOL_USAGE_DRIFT = "tool_usage_drift"
    ERROR_PATTERN = "error_pattern"


class SignalOrigin(str, Enum):
    BASE_OBSERVATION = "base_observation"
    DETECTOR = "detector"
    ANALYSIS = "analysis"


class EventObservationPayload(BaseModel):
    """Non-anomalous fact that a source event was observed and normalized."""

    model_config = ConfigDict(extra="forbid")

    observation_id: str = Field(min_length=1)
    source_event_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    occurred_at: datetime
    sequence_number: int = Field(ge=0)

    @field_validator("occurred_at")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        return value


class SignalPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LatencyAnomalyPayload(SignalPayload):
    metric: str = Field(min_length=1)
    observed_value: float
    expected_value: float
    deviation: float
    threshold: float = Field(ge=0)
    baseline_window: str = Field(min_length=1)


class RouteChangePayload(SignalPayload):
    previous_route: str = Field(min_length=1)
    current_route: str = Field(min_length=1)
    change_count: int = Field(ge=1)
    affected_runs: int = Field(ge=1)


class ConfidenceDropPayload(SignalPayload):
    metric: str = Field(min_length=1)
    previous_value: float
    current_value: float
    delta: float
    baseline: float


class RepeatedToolUsagePayload(SignalPayload):
    tool_name: str = Field(min_length=1)
    occurrence_count: int = Field(ge=2)
    sequence: list[str] = Field(min_length=2)
    severity: float = Field(ge=0, le=1)
    related_event_ids: list[str] = Field(default_factory=list)


class ToolUsageDriftPayload(SignalPayload):
    tool_name: str = Field(min_length=1)
    reference_frequency: float = Field(ge=0)
    current_frequency: float = Field(ge=0)
    delta: float
    drift_score: float = Field(ge=0)
    drift_metric: str = Field(min_length=1)
    reference_window_start: datetime
    reference_window_end: datetime

    @field_validator("reference_window_start", "reference_window_end")
    @classmethod
    def reference_windows_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("reference windows must be timezone-aware")
        return value


class ErrorPatternPayload(SignalPayload):
    error_type: str = Field(min_length=1)
    occurrence_count: int = Field(ge=2)
    related_event_ids: list[str] = Field(default_factory=list)