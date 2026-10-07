from datetime import datetime, timezone
from uuid import uuid4

from pydantic import Field, field_validator, model_validator

from ..base import Detector, DetectorPolicy
from ..inputs import DetectorInput
from ...signals import (
    RouteChangePayload,
    Signal,
    SignalScope,
    SignalType,
)


class RouteDetectorPolicy(DetectorPolicy):
    pass


class RouteDetectionInput(DetectorInput):
    previous_route: str = Field(min_length=1)
    current_route: str = Field(min_length=1)
    change_count: int = Field(ge=1)
    affected_runs: int = Field(ge=1)
    window_start: datetime
    window_end: datetime

    @field_validator("window_start", "window_end")
    @classmethod
    def windows_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("window boundaries must be timezone-aware")
        return value

    @model_validator(mode="after")
    def windows_must_be_ordered(self) -> "RouteDetectionInput":
        if self.window_start >= self.window_end:
            raise ValueError("window_start must be earlier than window_end")
        return self


class RouteChangeDetector(Detector[RouteDetectionInput, RouteDetectorPolicy]):
    def __init__(self, policy: RouteDetectorPolicy | None = None):
        self.policy = policy or RouteDetectorPolicy()

    @property
    def name(self) -> str:
        return "route_change"

    def detect(self, input_data: RouteDetectionInput) -> list[Signal]:
        if not self.policy.enabled:
            return []

        if input_data.affected_runs < self.policy.minimum_samples:
            return []

        if input_data.previous_route == input_data.current_route:
            return []

        payload = RouteChangePayload(
            previous_route=input_data.previous_route,
            current_route=input_data.current_route,
            change_count=input_data.change_count,
            affected_runs=input_data.affected_runs,
        )

        return [
            Signal(
                signal_id=str(uuid4()),
                agent_id=input_data.agent_id,
                signal_type=SignalType.ROUTE_CHANGE,
                scope=SignalScope.WINDOW,
                window_start=input_data.window_start,
                window_end=input_data.window_end,
                detected_at=datetime.now(timezone.utc),
                payload=payload,
            )
        ]