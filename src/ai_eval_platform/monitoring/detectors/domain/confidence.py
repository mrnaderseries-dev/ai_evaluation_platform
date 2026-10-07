from datetime import datetime, timezone
from uuid import uuid4

from pydantic import Field

from ..base import Detector, DetectorPolicy
from ..inputs import ScopedDetectorInput
from ...signals import (
    ConfidenceDropPayload,
    Signal,
    SignalScope,
    SignalType,
)


class ConfidenceDetectorPolicy(DetectorPolicy):
    drop_threshold: float = Field(default=0.0, ge=0)


class ConfidenceDetectionInput(ScopedDetectorInput):
    metric: str = Field(min_length=1)
    previous_value: float
    current_value: float
    baseline: float


class ConfidenceDropDetector(Detector[ConfidenceDetectionInput, ConfidenceDetectorPolicy]):
    def __init__(self, policy: ConfidenceDetectorPolicy | None = None):
        self.policy = policy or ConfidenceDetectorPolicy()

    @property
    def name(self) -> str:
        return "confidence_drop"

    def detect(
        self,
        input_data: ConfidenceDetectionInput,
    ) -> list[Signal]:
        if not self.policy.enabled:
            return []

        delta = input_data.previous_value - input_data.current_value

        if delta <= self.policy.drop_threshold:
            return []

        if input_data.current_value >= input_data.previous_value:
            return []

        payload = ConfidenceDropPayload(
            metric=input_data.metric,
            previous_value=input_data.previous_value,
            current_value=input_data.current_value,
            delta=delta,
            baseline=input_data.baseline,
        )

        signal_kwargs: dict[str, object] = {
            "signal_id": str(uuid4()),
            "agent_id": input_data.agent_id,
            "signal_type": SignalType.CONFIDENCE_DROP,
            "detected_at": datetime.now(timezone.utc),
            "payload": payload,
        }

        if input_data.run_id is not None:
            signal_kwargs["scope"] = SignalScope.RUN
            signal_kwargs["run_id"] = input_data.run_id
        elif input_data.evaluation_run_id is not None:
            signal_kwargs["scope"] = SignalScope.EVALUATION
            signal_kwargs["evaluation_run_id"] = input_data.evaluation_run_id
        else:
            signal_kwargs["scope"] = SignalScope.WINDOW
            signal_kwargs["window_start"] = input_data.window_start
            signal_kwargs["window_end"] = input_data.window_end

        return [Signal(**signal_kwargs)]