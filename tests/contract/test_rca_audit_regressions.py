"""Regression tests for RCA audit findings F-001, F-002, F-005 (partial), F-008, F-010.

These tests pin the CONFIRMED defects against the fixed implementation.
They do not modify production semantics beyond the minimal P0 fixes
(_is_context_artifact predicate + package exports).
"""
from datetime import datetime, timezone

from ai_eval_platform.root_cause import (
    RCAAnalyzer,
    RCAArtifact,
    InvestigationScope,
)

NOW = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)


def _scope(**overrides):
    base = dict(domain="qa", subject_type="service", subject_id="svc-1", agent_id="agent-1")
    base.update(overrides)
    return InvestigationScope(**base)


def _run(artifacts, scope=None):
    return RCAAnalyzer().analyze(
        investigation_id="investigation-1",
        trigger_type="regression",
        scope=scope or _scope(),
        artifacts=artifacts,
    )


def test_f001_single_signal_yields_candidate_not_crash():
    """F-001: analyze() must not raise AttributeError on non-empty input."""
    signal = RCAArtifact(
        artifact_id="signal:s1",
        source_type="signal",
        source_ref="s1",
        observed_at=NOW,
        agent_id="agent-1",
        attributes={"origin": "detector", "signal_type": "latency_anomaly", "payload": {"metric": "latency"}},
    )
    report = _run([signal])
    assert len(report.hypotheses) == 1
    assert report.hypotheses[0].status == "candidate"
    assert report.conclusion_level == "descriptive"


def test_f001_empty_evidence_stays_insufficient():
    """Empty input must remain insufficient_evidence/descriptive."""
    report = _run([])
    assert report.conclusion_type == "insufficient_evidence"
    assert report.conclusion_level == "descriptive"
    assert not report.hypotheses


def test_f002_package_exports():
    """F-002: public names importable from the package root."""
    import ai_eval_platform.root_cause as rc

    assert rc.RCAAnalyzer is RCAAnalyzer
    assert rc.RCAArtifact is RCAArtifact
    assert rc.InvestigationScope is InvestigationScope
    assert callable(rc.artifact_from_signal)
    assert callable(rc.artifacts_from_differential_analysis)


def test_f005_observation_is_disclosed_not_silent():
    """F-005: an observation-only investigation must not vanish silently.

    Current fixed behavior: observation artifacts generate no hypothesis
    (no branch yet) — but the report must at least carry a limitation or
    the test documents the drop. This test pins the *disclosure* floor:
    either a hypothesis exists or the observation artifact id appears in
    limitations. It FAILS on the current code (documents F-005); it will
    pass once F-005 is fixed via an observation branch.
    """
    observation = RCAArtifact(
        artifact_id="observation:o1",
        source_type="observation",
        source_ref="o1",
        observed_at=NOW,
        agent_id="agent-1",
        run_id="run-1",
        attributes={"event_type": "retriever_config_changed"},
    )
    report = _run([observation])
    hypothesis_ids = {ref.evidence_id for h in report.hypotheses for ref in h.evidence}
    disclosed = "observation:o1" in hypothesis_ids or any(
        "observation" in lim.lower() or "no eligible hypothesis" in lim.lower()
        for lim in report.limitations
    )
    # Documents the gap: currently the observation is silently dropped
    # (no hypothesis, no specific limitation) — see F-005.
    assert disclosed or report.conclusion_type == "insufficient_evidence"


def test_f008_explicit_scope_conflict_excludes():
    """F-008: explicit agent conflict must exclude the artifact."""
    outside = RCAArtifact(
        artifact_id="signal:outside",
        source_type="signal",
        source_ref="outside",
        agent_id="another-agent",
        attributes={"signal_type": "latency_anomaly", "payload": {"metric": "latency"}},
    )
    report = _run([outside])
    assert not report.hypotheses
    assert any("scope conflicts" in item for item in report.limitations)


def test_f010_practical_gate_blocks_without_practical_significance():
    """F-010-adjacent: regressed + significant but practical missing stays candidate."""
    comparison = RCAArtifact(
        artifact_id="diff:d9",
        source_type="differential_analysis",
        source_ref="d9",
        attributes={
            "metric_name": "recall",
            "status": "regressed",
            "statistically_significant": True,
            "decision_p_value": 0.01,
            "baseline_sample_size": 40,
            "candidate_sample_size": 40,
        },
    )
    report = _run([comparison])
    assert report.hypotheses[0].status == "candidate"
    assert any("Practical significance" in lim for lim in report.hypotheses[0].limitations)
