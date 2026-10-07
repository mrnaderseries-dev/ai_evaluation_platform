from .records import (
    AdapterUnavailable,
    ConfidenceRecord,
    EvaluationRecord,
    LatencyRecord,
    RunRecord,
    ToolCallRecord,
    WindowRecord,
)
from ..detectors.inputs import AnomalyDetectionInput, PatternDetectionInput


def pattern_input_for_run(
    agent_id: str,
    run_id: str,
    calls: list[ToolCallRecord],
    severity: float = 0.0,
) -> PatternDetectionInput:
    scoped = [call for call in calls if call.agent_id == agent_id and call.run_id == run_id]
    if not scoped:
        raise AdapterUnavailable("no tool calls for run")
    sequence = [call.tool_name for call in scoped]
    return PatternDetectionInput(
        agent_id=agent_id,
        run_id=run_id,
        event_sequences=[sequence],
        event_ids=[call.event_id for call in scoped],
        severity=severity,
    )


def anomaly_input_for_run(record: LatencyRecord, run_id: str) -> AnomalyDetectionInput:
    return AnomalyDetectionInput(
        agent_id=record.agent_id,
        run_id=run_id,
        metric_name=record.metric_name,
        observed_value=record.observed_value,
        expected_value=record.expected_value,
        baseline_value=record.baseline_value,
        baseline_window=record.baseline_window,
        threshold=record.threshold,
        sample_count=record.sample_count,
    )


def anomaly_input_for_evaluation(record: EvaluationRecord) -> AnomalyDetectionInput:
    return AnomalyDetectionInput(
        agent_id=record.agent_id,
        evaluation_run_id=record.evaluation_run_id,
        metric_name=record.metric_name,
        observed_value=record.observed_value,
        expected_value=record.expected_value,
        baseline_value=record.baseline_value,
        baseline_window=record.baseline_window,
        threshold=record.threshold,
        sample_count=record.sample_count,
    )


def anomaly_input_for_window(record: LatencyRecord, window: WindowRecord) -> AnomalyDetectionInput:
    if record.agent_id != window.agent_id:
        raise AdapterUnavailable("window agent mismatch")
    return AnomalyDetectionInput(
        agent_id=record.agent_id,
        window_start=window.window_start,
        window_end=window.window_end,
        metric_name=record.metric_name,
        observed_value=record.observed_value,
        expected_value=record.expected_value,
        baseline_value=record.baseline_value,
        baseline_window=record.baseline_window,
        threshold=record.threshold,
        sample_count=record.sample_count,
    )
