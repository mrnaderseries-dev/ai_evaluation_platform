from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class BaselineReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1)
    metric_name: str = Field(min_length=1)
    baseline_window: str = Field(min_length=1)
    expected_value: float
    reference_window_start: datetime
    reference_window_end: datetime

    @field_validator("reference_window_start", "reference_window_end")
    @classmethod
    def windows_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("window boundaries must be timezone-aware")
        return value

    @model_validator(mode="after")
    def windows_must_be_ordered(self) -> "BaselineReference":
        if self.reference_window_start >= self.reference_window_end:
            raise ValueError("reference window_start must be earlier than window_end")
        return self


class BaselineLookup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1)
    metric_name: str = Field(min_length=1)


class BaselineStore:
    def reference_for(self, lookup: BaselineLookup) -> BaselineReference | None:
        raise NotImplementedError
