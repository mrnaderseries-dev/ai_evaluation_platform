from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from ai_eval_platform.monitoring.adapters import (
    AdapterUnavailable,
    LatencyRecord,
    ToolCallRecord,
    WindowRecord,
    anomaly_input_for_evaluation,
    anomaly_input_for_run,
    anomaly_input_for_window,
    pattern_input_for_run,
)
from ai_eval_platform.monitoring.adapters.records import ConfidenceRecord, EvaluationRecord

from ai_eval_platform.monitoring.baselines import BaselineLookup, BaselineReference, BaselineStore
from ai_eval_platform.monitoring.detectors.strategies import AnomalyDetector, AnomalyDetectorPolicy, PatternDetector, PatternDetectorPolicy
from ai_eval_platform.monitoring.orchestration import DetectorOutcome, run_monitoring
from ai_eval_platform.monitoring.persistence import signal_to_record
from ai_eval_platform.monitoring.signals.types import SignalScope


def _latency():
    return LatencyRecord(
        agent_id="agent-1",
        metric_name="latency_ms",
        observed_value=900.0,
        expected_value=100.0,
        baseline_value=100.0,
        baseline_window="baseline-7d",
        threshold=10.0,
    )


def _calls():
    return [
        ToolCallRecord(agent_id="agent-1", run_id="run-1", tool_name="search", call_id="c1", event_id="e1"),
        ToolCallRecord(agent_id="agent-1", run_id="run-1", tool_name="search", call_id="c2", event_id="e2"),
    ]


def test_adapter_run_lineage_preserved():
    result = anomaly_input_for_run(_latency(), "run-77")
    assert result.agent_id == "agent-1"
    assert result.run_id == "run-77"
    assert result.evaluation_run_id is None


def test_adapter_evaluation_lineage():
    record = EvaluationRecord(
        agent_id="agent-1",
        evaluation_run_id="eval-3",
        metric_name="latency_ms",
        observed_value=500.0,
        expected_value=100.0,
        baseline_value=100.0,
        baseline_window="baseline-7d",
        threshold=10.0,
    )
    result = anomaly_input_for_evaluation(record)
    assert result.evaluation_run_id == "eval-3"
    assert result.run_id is None


def test_adapter_window_agent_mismatch():
    start = datetime.now(timezone.utc) - timedelta(hours=1)
    end = datetime.now(timezone.utc)
    window = WindowRecord(agent_id="agent-2", window_start=start, window_end=end)
    with pytest.raises(AdapterUnavailable):
        anomaly_input_for_window(_latency(), window)


def test_adapter_no_tool_calls_unavailable():
    with pytest.raises(AdapterUnavailable):
        pattern_input_for_run("agent-1", "run-9", [])


def test_adapter_pattern_preserves_event_ids():
    result = pattern_input_for_run("agent-1", "run-1", _calls(), severity=0.5)
    assert result.event_ids == ["e1", "e2"]
    assert result.severity == 0.5


def test_orchestration_multiple_detectors_agent_isolation():
    anomaly = AnomalyDetector(AnomalyDetectorPolicy())
    pattern = PatternDetector(PatternDetectorPolicy())
    outcome = run_monitoring(
        "agent-1",
        [
            (anomaly, anomaly_input_for_run(_latency(), "run-1")),
            (pattern, pattern_input_for_run("agent-1", "run-1", _calls())),
        ],
    )
    assert len(outcome.signals) == 2
    assert all(signal.agent_id == "agent-1" for signal in outcome.signals)


def test_orchestration_disabled_and_failure():
    anomaly = AnomalyDetector(AnomalyDetectorPolicy(enabled=False))

    class Broken:
        name = "broken"
        policy = AnomalyDetectorPolicy()

        def detect(self, _input_data):
            raise RuntimeError("boom")

    outcome = run_monitoring(
        "agent-1",
        [(anomaly, anomaly_input_for_run(_latency(), "run-1")), (Broken(), anomaly_input_for_run(_latency(), "run-1"))],
    )
    by_name = {run.detector_name: run for run in outcome.runs}
    assert by_name["anomaly"].outcome is DetectorOutcome.DISABLED
    assert by_name["broken"].outcome is DetectorOutcome.DETECTOR_FAILED


def test_baseline_store_is_interface_only():
    store = BaselineStore()
    with pytest.raises(NotImplementedError):
        store.reference_for(BaselineLookup(agent_id="a", metric_name="m"))


def test_baseline_reference_validates_windows():
    start = datetime.now(timezone.utc) - timedelta(days=7)
    end = datetime.now(timezone.utc)
    reference = BaselineReference(
        agent_id="a",
        metric_name="m",
        baseline_window="7d",
        expected_value=1.0,
        reference_window_start=start,
        reference_window_end=end,
    )
    assert reference.expected_value == 1.0


def test_persistence_record_round_trip():
    detector = AnomalyDetector(AnomalyDetectorPolicy())
    signals = detector.detect(anomaly_input_for_run(_latency(), "run-1"))
    record = signal_to_record(signals[0])
    assert record.signal_type == "latency_anomaly"
    assert record.payload["metric"] == "latency_ms"
    assert record.payload_type == "LatencyAnomalyPayload"
