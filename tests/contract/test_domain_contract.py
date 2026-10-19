from datetime import datetime, timezone
import pytest
from pydantic import ValidationError
from ai_eval_platform.root_cause.domain import (
    InvestigationScope, RCAEvidenceReference, HypothesisValidation,
    RCAHypothesis, RCAReport, compute_evidence_fingerprint,
)

NOW = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)


def scope():
    return InvestigationScope(domain="qa", subject_type="service", subject_id="svc-1")


def evidence(eid="e1", role="supporting", source_type="signal", **kwargs):
    base = dict(evidence_id=eid, source_type=source_type, source_ref=eid, role=role, rationale="Observed in persisted telemetry")
    base.update(kwargs)
    return RCAEvidenceReference(**base)


def hypothesis(status="supported", ev=None, validation=None):
    return RCAHypothesis(
        hypothesis_id="h1", title="Latency regression", statement="New path increased latency",
        mechanism="Additional retrieval work increased processing time", status=status,
        evidence=tuple(ev if ev is not None else [evidence()]), validation=validation,
    )


def report(**overrides):
    data = dict(
        investigation_id="i1", trigger_type="regression", status="completed", scope=scope(),
        hypotheses=(hypothesis(),), conclusion_type="probable_cause", conclusion_level="associational",
        summary="Latency rose after a change", algorithm_name="rca", algorithm_version="1.0",
        evidence_fingerprint="abc123", created_at=NOW,
    )
    data.update(overrides)
    return RCAReport(**data)


def test_scope_does_not_require_agent_id():
    assert scope().agent_id is None


def test_rejects_naive_datetime():
    with pytest.raises(ValidationError):
        InvestigationScope(domain="qa", subject_type="service", subject_id="svc-1", observed_after=datetime(2026, 1, 1))


def test_evidence_source_type_requires_matching_reference():
    with pytest.raises(ValidationError):
        evidence(source_type="differential_analysis")
    with pytest.raises(ValidationError):
        evidence(source_type="relationship", relationship_id=None)


def test_supported_hypothesis_requires_supporting_evidence():
    with pytest.raises(ValidationError):
        hypothesis(ev=[evidence(role="context")])


def test_confirmed_validation_must_be_reflected_and_traceable():
    validation = HypothesisValidation(method="human_review", outcome="confirmed", performed_at=NOW, artifact_id="e1", evidence=(evidence(),))
    with pytest.raises(ValidationError):
        hypothesis(status="supported", validation=validation)
    with pytest.raises(ValidationError):
        hypothesis(status="validated", ev=[evidence()], validation=HypothesisValidation(
            method="human_review", outcome="confirmed", performed_at=NOW, artifact_id="e1", evidence=(evidence("different"),)
        ))


def test_causal_conclusion_requires_causal_validation_method():
    validation = HypothesisValidation(method="human_review", outcome="confirmed", performed_at=NOW, artifact_id="e1", evidence=(evidence(),))
    h = hypothesis(status="validated", validation=validation)
    with pytest.raises(ValidationError):
        report(hypotheses=(h,), conclusion_level="validated_causal")


def test_causal_conclusion_accepts_confirmed_rollback_test():
    validation = HypothesisValidation(method="rollback_test", outcome="confirmed", performed_at=NOW, artifact_id="e1", evidence=(evidence(),))
    h = hypothesis(status="validated", validation=validation)
    assert report(hypotheses=(h,), conclusion_level="validated_causal").conclusion_level == "validated_causal"


def test_no_supported_cause_cannot_coexist_with_supported_hypothesis():
    with pytest.raises(ValidationError):
        report(conclusion_type="no_supported_cause")


def test_fingerprint_is_deterministic_and_order_independent():
    a, b = evidence("a"), evidence("b", role="counter")
    assert compute_evidence_fingerprint((a, b)) == compute_evidence_fingerprint((b, a))
    assert compute_evidence_fingerprint((a,)) != compute_evidence_fingerprint((b,))
