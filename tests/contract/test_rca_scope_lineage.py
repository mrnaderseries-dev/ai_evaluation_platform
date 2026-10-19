from __future__ import annotations

from ai_eval_platform.root_cause import (
    InvestigationScope,
    RCAAnalyzer,
    RCAArtifact,
    artifact_from_cicd,
)


def _scope(**overrides):
    base = dict(domain="qa", subject_type="service", subject_id="svc-1")
    base.update(overrides)
    return InvestigationScope(**base)


def _run(artifacts, scope=None, **kwargs):
    return RCAAnalyzer().analyze(
        investigation_id="investigation-scope-1",
        trigger_type="regression",
        scope=scope or _scope(),
        artifacts=artifacts,
        **kwargs,
    )


def test_scope_callers_may_omit_optional_fields():
    scope = _scope()
    assert scope.pipeline_run_id is None
    assert scope.environment is None
    artifact = RCAArtifact(
        artifact_id="signal:s1",
        source_type="signal",
        source_ref="s1",
        attributes={"signal_type": "latency_anomaly", "payload": {}},
    )
    assert artifact.pipeline_run_id is None
    assert artifact.environment is None
    report = _run([artifact], scope=scope)
    assert len(report.hypotheses) == 1


def test_scope_matching_pipeline_run_id_kept():
    scope = _scope(pipeline_run_id="pipe-run-1")
    artifact = RCAArtifact(
        artifact_id="signal:s1",
        source_type="signal",
        source_ref="s1",
        pipeline_run_id="pipe-run-1",
        attributes={"signal_type": "latency_anomaly", "payload": {}},
    )
    report = _run([artifact], scope=scope)
    assert len(report.hypotheses) == 1


def test_scope_conflicting_pipeline_run_id_excluded():
    scope = _scope(pipeline_run_id="pipe-run-1")
    artifact = RCAArtifact(
        artifact_id="signal:s1",
        source_type="signal",
        source_ref="s1",
        pipeline_run_id="pipe-run-2",
        attributes={"signal_type": "latency_anomaly", "payload": {}},
    )
    report = _run([artifact], scope=scope)
    assert report.hypotheses == ()
    assert any("scope conflicts" in lim for lim in report.limitations)


def test_scope_missing_pipeline_run_id_disclosed():
    scope = _scope(pipeline_run_id="pipe-run-1")
    artifact = RCAArtifact(
        artifact_id="signal:s1",
        source_type="signal",
        source_ref="s1",
        attributes={"signal_type": "latency_anomaly", "payload": {}},
    )
    report = _run([artifact], scope=scope)
    assert len(report.hypotheses) == 1
    assert any("pipeline_run_id" in lim for lim in report.limitations)


def test_scope_matching_environment_kept():
    scope = _scope(environment="prod")
    artifact = RCAArtifact(
        artifact_id="signal:s1",
        source_type="signal",
        source_ref="s1",
        environment="prod",
        attributes={"signal_type": "latency_anomaly", "payload": {}},
    )
    report = _run([artifact], scope=scope)
    assert len(report.hypotheses) == 1


def test_scope_conflicting_environment_excluded():
    scope = _scope(environment="prod")
    artifact = RCAArtifact(
        artifact_id="signal:s1",
        source_type="signal",
        source_ref="s1",
        environment="staging",
        attributes={"signal_type": "latency_anomaly", "payload": {}},
    )
    report = _run([artifact], scope=scope)
    assert report.hypotheses == ()
    assert any("scope conflicts" in lim for lim in report.limitations)


def test_scope_missing_environment_disclosed():
    scope = _scope(environment="prod")
    artifact = RCAArtifact(
        artifact_id="signal:s1",
        source_type="signal",
        source_ref="s1",
        attributes={"signal_type": "latency_anomaly", "payload": {}},
    )
    report = _run([artifact], scope=scope)
    assert len(report.hypotheses) == 1
    assert any("environment" in lim for lim in report.limitations)


def test_scope_pipeline_run_id_from_attributes_checked():
    scope = _scope(pipeline_run_id="pipe-run-1")
    artifact = RCAArtifact(
        artifact_id="signal:s1",
        source_type="signal",
        source_ref="s1",
        attributes={
            "signal_type": "latency_anomaly",
            "payload": {},
            "pipeline_run_id": "pipe-run-2",
        },
    )
    report = _run([artifact], scope=scope)
    assert report.hypotheses == ()


def test_scope_environment_from_attributes_checked():
    scope = _scope(environment="prod")
    artifact = RCAArtifact(
        artifact_id="signal:s1",
        source_type="signal",
        source_ref="s1",
        attributes={
            "signal_type": "latency_anomaly",
            "payload": {},
            "environment": "staging",
        },
    )
    report = _run([artifact], scope=scope)
    assert report.hypotheses == ()


def test_scope_conflicting_supporting_evidence_excluded():
    scope = _scope(pipeline_run_id="pipe-run-1")
    supporting = RCAArtifact(
        artifact_id="differential_analysis:d1:recall",
        source_type="differential_analysis",
        source_ref="d1",
        pipeline_run_id="pipe-run-2",
        attributes={
            "metric_name": "recall",
            "status": "regressed",
            "statistically_significant": True,
            "decision_p_value": 0.01,
            "baseline_sample_size": 40,
            "candidate_sample_size": 40,
            "practical_significance_met": True,
        },
    )
    counter = RCAArtifact(
        artifact_id="differential_analysis:d2:recall",
        source_type="differential_analysis",
        source_ref="d2",
        evaluation_run_id="cand-1",
        attributes={
            "metric_name": "recall",
            "status": "improved",
            "statistically_significant": True,
            "decision_p_value": 0.01,
            "baseline_sample_size": 40,
            "candidate_sample_size": 40,
            "practical_significance_met": True,
        },
    )
    report = _run([supporting, counter], scope=scope)
    covered = {
        ref.evidence_id for h in report.hypotheses for ref in h.evidence
    }
    assert "differential_analysis:d1:recall" not in covered


def test_scope_conflicting_counter_evidence_excluded():
    scope = _scope(environment="prod")
    regressed = RCAArtifact(
        artifact_id="differential_analysis:d1:recall",
        source_type="differential_analysis",
        source_ref="d1",
        evaluation_run_id="cand-1",
        attributes={
            "metric_name": "recall",
            "status": "regressed",
            "statistically_significant": True,
            "decision_p_value": 0.01,
            "baseline_sample_size": 40,
            "candidate_sample_size": 40,
            "practical_significance_met": True,
        },
    )
    foreign_counter = RCAArtifact(
        artifact_id="differential_analysis:d2:recall",
        source_type="differential_analysis",
        source_ref="d2",
        environment="staging",
        attributes={
            "metric_name": "recall",
            "status": "improved",
            "statistically_significant": True,
            "decision_p_value": 0.01,
            "baseline_sample_size": 40,
            "candidate_sample_size": 40,
            "practical_significance_met": True,
        },
    )
    report = _run([regressed, foreign_counter], scope=scope)
    covered = {
        ref.evidence_id for h in report.hypotheses for ref in h.evidence
    }
    assert "differential_analysis:d2:recall" not in covered


def test_cicd_pipeline_run_id_preserved_as_execution_lineage():
    artifact = artifact_from_cicd(
        {
            "check_id": "gate-1",
            "pipeline_id": "pipe-1",
            "pipeline_run_id": "pipe-run-7",
            "decision": "failed",
            "environment": "prod",
        }
    )
    assert artifact.pipeline_run_id == "pipe-run-7"
    assert artifact.attributes["pipeline_id"] == "pipe-1"
    assert artifact.environment == "prod"


def test_cicd_pipeline_id_never_mapped_to_pipeline_run_id():
    artifact = artifact_from_cicd(
        {"check_id": "gate-1", "pipeline_id": "pipe-1", "decision": "failed"}
    )
    assert artifact.pipeline_run_id is None
    scope = _scope(pipeline_run_id="pipe-run-1")
    report = _run([artifact], scope=scope)
    assert len(report.hypotheses) == 1
    assert any("pipeline_run_id" in lim for lim in report.limitations)


def test_scope_serialization_round_trip():
    scope = _scope(pipeline_run_id="pipe-run-1", environment="prod")
    payload = scope.model_dump(mode="json")
    assert payload["pipeline_run_id"] == "pipe-run-1"
    assert payload["environment"] == "prod"
    restored = InvestigationScope.model_validate(payload)
    assert restored == scope
    artifact = RCAArtifact(
        artifact_id="signal:s1",
        source_type="signal",
        source_ref="s1",
        pipeline_run_id="pipe-run-1",
        environment="prod",
        attributes={"signal_type": "latency_anomaly", "payload": {}},
    )
    artifact_payload = artifact.model_dump(mode="json")
    restored_artifact = RCAArtifact.model_validate(artifact_payload)
    assert restored_artifact == artifact