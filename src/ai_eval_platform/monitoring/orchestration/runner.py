from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from ..detectors.base import Detector
from ..signals import Signal


class DetectorOutcome(str, Enum):
    SIGNALS = "signals"
    NO_DATA = "no_data"
    INSUFFICIENT_SAMPLES = "insufficient_samples"
    NO_FINDING = "no_finding"
    DISABLED = "disabled"
    INVALID_INPUT = "invalid_input"
    CONFIGURATION_ERROR = "configuration_error"
    DETECTOR_FAILED = "detector_failed"


class DetectorRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detector_name: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    outcome: DetectorOutcome
    signals: list[Signal] = Field(default_factory=list)
    reason: str | None = None


class MonitoringRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1)
    runs: list[DetectorRun] = Field(default_factory=list)

    @property
    def signals(self) -> list[Signal]:
        collected: list[Signal] = []
        for run in self.runs:
            collected.extend(run.signals)
        return collected


def execute_detector(detector: Detector, input_data: BaseModel, agent_id: str) -> DetectorRun:
    policy = getattr(detector, "policy", None)
    enabled = bool(getattr(policy, "enabled", True))
    if not enabled:
        return DetectorRun(detector_name=detector.name, agent_id=agent_id, outcome=DetectorOutcome.DISABLED)
    try:
        signals = detector.detect(input_data)
    except (ValueError, TypeError) as exc:
        return DetectorRun(
            detector_name=detector.name,
            agent_id=agent_id,
            outcome=DetectorOutcome.INVALID_INPUT,
            reason=str(exc),
        )
    except Exception as exc:
        return DetectorRun(
            detector_name=detector.name,
            agent_id=agent_id,
            outcome=DetectorOutcome.DETECTOR_FAILED,
            reason=type(exc).__name__,
        )
    for signal in signals:
        if signal.agent_id != agent_id:
            return DetectorRun(
                detector_name=detector.name,
                agent_id=agent_id,
                outcome=DetectorOutcome.DETECTOR_FAILED,
                reason="agent boundary violated",
            )
    if not signals:
        return DetectorRun(detector_name=detector.name, agent_id=agent_id, outcome=DetectorOutcome.NO_FINDING)
    return DetectorRun(detector_name=detector.name, agent_id=agent_id, outcome=DetectorOutcome.SIGNALS, signals=signals)


def run_monitoring(agent_id: str, assignments: list[tuple[Detector, BaseModel]]) -> MonitoringRun:
    runs: list[DetectorRun] = []
    for detector, input_data in assignments:
        runs.append(execute_detector(detector, input_data, agent_id))
    return MonitoringRun(agent_id=agent_id, runs=runs)
