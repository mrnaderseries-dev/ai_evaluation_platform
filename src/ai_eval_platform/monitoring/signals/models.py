from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .types import (
    ConfidenceDropPayload,
    EventObservationPayload,
    ErrorPatternPayload,
    LatencyAnomalyPayload,
    RepeatedToolUsagePayload,
    RouteChangePayload,
    SignalOrigin,
    SignalScope,
    SignalType,
    ToolUsageDriftPayload,
)


PayloadType = (
    EventObservationPayload
    | LatencyAnomalyPayload
    | RouteChangePayload
    | ConfidenceDropPayload
    | RepeatedToolUsagePayload
    | ToolUsageDriftPayload
    | ErrorPatternPayload
)


class Signal(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
    )

    signal_id: str = Field(min_length=1)
    origin: SignalOrigin = SignalOrigin.DETECTOR
    source_event_id: str | None = Field(default=None, min_length=1)
    source_observation_id: str | None = Field(default=None, min_length=1)
    projection_version: str | None = Field(default=None, min_length=1)
    agent_id: str = Field(min_length=1)
    signal_type: SignalType
    scope: SignalScope
    run_id: str | None = None
    evaluation_run_id: str | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    detected_at: datetime
    payload: PayloadType

    @field_validator("window_start", "window_end", "detected_at")
    @classmethod
    def require_timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("monitoring timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_scope(self) -> "Signal":
        if self.scope is SignalScope.RUN:
            if self.run_id is None:
                raise ValueError("RUN-scoped signals require run_id")

            if self.evaluation_run_id is not None:
                raise ValueError(
                    "RUN-scoped signals cannot have evaluation_run_id"
                )

            if self.window_start is not None or self.window_end is not None:
                raise ValueError(
                    "RUN-scoped signals cannot have window boundaries"
                )

        elif self.scope is SignalScope.EVALUATION:
            if self.evaluation_run_id is None:
                raise ValueError(
                    "EVALUATION-scoped signals require evaluation_run_id"
                )

            if self.run_id is not None:
                raise ValueError(
                    "EVALUATION-scoped signals cannot have run_id"
                )

            if self.window_start is not None or self.window_end is not None:
                raise ValueError(
                    "EVALUATION-scoped signals cannot have window boundaries"
                )

        elif self.scope is SignalScope.WINDOW:
            if self.window_start is None or self.window_end is None:
                raise ValueError(
                    "WINDOW-scoped signals require window_start and window_end"
                )

            if self.window_start >= self.window_end:
                raise ValueError(
                    "window_start must be earlier than window_end"
                )

            if self.run_id is not None:
                raise ValueError(
                    "WINDOW-scoped signals cannot have run_id"
                )

        return self

    @model_validator(mode="after")
    def validate_payload_matches_type(self) -> "Signal":
        expected_payload = {
            SignalType.EVENT_OBSERVATION: EventObservationPayload,
            SignalType.LATENCY_ANOMALY: LatencyAnomalyPayload,
            SignalType.ROUTE_CHANGE: RouteChangePayload,
            SignalType.CONFIDENCE_DROP: ConfidenceDropPayload,
            SignalType.REPEATED_TOOL_USAGE: RepeatedToolUsagePayload,
            SignalType.TOOL_USAGE_DRIFT: ToolUsageDriftPayload,
            SignalType.ERROR_PATTERN: ErrorPatternPayload,
        }[self.signal_type]

        if not isinstance(self.payload, expected_payload):
            raise ValueError(
                f"signal_type={self.signal_type.value!r} requires "
                f"payload type {expected_payload.__name__}"
            )

        if self.signal_type is SignalType.EVENT_OBSERVATION:
            if self.origin is not SignalOrigin.BASE_OBSERVATION:
                raise ValueError("event_observation signals must use base_observation origin")
            if not self.source_event_id or not self.source_observation_id or not self.projection_version:
                raise ValueError("base observation signals require source event, observation, and projection version")
        elif self.origin is SignalOrigin.BASE_OBSERVATION:
            raise ValueError("base_observation origin requires event_observation signal type")

        return self