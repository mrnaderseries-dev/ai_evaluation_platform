from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from ai_eval_platform.monitoring.detectors.base import Detector
from ai_eval_platform.monitoring.detectors.domain import (
    ConfidenceDetectionInput,
    ConfidenceDetectorPolicy,
    ConfidenceDropDetector,
    ToolUsageDetectionInput,
    ToolUsageDetectorPolicy,
    ToolUsageDriftDetector,
)
from ai_eval_platform.monitoring.detectors.strategies.drift import drifted_features
from ai_eval_platform.monitoring.signals.types import ConfidenceDropPayload, SignalScope


def test_confidence_run_no_fabrication():
    detector = ConfidenceDropDetector(ConfidenceDetectorPolicy(drop_threshold=0.05))
    signals = detector.detect(
        ConfidenceDetectionInput(
            agent_id="agent-1",
            run_id="run-77",
            metric="confidence",
            previous_value=0.9,
            current_value=0.5,
            baseline=0.9,
        )
    )
    assert signals[0].run_id == "run-77"


def test_confidence_requires_lineage():
    with pytest.raises(ValidationError):
        ConfidenceDetectionInput(
            agent_id="agent-1",
            metric="confidence",
            previous_value=0.9,
            current_value=0.5,
            baseline=0.9,
        )


def test_confidence_evaluation_scope():
    detector = ConfidenceDropDetector(ConfidenceDetectorPolicy(drop_threshold=0.05))
    signals = detector.detect(
        ConfidenceDetectionInput(
            agent_id="agent-1",
            evaluation_run_id="eval-3",
            metric="confidence",
            previous_value=0.9,
            current_value=0.2,
            baseline=0.9,
        )
    )
    assert signals[0].scope is SignalScope.EVALUATION


def test_confidence_increase_empty():
    detector = ConfidenceDropDetector(ConfidenceDetectorPolicy(drop_threshold=0.0))
    signals = detector.detect(
        ConfidenceDetectionInput(
            agent_id="agent-1",
            run_id="run-1",
            metric="confidence",
            previous_value=0.5,
            current_value=0.9,
            baseline=0.5,
        )
    )
    assert signals == []


def test_tool_usage_delegates_to_strategy():
    start = datetime.now(timezone.utc) - timedelta(hours=2)
    end = datetime.now(timezone.utc) - timedelta(hours=1)
    detector = ToolUsageDriftDetector(ToolUsageDetectorPolicy(drift_threshold=0.1))
    signals = detector.detect(
        ToolUsageDetectionInput(
            agent_id="agent-1",
            tool_name="search",
            reference_frequency=0.1,
            current_frequency=0.8,
            drift_metric="absolute_delta",
            reference_window_start=start,
            reference_window_end=end,
            current_window_start=start,
            current_window_end=end,
        )
    )
    assert signals[0].payload.tool_name == "search"


def test_tool_usage_below_threshold_empty():
    start = datetime.now(timezone.utc) - timedelta(hours=2)
    end = datetime.now(timezone.utc) - timedelta(hours=1)
    detector = ToolUsageDriftDetector(ToolUsageDetectorPolicy(drift_threshold=0.9))
    signals = detector.detect(
        ToolUsageDetectionInput(
            agent_id="agent-1",
            tool_name="search",
            reference_frequency=0.1,
            current_frequency=0.2,
            drift_metric="absolute_delta",
            reference_window_start=start,
            reference_window_end=end,
            current_window_start=start,
            current_window_end=end,
        )
    )
    assert signals == []


def test_domain_contract_and_interaction():
    from ai_eval_platform.monitoring.detectors.domain import ConfidenceDropDetector as C
    from ai_eval_platform.monitoring.detectors.domain import RouteChangeDetector as R

    assert isinstance(R(), Detector)
    assert isinstance(C(), Detector)
    assert isinstance(ToolUsageDriftDetector(ToolUsageDetectorPolicy(drift_threshold=0.1)), Detector)
    assert drifted_features({"t": 0.0}, {"t": 1.0}, 0.5) != []


def test_confidence_payload_semantics():
    payload = ConfidenceDropPayload(metric="c", previous_value=0.9, current_value=0.4, delta=0.5, baseline=0.9)
    assert payload.delta == 0.5
