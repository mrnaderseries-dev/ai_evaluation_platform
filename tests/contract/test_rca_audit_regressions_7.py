"""RCA_7 regression tests: validation conflict reachability and mechanism determinism."""

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
        "practical_significance_met": True, "effect_size": 0.6,
        "confidence_interval": [0.2, 1.0],
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
         statement=CLAIM, limitations=()):
    base = RCAAnalyzer._make_hypothesis(
        artifact=artifact, rule_id=rule_id, title=title, statement=statement,
        mechanism=mechanism, references=tuple(refs), status="candidate",
        strength="weak", limitations=limitations,
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


def _assert_same_merged_output(merged_ab, merged_ba):
    assert merged_ab[0].hypothesis_id == merged_ba[0].hypothesis_id
    assert merged_ab[0].mechanism == merged_ba[0].mechanism
    assert merged_ab[0].status == merged_ba[0].status
    assert merged_ab[0].strength == merged_ba[0].strength
    assert {r.evidence_id for r in merged_ab[0].evidence} == {
        r.evidence_id for r in merged_ba[0].evidence}
    assert merged_ab[0].limitations == merged_ba[0].limitations
    assert (merged_ab[0].validation is None) == (
        merged_ba[0].validation is None)
    if merged_ab[0].validation is not None:
        assert merged_ab[0].validation.outcome == merged_ba[0].validation.outcome
    assert set(merged_ab[0].rule_ids) == set(merged_ba[0].rule_ids)
    RCAHypothesis.model_validate(merged_ab[0].model_dump())
    RCAHypothesis.model_validate(merged_ba[0].model_dump())


# ---------------------------------------------------------------------------
# 1. Mechanism conflict determinism
# ---------------------------------------------------------------------------

def test_merge_mechanism_deterministic_regardless_of_input_order():
    a = _diff("recall", "regressed", "da-a")
    b = _diff("recall", "regressed", "da-b")
    hyp_a = _hyp(a, "rule.a", "mechanism B", [_ref(a)])
    hyp_b = _hyp(b, "rule.b", "mechanism A", [_ref(b)])
    merged_ab = _merge(hyp_a, hyp_b)
    merged_ba = _merge(hyp_b, hyp_a)
    _assert_same_merged_output(merged_ab, merged_ba)
    assert merged_ab[0].mechanism == "mechanism A"
    assert merged_ba[0].mechanism == "mechanism A"


def test_merge_mechanism_conflict_discloses_limitation_deterministically():
    a = _diff("recall", "regressed", "da-a")
    b = _diff("recall", "regressed", "da-b")
    hyp_a = _hyp(a, "rule.a", "mechanism B", [_ref(a)])
    hyp_b = _hyp(b, "rule.b", "mechanism A", [_ref(b)])
    merged_ab = _merge(hyp_a, hyp_b)[0]
    merged_ba = _merge(hyp_b, hyp_a)[0]
    for merged in (merged_ab, merged_ba):
        assert merged.mechanism == "mechanism A"
        assert any(
            "different mechanisms" in limitation
            for limitation in merged.limitations
        )
    _assert_same_merged_output(
        RCAAnalyzer()._merge_hypotheses([hyp_a, hyp_b]),
        RCAAnalyzer()._merge_hypotheses([hyp_b, hyp_a]),
    )


# ---------------------------------------------------------------------------
# 2. Validation edge case: manually constructed boundary behavior
# ---------------------------------------------------------------------------

def test_validated_with_counter_evidence_and_no_validation_record_stays_validated():
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
    )
    merged = _merge(validated, rejected)[0]
    assert merged.status == "validated"
    assert merged.validation is not None
    assert merged.validation.outcome == "confirmed"
    assert any(
        reference.role == "counter"
        for reference in merged.evidence
    )
    assert not any(
        "conflicting validation" in limitation
        or "validation outcomes" in limitation
        for limitation in merged.limitations
    )
    RCAHypothesis.model_validate(merged.model_dump())


# ---------------------------------------------------------------------------
# 3. Validation edge case is not reachable via analyze()
# ---------------------------------------------------------------------------

def test_analyze_never_emits_validated_hypothesis():
    report = _run([
        _diff("recall", "regressed", "da-a"),
        _diff("precision", "regressed", "da-b"),
    ])
    for hypothesis in report.hypotheses:
        assert hypothesis.status != "validated"
        assert hypothesis.validation is None

