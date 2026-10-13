from datetime import datetime, timezone
from types import SimpleNamespace

from ai_eval_platform.analysis.engine import SignalSummaryEngine


def test_summary_uses_signal_evidence_and_groups_counts():
    signals = [
        SimpleNamespace(
            signal_id="sig-1",
            agent_id="agent-a",
            signal_type="repeated_tool_failure",
            detector_name="pattern",
            detected_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        SimpleNamespace(
            signal_id="sig-2",
            agent_id="agent-a",
            signal_type="repeated_tool_failure",
            detector_name="pattern",
            detected_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        ),
        SimpleNamespace(
            signal_id="sig-3",
            agent_id="agent-b",
            signal_type="confidence_drop",
            detector_name="confidence",
            detected_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
        ),
    ]

    result = SignalSummaryEngine().analyze(
        signals,
        agent_id="agent-a",
    )

    assert result.sample_size == 2
    assert result.evidence_signal_ids == ["sig-1", "sig-2"]
    assert result.metrics["signal_count"] == 2
    assert result.metrics["signal_counts_by_type"] == {
        "repeated_tool_failure": 2
    }
    assert result.window_start == datetime(
        2026, 1, 1, tzinfo=timezone.utc
    )
    assert result.window_end == datetime(
        2026, 1, 2, tzinfo=timezone.utc
    )


def test_empty_signal_set_reports_limitation():
    result = SignalSummaryEngine().analyze(
        [],
        agent_id="agent-a",
    )

    assert result.sample_size == 0
    assert result.metrics["signal_count"] == 0
    assert result.limitations
