from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ai_eval_platform.root_cause import (
    RCAAnalyzer,
    RCAArtifact,
    InvestigationScope,
)
from ai_eval_platform.root_cause.domain import RCAHypothesis
from ai_eval_platform.root_cause.evidence import (
    RCAEvidenceReference,
    reference_for,
)

NOW = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)


def _scope(**overrides):
    base = dict(domain="qa", subject_type="service", subject_id="svc-1",
                agent_id="agent-1", run_id="run-1", evaluation_run_id="cand-1")
    base.update(overrides)
    return InvestigationScope(**base)


def _run(artifacts, scope=None):
    return RCAAnalyzer().analyze(
        investigation_id="investigation-1",
        trigger_type="regression",
        scope=scope or _scope(),
        artifacts=artifacts,
    )


def _diff(metric, status, source_ref="da-1", significant=True, p=0.01,
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


def _supporting_ref(artifact, role="supporting"):
    return reference_for(artifact, role=role, rationale="test rationale")


# ---------------------------------------------------------------------------
# 1. Claim identity independent of rule_id (RCA_5 cross-rule consolidation)
# ---------------------------------------------------------------------------

def test_make_hypothesis_identity_independent_of_rule_id():
    artifact = _diff("recall", "regressed", source_ref="da-1")
    ref = _supporting_ref(artifact)
    hyp_a = RCAAnalyzer._make_hypothesis(
        artifact=artifact, rule_id="rule.a", title="Same claim",
        statement="The candidate evaluation is associated with a regression in recall.",
        mechanism="mechanism a", references=(ref,), status="supported",
        strength="moderate", limitations=(),
    )
    hyp_b = RCAAnalyzer._make_hypothesis(
        artifact=artifact, rule_id="rule.b", title="Same claim",
        statement="The candidate evaluation is associated with a regression in recall.",
        mechanism="mechanism b", references=(ref,), status="supported",
        strength="moderate", limitations=(),
    )
    assert hyp_a.hypothesis_id == hyp_b.hypothesis_id
    assert hyp_a.rule_ids == ("rule.a",)
    assert hyp_b.rule_ids == ("rule.b",)


def test_cross_rule_merge_unions_rule_ids_and_evidence():
    artifact = _diff("recall", "regressed", source_ref="da-1").model_copy(
        update={"domain": "shared-domain"}
    )
    ref_a = _supporting_ref(artifact)
    ref_b = RCAEvidenceReference(
        evidence_id="signal:s1", source_type="signal", source_ref="s1",
        role="supporting", rationale="test rationale",
    )
    hyp_a = RCAAnalyzer._make_hypothesis(
        artifact=artifact, rule_id="rule.a", title="Same claim",
        statement="The candidate evaluation is associated with a regression in recall.",
        mechanism="mechanism a", references=(ref_a,), status="supported",
        strength="moderate", limitations=(),
    )
    hyp_b = RCAAnalyzer._make_hypothesis(
        artifact=artifact, rule_id="rule.b", title="Same claim",
        statement="The candidate evaluation is associated with a regression in recall.",
        mechanism="mechanism b", references=(ref_b,), status="supported",
        strength="moderate", limitations=(),
    )
    assert hyp_a.hypothesis_id == hyp_b.hypothesis_id
    merged = RCAAnalyzer()._merge_hypotheses([hyp_a, hyp_b])
    assert len(merged) == 1
    assert set(merged[0].rule_ids) == {"rule.a", "rule.b"}
    assert len(merged[0].evidence) == 2
    assert {ref.evidence_id for ref in merged[0].evidence} == {
        ref_a.evidence_id, ref_b.evidence_id,
    }


# ---------------------------------------------------------------------------
# 2. Different claims or scopes remain separate
# ---------------------------------------------------------------------------

def test_different_claims_different_rules_remain_separate():
    a = _diff("recall", "regressed", source_ref="da-a")
    b = _diff("precision", "regressed", source_ref="da-b")
    report = _run([a, b])
    ids = {h.hypothesis_id for h in report.hypotheses}
    assert len(ids) == 2
    assert all(h.status == "supported" for h in report.hypotheses)


def test_same_claim_different_scopes_remain_separate():
    scope_a = _scope(domain="domain-a")
    scope_b = _scope(domain="domain-b")
    a = _diff("recall", "regressed", source_ref="da-a")
    a = a.model_copy(update={"domain": "domain-a"})
    b = _diff("recall", "regressed", source_ref="da-b")
    b = b.model_copy(update={"domain": "domain-b"})
    report_a = _run([a], scope=scope_a)
    report_b = _run([b], scope=scope_b)
    assert report_a.hypotheses[0].hypothesis_id != report_b.hypotheses[0].hypothesis_id


# ---------------------------------------------------------------------------
# 3. Evidence deduplication and strength recomputation after merge
# ---------------------------------------------------------------------------

def test_cross_rule_merge_strength_from_deduped_evidence():
    artifact = _diff("recall", "regressed", source_ref="da-1")
    ref_a = _supporting_ref(artifact)
    ref_b = _supporting_ref(artifact)
    hyp_a = RCAAnalyzer._make_hypothesis(
        artifact=artifact, rule_id="rule.a", title="Same claim",
        statement="The candidate evaluation is associated with a regression in recall.",
        mechanism="m a", references=(ref_a,), status="supported",
        strength="moderate", limitations=(),
    )
    hyp_b = RCAAnalyzer._make_hypothesis(
        artifact=artifact, rule_id="rule.b", title="Same claim",
        statement="The candidate evaluation is associated with a regression in recall.",
        mechanism="m b", references=(ref_b,), status="supported",
        strength="moderate", limitations=(),
    )
    merged = RCAAnalyzer()._merge_hypotheses([hyp_a, hyp_b])
    assert len(merged) == 1
    assert len(merged[0].evidence) == 1
    assert merged[0].strength == "moderate"


def test_cross_rule_merge_two_distinct_sources_strength_strong():
    artifact_a = _diff("recall", "regressed", source_ref="da-a")
    artifact_b = _diff("recall", "regressed", source_ref="da-b")
    ref_a = _supporting_ref(artifact_a)
    ref_b = _supporting_ref(artifact_b)
    hyp_a = RCAAnalyzer._make_hypothesis(
        artifact=artifact_a, rule_id="rule.a", title="Same claim",
        statement="The candidate evaluation is associated with a regression in recall.",
        mechanism="m a", references=(ref_a,), status="supported",
        strength="moderate", limitations=(),
    )
    hyp_b = RCAAnalyzer._make_hypothesis(
        artifact=artifact_b, rule_id="rule.b", title="Same claim",
        statement="The candidate evaluation is associated with a regression in recall.",
        mechanism="m b", references=(ref_b,), status="supported",
        strength="moderate", limitations=(),
    )
    merged = RCAAnalyzer()._merge_hypotheses([hyp_a, hyp_b])
    assert len(merged) == 1
    assert len(merged[0].evidence) == 2
    assert merged[0].strength == "strong"


# ---------------------------------------------------------------------------
# 4. Determinism regardless of rule-processing order
# ---------------------------------------------------------------------------

def test_analysis_deterministic_regardless_of_input_order():
    a = _diff("recall", "regressed", source_ref="da-a")
    b = _diff("precision", "regressed", source_ref="da-b")
    report_ab = _run([a, b])
    report_ba = _run([b, a])
    ids_ab = {h.hypothesis_id for h in report_ab.hypotheses}
    ids_ba = {h.hypothesis_id for h in report_ba.hypotheses}
    assert ids_ab == ids_ba
    assert report_ab.conclusion_type == report_ba.conclusion_type
    assert report_ab.conclusion_level == report_ba.conclusion_level


def test_merge_deterministic_regardless_of_input_order():
    artifact_a = _diff("recall", "regressed", source_ref="da-a")
    artifact_b = _diff("recall", "regressed", source_ref="da-b")
    ref_a = _supporting_ref(artifact_a)
    ref_b = _supporting_ref(artifact_b)
    hyp_a = RCAAnalyzer._make_hypothesis(
        artifact=artifact_a, rule_id="rule.a", title="Same claim",
        statement="The candidate evaluation is associated with a regression in recall.",
        mechanism="m a", references=(ref_a,), status="supported",
        strength="moderate", limitations=(),
    )
    hyp_b = RCAAnalyzer._make_hypothesis(
        artifact=artifact_b, rule_id="rule.b", title="Same claim",
        statement="The candidate evaluation is associated with a regression in recall.",
        mechanism="m b", references=(ref_b,), status="supported",
        strength="moderate", limitations=(),
    )
    merged_ab = RCAAnalyzer()._merge_hypotheses([hyp_a, hyp_b])
    merged_ba = RCAAnalyzer()._merge_hypotheses([hyp_b, hyp_a])
    assert len(merged_ab) == 1 and len(merged_ba) == 1
    assert merged_ab[0].hypothesis_id == merged_ba[0].hypothesis_id
    assert set(merged_ab[0].rule_ids) == set(merged_ba[0].rule_ids)
    assert {ref.evidence_id for ref in merged_ab[0].evidence} == {
        ref.evidence_id for ref in merged_ba[0].evidence
    }