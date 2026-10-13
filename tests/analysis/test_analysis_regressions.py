from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from ai_eval_platform.analysis.engine import SignalSummaryEngine


def _signal(signal_id, agent_id, signal_type, detected_at):
    return SimpleNamespace(
        signal_id=signal_id,
        agent_id=agent_id,
        signal_type=signal_type,
        detector_name=None,
        detected_at=detected_at,
    )


def test_excluded_signals_are_reported_as_limitations():
    result = SignalSummaryEngine().analyze(
        [
            _signal("sig-1", "agent-a", "t", datetime(2026, 1, 1, tzinfo=timezone.utc)),
            _signal("sig-2", "agent-b", "t", datetime(2026, 1, 2, tzinfo=timezone.utc)),
            _signal(None, "agent-a", "t", datetime(2026, 1, 3, tzinfo=timezone.utc)),
        ],
        agent_id="agent-a",
    )

    assert result.sample_size == 1
    assert result.evidence_signal_ids == ["sig-1"]
    assert result.metrics["excluded_signals"] == {
        "agent_mismatch": 1,
        "missing_signal_id": 1,
    }
    assert len(result.limitations) == 2


def test_signal_without_detected_at_is_excluded_from_window():
    result = SignalSummaryEngine().analyze(
        [
            _signal("sig-1", "agent-a", "t", datetime(2026, 1, 1, tzinfo=timezone.utc)),
            _signal("sig-2", "agent-a", "t", None),
        ],
        agent_id="agent-a",
    )

    assert result.sample_size == 2
    assert result.evidence_signal_ids == ["sig-1", "sig-2"]
    assert result.window_start == result.window_end
    assert result.window_start == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert result.metrics["excluded_signals"] == {"missing_detected_at": 1}
    assert any("missing_detected_at" in item for item in result.limitations)


def test_mixed_timezone_and_naive_timestamps_do_not_raise():
    result = SignalSummaryEngine().analyze(
        [
            _signal("sig-1", "agent-a", "t", datetime(2026, 1, 1)),
            _signal("sig-2", "agent-a", "t", datetime(2026, 1, 2, tzinfo=timezone.utc)),
        ],
        agent_id="agent-a",
    )

    assert result.window_start.tzinfo is not None
    assert result.window_end.tzinfo is not None
    assert result.window_start == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert result.window_end == datetime(2026, 1, 2, tzinfo=timezone.utc)


def test_null_signal_type_is_not_counted_as_none_key():
    result = SignalSummaryEngine().analyze(
        [_signal("sig-1", "agent-a", None, datetime(2026, 1, 1, tzinfo=timezone.utc))],
        agent_id="agent-a",
    )

    assert result.metrics["signal_counts_by_type"] == {"unknown": 1}
    assert result.metrics["signal_counts_by_detector"] == {"unknown": 1}


def test_all_signals_for_other_agents_reports_exclusions():
    result = SignalSummaryEngine().analyze(
        [_signal("sig-1", "agent-b", "t", datetime(2026, 1, 1, tzinfo=timezone.utc))],
        agent_id="agent-a",
    )

    assert result.sample_size == 0
    assert result.evidence_signal_ids == []
    assert result.window_start is None
    assert result.window_end is None
    assert any("agent_mismatch" in item for item in result.limitations)
    assert any("No persisted signals" in item for item in result.limitations)


@pytest.mark.parametrize("agent_id", ["", "   "])
def test_empty_agent_id_is_rejected(agent_id):
    with pytest.raises(ValueError):
        SignalSummaryEngine().analyze([], agent_id=agent_id)
