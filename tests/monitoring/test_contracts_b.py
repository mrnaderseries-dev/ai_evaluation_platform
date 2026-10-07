from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from ai_eval_platform.monitoring.detectors.domain import (
    ConfidenceDetectionInput,
    ConfidenceDetectorPolicy,
    ConfidenceDropDetector,
    RouteChangeDetector,
    RouteDetectionInput,
    RouteDetectorPolicy,
    ToolUsageDetectionInput,
    ToolUsageDetectorPolicy,
    ToolUsageDriftDetector,
)
from ai_eval_platform.monitoring.detectors.inputs import DriftDetectionInput
from ai_eval_platform.monitoring.detectors.strategies import (
    DriftDetector,
    DriftDetectorPolicy,
    PatternDetector,
    PatternDetectorPolicy,
)
from ai_eval_platform.monitoring.signals.types import SignalScope, ToolUsageDriftPayload
from tests.monitoring.test_contracts_a import _run_pattern_input, _window


def _drift_input(**overrides):
    reference_start = datetime.now(timezone.utc) - timedelta(days=14)
    reference_end = datetime.now(timezone.utc) - timedelta(days=7)
    current_start, current_end = _window()
    values = {
        "agent_id": "agent-1",
        "reference_window_start": reference_start,
        "reference_window_end": reference_end,
        "current_window_start": current_start,
        "current_window_end": current_end,
        "reference_distribution": {"search": 0.1},
        "current_distribution": {"search": 0.9},
        "drift_metric": "absolute_delta",
        "threshold": 0.1,
    }
    values.update(overrides)
    return DriftDetectionInput(**values)


def test_drift_preserves_reference_window():
    detector = DriftDetector(DriftDetectorPolicy(drift_threshold=0.1))
    signals = detector.detect(_drift_input())
    assert len(signals) == 1
    payload = signals[0].payload
    assert isinstance(payload, ToolUsageDriftPayload)
    assert payload.drift_metric == "absolute_delta"
    assert payload.reference_window_start is not None


def test_drift_disabled_empty():
    detector = DriftDetector(DriftDetectorPolicy(drift_threshold=0.1, enabled=False))
    assert detector.detect(_drift_input()) == []


def test_drift_minimum_samples():
    detector = DriftDetector(DriftDetectorPolicy(drift_threshold=0.1, minimum_samples=5))
    assert detector.detect(_drift_input()) == []


def test_drift_below_threshold_empty():
    detector = DriftDetector(DriftDetectorPolicy(drift_threshold=5.0))
    assert detector.detect(_drift_input()) == []


def test_pattern_window_scope():
    start, end = _window()
    detector = PatternDetector(PatternDetectorPolicy())
    signals = detector.detect(_run_pattern_input(run_id=None, window_start=start, window_end=end))
    assert signals[0].scope is SignalScope.WINDOW
    assert signals[0].window_start == start


def test_pattern_evaluation_scope():
    detector = PatternDetector(PatternDetectorPolicy())
    signals = detector.detect(_run_pattern_input(run_id=None, evaluation_run_id="eval-9"))
    assert signals[0].scope is SignalScope.EVALUATION
    assert signals[0].evaluation_run_id == "eval-9"


def test_pattern_minimum_samples():
    detector = PatternDetector(PatternDetectorPolicy(minimum_samples=2))
    assert detector.detect(_run_pattern_input(event_sequences=[])) == []


def test_pattern_uses_input_severity():
    detector = PatternDetector(PatternDetectorPolicy())
    signals = detector.detect(_run_pattern_input(severity=0.4))
    assert signals[0].payload.severity == 0.4
