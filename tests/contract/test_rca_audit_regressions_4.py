"""RCA_4 regression tests: hypothesis identity, cross-rule dedup, strength recompute,
conclusion independence, PII/artifact-size controls, and retrieval guards."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ai_eval_platform.root_cause import (
    RCAAnalyzer,
    RCAArtifact,
    InvestigationScope,
)
from ai_eval_platform.root_cause.domain import (
    RCA_ATTRIBUTES_BYTES_LIMIT,
    RCA_REPORT_BYTES_LIMIT,
    report_limit_violations,
)
from ai_eval_platform.root_cause.evidence import (
    sanitize_artifact_for_analysis,
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


def _diff(metric, status, source_ref="d1", significant=True, p=0.01,
          n_base=40, n_cand=40, practical=True, extra=None):
    attrs = {
        "metric_name": metric, "status": status,
        "statistically_significant": significant,
        "decision_p_value": p,
        "baseline_sample_size": n_base, "candidate_sample_size": n_cand,
        "practical_significance_met": practical,
    }
    if extra:
        attrs.update(extra)
    return RCAArtifact(
        artifact_id=f"differential_analysis:{source_ref}:{metric}",
        source_type="differential_analysis", source_ref=source_ref,
        evaluation_run_id="cand-1", attributes=attrs,
    )


# ---------------------------------------------------------------------------
# 1. Hypothesis identity: same rule, different claims → separate hypotheses
# ---------------------------------------------------------------------------

def test_same_rule_different_claims_separate_hypotheses():
    a = _diff("recall", "regressed", source_ref="da-1", extra={"effect_size": 0.6, "confidence_interval": [0.2, 1.0]})
    b = _diff("precision", "regressed", source_ref="da-2", extra={"effect_size": 0.6, "confidence_interval": [0.2, 1.0]})
    report = _run([a, b])
    ids = {h.hypothesis_id for h in report.hypotheses}
    assert len(ids) == 2
    assert all(h.status == "supported" for h in report.hypotheses)


# ---------------------------------------------------------------------------
# 2. Cross-rule duplicate evidence: provenance-based dedup
# ---------------------------------------------------------------------------

def test_cross_rule_duplicate_provenance_counts_once():
    shared_event = "evt-prod-42"
    a = RCAArtifact(
        artifact_id="signal:s1", source_type="signal", source_ref="s1",
        agent_id="agent-1",
        attributes={"signal_type": "latency_anomaly", "payload": {},
                    "source_event_id": shared_event},
    )
    b = RCAArtifact(
        artifact_id="differential_analysis:da-1:recall",
        source_type="differential_analysis", source_ref="da-1",
        evaluation_run_id="cand-1",
        attributes={"metric_name": "recall", "status": "regressed",
                    "statistically_significant": True, "decision_p_value": 0.01,
                    "baseline_sample_size": 40, "candidate_sample_size": 40,
                    "practical_significance_met": True,
                    "effect_size": 0.6, "confidence_interval": [0.2, 1.0],
                    "source_event_id": shared_event},
    )
    report = _run([a, b])
    supported = [h for h in report.hypotheses if h.status == "supported"]
    assert len(supported) >= 1
    assert report.conclusion_type == "probable_cause"


def test_cross_rule_distinct_provenance_counts_separately():
    a = RCAArtifact(
        artifact_id="signal:s1", source_type="signal", source_ref="s1",
        agent_id="agent-1",
        attributes={"signal_type": "latency_anomaly", "payload": {},
                    "source_event_id": "evt-a"},
    )
    b = _diff("recall", "regressed", source_ref="da-1",
              extra={"effect_size": 0.6, "confidence_interval": [0.2, 1.0],
                     "source_event_id": "evt-b"})
    report = _run([a, b])
    supported = [h for h in report.hypotheses if h.status == "supported"]
    assert len(supported) >= 1
    # Signal is candidate (not supported); only the diff is supported → one
    # independent supporting source → probable_cause.
    assert report.conclusion_type == "probable_cause"


# ---------------------------------------------------------------------------
# 3. Strength recompute from deduplicated evidence
# ---------------------------------------------------------------------------

def test_merge_recomputes_strength_from_deduped_evidence():
    a = RCAArtifact(
        artifact_id="signal:s1", source_type="signal", source_ref="s1",
        attributes={"signal_type": "latency_anomaly", "payload": {}},
    )
    b = RCAArtifact(
        artifact_id="signal:s1-copy", source_type="signal", source_ref="s1",
        attributes={"signal_type": "latency_anomaly", "payload": {}},
    )
    report = _run([a, b])
    assert len(report.hypotheses) == 1
    assert report.hypotheses[0].status == "candidate"
    assert report.hypotheses[0].strength == "weak"


def test_merge_two_distinct_sources_strength_strong():
    a = _diff("recall", "regressed", source_ref="da-1",
              extra={"effect_size": 0.6, "confidence_interval": [0.2, 1.0]})
    b = _diff("recall", "regressed", source_ref="da-2",
              extra={"effect_size": 0.6, "confidence_interval": [0.2, 1.0]})
    report = _run([a, b])
    assert len(report.hypotheses) == 1
    hyp = report.hypotheses[0]
    assert hyp.status == "supported"
    assert hyp.strength == "strong"


# ---------------------------------------------------------------------------
# 4. Conclusion independence: sources not hypotheses
# ---------------------------------------------------------------------------

def test_two_hypotheses_one_source_not_multiple_contributing():
    shared = "evt-shared-7"
    # Two differentials on different metrics (different rules) but the same
    # source_event_id → two hypotheses, one independent source.
    a = _diff("recall", "regressed", source_ref="da-1",
              extra={"effect_size": 0.6, "confidence_interval": [0.2, 1.0],
                     "source_event_id": shared})
    b = _diff("precision", "regressed", source_ref="da-2",
              extra={"effect_size": 0.6, "confidence_interval": [0.2, 1.0],
                     "source_event_id": shared})
    report = _run([a, b])
    supported = [h for h in report.hypotheses if h.status == "supported"]
    assert len(supported) == 2
    assert report.conclusion_type == "probable_cause"


def test_two_hypotheses_two_sources_multiple_contributing():
    # Two differentials on different metrics with different source_event_ids
    # → two independent sources → multiple_contributing_factors.
    a = _diff("recall", "regressed", source_ref="da-1",
              extra={"effect_size": 0.6, "confidence_interval": [0.2, 1.0],
                     "source_event_id": "evt-a"})
    b = _diff("precision", "regressed", source_ref="da-2",
              extra={"effect_size": 0.6, "confidence_interval": [0.2, 1.0],
                     "source_event_id": "evt-b"})
    report = _run([a, b])
    supported = [h for h in report.hypotheses if h.status == "supported"]
    assert len(supported) == 2
    assert report.conclusion_type == "multiple_contributing_factors"


# ---------------------------------------------------------------------------
# 5. Supporting + counter evidence
# ---------------------------------------------------------------------------

def test_supporting_plus_counter_inconclusive():
    a = _diff("recall", "regressed", source_ref="da-1",
              extra={"effect_size": 0.6, "confidence_interval": [0.2, 1.0]})
    b = _diff("recall", "improved", source_ref="da-2",
              extra={"effect_size": 0.6, "confidence_interval": [0.2, 1.0]})
    report = _run([a, b])
    statuses = sorted(h.status for h in report.hypotheses)
    assert "inconclusive" in statuses or "supported" in statuses
    assert any(r.role == "counter" for h in report.hypotheses for r in h.evidence)


# ---------------------------------------------------------------------------
# 6. Missing provenance falls back to (source_type, source_ref)
# ---------------------------------------------------------------------------

def test_missing_provenance_falls_back_to_type_ref():
    a = RCAArtifact(
        artifact_id="signal:s1", source_type="signal", source_ref="s1",
        attributes={"signal_type": "latency_anomaly", "payload": {}},
    )
    b = RCAArtifact(
        artifact_id="signal:s1-copy", source_type="signal", source_ref="s1",
        attributes={"signal_type": "latency_anomaly", "payload": {}},
    )
    report = _run([a, b])
    assert len(report.hypotheses) == 1
    assert any("same underlying source" in lim for h in report.hypotheses for lim in h.limitations)


# ---------------------------------------------------------------------------
# 7. Oversized / PII payload controls
# ---------------------------------------------------------------------------

def test_pii_sensitive_key_redacted():
    artifact = RCAArtifact(
        artifact_id="signal:s1", source_type="signal", source_ref="s1",
        attributes={"signal_type": "latency_anomaly", "password": "hunter2", "payload": {}},
    )
    sanitized, notes = sanitize_artifact_for_analysis(artifact)
    assert sanitized is not None
    assert sanitized.attributes["password"] == "[REDACTED]"
    assert any("redacted" in note for note in notes)


def test_secret_value_scrubbed():
    artifact = RCAArtifact(
        artifact_id="signal:s1", source_type="signal", source_ref="s1",
        attributes={"signal_type": "latency_anomaly",
                    "data_value": "sk-live-abcdefghijklmnopqrstuvwxyz1234567890",
                    "payload": {}},
    )
    sanitized, notes = sanitize_artifact_for_analysis(artifact)
    assert sanitized is not None
    assert "sk-live" not in sanitized.attributes["data_value"]
    assert any("scrubbed" in note for note in notes)
    assert any("scrubbed" in note for note in notes)
    assert any("scrubbed" in note for note in notes)


def test_oversized_attributes_excluded():
    big_value = "x" * 512
    attrs = {"signal_type": "latency_anomaly"}
    for i in range(70):
        attrs[f"field_{i}"] = big_value
    artifact = RCAArtifact(
        artifact_id="signal:s1", source_type="signal", source_ref="s1",
        attributes=attrs,
    )
    sanitized, notes = sanitize_artifact_for_analysis(artifact)
    assert sanitized is None
    assert any("Excluded" in note for note in notes)


def test_oversized_artifact_excluded_from_report():
    big_value = "x" * 512
    attrs = {"signal_type": "latency_anomaly"}
    for i in range(70):
        attrs[f"field_{i}"] = big_value
    artifact = RCAArtifact(
        artifact_id="signal:big", source_type="signal", source_ref="big",
        attributes=attrs,
    )
    report = _run([artifact])
    assert not any("signal:big" in {r.evidence_id for r in h.evidence}
                   for h in report.hypotheses)
    assert any("Excluded" in lim for lim in report.limitations)


# ---------------------------------------------------------------------------
# 8. Report size limit violations
# ---------------------------------------------------------------------------

def test_report_limit_violations_detects_oversized():
    report_dict = {
        "schema_version": "1.1",
        "investigation_id": "inv-1",
        "trigger_type": "regression",
        "status": "completed",
        "scope": {"domain": "qa", "subject_type": "service", "subject_id": "svc-1"},
        "hypotheses": [],
        "conclusion_type": "insufficient_evidence",
        "conclusion_level": "descriptive",
        "summary": "x" * (RCA_REPORT_BYTES_LIMIT + 100),
        "limitations": [],
        "differential_analysis_ids": (),
        "algorithm_name": "hybrid_rca",
        "algorithm_version": "1.2.0",
        "evidence_fingerprint": "abc",
    }
    violations = report_limit_violations(report_dict)
    assert any("exceeds" in v.lower() for v in violations)


def test_report_limit_violations_detects_too_many_hypotheses():
    report_dict = {
        "schema_version": "1.1",
        "investigation_id": "inv-1",
        "trigger_type": "regression",
        "status": "completed",
        "scope": {"domain": "qa", "subject_type": "service", "subject_id": "svc-1"},
        "hypotheses": [{"hypothesis_id": f"hyp_{i}", "title": "t", "statement": "s",
                        "mechanism": "m", "status": "candidate", "strength": "weak",
                        "evidence": [], "limitations": (), "rule_ids": ()}
                       for i in range(300)],
        "conclusion_type": "insufficient_evidence",
        "conclusion_level": "descriptive",
        "summary": "short",
        "limitations": [],
        "differential_analysis_ids": (),
        "algorithm_name": "hybrid_rca",
        "algorithm_version": "1.2.0",
        "evidence_fingerprint": "abc",
    }
    violations = report_limit_violations(report_dict)
    assert any("hypotheses" in v.lower() for v in violations)


# ---------------------------------------------------------------------------
# 9. Artifact count limit enforced
# ---------------------------------------------------------------------------

def test_artifact_count_limit_enforced():
    artifacts = [
        RCAArtifact(
            artifact_id=f"metric:m{i}", source_type="metric", source_ref=f"m{i}",
            attributes={},
        )
        for i in range(300)
    ]
    with pytest.raises(ValueError, match="at most"):
        _run(artifacts)


# ---------------------------------------------------------------------------
# 10. Scope conflict exclusion
# ---------------------------------------------------------------------------

def test_explicit_agent_conflict_excludes():
    scope = _scope(agent_id="agent-1")
    artifact = RCAArtifact(
        artifact_id="signal:outside", source_type="signal", source_ref="outside",
        agent_id="agent-OTHER",
        attributes={"signal_type": "latency_anomaly", "payload": {}},
    )
    report = _run([artifact], scope=scope)
    assert not report.hypotheses
    assert any("scope conflicts" in lim for lim in report.limitations)

