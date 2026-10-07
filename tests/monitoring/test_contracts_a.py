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
from ai_eval_platform.monitoring.detectors.inputs import (
    AnomalyDetectionInput,
    DriftDetectionInput,
    PatternDetectionInput,
)
from ai_eval_platform.monitoring.detectors.strategies import (
    AnomalyDetector,
    AnomalyDetectorPolicy,
    DriftDetector,
    DriftDetectorPolicy,
    PatternDetector,
    PatternDetectorPolicy,
)
from ai_eval_platform.monitoring.signals.models import Signal
from ai_eval_platform.monitoring.signals.types import (
    ConfidenceDropPayload,
    LatencyAnomalyPayload,
    RepeatedToolUsagePayload,
    RouteChangePayload,
    SignalScope,
    SignalType,
    ToolUsageDriftPayload,
)


def _window():
    end = datetime.now(timezone.utc)
    return end - timedelta(hours=1), end


def _run_anomaly_input(**overrides):
    values = {
        "agent_id": "agent-1",
        "run_id": "run-1",
        "metric_name": "latency_ms",
        "observed_value": 900.0,
        "expected_value": 100.0,
        "baseline_value": 100.0,
        "baseline_window": "baseline-7d",
        "threshold": 10.0,
    }
    values.update(overrides)
    return AnomalyDetectionInput(**values)


def _run_pattern_input(**overrides):
    values = {
        "agent_id": "agent-1",
        "run_id": "run-1",
        "event_sequences": [["search", "search", "search"]],
        "event_ids": ["e1"],
        "severity": 0.4,
    }
    values.update(overrides)
    return PatternDetectionInput(**values)
