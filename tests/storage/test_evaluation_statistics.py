from __future__ import annotations

import pytest

from ai_eval_platform.analysis.statistical_analyzer import (
    StatisticalAnalyzer as LegacyStatisticalAnalyzer,
)
from ai_eval_platform.statistics import (
    EvaluationStatisticsService,
    StatisticalAnalyzer,
    wilson_interval,
)
from ai_eval_platform.storage.models import EvaluationCase, EvaluationResult


def _result(
    result_id: str,
    case_id: str | None,
    run_id: str,
    *,
    metric_name: str = "quality",
    score: float | None = None,
    passed: bool | None = None,
) -> EvaluationResult:
    return EvaluationResult(
        evaluation_result_id=result_id,
        evaluation_run_id="erun-1",
        case_id=case_id,
        run_id=run_id,
        metric_name=metric_name,
        score=score,
        passed=passed,
    )


def test_evaluation_summary_separates_metrics_and_observation_units(
    session,
    seeded_evaluation_run,
):
    session.add_all(
        [
            EvaluationCase(
                case_id="case-2",
                dataset_version_id="dsv-1",
                question="Second question",
            ),
            EvaluationCase(
                case_id="case-3",
                dataset_version_id="dsv-1",
                question="Third question",
            ),
        ]
    )
    session.add_all(
        [
            _result("result-case-1", "case-1", "run-1", score=0.2, passed=False),
            _result("result-case-2", "case-2", "run-2", score=0.6, passed=True),
            _result("result-case-3", "case-3", "run-3", score=1.0, passed=True),
            _result("result-run", None, "run-1", score=0.4, passed=False),
            _result(
                "result-other-metric",
                "case-1",
                "run-1",
                metric_name="coverage",
                score=None,
                passed=None,
            ),
        ]
    )
    session.flush()

    summaries = EvaluationStatisticsService(session).summarize_evaluation_run(
        seeded_evaluation_run
    )

    assert [(item.metric_name, item.observation_unit) for item in summaries] == [
        ("coverage", "case"),
        ("quality", "case"),
        ("quality", "run"),
    ]
    coverage, case_quality, run_quality = summaries
    assert coverage.score_statistics is None
    assert coverage.pass_rate is None
    assert coverage.source_result_ids == ("result-other-metric",)

    assert case_quality.score_observation_count == 3
    assert case_quality.score_statistics.mean == pytest.approx(0.6)
    assert case_quality.pass_observation_count == 3
    assert case_quality.passed_count == 2
    assert case_quality.pass_rate == pytest.approx(2 / 3)
    assert case_quality.pass_rate_confidence_interval.successes == 2
    assert case_quality.source_result_ids == (
        "result-case-1",
        "result-case-2",
        "result-case-3",
    )
    assert any("independent representative" in note for note in case_quality.limitations)

    assert run_quality.observation_unit == "run"
    assert run_quality.score_statistics.mean == pytest.approx(0.4)
    assert run_quality.pass_rate == 0.0


def test_evaluation_summary_empty_run_returns_no_fabricated_metric_rows(
    session,
    seeded_evaluation_run,
):
    assert (
        EvaluationStatisticsService(session).summarize_evaluation_run(
            seeded_evaluation_run
        )
        == ()
    )

    with pytest.raises(ValueError, match="confidence_level"):
        EvaluationStatisticsService(session).summarize_evaluation_run(
            seeded_evaluation_run,
            confidence_level=1.0,
        )


@pytest.mark.parametrize("successes,total", [(0, 1), (1, 1), (5, 10)])
def test_wilson_interval_is_bounded_and_contains_observed_rate(successes, total):
    interval = wilson_interval(successes, total)

    assert interval.method == "wilson_score"
    assert 0 <= interval.lower_bound <= interval.proportion <= interval.upper_bound <= 1


def test_wilson_interval_rejects_invalid_counts_and_confidence_levels():
    with pytest.raises(TypeError):
        wilson_interval(True, 1)
    with pytest.raises(ValueError):
        wilson_interval(2, 1)
    with pytest.raises(ValueError):
        wilson_interval(0, 0)
    with pytest.raises(ValueError):
        wilson_interval(1, 2, confidence_level=1.0)


def test_statistical_analyzer_is_available_from_separate_package_and_legacy_path():
    assert LegacyStatisticalAnalyzer is StatisticalAnalyzer
    assert StatisticalAnalyzer().proportion_interval(2, 3).proportion == pytest.approx(
        2 / 3
    )


def test_evaluation_statistics_rejects_blank_run_id(session):
    with pytest.raises(ValueError, match="evaluation_run_id"):
        EvaluationStatisticsService(session).summarize_evaluation_run("  ")