from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ai_eval_platform.analysis.service import AnalysisService
from ai_eval_platform.storage.models import PersistedSignal


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@pytest.mark.parametrize("agent_id", ["", "   "])
def test_empty_agent_id_is_rejected(session, agent_id):
    with pytest.raises(ValueError):
        AnalysisService(session).summarize_agent(agent_id)


def test_truncated_result_reports_limitation(session, seeded_run):
    base = _utcnow()
    signals = [
        PersistedSignal(
            signal_id=f"t-{i}",
            agent_id="agent-1",
            signal_type="repeated_tool_usage",
            scope="run",
            run_id=seeded_run,
            detected_at=base + timedelta(seconds=i),
            payload={},
            payload_type="RepeatedToolUsagePayload",
        )
        for i in range(3)
    ]

    session.add_all(signals)
    session.flush()

    service = AnalysisService(session)
    result = service.summarize_agent("agent-1", limit=2)

    assert result.sample_size == 2
    assert any("truncated" in item for item in result.limitations)


def test_exact_limit_does_not_report_truncation(session, seeded_run):
    base = _utcnow()
    session.add_all(
        [
            PersistedSignal(
                signal_id=f"t-{i}",
                agent_id="agent-1",
                signal_type="repeated_tool_usage",
                scope="run",
                run_id=seeded_run,
                detected_at=base + timedelta(seconds=i),
                payload={},
                payload_type="RepeatedToolUsagePayload",
            )
            for i in range(3)
        ]
    )
    session.flush()

    result = AnalysisService(session).summarize_agent("agent-1", limit=3)

    assert result.sample_size == 3
    assert not any("truncated" in item for item in result.limitations)


def test_empty_persisted_signals_report_limitation(session, seeded_run):
    result = AnalysisService(session).summarize_agent("agent-1")

    assert result.sample_size == 0
    assert result.evidence_signal_ids == []
    assert result.window_start is None
    assert result.window_end is None
    assert result.limitations


def test_evidence_matches_analyzed_signals(session, seeded_run):
    base = _utcnow()
    session.add_all(
        [
            PersistedSignal(
                signal_id=f"t-{i}",
                agent_id="agent-1",
                signal_type="repeated_tool_usage",
                scope="run",
                run_id=seeded_run,
                detected_at=base + timedelta(seconds=i),
                payload={},
                payload_type="RepeatedToolUsagePayload",
            )
            for i in range(3)
        ]
    )
    session.flush()

    result = AnalysisService(session).summarize_agent("agent-1")

    assert len(result.evidence_signal_ids) == result.sample_size
    assert len(set(result.evidence_signal_ids)) == result.sample_size
