from datetime import datetime, timezone
from uuid import uuid4

from pydantic import Field, field_validator, model_validator

from ..base import Detector, DetectorPolicy
from ..inputs import DetectorInput
from ..strategies.drift import drifted_features
from ...signals import (
    Signal,
    SignalScope,
    SignalType,
    ToolUsageDriftPayload,
)


class ToolUsageDetectorPolicy(DetectorPolicy):
    drift_threshold: float = Field(ge=0)


class ToolUsageDetectionInput(DetectorInput):
    tool_name: str = Field(min_length=1)
    reference_frequency: float = Field(ge=0)
    current_frequency: float = Field(ge=0)
    drift_metric: str = Field(min_length=1)
    reference_window_start: datetime
    reference_window_end: datetime
    current_window_start: datetime
    current_window_end: datetime

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
    def windows_must_be_ordered(self) -> "ToolUsageDetectionInput":
        if self.reference_window_start >= self.reference_window_end:
            raise ValueError("reference window_start must be earlier than window_end")
        if self.current_window_start >= self.current_window_end:
            raise ValueError("current window_start must be earlier than window_end")
        return self


class ToolUsageDriftDetector(Detector[ToolUsageDetectionInput, ToolUsageDetectorPolicy]):
    def __init__(self, policy: ToolUsageDetectorPolicy):
        self.policy = policy

    @property
    def name(self) -> str:
        return "tool_usage_drift"

    def detect(
        self,
        input_data: ToolUsageDetectionInput,
    ) -> list[Signal]:
        if not self.policy.enabled:
            return []

        findings = drifted_features(
            {input_data.tool_name: input_data.reference_frequency},
            {input_data.tool_name: input_data.current_frequency},
            self.policy.drift_threshold,
        )

        if not findings:
            return []

        _, reference_value, current_value, delta = findings[0]

        payload = ToolUsageDriftPayload(
            tool_name=input_data.tool_name,
            reference_frequency=reference_value,
            current_frequency=current_value,
            delta=delta,
            drift_score=delta,
            drift_metric=input_data.drift_metric,
            reference_window_start=input_data.reference_window_start,
            reference_window_end=input_data.reference_window_end,
        )

        return [
            Signal(
                signal_id=str(uuid4()),
                agent_id=input_data.agent_id,
                signal_type=SignalType.TOOL_USAGE_DRIFT,
                scope=SignalScope.WINDOW,
                window_start=input_data.current_window_start,
                window_end=input_data.current_window_end,
                detected_at=datetime.now(timezone.utc),
                payload=payload,
            )
        ]