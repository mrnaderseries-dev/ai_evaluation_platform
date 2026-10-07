from datetime import datetime, timezone
from uuid import uuid4

from pydantic import Field

from ..base import Detector, DetectorPolicy
from ..inputs import PatternDetectionInput
from ...signals import (
    RepeatedToolUsagePayload,
    Signal,
    SignalScope,
    SignalType,
)


class PatternDetectorPolicy(DetectorPolicy):
    minimum_occurrences: int = Field(default=2, ge=2)


def is_repeated_sequence(sequence: list[str], minimum_occurrences: int) -> bool:
    if len(sequence) < minimum_occurrences:
        return False
    return len(set(sequence)) == 1


class PatternDetector(
    Detector[PatternDetectionInput, PatternDetectorPolicy]
):
    def __init__(self, policy: PatternDetectorPolicy):
        self.policy = policy

    @property
    def name(self) -> str:
        return "pattern"

    def detect(
        self,
        input_data: PatternDetectionInput,
    ) -> list[Signal]:
        if not self.policy.enabled:
            return []

        sample_count = len(input_data.event_sequences)

        if sample_count < self.policy.minimum_samples:
            return []

        signals: list[Signal] = []

        for sequence in input_data.event_sequences:
            if not is_repeated_sequence(sequence, self.policy.minimum_occurrences):
                continue

            payload = RepeatedToolUsagePayload(
                tool_name=sequence[0],
                occurrence_count=len(sequence),
                sequence=sequence,
                severity=input_data.severity,
                related_event_ids=input_data.event_ids,
            )

            scope = self.scope_for(input_data)

            signal_kwargs: dict[str, object] = {
                "signal_id": str(uuid4()),
                "agent_id": input_data.agent_id,
                "signal_type": SignalType.REPEATED_TOOL_USAGE,
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

            signals.append(Signal(**signal_kwargs))

        return signals

    def scope_for(self, input_data: PatternDetectionInput) -> SignalScope:
        if input_data.run_id is not None:
            return SignalScope.RUN
        if input_data.evaluation_run_id is not None:
            return SignalScope.EVALUATION
        return SignalScope.WINDOW