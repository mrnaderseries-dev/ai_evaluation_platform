from datetime import datetime, timezone
from uuid import uuid4

from pydantic import Field

from ..base import Detector, DetectorPolicy
from ..inputs import DriftDetectionInput
from ...signals import (
    Signal,
    SignalScope,
    SignalType,
    ToolUsageDriftPayload,
)


class DriftDetectorPolicy(DetectorPolicy):
    drift_threshold: float = Field(ge=0)


def drifted_features(
    reference: dict[str, float], current: dict[str, float], threshold: float
) -> list[tuple[str, float, float, float]]:
    names = set(reference) | set(current)
    findings: list[tuple[str, float, float, float]] = []
    for name in sorted(names):
        reference_value = reference.get(name, 0.0)
        current_value = current.get(name, 0.0)
        delta = abs(current_value - reference_value)
        if delta > threshold:
            findings.append((name, reference_value, current_value, delta))
    return findings


class DriftDetector(
    Detector[DriftDetectionInput, DriftDetectorPolicy]
):
    def __init__(self, policy: DriftDetectorPolicy):
        self.policy = policy

    @property
    def name(self) -> str:
        return "drift"

    def detect(
        self,
        input_data: DriftDetectionInput,
    ) -> list[Signal]:
        if not self.policy.enabled:
            return []

        if not input_data.reference_distribution:
            return []

        if not input_data.current_distribution:
            return []

        sample_count = len(set(input_data.reference_distribution) | set(input_data.current_distribution))

        if sample_count < self.policy.minimum_samples:
            return []

        signals: list[Signal] = []

        for tool_name, reference_value, current_value, delta in drifted_features(
            input_data.reference_distribution, input_data.current_distribution, self.policy.drift_threshold
        ):
            payload = ToolUsageDriftPayload(
                tool_name=tool_name,
                reference_frequency=reference_value,
                current_frequency=current_value,
                delta=delta,
                drift_score=delta,
                drift_metric=input_data.drift_metric,
                reference_window_start=input_data.reference_window_start,
                reference_window_end=input_data.reference_window_end,
            )

            signals.append(
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
            )

        return signals