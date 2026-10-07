from datetime import datetime, timezone
from uuid import uuid4

from pydantic import Field

from ..base import Detector, DetectorPolicy
from ..inputs import AnomalyDetectionInput
from ...signals import (
    LatencyAnomalyPayload,
    Signal,
    SignalScope,
    SignalType,
)


class AnomalyDetectorPolicy(DetectorPolicy):
    absolute_threshold: float | None = Field(default=None, ge=0)


def deviation_exceeds_threshold(observed: float, expected: float, threshold: float) -> tuple[bool, float]:
    deviation = abs(observed - expected)
    return deviation > threshold, deviation


class AnomalyDetector(
    Detector[AnomalyDetectionInput, AnomalyDetectorPolicy]
):
    def __init__(self, policy: AnomalyDetectorPolicy):
        self.policy = policy

    @property
    def name(self) -> str:
        return "anomaly"

    def detect(
        self,
        input_data: AnomalyDetectionInput,
    ) -> list[Signal]:
        if not self.policy.enabled:
            return []

        if input_data.sample_count < self.policy.minimum_samples:
            return []

        threshold = (
            self.policy.absolute_threshold
            if self.policy.absolute_threshold is not None
            else input_data.threshold
        )

        triggered, deviation = deviation_exceeds_threshold(
            input_data.observed_value, input_data.expected_value, threshold
        )

        if not triggered:
            return []

        payload = LatencyAnomalyPayload(
            metric=input_data.metric_name,
            observed_value=input_data.observed_value,
            expected_value=input_data.expected_value,
            deviation=deviation,
            threshold=threshold,
            baseline_window=input_data.baseline_window,
        )

        scope = self.scope_for(input_data)

        signal_kwargs: dict[str, object] = {
            "signal_id": str(uuid4()),
            "agent_id": input_data.agent_id,
            "signal_type": SignalType.LATENCY_ANOMALY,
            "scope": scope,
            "detected_at": datetime.now(timezone.utc),
            "payload": payload,
        }

        if input_data.run_id is not None:
            signal_kwargs["run_id"] = input_data.run_id
        elif input_data.evaluation_run_id is not None:
            signal_kwargs["evaluation_run_id"] = input_data.evaluation_run_id
        else:
            signal_kwargs["window_start"] = input_data.window_start
            signal_kwargs["window_end"] = input_data.window_end

        return [Signal(**signal_kwargs)]

    def scope_for(self, input_data: AnomalyDetectionInput) -> SignalScope:
        if input_data.run_id is not None:
            return SignalScope.RUN
        if input_data.evaluation_run_id is not None:
            return SignalScope.EVALUATION
        return SignalScope.WINDOW