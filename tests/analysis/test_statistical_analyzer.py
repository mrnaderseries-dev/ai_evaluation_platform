# Statistical analysis layer — focused regression tests.
from __future__ import annotations

from math import isclose

import pytest
from scipy.stats import mannwhitneyu

from ai_eval_platform.analysis.statistical_analyzer import StatisticalAnalyzer


def test_describe_single_observation_has_no_std():
    result = StatisticalAnalyzer().describe([4.0])

    assert result.sample_size == 1
    assert result.mean == 4.0
    assert result.median == 4.0
    assert result.standard_deviation is None
    assert result.minimum == 4.0
    assert result.maximum == 4.0
    assert result.p95 == 4.0


def test_describe_uses_sample_standard_deviation():
    result = StatisticalAnalyzer().describe([1.0, 2.0, 3.0, 4.0])

    assert isclose(result.standard_deviation, 1.2909944487358056)


def test_describe_rejects_bool_nan_and_infinite():
    analyzer = StatisticalAnalyzer()

    with pytest.raises(TypeError):
        analyzer.describe([True, False])

    with pytest.raises(ValueError):
        analyzer.describe([1.0, float("nan")])

    with pytest.raises(ValueError):
        analyzer.describe([1.0, float("inf")])


def test_correlate_rejects_constant_variable():
    with pytest.raises(ValueError):
        StatisticalAnalyzer().correlate([5.0, 5.0, 5.0], [1.0, 2.0, 3.0])


def test_mann_whitney_effect_size_direction_matches_u():
    first = [10.0, 11.0, 12.0]
    second = [1.0, 2.0, 3.0]

    result = StatisticalAnalyzer().compare_independent_groups(first, second)

    scipy_u = mannwhitneyu(
        first, second, alternative="two-sided", method="auto"
    ).statistic
    assert result.statistic == scipy_u == 9.0
    assert result.rank_biserial_correlation == pytest.approx(
        2.0 * 9.0 / 9.0 - 1.0
    )
    assert result.rank_biserial_correlation == pytest.approx(1.0)
    assert result.median_difference == pytest.approx(-9.0)
    assert result.statistically_significant == (result.p_value < result.alpha)


def test_rank_biserial_sign_follows_kerby_convention():
    analyzer = StatisticalAnalyzer()

    first_larger = analyzer.compare_independent_groups(
        [10.0, 11.0, 12.0], [1.0, 2.0, 3.0]
    )
    assert first_larger.rank_biserial_correlation == pytest.approx(1.0)
    assert first_larger.median_difference < 0.0

    first_smaller = analyzer.compare_independent_groups(
        [1.0, 2.0, 3.0], [10.0, 11.0, 12.0]
    )
    assert first_smaller.rank_biserial_correlation == pytest.approx(-1.0)
    assert first_smaller.median_difference > 0.0

    tied = analyzer.compare_independent_groups(
        [1.0, 2.0, 3.0], [1.0, 2.0, 3.0]
    )
    assert tied.rank_biserial_correlation == pytest.approx(0.0)
    assert tied.median_difference == pytest.approx(0.0)
