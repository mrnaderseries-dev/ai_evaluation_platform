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
from ai_eval_platform.monitoring.detectors.strategies import AnomalyDetector, AnomalyDetectorPolicy
from ai_eval_platform.monitoring.signals.models import Signal
from ai_eval_platform.monitoring.signals.types import (
    LatencyAnomalyPayload,
    RouteChangePayload,
    SignalScope,
    SignalType,
)
from tests.monitoring.test_contracts_a import _run_anomaly_input, _window


def _signal_kwargs(**overrides):
    start, end = _window()
    values = {
        "signal_id": "sig-1",
        "agent_id": "agent-1",
        "signal_type": SignalType.LATENCY_ANOMALY,
        "scope": SignalScope.RUN,
        "run_id": "run-1",
        "detected_at": datetime.now(timezone.utc),
        "payload": LatencyAnomalyPayload(
            metric="latency_ms",
            observed_value=900.0,
            expected_value=100.0,
            deviation=800.0,
            threshold=10.0,
            baseline_window="baseline-7d",
        ),
    }
    values.update(overrides)
    return values


def test_imports_resolve():
    import ai_eval_platform.monitoring
    import ai_eval_platform.monitoring.detectors
    import ai_eval_platform.monitoring.detectors.domain
    import ai_eval_platform.monitoring.detectors.strategies
    import ai_eval_platform.monitoring.signals


def test_run_scope_valid():
    signal = Signal(**_signal_kwargs())
    assert signal.run_id == "run-1"


def test_run_scope_rejects_windows():
    start, end = _window()
    with pytest.raises(ValidationError):
        Signal(**_signal_kwargs(window_start=start, window_end=end))


def test_run_scope_requires_run_id():
    with pytest.raises(ValidationError):
        Signal(**_signal_kwargs(run_id=None))


def test_window_equal_bounds_rejected():
    moment = datetime.now(timezone.utc)
    with pytest.raises(ValidationError):
        Signal(**_signal_kwargs(scope=SignalScope.WINDOW, run_id=None, window_start=moment, window_end=moment))


def test_window_reversed_bounds_rejected():
    start, end = _window()
    with pytest.raises(ValidationError):
        Signal(**_signal_kwargs(scope=SignalScope.WINDOW, run_id=None, window_start=end, window_end=start))


def test_evaluation_scope_valid():
    signal = Signal(**_signal_kwargs(scope=SignalScope.EVALUATION, run_id=None, evaluation_run_id="eval-1"))
    assert signal.evaluation_run_id == "eval-1"


def test_evaluation_scope_rejects_run_id():
    with pytest.raises(ValidationError):
        Signal(**_signal_kwargs(scope=SignalScope.EVALUATION, evaluation_run_id="eval-1"))


def test_wrong_payload_rejected():
    with pytest.raises(ValidationError):
        Signal(
            **_signal_kwargs(
                payload=RouteChangePayload(previous_route="a", current_route="b", change_count=1, affected_runs=1)
            )
        )


def test_naive_detected_at_rejected():
    with pytest.raises(ValidationError):
        Signal(**_signal_kwargs(detected_at=datetime.now()))


def test_anomaly_run_scope():
    detector = AnomalyDetector(AnomalyDetectorPolicy())
    signals = detector.detect(_run_anomaly_input())
    assert signals[0].scope is SignalScope.RUN
    assert signals[0].run_id == "run-1"


def test_anomaly_disabled_and_min_samples():
    assert AnomalyDetector(AnomalyDetectorPolicy(enabled=False)).detect(_run_anomaly_input()) == []
    gated = AnomalyDetector(AnomalyDetectorPolicy(minimum_samples=3))
    assert gated.detect(_run_anomaly_input(sample_count=1)) == []
    assert len(gated.detect(_run_anomaly_input(sample_count=3))) == 1


def test_route_uses_supplied_window():
    start, end = _window()
    detector = RouteChangeDetector()
    signals = detector.detect(
        RouteDetectionInput(
            agent_id="agent-1",
            previous_route="rag",
            current_route="direct",
            change_count=3,
            affected_runs=2,
            window_start=start,
            window_end=end,
        )
    )
    assert signals[0].window_start == start


def test_route_same_route_and_min_samples():
    start, end = _window()
    detector = RouteChangeDetector()
    same = RouteDetectionInput(
        agent_id="agent-1",
        previous_route="rag",
        current_route="rag",
        change_count=1,
        affected_runs=1,
        window_start=start,
        window_end=end,
    )
    assert detector.detect(same) == []
    gated = RouteChangeDetector(RouteDetectorPolicy(minimum_samples=3))
    changed = RouteDetectionInput(
        agent_id="agent-1",
        previous_route="a",
        current_route="b",
        change_count=1,
        affected_runs=1,
        window_start=start,
        window_end=end,
    )
    assert gated.detect(changed) == []
