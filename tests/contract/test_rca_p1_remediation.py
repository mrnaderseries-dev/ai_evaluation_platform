"""P1 remediation regression tests: CI/CD boundary, scope integrity,
source-level dedup, statistical-gate alignment, counter-evidence semantics,
and the full source-type handling matrix.
"""
from datetime import datetime, timedelta, timezone

from ai_eval_platform.root_cause import (
    RCAAnalyzer,
    RCAArtifact,
    InvestigationScope,
    artifact_from_cicd,
)

NOW = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)


def _scope(**overrides):
    base = dict(domain="qa", subject_type="service", subject_id="svc-1", agent_id="agent-1")
    base.update(overrides)
    return InvestigationScope(**base)


def _run(artifacts, scope=None, **kwargs):
    return RCAAnalyzer().analyze(
        investigation_id="investigation-1",
        trigger_type="regression",
        scope=scope or _scope(),
        artifacts=artifacts,
        **kwargs,
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


def test_matrix_signal_generates_candidate_symptom():
    report = _run([RCAArtifact(
        artifact_id="signal:s1", source_type="signal", source_ref="s1",
        observed_at=NOW, agent_id="agent-1",
        attributes={"origin": "detector", "signal_type": "latency_anomaly", "payload": {}},
    )])
    assert len(report.hypotheses) == 1
    assert report.hypotheses[0].status == "candidate"
    assert report.hypotheses[0].evidence[0].role == "supporting"


def test_matrix_observation_generates_context_hypothesis():
    report = _run([RCAArtifact(
        artifact_id="observation:o1", source_type="observation", source_ref="o1",
        observed_at=NOW, agent_id="agent-1",
        attributes={"event_type": "config_changed"},
    )])
    assert len(report.hypotheses) == 1
    assert report.hypotheses[0].status == "candidate"
    assert report.hypotheses[0].evidence[0].role == "context"


def test_matrix_statistical_analysis_is_context_with_limitation():
    report = _run([RCAArtifact(
        artifact_id="stat:s1", source_type="statistical_analysis", source_ref="s1",
        attributes={"test_name": "mann_whitney_u"},
    )])
    assert len(report.hypotheses) == 1
    assert report.hypotheses[0].evidence[0].role == "context"
    assert any("raw primitive" in lim for lim in report.limitations)


def test_matrix_analysis_result_is_context_with_limitation():
    report = _run([RCAArtifact(
        artifact_id="analysis:a1", source_type="analysis_result", source_ref="a1",
        attributes={"analysis_type": "signal_summary"},
    )])
    assert len(report.hypotheses) == 1
    assert report.hypotheses[0].evidence[0].role == "context"
    assert any("descriptive" in lim for lim in report.limitations)


def test_matrix_validation_artifact_disclosed_not_hypothesis():
    report = _run([RCAArtifact(
        artifact_id="signal:s1", source_type="signal", source_ref="s1",
        attributes={"signal_type": "latency_anomaly", "payload": {}},
    ), RCAArtifact(
        artifact_id="val:v1", source_type="validation_artifact", source_ref="v1",
        attributes={},
    )])
    assert any("Validation artifact v1" in lim for lim in report.limitations)


def test_matrix_mixed_set_no_silent_loss():
    artifacts = [
        RCAArtifact(artifact_id="signal:s1", source_type="signal", source_ref="s1",
                    attributes={"signal_type": "latency_anomaly", "payload": {}}),
        RCAArtifact(artifact_id="observation:o1", source_type="observation", source_ref="o1",
                    attributes={"event_type": "x"}),
        RCAArtifact(artifact_id="trace:t1", source_type="trace", source_ref="t1", attributes={}),
        RCAArtifact(artifact_id="metric:m1", source_type="metric", source_ref="m1", attributes={}),
        RCAArtifact(artifact_id="log:l1", source_type="log", source_ref="l1", attributes={}),
        RCAArtifact(artifact_id="stat:s1", source_type="statistical_analysis", source_ref="s1", attributes={}),
        RCAArtifact(artifact_id="analysis:a1", source_type="analysis_result", source_ref="a1", attributes={}),
        RCAArtifact(artifact_id="val:v1", source_type="validation_artifact", source_ref="v1", attributes={}),
        _diff("recall", "regressed"),
        RCAArtifact(artifact_id="relationship:r1", source_type="relationship", source_ref="r1",
                    attributes={"relationship_id": "r1", "relationship_type": "temporal",
                                "status": "candidate", "evidence_ids": ["signal:s1"]}),
        RCAArtifact(artifact_id="ci_cd_result:gate-1", source_type="ci_cd_result", source_ref="gate-1",
                    attributes={"decision": "failed"}),
    ]
    report = _run(artifacts)
    covered = {ref.evidence_id for h in report.hypotheses for ref in h.evidence}
    for artifact in artifacts:
        if artifact.source_type == "validation_artifact":
            assert any("Validation artifact v1" in lim for lim in report.limitations)
        else:
            assert artifact.artifact_id in covered, f"lost: {artifact.artifact_id}"


def test_cicd_failure_with_matching_regression():
    report = _run([
        _diff("recall", "regressed"),
        RCAArtifact(artifact_id="ci_cd_result:gate-1", source_type="ci_cd_result",
                    source_ref="gate-1",
                    attributes={"decision": "failed", "failed_tests": ["test_recall"],
                                "commit_sha": "abc", "system_id": "svc-1"}),
    ])
    gate_hypos = [h for h in report.hypotheses if h.rule_ids[0].startswith("ci_cd.result")]
    assert len(gate_hypos) == 1
    assert gate_hypos[0].status == "candidate"
    assert gate_hypos[0].evidence[0].role == "supporting"
    assert any("not automatic" in lim for lim in gate_hypos[0].limitations)


def test_cicd_failure_without_matching_regression():
    report = _run([
        RCAArtifact(artifact_id="ci_cd_result:gate-1", source_type="ci_cd_result",
                    source_ref="gate-1", attributes={"decision": "failed"}),
    ])
    assert len(report.hypotheses) == 1
    assert report.hypotheses[0].status == "candidate"
    assert report.conclusion_level == "descriptive"


def test_cicd_pass_with_unrelated_regression_is_counter():
    report = _run([
        RCAArtifact(artifact_id="ci_cd_result:gate-1", source_type="ci_cd_result",
                    source_ref="gate-1", attributes={"decision": "passed"}),
    ])
    assert report.hypotheses[0].evidence[0].role == "counter"
    assert any("do not prove" in lim for lim in report.hypotheses[0].limitations)


def test_cicd_missing_metadata_disclosed():
    report = _run([
        RCAArtifact(artifact_id="ci_cd_result:gate-1", source_type="ci_cd_result",
                    source_ref="gate-1", attributes={"decision": "failed"}),
    ])
    assert any("missing" in lim.lower() for lim in report.hypotheses[0].limitations)


def test_cicd_scope_mismatch_excluded():
    scope = _scope(commit_sha="aaa")
    gate = RCAArtifact(
        artifact_id="ci_cd_result:gate-1", source_type="ci_cd_result", source_ref="gate-1",
        attributes={"decision": "failed", "commit_sha": "bbb"},
    )
    report = _run([gate], scope=scope)
    assert not report.hypotheses
    assert any("scope conflicts" in lim for lim in report.limitations)


def test_cicd_rollback_reference_is_not_validation():
    report = _run([
        RCAArtifact(artifact_id="ci_cd_result:gate-1", source_type="ci_cd_result",
                    source_ref="gate-1",
                    attributes={"decision": "failed", "rollback_outcome": "improved"}),
    ])
    assert report.conclusion_level == "descriptive"
    assert any("not validation" in lim for lim in report.hypotheses[0].limitations)


def test_cicd_adapter_preserves_lineage_without_invention():
    artifact = artifact_from_cicd({
        "check_id": "gate-9", "decision": "failed",
        "commit_sha": "deadbeef", "environment": "prod",
        "failed_tests": ["t1", "t2"], "system_id": "svc-1",
        "observed_at": NOW,
    })
    assert artifact.source_type == "ci_cd_result"
    assert artifact.source_ref == "gate-9"
    assert artifact.observed_at == NOW
    assert artifact.system_id == "svc-1"
    assert artifact.attributes["commit_sha"] == "deadbeef"
    assert artifact.attributes["failed_tests"] == ["t1", "t2"]
    bare = artifact_from_cicd({"decision": "passed"})
    assert bare.source_ref == "unknown"
    assert "commit_sha" not in bare.attributes


def test_scope_matching_run_different_system_excluded():
    scope = _scope(system_id="sys-a")
    artifact = RCAArtifact(
        artifact_id="signal:s1", source_type="signal", source_ref="s1",
        system_id="sys-b", attributes={"signal_type": "latency_anomaly", "payload": {}},
    )
    assert not _run([artifact], scope=scope).hypotheses


def test_scope_matching_system_different_version_excluded():
    scope = _scope(system_id="sys-a", system_version="v1")
    artifact = RCAArtifact(
        artifact_id="signal:s1", source_type="signal", source_ref="s1",
        system_id="sys-a", system_version="v2",
        attributes={"signal_type": "latency_anomaly", "payload": {}},
    )
    assert not _run([artifact], scope=scope).hypotheses


def test_scope_missing_version_is_uncertainty_not_exclusion():
    scope = _scope(system_id="sys-a", system_version="v1")
    artifact = RCAArtifact(
        artifact_id="signal:s1", source_type="signal", source_ref="s1",
        system_id="sys-a",
        attributes={"signal_type": "latency_anomaly", "payload": {}},
    )
    report = _run([artifact], scope=scope)
    assert len(report.hypotheses) == 1
    assert any("system_version" in lim for lim in report.limitations)


def test_scope_stale_differential_excluded_by_run_lineage():
    scope = _scope(evaluation_run_id="cand-1")
    stale = _diff("recall", "regressed", source_ref="stale",
                  extra={"baseline_evaluation_run_id": "base-0",
                         "candidate_evaluation_run_id": "cand-0"})
    assert not _run([stale], scope=scope).hypotheses


def test_scope_context_window_inside_outside():
    signal = RCAArtifact(
        artifact_id="signal:s1", source_type="signal", source_ref="s1",
        observed_at=NOW, agent_id="agent-1", run_id="run-1",
        attributes={"signal_type": "latency_anomaly", "payload": {}},
    )
    inside = RCAArtifact(
        artifact_id="log:near", source_type="log", source_ref="near",
        observed_at=NOW + timedelta(seconds=60), agent_id="agent-1", run_id="run-1",
        attributes={},
    )
    outside = RCAArtifact(
        artifact_id="log:far", source_type="log", source_ref="far",
        observed_at=NOW + timedelta(seconds=3600), agent_id="agent-1", run_id="run-1",
        attributes={},
    )
    report = _run([signal, inside, outside])
    signal_hypo = next(h for h in report.hypotheses if "signal:s1" in {r.evidence_id for r in h.evidence})
    ids = {r.evidence_id for r in signal_hypo.evidence}
    assert "log:near" in ids
    assert "log:far" not in ids


def test_scope_configurable_window():
    analyzer = RCAAnalyzer()
    analyzer.context_window_seconds = 7200.0
    signal = RCAArtifact(
        artifact_id="signal:s1", source_type="signal", source_ref="s1",
        observed_at=NOW, agent_id="agent-1", run_id="run-1",
        attributes={"signal_type": "latency_anomaly", "payload": {}},
    )
    far = RCAArtifact(
        artifact_id="log:far", source_type="log", source_ref="far",
        observed_at=NOW + timedelta(seconds=3600), agent_id="agent-1", run_id="run-1",
        attributes={},
    )
    report = analyzer.analyze(
        investigation_id="i", trigger_type="regression", scope=_scope(), artifacts=[signal, far],
    )
    signal_hypo = next(h for h in report.hypotheses if "signal:s1" in {r.evidence_id for r in h.evidence})
    assert "log:far" in {r.evidence_id for r in signal_hypo.evidence}


def test_scope_missing_timestamps_disclosed():
    signal = RCAArtifact(
        artifact_id="signal:s1", source_type="signal", source_ref="s1",
        agent_id="agent-1", run_id="run-1",
        attributes={"signal_type": "latency_anomaly", "payload": {}},
    )
    log = RCAArtifact(
        artifact_id="log:l1", source_type="log", source_ref="l1",
        agent_id="agent-1", run_id="run-1", attributes={},
    )
    report = _run([signal, log])
    signal_hypo = next(h for h in report.hypotheses if "signal:s1" in {r.evidence_id for r in h.evidence})
    assert "log:l1" in {r.evidence_id for r in signal_hypo.evidence}
    assert any("unverified" in r.rationale for r in signal_hypo.evidence if r.evidence_id == "log:l1")


def test_amplification_independent_analyses_both_count():
    first = _diff("recall", "regressed", source_ref="diff-a")
    second = _diff("recall", "regressed", source_ref="diff-b")
    report = _run([first, second])
    supporting_ids = {
        r.evidence_id for h in report.hypotheses for r in h.evidence if r.role == "supporting"
    }
    assert "differential_analysis:diff-a:recall" in supporting_ids
    assert "differential_analysis:diff-b:recall" in supporting_ids


def test_amplification_order_permutation_stable():
    first = _diff("recall", "regressed", source_ref="diff-a")
    second = _diff("precision", "regressed", source_ref="diff-b")
    forward = _run([first, second])
    backward = _run([second, first])
    assert [h.hypothesis_id for h in forward.hypotheses] == [h.hypothesis_id for h in backward.hypotheses]
    assert forward.evidence_fingerprint == backward.evidence_fingerprint
    assert forward.conclusion_type == backward.conclusion_type


def test_stats_raw_significant_but_flag_false_stays_candidate():
    report = _run([_diff("recall", "regressed", significant=False, p=0.01)])
    assert report.hypotheses[0].status == "candidate"


def test_stats_significant_with_effect_and_ci_supported():
    report = _run([_diff("recall", "regressed", extra={
        "effect_size": 0.6,
        "confidence_interval": [0.2, 1.0],
        "median_difference": -0.08,
    })])
    assert report.hypotheses[0].status == "supported"
    assert report.hypotheses[0].strength == "strong"


def test_stats_significant_but_negligible_stays_candidate():
    report = _run([_diff("recall", "regressed", practical=False)])
    assert report.hypotheses[0].status == "candidate"


def test_stats_small_groups_stay_candidate():
    report = _run([_diff("recall", "regressed", n_base=2, n_cand=3)])
    assert report.hypotheses[0].status == "candidate"
    assert any("minimum" in lim for lim in report.hypotheses[0].limitations)


def test_stats_nonfinite_p_with_flag_disclosed():
    report = _run([_diff("recall", "regressed", p=float("inf"))])
    assert report.hypotheses[0].status == "candidate"
    assert any("non-finite" in lim or "not significant" in lim for lim in report.limitations)


def test_stats_improvement_is_counter_not_support():
    report = _run([_diff("recall", "improved")])
    assert report.hypotheses[0].evidence[0].role == "counter"
    assert report.hypotheses[0].status == "candidate"


def test_stats_incompatible_metric_direction_flagged():
    report = _run([_diff("recall", "regressed", extra={"metric_direction": "sideways"})])
    assert report.hypotheses[0].status == "candidate"
    assert any("metric direction" in lim for lim in report.hypotheses[0].limitations)


def test_counter_supported_plus_counter_visible():
    report = _run([
        _diff("recall", "regressed", source_ref="diff-a"),
        _diff("recall", "improved", source_ref="diff-b"),
    ])
    statuses = sorted(h.status for h in report.hypotheses)
    assert "supported" in statuses or "inconclusive" in statuses
    assert any(r.role == "counter" for h in report.hypotheses for r in h.evidence)


def test_counter_scope_mismatch_not_used_as_counter():
    scope = _scope(agent_id="agent-1")
    supporting = _diff("recall", "regressed", source_ref="diff-a")
    foreign_counter = RCAArtifact(
        artifact_id="differential_analysis:diff-b:recall",
        source_type="differential_analysis", source_ref="diff-b",
        evaluation_run_id="cand-1", agent_id="agent-OTHER",
        attributes={
            "metric_name": "recall", "status": "improved",
            "statistically_significant": True, "decision_p_value": 0.01,
            "baseline_sample_size": 40, "candidate_sample_size": 40,
            "practical_significance_met": True,
            "baseline_evaluation_run_id": "base-1",
            "candidate_evaluation_run_id": "cand-1",
        },
    )
    report = _run([supporting, foreign_counter], scope=scope)
    assert any("scope conflicts" in lim for lim in report.limitations)
    assert report.hypotheses[0].status == "supported"


def test_scope_cross_agent_context_not_attached():
    signal = RCAArtifact(
        artifact_id="signal:s1", source_type="signal", source_ref="s1",
        observed_at=NOW, agent_id="agent-1", run_id="run-1",
        attributes={"signal_type": "latency_anomaly", "payload": {}},
    )
    other = RCAArtifact(
        artifact_id="log:other", source_type="log", source_ref="other",
        observed_at=NOW, agent_id="agent-OTHER", run_id="run-1", attributes={},
    )
    report = _run([signal, other])
    signal_hypo = next(h for h in report.hypotheses if "signal:s1" in {r.evidence_id for r in h.evidence})
    assert "log:other" not in {r.evidence_id for r in signal_hypo.evidence}


def test_matrix_unsupported_source_type_disclosed():
    # The domain Literal forbids unknown source types at construction; this
    # simulates a legacy/corrupt payload that bypassed validation to prove
    # the analyzer discloses the skip instead of dropping it silently.
    rogue = RCAArtifact.model_construct(
        artifact_id="quantum:q1",
        source_type="quantum_leak",
        source_ref="q1",
        attributes={},
        limitations=(),
    )
    report = _run([rogue])
    assert not report.hypotheses
    assert any("unsupported source_type" in lim for lim in report.limitations)


def test_dedup_same_source_ref_artifacts_single_vote():
    first = RCAArtifact(
        artifact_id="signal:s1", source_type="signal", source_ref="s1",
        attributes={"signal_type": "latency_anomaly", "payload": {}},
    )
    duplicate = RCAArtifact(
        artifact_id="signal:s1-copy", source_type="signal", source_ref="s1",
        attributes={"signal_type": "latency_anomaly", "payload": {}},
    )
    report = _run([first, duplicate])
    assert len(report.hypotheses) == 1
    hypo = report.hypotheses[0]
    assert len(hypo.evidence) == 1
    assert hypo.evidence[0].evidence_id == "signal:s1"
    assert any("same underlying source" in lim for lim in hypo.limitations)
    assert any(
        "independent corroboration" in lim for lim in hypo.limitations
    )


def test_dedup_same_relationship_artifacts_single_vote():
    rel1 = RCAArtifact(
        artifact_id="relationship:r1", source_type="relationship",
        source_ref="r1",
        attributes={"relationship_id": "r1", "relationship_type": "temporal",
                    "status": "candidate", "evidence_ids": ["signal:s1"]},
    )
    rel2 = RCAArtifact(
        artifact_id="relationship:r1-copy", source_type="relationship",
        source_ref="r1",
        attributes={"relationship_id": "r1", "relationship_type": "temporal",
                    "status": "candidate", "evidence_ids": ["signal:s1"]},
    )
    report = _run([rel1, rel2])
    rel_hypos = [
        h for h in report.hypotheses if h.rule_ids[0].startswith("relationship.")
    ]
    assert len(rel_hypos) == 1
    assert len(rel_hypos[0].evidence) == 1
    assert any(
        "same underlying source" in lim for lim in rel_hypos[0].limitations
    )


def test_multiple_relationships_do_not_re_cite_anchor_evidence():
    signal = RCAArtifact(
        artifact_id="signal:s1", source_type="signal", source_ref="s1",
        attributes={"signal_type": "latency_anomaly", "payload": {}},
    )
    rel1 = RCAArtifact(
        artifact_id="relationship:r1", source_type="relationship",
        source_ref="r1",
        attributes={"relationship_id": "r1", "relationship_type": "temporal",
                    "status": "candidate", "evidence_ids": ["signal:s1"]},
    )
    rel2 = RCAArtifact(
        artifact_id="relationship:r2", source_type="relationship",
        source_ref="r2",
        attributes={"relationship_id": "r2", "relationship_type": "causal",
                    "status": "candidate", "evidence_ids": ["signal:s1"]},
    )
    report = _run([signal, rel1, rel2])
    citations = [
        ref.evidence_id for h in report.hypotheses for ref in h.evidence
    ]
    # The shared anchor evidence is cited once; the two distinct
    # relationships stay visible as their own references (separate rules
    # for separate relationship types).
    assert citations.count("signal:s1") == 1
    rel_hypos = [
        h for h in report.hypotheses if h.rule_ids[0].startswith("relationship.")
    ]
    assert len(rel_hypos) == 2
    assert {ref.evidence_id for h in rel_hypos for ref in h.evidence} == {
        "relationship:r1",
        "relationship:r2",
    }


def test_amplification_multi_metric_one_report_each_counted():
    report = _run([
        _diff("recall", "regressed", source_ref="da-1"),
        _diff("precision", "regressed", source_ref="da-1"),
    ])
    covered = {
        ref.evidence_id for h in report.hypotheses for ref in h.evidence
    }
    assert "differential_analysis:da-1:recall" in covered
    assert "differential_analysis:da-1:precision" in covered


def test_scope_dataset_version_mismatch_excluded():
    scope = _scope(dataset_version="ds-1")
    artifact = RCAArtifact(
        artifact_id="ci_cd_result:gate-1", source_type="ci_cd_result",
        source_ref="gate-1",
        attributes={"decision": "failed", "dataset_version": "ds-2"},
    )
    report = _run([artifact], scope=scope)
    assert not report.hypotheses
    assert any("scope conflicts" in lim for lim in report.limitations)


def test_scope_dataset_version_missing_disclosed():
    scope = _scope(dataset_version="ds-1")
    artifact = RCAArtifact(
        artifact_id="signal:s1", source_type="signal", source_ref="s1",
        attributes={"signal_type": "latency_anomaly", "payload": {}},
    )
    report = _run([artifact], scope=scope)
    assert len(report.hypotheses) == 1
    assert any("dataset_version" in lim for lim in report.limitations)


def test_enrichment_environment_mismatch_not_attached():
    anchor = RCAArtifact(
        artifact_id="signal:s1", source_type="signal", source_ref="s1",
        observed_at=NOW, agent_id="agent-1", run_id="run-1",
        attributes={"signal_type": "latency_anomaly", "payload": {},
                    "environment": "prod"},
    )
    matching = RCAArtifact(
        artifact_id="log:prod", source_type="log", source_ref="prod",
        observed_at=NOW, agent_id="agent-1", run_id="run-1",
        attributes={"environment": "prod"},
    )
    foreign = RCAArtifact(
        artifact_id="log:staging", source_type="log", source_ref="staging",
        observed_at=NOW, agent_id="agent-1", run_id="run-1",
        attributes={"environment": "staging"},
    )
    report = _run([anchor, matching, foreign])
    signal_hypo = next(
        h for h in report.hypotheses
        if "signal:s1" in {r.evidence_id for r in h.evidence}
    )
    ids = {r.evidence_id for r in signal_hypo.evidence}
    assert "log:prod" in ids
    assert "log:staging" not in ids


def test_missing_evidence_stays_descriptive():
    report = _run([])
    assert report.hypotheses == ()
    assert report.conclusion_level == "descriptive"
    assert any(
        "No eligible in-scope evidence" in lim for lim in report.limitations
    )


def _stats_hypotheses(report):
    return [
        hypothesis
        for hypothesis in report.hypotheses
        if hypothesis.rule_ids[0].startswith("statistics.differential")
    ]


def test_merge_same_rule_two_supported_one_hypothesis_no_escalation():
    # Two independent reports on the same metric under one rule must merge
    # into a single hypothesis that counts both sources, instead of inflating
    # the conclusion to multiple_contributing_factors (RCA_2 §7).
    report = _run([
        _diff("recall", "regressed", source_ref="diff-a"),
        _diff("recall", "regressed", source_ref="diff-b"),
    ])
    stats = _stats_hypotheses(report)
    assert len(stats) == 1
    merged = stats[0]
    assert merged.status == "supported"
    assert merged.validation is None
    assert {reference.evidence_id for reference in merged.evidence} == {
        "differential_analysis:diff-a:recall",
        "differential_analysis:diff-b:recall",
    }
    assert report.conclusion_type == "probable_cause"
    assert report.conclusion_level == "associational"
    assert report.conclusion_level != "validated_causal"


def test_merge_rank_winner_keeps_weaker_constituent_uncertainty():
    # A supported and a candidate constituent under one rule merge to the
    # rank winner among the constituents (never validated, never weaker than
    # the winner), and the weaker constituent's uncertainty survives.
    report = _run([
        _diff("recall", "regressed", source_ref="diff-a", significant=True),
        _diff(
            "recall",
            "regressed",
            source_ref="diff-b",
            significant=False,
            p=0.4,
            practical=False,
        ),
    ])
    stats = _stats_hypotheses(report)
    assert len(stats) == 1
    merged = stats[0]
    assert merged.status == "supported"
    assert merged.status != "validated"
    assert merged.strength != "weak"
    assert merged.validation is None
    assert len(merged.evidence) == 2
    assert any(
        "statistical significance" in limitation
        for limitation in merged.limitations
    )
    assert report.conclusion_level == "associational"
    assert report.conclusion_level != "validated_causal"


def test_merge_candidate_only_group_stays_descriptive():
    report = _run([
        _diff(
            "recall",
            "regressed",
            source_ref="diff-a",
            significant=False,
            p=0.4,
            practical=False,
        ),
        _diff(
            "recall",
            "regressed",
            source_ref="diff-b",
            significant=False,
            p=0.5,
            practical=False,
        ),
    ])
    stats = _stats_hypotheses(report)
    assert len(stats) == 1
    merged = stats[0]
    assert merged.status == "candidate"
    assert merged.strength == "weak"
    assert len(merged.evidence) == 2
    assert report.conclusion_type == "no_supported_cause"
    assert report.conclusion_level == "descriptive"
