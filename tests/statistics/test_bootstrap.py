import pytest

from ai_eval_platform.statistics.bootstrap import (
    BootstrapInterval,
    bootstrap_median_difference_interval,
)


def test_independent_interval_reports_candidate_minus_baseline_estimate():
    interval = bootstrap_median_difference_interval(
        [1.0, 2.0, 3.0],
        [4.0, 5.0, 6.0],
        resamples=500,
        seed=7,
    )

    assert isinstance(interval, BootstrapInterval)
    assert interval.method == "percentile_bootstrap"
    assert interval.difference_convention == "candidate_minus_baseline"
    assert interval.paired is False
    assert interval.point_estimate == pytest.approx(3.0)
    assert interval.lower_bound <= interval.upper_bound
    assert interval.resamples == 500
    assert interval.seed == 7


def test_seeded_bootstrap_is_exactly_reproducible():
    baseline = [0.1, 0.4, 0.9, 1.7, 2.2, 3.9, 4.4]
    candidate = [0.6, 1.1, 1.8, 2.7, 3.3, 4.9, 5.5]

    first = bootstrap_median_difference_interval(
        baseline,
        candidate,
        resamples=400,
        seed=11,
    )
    second = bootstrap_median_difference_interval(
        baseline,
        candidate,
        resamples=400,
        seed=11,
    )

    assert first == second


def test_different_seeds_produce_different_intervals():
    baseline = [0.1, 0.4, 0.9, 1.7, 2.2, 3.9, 4.4]
    candidate = [0.6, 1.1, 1.8, 2.7, 3.3, 4.9, 5.5]

    first = bootstrap_median_difference_interval(
        baseline,
        candidate,
        resamples=400,
        seed=1,
    )
    second = bootstrap_median_difference_interval(
        baseline,
        candidate,
        resamples=400,
        seed=2,
    )

    assert (
        first.lower_bound,
        first.upper_bound,
    ) != (second.lower_bound, second.upper_bound)


def test_completely_separated_groups_produce_positive_interval():
    interval = bootstrap_median_difference_interval(
        [10.0, 11.0, 12.0, 13.0, 14.0],
        [15.0, 16.0, 17.0, 18.0, 19.0],
        resamples=1000,
        seed=3,
    )

    assert interval.point_estimate == pytest.approx(5.0)
    assert interval.lower_bound > 0.0


def test_paired_interval_of_constant_difference_collapses_to_that_difference():
    interval = bootstrap_median_difference_interval(
        [1.0, 2.0, 3.0, 4.0],
        [2.0, 3.0, 4.0, 5.0],
        paired=True,
        resamples=300,
        seed=5,
    )

    assert interval.paired is True
    assert interval.point_estimate == pytest.approx(1.0)
    assert interval.lower_bound == pytest.approx(1.0)
    assert interval.upper_bound == pytest.approx(1.0)


def test_paired_mode_requires_equal_lengths():
    with pytest.raises(ValueError, match="equal lengths"):
        bootstrap_median_difference_interval(
            [1.0, 2.0, 3.0],
            [1.0, 2.0],
            paired=True,
        )


def test_rejects_invalid_resamples_and_seed():
    with pytest.raises(TypeError, match="resamples"):
        bootstrap_median_difference_interval([1.0], [2.0], resamples=True)
    with pytest.raises(ValueError, match="resamples"):
        bootstrap_median_difference_interval([1.0], [2.0], resamples=0)
    with pytest.raises(TypeError, match="seed"):
        bootstrap_median_difference_interval([1.0], [2.0], seed=1.5)
    with pytest.raises(ValueError, match="seed"):
        bootstrap_median_difference_interval([1.0], [2.0], seed=-1)


def test_rejects_non_finite_inputs_and_invalid_confidence_level():
    with pytest.raises(ValueError, match="finite"):
        bootstrap_median_difference_interval(
            [1.0, float("inf")],
            [2.0, 3.0],
        )
    with pytest.raises(ValueError, match="confidence_level"):
        bootstrap_median_difference_interval(
            [1.0],
            [2.0],
            confidence_level=1.0,
        )


def test_interval_model_rejects_inverted_bounds():
    with pytest.raises(ValueError, match="lower_bound"):
        BootstrapInterval(
            method="percentile_bootstrap",
            difference_convention="candidate_minus_baseline",
            paired=False,
            point_estimate=0.5,
            confidence_level=0.95,
            lower_bound=1.0,
            upper_bound=0.5,
            resamples=100,
            seed=0,
        )


def test_independent_interval_is_invariant_to_input_order():
    # Same multisets, different row order, same seed: the interval must be
    # identical because the independent design canonicalizes its inputs
    # before resampling positions (audit finding F8).
    baseline = [0.1, 0.4, 0.9, 1.7, 2.2, 3.9, 4.4]
    candidate = [0.6, 1.1, 1.8, 2.7, 3.3, 4.9, 5.5]

    ordered = bootstrap_median_difference_interval(
        baseline,
        candidate,
        resamples=400,
        seed=11,
    )
    permuted = bootstrap_median_difference_interval(
        list(reversed(baseline)),
        [candidate[3], candidate[0], candidate[6], candidate[2],
         candidate[5], candidate[1], candidate[4]],
        resamples=400,
        seed=11,
    )

    assert permuted == ordered


def test_resamples_below_the_decision_grade_floor_are_rejected():
    # Fewer than MIN_BOOTSTRAP_RESAMPLES draws cannot be presented as a
    # decision-grade confidence interval (audit finding F6).
    for degenerate in (1, 2, 199):
        with pytest.raises(ValueError, match="resamples"):
            bootstrap_median_difference_interval(
                [1.0, 2.0, 3.0],
                [2.0, 3.0, 4.0],
                resamples=degenerate,
            )
