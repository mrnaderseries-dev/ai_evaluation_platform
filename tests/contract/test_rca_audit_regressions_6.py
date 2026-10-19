from __future__ import annotations

from datetime import datetime, timezone

from ai_eval_platform.root_cause import (
    RCAAnalyzer,
    RCAArtifact,
    InvestigationScope,
)
from ai_eval_platform.root_cause.domain import (
    HypothesisValidation,
    RCAHypothesis,
)
from ai_eval_platform.root_cause.evidence import (
    RCAEvidenceReference,
    reference_for,
)

NOW = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)

CLAIM = "The candidate evaluation is associated with a regression in recall."


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


def _diff(metric, status, source_ref, baseline="base-1", candidate="cand-1"):
    attrs = {
        "metric_name": metric, "status": status,
        "statistically_significant": True, "decision_p_value": 0.01,
        "baseline_sample_size": 40, "candidate_sample_size": 40,
        "practical_significance_met": True,
        "effect_size": 0.6, "confidence_interval": [0.2, 1.0],
    }
    if baseline is not None:
        attrs["baseline_evaluation_run_id"] = baseline
    if candidate is not None:
        attrs["candidate_evaluation_run_id"] = candidate
    return RCAArtifact(
        artifact_id=f"differential_analysis:{source_ref}:{metric}",
        source_type="differential_analysis", source_ref=source_ref,
        evaluation_run_id="cand-1", attributes=attrs,
    )


def _ref(artifact, role="supporting"):
    return reference_for(artifact, role=role, rationale="test rationale")


def _hyp(artifact, rule_id, mechanism, refs, status="supported",
         strength="moderate", validation=None, title="Same claim",
         statement=CLAIM):
    base = RCAAnalyzer._make_hypothesis(
        artifact=artifact, rule_id=rule_id, title=title, statement=statement,
        mechanism=mechanism, references=tuple(refs), status="candidate",
        strength="weak", limitations=(),
    )
    return RCAHypothesis(
        hypothesis_id=base.hypothesis_id,
        title=base.title, statement=base.statement,
        mechanism=mechanism, status=status, strength=strength,
        evidence=base.evidence, validation=validation,
        limitations=base.limitations, rule_ids=base.rule_ids,
    )


def _merge(hyp_a, hyp_b):
    return RCAAnalyzer()._merge_hypotheses([hyp_a, hyp_b])

def test_conflicting_validation_outcomes_cannot_remain_validated():
    a = _diff("recall", "regressed", "da-a")
    b = _diff("recall", "regressed", "da-b")
    sup = _ref(a, "supporting")
    cnt = _ref(b, "counter")
    validated = _hyp(
        a, "rule.a", "mech", [sup], status="validated", strength="strong",
        validation=HypothesisValidation(
            artifact_id="va-confirmed", method="rollback_test",
            outcome="confirmed", performed_at=NOW, evidence=(sup,),
        ),
    )
    rejected = _hyp(
        b, "rule.b", "mech", [cnt], status="rejected", strength="weak",
        validation=HypothesisValidation(
            artifact_id="va-refuted", method="reproduction",
            outcome="refuted", performed_at=NOW, evidence=(cnt,),
        ),
    )
    merged_ab = _merge(validated, rejected)[0]
    merged_ba = _merge(rejected, validated)[0]
    for merged in (merged_ab, merged_ba):
        assert merged.status == "inconclusive"
        assert merged.validation is None
        assert any(
            "conflicting validation outcomes" in limitation
            for limitation in merged.limitations
        )
        RCAHypothesis.model_validate(merged.model_dump())


def test_conflicting_inconclusive_validation_record_detached():
    a = _diff("recall", "regressed", "da-a")
    b = _diff("recall", "regressed", "da-b")
    sup = _ref(a, "supporting")
    cnt = _ref(b, "counter")
    validated = _hyp(
        a, "rule.a", "mech", [sup], status="validated", strength="strong",
        validation=HypothesisValidation(
            artifact_id="va-confirmed", method="rollback_test",
            outcome="confirmed", performed_at=NOW, evidence=(sup,),
        ),
    )
    reviewed = _hyp(
        b, "rule.b", "mech", [cnt], status="candidate", strength="weak",
        validation=HypothesisValidation(
            artifact_id="va-inconclusive", method="human_review",
            outcome="inconclusive", performed_at=NOW, evidence=(cnt,),
        ),
    )
    merged = _merge(validated, reviewed)[0]
    assert merged.status == "inconclusive"
    assert merged.validation is None
    assert any(
        "conflicting validation outcomes" in limitation
        for limitation in merged.limitations
    )
    RCAHypothesis.model_validate(merged.model_dump())


def test_unadopted_refuted_validation_demotes_and_discloses():
    a = _diff("recall", "regressed", "da-a")
    b = _diff("recall", "regressed", "da-b")
    sup = _ref(a, "supporting")
    cnt = _ref(b, "counter")
    supported = _hyp(a, "rule.a", "mech", [sup], status="supported")
    rejected = _hyp(
        b, "rule.b", "mech", [cnt], status="rejected", strength="weak",
        validation=HypothesisValidation(
            artifact_id="va-refuted", method="reproduction",
            outcome="refuted", performed_at=NOW, evidence=(cnt,),
        ),
    )
    merged_ab = _merge(supported, rejected)[0]
    merged_ba = _merge(rejected, supported)[0]
    for merged in (merged_ab, merged_ba):
        assert merged.status == "inconclusive"
        assert merged.validation is None
        assert any(
            "was not adopted" in limitation
            for limitation in merged.limitations
        )
        RCAHypothesis.model_validate(merged.model_dump())


def test_mechanism_conflict_disclosed_in_both_orders():
    a = _diff("recall", "regressed", "da-a")
    b = _diff("recall", "regressed", "da-b")
    hyp_a = _hyp(a, "rule.a", "mechanism A", [_ref(a)])
    hyp_b = _hyp(b, "rule.b", "mechanism B", [_ref(b)])
    merged_ab = _merge(hyp_a, hyp_b)[0]
    merged_ba = _merge(hyp_b, hyp_a)[0]
    for merged in (merged_ab, merged_ba):
        assert merged.mechanism in {"mechanism A", "mechanism B"}
        assert any(
            "different mechanisms" in limitation
            for limitation in merged.limitations
        )
    assert merged_ab.status == merged_ba.status
    assert merged_ab.strength == merged_ba.strength


def test_conflicting_evidence_roles_merge_deterministically():
    a = _diff("recall", "regressed", "da-a")
    b = _diff("recall", "regressed", "da-b")
    supporting = RCAEvidenceReference(
        evidence_id="signal:x1", source_type="signal", source_ref="x1",
        role="supporting", rationale="test rationale",
    )
    counter = RCAEvidenceReference(
        evidence_id="signal:x1", source_type="signal", source_ref="x1",
        role="counter", rationale="test rationale",
    )
    hyp_supporting = _hyp(a, "rule.a", "mech", [supporting], status="supported")
    hyp_counter = _hyp(b, "rule.b", "mech", [counter], status="candidate",
                       strength="weak")
    merged_ab = _merge(hyp_supporting, hyp_counter)[0]
    merged_ba = _merge(hyp_counter, hyp_supporting)[0]
    for merged in (merged_ab, merged_ba):
        assert merged.status == "supported"
        assert [reference.role for reference in merged.evidence] == ["supporting"]
        assert any(
            "conflicting roles" in limitation
            for limitation in merged.limitations
        )
        RCAHypothesis.model_validate(merged.model_dump())
    assert merged_ab.status == merged_ba.status
    assert merged_ab.strength == merged_ba.strength
    assert {r.evidence_id for r in merged_ab.evidence} == {
        r.evidence_id for r in merged_ba.evidence
    }


def test_same_dataset_differentials_not_counted_independent():
    report = _run([
        _diff("recall", "regressed", "da-1"),
        _diff("precision", "regressed", "da-2"),
    ])
    supported = [h for h in report.hypotheses if h.status == "supported"]
    assert len(supported) == 2
    assert report.conclusion_type == "probable_cause"
    assert any(
        "underlying source" in limitation
        for limitation in report.limitations
    )


def test_same_dataset_differential_analyses_strength_capped():
    report = _run([
        _diff("recall", "regressed", "da-1"),
        _diff("recall", "regressed", "da-2"),
    ])
    assert len(report.hypotheses) == 1
    hypothesis = report.hypotheses[0]
    assert hypothesis.status == "supported"
    assert hypothesis.strength == "moderate"
    assert {reference.evidence_id for reference in hypothesis.evidence} == {
        "differential_analysis:da-1:recall",
        "differential_analysis:da-2:recall",
    }
    assert any(
        "counted as one independent source" in limitation
        for limitation in hypothesis.limitations
    )


def test_distinct_dataset_differentials_still_multiple_contributing():
    report = _run([
        _diff("recall", "regressed", "da-1", baseline="base-1"),
        _diff("precision", "regressed", "da-2", baseline="base-2"),
    ])
    supported = [h for h in report.hypotheses if h.status == "supported"]
    assert len(supported) == 2
    assert report.conclusion_type == "multiple_contributing_factors"

