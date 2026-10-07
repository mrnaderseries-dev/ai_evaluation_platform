from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DetectorInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1)

    @field_validator("agent_id")
    @classmethod
    def agent_id_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("agent_id must not be blank")
        return value


class ScopedDetectorInput(DetectorInput):
    run_id: str | None = None
    evaluation_run_id: str | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None

    @field_validator("window_start", "window_end")
    @classmethod
    def windows_must_be_timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("window boundaries must be timezone-aware")
        return value

    @model_validator(mode="after")
    def exactly_one_scope(self) -> "ScopedDetectorInput":
        run = self.run_id is not None
        evaluation = self.evaluation_run_id is not None
        window = self.window_start is not None or self.window_end is not None
        provided = sum([run, evaluation, window])
        if provided != 1:
            raise ValueError("input must carry exactly one scope: run, evaluation, or window")
        if window:
            if self.window_start is None or self.window_end is None:
                raise ValueError("window scope requires window_start and window_end")
            if self.window_start >= self.window_end:
                raise ValueError("window_start must be earlier than window_end")
        return self


class PatternDetectionInput(ScopedDetectorInput):
    event_sequences: list[list[str]] = Field(default_factory=list)
    event_ids: list[str] = Field(default_factory=list)
    severity: float = Field(default=0.0, ge=0, le=1)


class AnomalyDetectionInput(ScopedDetectorInput):
    metric_name: str = Field(min_length=1)
    observed_value: float
    expected_value: float
    baseline_value: float
    baseline_window: str = Field(min_length=1)
    threshold: float = Field(ge=0)
    sample_count: int = Field(default=1, ge=1)


class DriftDetectionInput(DetectorInput):
    reference_window_start: datetime
    reference_window_end: datetime
    current_window_start: datetime
    current_window_end: datetime
    reference_distribution: dict[str, float]
    current_distribution: dict[str, float]
    drift_metric: str = Field(min_length=1)
    threshold: float = Field(ge=0)

    @field_validator(
        "reference_window_start",
        "reference_window_end",
        "current_window_start",
        "current_window_end",
    )
    @classmethod
    def windows_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("window boundaries must be timezone-aware")
        return value

    @model_validator(mode="after")
    def windows_must_be_ordered(self) -> "DriftDetectionInput":
        if self.reference_window_start >= self.reference_window_end:
            raise ValueError("reference window_start must be earlier than window_end")
        if self.current_window_start >= self.current_window_end:
            raise ValueError("current window_start must be earlier than window_end")
        return self
