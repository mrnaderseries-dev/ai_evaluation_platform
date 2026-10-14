from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from ai_eval_platform.statistics import (
    ComparisonConfig,
    EvaluationRunComparisonService,
    MetricComparison,
    MetricRequest,
    RunComparison,
)
from ai_eval_platform.storage.models import (
    Agent,
    AgentVersion,
    Dataset,
    DatasetVersion,
    EvaluationCase,
    EvaluationResult,
    EvaluationRun,
    Run,
)
from ai_eval_platform.storage.repositories import (
    AnalysisRepository,
    DifferentialAnalysisRepository,
    EntityNotFoundError,
    EvaluationResultRepository,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _config(**overrides) -> ComparisonConfig:
    defaults = {
        "bootstrap_resamples": 300,
        "bootstrap_seed": 17,
    }
    defaults.update(overrides)
    return ComparisonConfig(**defaults)


def _quality_request(
    unit: str = "case",
    direction: str = "higher_is_better",
) -> MetricRequest:
    return MetricRequest(
        metric_name="quality",
        observation_unit=unit,
        metric_direction=direction,
    )


def _latency_request(direction: str = "lower_is_better") -> MetricRequest:
    return MetricRequest(
        metric_name="latency_score",
        observation_unit="case",
        metric_direction=direction,
    )


def _seed_graph(session) -> None:
    session.add(Agent(agent_id="agent-1", name="agent-1"))
    session.add(
        AgentVersion(agent_version_id="av-1", agent_id="agent-1", version="1.0.0")
    )
    session.add(
        AgentVersion(agent_version_id="av-2", agent_id="agent-1", version="2.0.0")
    )
    session.add(Dataset(dataset_id="ds-1", name="ds-1"))
    session.add(
        DatasetVersion(dataset_version_id="dsv-1", dataset_id="ds-1", version="1.0.0")
    )
    session.add(
        DatasetVersion(dataset_version_id="dsv-2", dataset_id="ds-1", version="2.0.0")
    )
    session.add_all(
        EvaluationCase(
            case_id=f"case-{index}",
            dataset_version_id="dsv-1",
            question=f"question {index}",
        )
        for index in range(1, 11)
    )
    session.add(
        EvaluationCase(
            case_id="case-99",
            dataset_version_id="dsv-1",
            question="question 99",
        )
    )
    session.add(
        EvaluationRun(
            evaluation_run_id="erun-base",
            agent_version_id="av-1",
            dataset_version_id="dsv-1",
            status="completed",
        )
    )
    session.add(
        EvaluationRun(
            evaluation_run_id="erun-cand",
            agent_version_id="av-2",
            dataset_version_id="dsv-1",
            status="completed",
        )
    )
    session.add(
        EvaluationRun(
            evaluation_run_id="erun-cand-dsv2",
            agent_version_id="av-2",
            dataset_version_id="dsv-2",
            status="completed",
        )
    )
    session.add(
        EvaluationRun(
            evaluation_run_id="erun-no-dataset",
            agent_version_id="av-2",
            status="completed",
        )
    )
    for run_id in ("run-1", "run-2", "run-3", "run-4", "run-5"):
        session.add(
            Run(
                run_id=run_id,
                agent_id="agent-1",
                agent_version_id="av-1",
                started_at=utcnow(),
            )
        )
    session.flush()


def _seed_scores(
    session,
    evaluation_run_id: str,
    run_id: str,
    metric_name: str,
    scores: dict[str, float | None],
    id_prefix: str,
    *,
    passed: bool | None = None,
) -> None:
    session.add_all(
        EvaluationResult(
            evaluation_result_id=f"{id_prefix}-{index}",
            evaluation_run_id=evaluation_run_id,
            case_id=case_id,
            run_id=run_id,
            metric_name=metric_name,
            score=score,
            passed=passed,
        )
        for index, (case_id, score) in enumerate(scores.items(), start=1)
    )
    session.flush()


def _case_scores(values) -> dict[str, float]:
    return {f"case-{index}": value for index, value in enumerate(values, start=1)}


def test_independent_improvement_for_higher_is_better_metric(session):
    _seed_graph(session)
    _seed_scores(
        session,
        "erun-base",
        "run-1",
        "quality",
        _case_scores([0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]),
        "rb",
    )
    _seed_scores(
        session,
        "erun-cand",
        "run-2",
        "quality",
        _case_scores([0.95, 0.96, 0.97, 0.98, 0.99, 1.0, 1.01, 1.02]),
        "rc",
    )

    comparison = EvaluationRunComparisonService(session).compare_runs(
        "erun-base",
        "erun-cand",
        [_quality_request()],
        config=_config(),
    )

    assert comparison.dataset_lineage_status == "matched"
    assert comparison.baseline_agent_version_id == "av-1"
    assert comparison.candidate_agent_version_id == "av-2"
    metric = comparison.metrics[0]
    assert metric.status == "improved"
    assert metric.direction == "candidate_higher"
    assert metric.method == "mann_whitney_u"
    assert metric.raw_p_value == pytest.approx(2 / 12870)
    assert metric.adjusted_p_value == pytest.approx(2 / 12870)
    assert metric.decision_p_value_source == "adjusted_p_value"
    assert metric.rank_biserial_correlation == pytest.approx(1.0)
    assert metric.median_difference == pytest.approx(0.435)
    assert metric.confidence_interval.lower_bound > 0.0
    assert metric.confidence_interval.seed == 17
    assert metric.baseline_sample_size == 8
    assert metric.candidate_sample_size == 8
    assert metric.baseline_source_result_ids == tuple(f"rb-{i}" for i in range(1, 9))
    assert metric.candidate_source_result_ids == tuple(f"rc-{i}" for i in range(1, 9))


def test_independent_regression_for_higher_is_better_metric(session):
    _seed_graph(session)
    _seed_scores(
        session,
        "erun-base",
        "run-1",
        "quality",
        _case_scores([0.95, 0.96, 0.97, 0.98, 0.99, 1.0, 1.01, 1.02]),
        "rb",
    )
    _seed_scores(
        session,
        "erun-cand",
        "run-2",
        "quality",
        _case_scores([0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]),
        "rc",
    )

    metric = EvaluationRunComparisonService(session).compare_runs(
        "erun-base",
        "erun-cand",
        [_quality_request()],
        config=_config(),
    ).metrics[0]

    assert metric.status == "regressed"
    assert metric.direction == "candidate_lower"
    assert metric.rank_biserial_correlation == pytest.approx(-1.0)
    assert metric.median_difference < 0.0
    assert metric.confidence_interval.upper_bound < 0.0


def test_lower_is_better_metric_maps_candidate_lower_to_improvement(session):
    _seed_graph(session)
    _seed_scores(
        session,
        "erun-base",
        "run-1",
        "latency_score",
        _case_scores([0.9, 0.91, 0.92, 0.93, 0.94, 0.95, 0.96, 0.97]),
        "lrb",
    )
    _seed_scores(
        session,
        "erun-cand",
        "run-2",
        "latency_score",
        _case_scores([0.2, 0.21, 0.22, 0.23, 0.24, 0.25, 0.26, 0.27]),
        "lrc",
    )

    metric = EvaluationRunComparisonService(session).compare_runs(
        "erun-base",
        "erun-cand",
        [_latency_request()],
        config=_config(),
    ).metrics[0]

    assert metric.direction == "candidate_lower"
    assert metric.status == "improved"


def test_lower_is_better_metric_maps_candidate_higher_to_regression(session):
    _seed_graph(session)
    _seed_scores(
        session,
        "erun-base",
        "run-1",
        "latency_score",
        _case_scores([0.2, 0.21, 0.22, 0.23, 0.24, 0.25, 0.26, 0.27]),
        "lrb",
    )
    _seed_scores(
        session,
        "erun-cand",
        "run-2",
        "latency_score",
        _case_scores([0.9, 0.91, 0.92, 0.93, 0.94, 0.95, 0.96, 0.97]),
        "lrc",
    )

    metric = EvaluationRunComparisonService(session).compare_runs(
        "erun-base",
        "erun-cand",
        [_latency_request()],
        config=_config(),
    ).metrics[0]

    assert metric.direction == "candidate_higher"
    assert metric.status == "regressed"


def test_equal_scores_yield_no_clear_direction_and_no_equivalence_claim(session):
    _seed_graph(session)
    scores = [0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
    _seed_scores(session, "erun-base", "run-1", "quality", _case_scores(scores), "rb")
    _seed_scores(session, "erun-cand", "run-2", "quality", _case_scores(scores), "rc")

    metric = EvaluationRunComparisonService(session).compare_runs(
        "erun-base",
        "erun-cand",
        [_quality_request()],
        config=_config(),
    ).metrics[0]

    assert metric.status == "no_clear_difference"
    assert metric.direction == "no_clear_direction"
    assert metric.median_difference == pytest.approx(0.0)
    assert metric.raw_p_value == pytest.approx(1.0)
    assert metric.statistically_significant is False
    assert any("equivalence" in note for note in metric.limitations)

def test_independent_design_reports_insufficient_evidence_for_identical_constant_groups(
    session,
):
    # F2: both runs record the identical constant score for every case, so
    # the pooled set is a single tie and the Mann-Whitney U statistic has
    # zero variance. SciPy returns a non-finite p-value; the comparator
    # mirrors the paired all-zero handling with a validated insufficient
    # evidence outcome rather than leaking a numpy warning and an opaque
    # ArithmeticError.
    _seed_graph(session)
    constant = 1.0
    _seed_scores(
        session,
        "erun-base",
        "run-1",
        "quality",
        _case_scores([constant] * 6),
        "rb",
    )
    _seed_scores(
        session,
        "erun-cand",
        "run-2",
        "quality",
        _case_scores([constant] * 6),
        "rc",
    )

    metric = EvaluationRunComparisonService(session).compare_runs(
        "erun-base",
        "erun-cand",
        [_quality_request()],
        config=_config(),
    ).metrics[0]

    assert metric.status == "insufficient_evidence"
    assert any(
        "identical" in reason and str(constant) in reason
        for reason in metric.insufficient_evidence_reasons
    )
    assert metric.raw_p_value is None
    assert metric.adjusted_p_value is None


def test_independent_design_handles_different_constant_groups_as_valid_comparison(
    session,
):
    # F2 complement: the baseline records one constant and the candidate a
    # different constant for every case. Unlike the identical-constant case
    # above, the pooled set is not a single tie, so the Mann-Whitney U test
    # is well defined and the comparison proceeds to a normal outcome rather
    # than a validated insufficient-evidence result.
    _seed_graph(session)
    _seed_scores(
        session,
        "erun-base",
        "run-1",
        "quality",
        _case_scores([1.0] * 6),
        "rb",
    )
    _seed_scores(
        session,
        "erun-cand",
        "run-2",
        "quality",
        _case_scores([2.0] * 6),
        "rc",
    )

    metric = EvaluationRunComparisonService(session).compare_runs(
        "erun-base",
        "erun-cand",
        [_quality_request()],
        config=_config(),
    ).metrics[0]

    assert metric.status == "improved"
    assert metric.direction == "candidate_higher"
    assert metric.method == "mann_whitney_u"
    assert metric.rank_biserial_correlation == pytest.approx(1.0)
    assert metric.median_difference == pytest.approx(1.0)
    assert metric.raw_p_value is not None
    assert metric.adjusted_p_value is not None
    assert not any(
        "identical" in reason for reason in metric.insufficient_evidence_reasons
    )


def test_paired_design_matches_exact_signed_rank_reference(session):
    _seed_graph(session)
    _seed_scores(
        session,
        "erun-base",
        "run-1",
        "quality",
        _case_scores([1.0, 2.0, 3.0, 4.0, 5.0, 6.0]),
        "rb",
    )
    _seed_scores(
        session,
        "erun-cand",
        "run-2",
        "quality",
        _case_scores([2.0, 3.0, 4.0, 5.0, 6.0, 7.0]),
        "rc",
    )

    metric = EvaluationRunComparisonService(session).compare_runs(
        "erun-base",
        "erun-cand",
        [_quality_request()],
        config=_config(design="paired"),
    ).metrics[0]

    assert metric.method == "wilcoxon_signed_rank"
    assert metric.status == "improved"
    assert metric.compared_pair_count == 6
    assert metric.raw_p_value == pytest.approx(2 / 64)
    assert metric.rank_biserial_correlation == pytest.approx(1.0)
    assert metric.median_difference == pytest.approx(1.0)
    assert metric.confidence_interval.paired is True
    assert metric.confidence_interval.lower_bound > 0.0


def test_paired_design_rejects_run_level_observations(session):
    _seed_graph(session)
    session.add(
        EvaluationResult(
            evaluation_result_id="rrb-1",
            evaluation_run_id="erun-base",
            case_id=None,
            run_id="run-1",
            metric_name="quality",
            score=0.4,
        )
    )
    session.add(
        EvaluationResult(
            evaluation_result_id="rrc-1",
            evaluation_run_id="erun-cand",
            case_id=None,
            run_id="run-2",
            metric_name="quality",
            score=0.5,
        )
    )
    session.flush()

    with pytest.raises(ValueError, match="pairing keys"):
        EvaluationRunComparisonService(session).compare_runs(
            "erun-base",
            "erun-cand",
            [_quality_request(unit="run")],
            config=_config(design="paired"),
        )


def test_paired_design_rejects_mismatched_pairing_keys(session):
    _seed_graph(session)
    baseline_scores = _case_scores([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    candidate_scores = _case_scores([2.0, 3.0, 4.0, 5.0, 6.0, 7.0])
    del candidate_scores["case-1"]
    candidate_scores["case-99"] = 8.0
    _seed_scores(session, "erun-base", "run-1", "quality", baseline_scores, "rb")
    _seed_scores(session, "erun-cand", "run-2", "quality", candidate_scores, "rc")

    with pytest.raises(ValueError, match="pairing keys do not match"):
        EvaluationRunComparisonService(session).compare_runs(
            "erun-base",
            "erun-cand",
            [_quality_request()],
            config=_config(design="paired"),
        )


def test_paired_design_rejects_duplicate_pairing_keys(session, monkeypatch):
    _seed_graph(session)
    duplicated = [
        EvaluationResult(
            evaluation_result_id=f"dup-{index}",
            evaluation_run_id="erun-base",
            case_id="case-1",
            run_id="run-1",
            metric_name="quality",
            score=0.5,
        )
        for index in (1, 2)
    ]
    monkeypatch.setattr(
        EvaluationResultRepository,
        "list_for_run",
        lambda self, evaluation_run_id: list(duplicated),
    )

    with pytest.raises(ValueError, match="duplicate pairing key"):
        EvaluationRunComparisonService(session).compare_runs(
            "erun-base",
            "erun-cand",
            [_quality_request()],
            config=_config(design="paired"),
        )


def test_paired_design_reports_insufficient_evidence_for_small_pair_count(session):
    _seed_graph(session)
    _seed_scores(
        session,
        "erun-base",
        "run-1",
        "quality",
        _case_scores([1.0, 2.0, 3.0, 4.0]),
        "rb",
    )
    _seed_scores(
        session,
        "erun-cand",
        "run-2",
        "quality",
        _case_scores([2.0, 3.0, 4.0, 5.0]),
        "rc",
    )

    metric = EvaluationRunComparisonService(session).compare_runs(
        "erun-base",
        "erun-cand",
        [_quality_request()],
        config=_config(design="paired"),
    ).metrics[0]

    assert metric.status == "insufficient_evidence"
    assert metric.compared_pair_count == 4
    assert metric.raw_p_value is None
    assert metric.adjusted_p_value is None
    assert any("matched pairs" in reason for reason in metric.insufficient_evidence_reasons)


def test_paired_design_reports_insufficient_evidence_when_non_zero_count_falls_below_floor(
    session,
):
    # The configured floor applies to the effective sample size (non-zero
    # differences actually fed to Wilcoxon), not to the raw matched-pair
    # count (audit finding F3). Seven pairs are available, but only one has
    # a non-zero difference, below the configured floor of five.
    _seed_graph(session)
    _seed_scores(
        session,
        "erun-base",
        "run-1",
        "quality",
        _case_scores([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]),
        "rb",
    )
    _seed_scores(
        session,
        "erun-cand",
        "run-2",
        "quality",
        _case_scores([2.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]),
        "rc",
    )

    metric = EvaluationRunComparisonService(session).compare_runs(
        "erun-base",
        "erun-cand",
        [_quality_request()],
        config=_config(design="paired", min_observations_per_group=5),
    ).metrics[0]

    assert metric.status == "insufficient_evidence"
    assert metric.compared_pair_count == 7
    assert metric.raw_p_value is None
    assert metric.adjusted_p_value is None
    assert any(
        "non-zero" in reason for reason in metric.insufficient_evidence_reasons
    )


def test_paired_design_rejects_unsupported_all_zero_differences_as_insufficient(
    session,
):
    _seed_graph(session)
    scores = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    _seed_scores(session, "erun-base", "run-1", "quality", _case_scores(scores), "rb")
    _seed_scores(session, "erun-cand", "run-2", "quality", _case_scores(scores), "rc")

    metric = EvaluationRunComparisonService(session).compare_runs(
        "erun-base",
        "erun-cand",
        [_quality_request()],
        config=_config(design="paired"),
    ).metrics[0]

    assert metric.status == "insufficient_evidence"
    assert any("zero" in reason for reason in metric.insufficient_evidence_reasons)
    assert metric.raw_p_value is None


def test_rejects_same_run_as_baseline_and_candidate(session):
    _seed_graph(session)

    with pytest.raises(ValueError, match="must differ"):
        EvaluationRunComparisonService(session).compare_runs(
            "erun-base",
            "erun-base",
            [_quality_request()],
            config=_config(),
        )


def test_rejects_unknown_evaluation_run(session):
    _seed_graph(session)

    with pytest.raises(EntityNotFoundError):
        EvaluationRunComparisonService(session).compare_runs(
            "erun-base",
            "erun-missing",
            [_quality_request()],
            config=_config(),
        )


def test_rejects_metric_missing_from_one_run(session):
    _seed_graph(session)
    _seed_scores(
        session,
        "erun-base",
        "run-1",
        "quality",
        _case_scores([0.1, 0.2, 0.3, 0.4, 0.5]),
        "rb",
    )

    with pytest.raises(ValueError, match="missing from the candidate"):
        EvaluationRunComparisonService(session).compare_runs(
            "erun-base",
            "erun-cand",
            [_quality_request()],
            config=_config(),
        )


def test_rejects_incompatible_observation_unit(session):
    _seed_graph(session)
    _seed_scores(
        session,
        "erun-base",
        "run-1",
        "quality",
        _case_scores([0.1, 0.2, 0.3, 0.4, 0.5]),
        "rb",
    )
    _seed_scores(
        session,
        "erun-cand",
        "run-2",
        "quality",
        _case_scores([0.5, 0.6, 0.7, 0.8, 0.9]),
        "rc",
    )

    with pytest.raises(ValueError, match="missing from the baseline"):
        EvaluationRunComparisonService(session).compare_runs(
            "erun-base",
            "erun-cand",
            [_quality_request(unit="run")],
            config=_config(),
        )


def test_dataset_version_mismatch_is_rejected_by_default(session):
    _seed_graph(session)
    _seed_scores(
        session,
        "erun-base",
        "run-1",
        "quality",
        _case_scores([0.1, 0.2, 0.3, 0.4, 0.5]),
        "rb",
    )
    _seed_scores(
        session,
        "erun-cand-dsv2",
        "run-2",
        "quality",
        _case_scores([0.5, 0.6, 0.7, 0.8, 0.9]),
        "rc2",
    )

    with pytest.raises(ValueError, match="different dataset versions"):
        EvaluationRunComparisonService(session).compare_runs(
            "erun-base",
            "erun-cand-dsv2",
            [_quality_request()],
            config=_config(),
        )


def test_missing_dataset_lineage_is_rejected_by_default(session):
    _seed_graph(session)
    _seed_scores(
        session,
        "erun-base",
        "run-1",
        "quality",
        _case_scores([0.1, 0.2, 0.3, 0.4, 0.5]),
        "rb",
    )
    _seed_scores(
        session,
        "erun-no-dataset",
        "run-2",
        "quality",
        _case_scores([0.5, 0.6, 0.7, 0.8, 0.9]),
        "rnd",
    )

    with pytest.raises(ValueError, match="lineage is missing"):
        EvaluationRunComparisonService(session).compare_runs(
            "erun-base",
            "erun-no-dataset",
            [_quality_request()],
            config=_config(),
        )


def test_relaxed_lineage_requirement_records_explicit_limitation(session):
    _seed_graph(session)
    _seed_scores(
        session,
        "erun-base",
        "run-1",
        "quality",
        _case_scores([0.1, 0.2, 0.3, 0.4, 0.5]),
        "rb",
    )
    _seed_scores(
        session,
        "erun-cand-dsv2",
        "run-2",
        "quality",
        _case_scores([0.5, 0.6, 0.7, 0.8, 0.9]),
        "rc2",
    )

    comparison = EvaluationRunComparisonService(session).compare_runs(
        "erun-base",
        "erun-cand-dsv2",
        [_quality_request()],
        config=_config(require_matching_dataset_version=False),
    )

    assert comparison.dataset_lineage_status == "mismatched"
    assert comparison.candidate_dataset_version_id == "dsv-2"
    assert any("different dataset versions" in note for note in comparison.limitations)


def test_insufficient_evidence_is_not_promoted_despite_perfect_separation(session):
    _seed_graph(session)
    _seed_scores(
        session,
        "erun-base",
        "run-1",
        "quality",
        _case_scores([0.1, 0.2, 0.3, 0.4]),
        "rb",
    )
    _seed_scores(
        session,
        "erun-cand",
        "run-2",
        "quality",
        _case_scores([0.8, 0.9, 1.0, 1.1]),
        "rc",
    )

    metric = EvaluationRunComparisonService(session).compare_runs(
        "erun-base",
        "erun-cand",
        [_quality_request()],
        config=_config(),
    ).metrics[0]

    assert metric.status == "insufficient_evidence"
    assert metric.raw_p_value is None
    assert metric.adjusted_p_value is None
    assert metric.decision_p_value is None
    assert metric.confidence_interval is None
    assert metric.statistically_significant is False
    assert any("at least 5" in reason for reason in metric.insufficient_evidence_reasons)


def test_non_finite_scores_are_rejected(session):
    _seed_graph(session)
    bad_scores = _case_scores([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, float("inf"), 0.8])
    _seed_scores(session, "erun-base", "run-1", "quality", bad_scores, "rb")
    _seed_scores(
        session,
        "erun-cand",
        "run-2",
        "quality",
        _case_scores([0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2]),
        "rc",
    )

    with pytest.raises(ValueError, match="non-finite"):
        EvaluationRunComparisonService(session).compare_runs(
            "erun-base",
            "erun-cand",
            [_quality_request()],
            config=_config(),
        )


def test_null_scores_are_excluded_with_recorded_limitation(session):
    _seed_graph(session)
    baseline_scores = _case_scores([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8])
    candidate_scores = _case_scores([0.9, 0.91, 0.92, 0.93, 0.94, 0.95, None, 0.97])
    _seed_scores(session, "erun-base", "run-1", "quality", baseline_scores, "rb")
    _seed_scores(session, "erun-cand", "run-2", "quality", candidate_scores, "rc")

    metric = EvaluationRunComparisonService(session).compare_runs(
        "erun-base",
        "erun-cand",
        [_quality_request()],
        config=_config(),
    ).metrics[0]

    assert metric.excluded_missing_score_count == 1
    assert metric.candidate_sample_size == 7
    assert any("without scores" in note for note in metric.limitations)


def test_metric_without_any_scores_reports_insufficient_evidence(session):
    _seed_graph(session)
    _seed_scores(
        session,
        "erun-base",
        "run-1",
        "quality",
        {f"case-{index}": None for index in range(1, 9)},
        "rb",
        passed=True,
    )
    _seed_scores(
        session,
        "erun-cand",
        "run-2",
        "quality",
        {f"case-{index}": None for index in range(1, 9)},
        "rc",
        passed=True,
    )

    metric = EvaluationRunComparisonService(session).compare_runs(
        "erun-base",
        "erun-cand",
        [_quality_request()],
        config=_config(),
    ).metrics[0]

    assert metric.status == "insufficient_evidence"
    assert any(
        "pass-rate comparison is not supported" in reason
        for reason in metric.insufficient_evidence_reasons
    )
    assert metric.raw_p_value is None


def test_holm_family_with_mixed_directions_and_unavailable_p_value(session):
    _seed_graph(session)
    _seed_scores(
        session,
        "erun-base",
        "run-1",
        "quality",
        _case_scores([0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]),
        "rb",
    )
    _seed_scores(
        session,
        "erun-cand",
        "run-2",
        "quality",
        _case_scores([0.95, 0.96, 0.97, 0.98, 0.99, 1.0, 1.01, 1.02]),
        "rc",
    )
    _seed_scores(
        session,
        "erun-base",
        "run-3",
        "latency_score",
        _case_scores([0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]),
        "lrb",
    )
    _seed_scores(
        session,
        "erun-cand",
        "run-4",
        "latency_score",
        _case_scores([0.95, 0.96, 0.97, 0.98, 0.99, 1.0, 1.01, 1.02]),
        "lrc",
    )
    _seed_scores(
        session,
        "erun-base",
        "run-1",
        "novelty",
        _case_scores([0.1, 0.2, 0.3]),
        "nrb",
    )
    _seed_scores(
        session,
        "erun-cand",
        "run-2",
        "novelty",
        _case_scores([0.4, 0.5, 0.6]),
        "nrc",
    )

    comparison = EvaluationRunComparisonService(session).compare_runs(
        "erun-base",
        "erun-cand",
        [
            _quality_request(),
            _latency_request(),
            MetricRequest(
                metric_name="novelty",
                observation_unit="case",
                metric_direction="higher_is_better",
            ),
        ],
        config=_config(),
    )

    quality, latency, novelty = comparison.metrics
    assert comparison.family.correction == "holm"
    assert comparison.family.metrics_with_p_value == 2
    assert comparison.family.metrics == ("quality", "latency_score", "novelty")
    assert "erun-base->erun-cand" in comparison.family.family_id

    expected_single_p = 2 / 12870
    assert quality.raw_p_value == pytest.approx(expected_single_p)
    assert quality.adjusted_p_value == pytest.approx(2 * expected_single_p)
    assert quality.status == "improved"
    assert latency.raw_p_value == pytest.approx(expected_single_p)
    assert latency.adjusted_p_value == pytest.approx(2 * expected_single_p)
    assert latency.status == "regressed"
    assert latency.direction == "candidate_higher"

    assert novelty.status == "insufficient_evidence"
    assert novelty.raw_p_value is None
    assert novelty.adjusted_p_value is None


def test_holm_adjustment_blocks_decision_when_adjusted_p_exceeds_alpha(session):
    _seed_graph(session)
    _seed_scores(
        session,
        "erun-base",
        "run-1",
        "quality",
        _case_scores([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]),
        "rb",
    )
    _seed_scores(
        session,
        "erun-cand",
        "run-2",
        "quality",
        _case_scores([4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0]),
        "rc",
    )
    _seed_scores(
        session,
        "erun-base",
        "run-3",
        "fluency",
        _case_scores([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]),
        "frb",
    )
    _seed_scores(
        session,
        "erun-cand",
        "run-4",
        "fluency",
        _case_scores([4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0, 13.0]),
        "frc",
    )

    comparison = EvaluationRunComparisonService(session).compare_runs(
        "erun-base",
        "erun-cand",
        [
            _quality_request(),
            MetricRequest(
                metric_name="fluency",
                observation_unit="case",
                metric_direction="higher_is_better",
            ),
        ],
        config=_config(),
    )

    quality, fluency = comparison.metrics
    assert quality.raw_p_value < 0.05
    assert quality.adjusted_p_value > 0.05
    assert quality.status == "no_clear_difference"
    assert quality.statistically_significant is False
    assert quality.decision_p_value == pytest.approx(quality.adjusted_p_value)
    assert any(
        "Holm-adjusted" in reason
        for reason in quality.insufficient_evidence_reasons
    )

    assert fluency.raw_p_value > 0.05
    assert fluency.adjusted_p_value > fluency.raw_p_value
    assert fluency.status == "no_clear_difference"
    assert any("equivalence" in note for note in fluency.limitations)


def test_bootstrap_interval_is_reproducible_and_seed_sensitive(session):
    _seed_graph(session)
    noisy_baseline = [0.12, 0.55, 0.33, 0.91, 0.24, 0.77, 1.4, 0.62]
    noisy_candidate = [1.1, 0.75, 1.35, 0.95, 1.62, 1.05, 0.88, 1.48]
    _seed_scores(session, "erun-base", "run-1", "quality", _case_scores(noisy_baseline), "rb")
    _seed_scores(session, "erun-cand", "run-2", "quality", _case_scores(noisy_candidate), "rc")

    service = EvaluationRunComparisonService(session)
    first = service.compare_runs(
        "erun-base",
        "erun-cand",
        [_quality_request()],
        config=_config(bootstrap_seed=5),
    ).metrics[0].confidence_interval
    second = service.compare_runs(
        "erun-base",
        "erun-cand",
        [_quality_request()],
        config=_config(bootstrap_seed=5),
    ).metrics[0].confidence_interval
    third = service.compare_runs(
        "erun-base",
        "erun-cand",
        [_quality_request()],
        config=_config(bootstrap_seed=99),
    ).metrics[0].confidence_interval

    assert first == second
    assert (first.lower_bound, first.upper_bound) != (
        third.lower_bound,
        third.upper_bound,
    )


def test_saved_comparison_round_trips_through_persistence(session):
    import json

    _seed_graph(session)
    _seed_scores(
        session,
        "erun-base",
        "run-1",
        "quality",
        _case_scores([0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]),
        "rb",
    )
    _seed_scores(
        session,
        "erun-cand",
        "run-2",
        "quality",
        _case_scores([0.95, 0.96, 0.97, 0.98, 0.99, 1.0, 1.01, 1.02]),
        "rc",
    )

    service = EvaluationRunComparisonService(session)
    comparison = service.compare_runs(
        "erun-base",
        "erun-cand",
        [_quality_request()],
        config=_config(),
    )
    analysis, differential = service.save(comparison)

    assert analysis.analysis_type == "evaluation_run_comparison"
    assert analysis.source_evaluation_run_id == "erun-cand"
    assert differential.baseline_evaluation_run_id == "erun-base"
    assert differential.candidate_evaluation_run_id == "erun-cand"
    assert differential.metric_deltas["quality"]["status"] == "improved"
    assert differential.statistical_tests["quality"]["raw_p_value"] == pytest.approx(
        2 / 12870
    )

    stored_differentials = DifferentialAnalysisRepository(session).list_for_candidate(
        "erun-cand"
    )
    assert len(stored_differentials) == 1
    stored_analyses = AnalysisRepository(session).list_for_evaluation_run(
        "erun-cand",
        analysis_type="evaluation_run_comparison",
    )
    assert len(stored_analyses) == 1

    rehydrated = RunComparison.model_validate(
        json.loads(json.dumps(stored_analyses[0].metrics["comparison"]))
    )
    assert rehydrated == comparison
    assert stored_differentials[0].statistical_tests["quality"] == json.loads(
        json.dumps(comparison.model_dump(mode="json")["metrics"][0])
    )


def test_metric_comparison_contract_rejects_incoherent_direction():
    with pytest.raises(ValidationError, match="candidate_higher"):
        MetricComparison(
            metric_name="quality",
            observation_unit="case",
            design="independent",
            method="mann_whitney_u",
            metric_direction="higher_is_better",
            direction="candidate_lower",
            status="insufficient_evidence",
            difference_convention="candidate_minus_baseline",
            effect_size_definition="kerby_rank_biserial_candidate_oriented",
            median_difference=0.5,
            insufficient_evidence_reasons=("only four observations",),
        )


def test_metric_comparison_contract_rejects_improvement_without_significance():
    with pytest.raises(ValidationError, match="significance"):
        MetricComparison(
            metric_name="quality",
            observation_unit="case",
            design="independent",
            method="mann_whitney_u",
            metric_direction="higher_is_better",
            direction="candidate_higher",
            status="improved",
            difference_convention="candidate_minus_baseline",
            effect_size_definition="kerby_rank_biserial_candidate_oriented",
            median_difference=0.5,
        )


def test_metric_comparison_contract_rejects_insufficient_evidence_with_p_value():
    with pytest.raises(ValidationError, match="must not carry p-values"):
        MetricComparison(
            metric_name="quality",
            observation_unit="case",
            design="independent",
            method="mann_whitney_u",
            metric_direction="higher_is_better",
            direction="candidate_higher",
            status="insufficient_evidence",
            difference_convention="candidate_minus_baseline",
            effect_size_definition="kerby_rank_biserial_candidate_oriented",
            median_difference=0.5,
            raw_p_value=0.01,
            insufficient_evidence_reasons=("only four observations",),
        )


def test_metric_comparison_contract_rejects_improvement_wrongly_mapped_for_direction():
    with pytest.raises(ValidationError, match="candidate_lower"):
        MetricComparison(
            metric_name="latency_score",
            observation_unit="case",
            design="independent",
            method="mann_whitney_u",
            metric_direction="lower_is_better",
            direction="candidate_higher",
            status="improved",
            difference_convention="candidate_minus_baseline",
            effect_size_definition="kerby_rank_biserial_candidate_oriented",
            median_difference=0.5,
            statistically_significant=True,
            raw_p_value=0.001,
            adjusted_p_value=0.001,
            decision_p_value=0.001,
            decision_p_value_source="adjusted_p_value",
        )


def test_comparison_config_rejects_invalid_values():
    with pytest.raises(ValueError, match="alpha"):
        _config(alpha=1.2)
    with pytest.raises(TypeError, match="alpha"):
        _config(alpha=True)
    with pytest.raises(ValueError, match="practical_significance_threshold"):
        _config(practical_significance_threshold=-1.0)
    with pytest.raises(ValueError, match="practical_significance_threshold"):
        _config(practical_significance_threshold=float("inf"))
    with pytest.raises(ValueError, match="bootstrap_resamples"):
        _config(bootstrap_resamples=0)
    with pytest.raises(TypeError, match="bootstrap_resamples"):
        _config(bootstrap_resamples=True)
    with pytest.raises(ValueError, match="min_observations_per_group"):
        _config(min_observations_per_group=0)
    with pytest.raises(TypeError, match="bootstrap_seed"):
        _config(bootstrap_seed=1.5)
    with pytest.raises(ValueError, match="bootstrap_seed"):
        _config(bootstrap_seed=-1)
    with pytest.raises(ValueError):
        _config(design="bootstrap")


def test_compare_runs_rejects_invalid_identifiers_and_requests(session):
    _seed_graph(session)
    service = EvaluationRunComparisonService(session)

    with pytest.raises(ValueError, match="baseline_evaluation_run_id"):
        service.compare_runs("", "erun-cand", [_quality_request()], config=_config())
    with pytest.raises(ValueError, match="candidate_evaluation_run_id"):
        service.compare_runs("erun-base", "  ", [_quality_request()], config=_config())
    with pytest.raises(ValueError, match="at least one metric request"):
        service.compare_runs("erun-base", "erun-cand", [], config=_config())
    with pytest.raises(TypeError, match="MetricRequest"):
        service.compare_runs(
            "erun-base",
            "erun-cand",
            ["quality"],
            config=_config(),
        )
    with pytest.raises(ValueError, match="once per run comparison"):
        service.compare_runs(
            "erun-base",
            "erun-cand",
            [_quality_request(), _quality_request()],
            config=_config(),
        )
    with pytest.raises(TypeError, match="ComparisonConfig"):
        service.compare_runs(
            "erun-base",
            "erun-cand",
            [_quality_request()],
            config={"metric_direction": "higher_is_better"},
        )


def test_paired_candidate_worse_comparison_reports_negative_effect_size(session):
    _seed_graph(session)
    _seed_scores(
        session,
        "erun-base",
        "run-1",
        "quality",
        _case_scores([1.0, 2.0, 3.0, 4.0, 5.0, 6.0]),
        "rb",
    )
    _seed_scores(
        session,
        "erun-cand",
        "run-2",
        "quality",
        _case_scores([0.0, 1.0, 2.0, 3.0, 4.0, 5.0]),
        "rc",
    )

    metric = EvaluationRunComparisonService(session).compare_runs(
        "erun-base",
        "erun-cand",
        [_quality_request()],
        config=_config(design="paired"),
    ).metrics[0]

    # Every difference is candidate - baseline = -1: the six tied |d| = 1
    # take rank 3.5 each, so W+ = 0, W- = 21, rb = (0 - 21) / 21 = -1.
    # The broken two-sided-minimum formula produced +1 here (audit F1).
    assert metric.status == "regressed"
    assert metric.direction == "candidate_lower"
    assert metric.rank_biserial_correlation == pytest.approx(-1.0)
    assert metric.median_difference == pytest.approx(-1.0)
    assert metric.raw_p_value == pytest.approx(2 / 64)
    assert metric.adjusted_p_value == pytest.approx(2 / 64)
    assert metric.confidence_interval.upper_bound < 0.0


def test_paired_mixed_sign_effect_size_matches_hand_computed_rank_sums(session):
    _seed_graph(session)
    # Differences candidate - baseline: [1, -1, 2, -1, -2].
    _seed_scores(
        session,
        "erun-base",
        "run-1",
        "quality",
        _case_scores([1.0, 2.0, 3.0, 4.0, 5.0]),
        "rb",
    )
    _seed_scores(
        session,
        "erun-cand",
        "run-2",
        "quality",
        _case_scores([2.0, 1.0, 5.0, 3.0, 3.0]),
        "rc",
    )

    metric = EvaluationRunComparisonService(session).compare_runs(
        "erun-base",
        "erun-cand",
        [_quality_request()],
        config=_config(design="paired"),
    ).metrics[0]

    # |d| sorted = [1, 1, 1, 2, 2]: ranks [2, 2, 2, 4.5, 4.5].
    # W+ = 2 (d = +1) + 4.5 (d = +2) = 6.5
    # W- = 2 + 2 (d = -1 twice) + 4.5 (d = -2) = 8.5
    # rb = (6.5 - 8.5) / 15 = -2/15.
    assert metric.rank_biserial_correlation == pytest.approx(-2.0 / 15.0)
    assert metric.median_difference == pytest.approx(-1.0)
    assert metric.direction == "candidate_lower"
    assert metric.raw_p_value == pytest.approx(1.0)
    assert metric.status == "no_clear_difference"








