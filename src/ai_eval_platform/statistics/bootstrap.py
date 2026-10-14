from typing import Literal

import numpy as np
from pydantic import Field, model_validator

from .confidence import validate_confidence_level
from .descriptive import FiniteFloat, StatisticalResult, finite_result, validate_values

MIN_BOOTSTRAP_RESAMPLES = 200
"""Lowest resample count accepted for a percentile-bootstrap interval.

Intervals backed by fewer draws cannot be honestly presented as
decision-grade confidence intervals (audit finding F6): with a single
draw, for example, the "95% confidence interval" collapses to that one
draw while still carrying its nominal confidence level.
"""


class BootstrapInterval(StatisticalResult):
    method: Literal["percentile_bootstrap"]
    difference_convention: Literal["candidate_minus_baseline"]
    paired: bool
    point_estimate: FiniteFloat
    confidence_level: float = Field(allow_inf_nan=False, gt=0, lt=1)
    lower_bound: FiniteFloat
    upper_bound: FiniteFloat
    resamples: int = Field(ge=1)
    seed: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_interval_order(self):
        if self.lower_bound > self.upper_bound:
            raise ValueError("lower_bound must not exceed upper_bound")
        return self


def _validate_resamples(resamples: int) -> int:
    if isinstance(resamples, bool) or not isinstance(resamples, int):
        raise TypeError("resamples must be an integer")
    if resamples < MIN_BOOTSTRAP_RESAMPLES:
        raise ValueError(
            f"resamples must be at least {MIN_BOOTSTRAP_RESAMPLES}; "
            "fewer draws cannot be presented as a decision-grade "
            "confidence interval"
        )
    return resamples


def _validate_seed(seed: int) -> int:
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise TypeError("seed must be an integer")
    if seed < 0:
        raise ValueError("seed must be non-negative")
    return seed


def bootstrap_median_difference_interval(
    baseline: list[float] | tuple[float, ...],
    candidate: list[float] | tuple[float, ...],
    *,
    paired: bool = False,
    confidence_level: float = 0.95,
    resamples: int = 5000,
    seed: int = 0,
) -> BootstrapInterval:
    if not isinstance(paired, bool):
        raise TypeError("paired must be a boolean")
    baseline_values = validate_values(baseline, minimum_size=1)
    candidate_values = validate_values(candidate, minimum_size=1)
    if paired and len(baseline_values) != len(candidate_values):
        raise ValueError("paired samples must have equal lengths")
    confidence_level = validate_confidence_level(confidence_level)
    resamples = _validate_resamples(resamples)
    seed = _validate_seed(seed)

    baseline_array = np.asarray(baseline_values, dtype=np.float64)
    candidate_array = np.asarray(candidate_values, dtype=np.float64)
    if not paired:
        # Canonicalize the input order so that equivalent inputs in any
        # order yield identical seeded intervals for the independent
        # design: the resampling loop draws positions, so a permuted array
        # would otherwise produce different draws from the same seed
        # (audit finding F8). The paired design is not sorted here because
        # per-side sorting would break the baseline/candidate pairing.
        baseline_array = np.sort(baseline_array)
        candidate_array = np.sort(candidate_array)
    rng = np.random.default_rng(seed)

    def statistic(first: np.ndarray, second: np.ndarray) -> float:
        with np.errstate(over="ignore", invalid="ignore"):
            if paired:
                return float(np.median(second - first))
            return float(np.median(second)) - float(np.median(first))

    point_estimate = finite_result(
        statistic(baseline_array, candidate_array),
        "point_estimate",
    )
    draws = np.empty(resamples, dtype=np.float64)
    for index in range(resamples):
        if paired:
            positions = rng.integers(
                0,
                len(baseline_array),
                size=len(baseline_array),
            )
            draws[index] = statistic(
                baseline_array[positions],
                candidate_array[positions],
            )
        else:
            draws[index] = statistic(
                rng.choice(baseline_array, size=len(baseline_array), replace=True),
                rng.choice(candidate_array, size=len(candidate_array), replace=True),
            )

    lower_bound, upper_bound = np.percentile(
        draws,
        [50.0 * (1.0 - confidence_level), 50.0 * (1.0 + confidence_level)],
        method="linear",
    )

    return BootstrapInterval(
        method="percentile_bootstrap",
        difference_convention="candidate_minus_baseline",
        paired=paired,
        point_estimate=point_estimate,
        confidence_level=confidence_level,
        lower_bound=finite_result(lower_bound, "lower_bound"),
        upper_bound=finite_result(upper_bound, "upper_bound"),
        resamples=resamples,
        seed=seed,
    )
