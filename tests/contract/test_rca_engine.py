from datetime import datetime, timezone

from ai_eval_platform.root_cause import RCAAnalyzer, RCAArtifact, InvestigationScope

NOW = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)


def scope():
    return InvestigationScope(domain="qa", subject_type="service", subject_id="svc-1", agent_id="agent-1")


def run(artifacts):
    return RCAAnalyzer().analyze(
        investigation_id="investigation-1", trigger_type="regression", scope=scope(),
        artifacts=artifacts,
    )


def test_empty_evidence_is_descriptive_and_not_causal():
    report = run([])
    assert report.conclusion_type == "insufficient_evidence"
    assert report.conclusion_level == "descriptive"
    assert not report.hypotheses


def test_non_detector_signal_is_accepted_as_context_not_root_cause():
    signal = RCAArtifact(
        artifact_id="signal:s1", source_type="signal", source_ref="s1", observed_at=NOW,
        agent_id="agent-1", attributes={"origin": "base_observation", "signal_type": "event_observation", "payload": {}},
    )
    report = run([signal])
    assert len(report.hypotheses) == 1
    assert report.hypotheses[0].status == "candidate"
    assert report.conclusion_level == "descriptive"


def test_statistically_supported_regression_is_association_not_causation():
    comparison = RCAArtifact(
        artifact_id="diff:d1", source_type="differential_analysis", source_ref="d1",
        attributes={
            "metric_name": "recall", "status": "regressed", "statistically_significant": True,
            "decision_p_value": 0.01, "baseline_sample_size": 40, "candidate_sample_size": 40,
            "median_difference": -0.08, "practical_significance_met": True,
        },
    )
    report = run([comparison])
    assert report.hypotheses[0].status == "supported"
    assert report.conclusion_type == "probable_cause"
    assert report.conclusion_level == "associational"


def test_small_sample_does_not_become_supported_from_p_value_alone():
    comparison = RCAArtifact(
        artifact_id="diff:d2", source_type="differential_analysis", source_ref="d2",
        attributes={"metric_name": "recall", "status": "regressed", "statistically_significant": True,
                    "baseline_sample_size": 2, "candidate_sample_size": 3},
    )
    report = run([comparison])
    assert report.hypotheses[0].status == "candidate"
    assert report.conclusion_level == "descriptive"


def test_ci_gate_failure_does_not_establish_cause():
    gate = RCAArtifact(
        artifact_id="ci:gate-1", source_type="ci_cd_result", source_ref="gate-1",
        attributes={"decision": "failed", "policy_version": "p1"},
    )
    report = run([gate])
    assert report.hypotheses[0].status == "candidate"
    assert report.conclusion_level == "descriptive"


def test_relationship_is_context_and_not_causal_proof():
    relation = RCAArtifact(
        artifact_id="relationship:r1", source_type="relationship", source_ref="r1",
        attributes={"relationship_id": "r1", "relationship_type": "temporal", "status": "candidate", "evidence_ids": ["e1", "e2"]},
    )
    report = run([relation])
    assert len(report.hypotheses) == 1
    assert report.hypotheses[0].status == "candidate"
    assert report.hypotheses[0].evidence[0].role == "context"
    assert report.conclusion_level == "descriptive"
    assert any("unavailable" in item for item in report.limitations)


def test_duplicate_artifact_ids_rejected():
    artifact = RCAArtifact(artifact_id="x", source_type="metric", source_ref="x")
    try:
        run([artifact, artifact])
    except ValueError as exc:
        assert "unique" in str(exc)
    else:
        raise AssertionError("duplicate artifact IDs should fail")


def test_persisted_differential_analysis_adapter_preserves_metric_statistics():
    from types import SimpleNamespace
    from ai_eval_platform.root_cause import artifacts_from_differential_analysis

    persisted = SimpleNamespace(
        differential_analysis_id="diff-1", analysis_id="analysis-1",
        baseline_evaluation_run_id="base-1", candidate_evaluation_run_id="cand-1",
        metric_deltas={"recall": {"status": "regressed", "median_difference": -0.1}},
        statistical_tests={"recall": {"status": "regressed", "statistically_significant": True,
                                      "baseline_sample_size": 20, "candidate_sample_size": 20,
                                      "decision_p_value": 0.01, "metric_direction": "higher_is_better"}},
    )
    artifacts = artifacts_from_differential_analysis(persisted)
    assert len(artifacts) == 1
    assert artifacts[0].source_ref == "diff-1"
    assert artifacts[0].attributes["metric_name"] == "recall"
    report = run(artifacts)
    assert report.differential_analysis_ids == ("diff-1",)
    # Adapter carries no practical-significance flag, so the conservative
    # gate keeps this candidate (practical significance unestablished).
    assert report.hypotheses[0].status == "candidate"


def test_explicit_scope_conflict_excludes_artifact():
    outside = RCAArtifact(
        artifact_id="signal:outside", source_type="signal", source_ref="outside",
        agent_id="another-agent", attributes={"signal_type": "latency_anomaly", "payload": {"metric": "latency"}},
    )
    report = run([outside])
    assert not report.hypotheses
    assert any("scope conflicts" in item for item in report.limitations)


def test_nearby_observation_is_attached_as_context_not_support():
    signal = RCAArtifact(
        artifact_id="signal:s2", source_type="signal", source_ref="s2", observed_at=NOW,
        agent_id="agent-1", run_id="run-1",
        attributes={"origin": "detector", "signal_type": "latency_anomaly", "payload": {"metric": "latency"}},
    )
    observation = RCAArtifact(
        artifact_id="observation:o1", source_type="observation", source_ref="o1",
        observed_at=NOW, agent_id="agent-1", run_id="run-1",
        attributes={"event_type": "retriever_config_changed"},
    )
    report = run([signal, observation])
    by_id = {ref.evidence_id: ref.role for h in report.hypotheses for ref in h.evidence}
    assert by_id["observation:o1"] == "context"
    assert by_id["signal:s2"] == "supporting"
    # Observation-only context also yields its own context hypothesis; the
    # signal hypothesis carries both symptom (supporting) and context.
    signal_hypos = [h for h in report.hypotheses if "signal:s2" in {r.evidence_id for r in h.evidence}]
    assert len(signal_hypos) == 1
    assert signal_hypos[0].evidence[0].role == "context"
    assert signal_hypos[0].evidence[1].role == "supporting"
