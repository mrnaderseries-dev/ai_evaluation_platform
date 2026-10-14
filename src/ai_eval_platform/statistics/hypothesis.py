from math import isfinite
from numbers import Real
from typing import Literal

import numpy as np
from pydantic import Field, model_validator
from scipy.stats import mannwhitneyu, pearsonr, spearmanr

from .descriptive import (
    FiniteFloat,
    StatisticalResult,
    finite_result,
    validate_values,
)
from .effect_size import rank_biserial_correlation


class CorrelationResult(StatisticalResult):
    method: Literal["pearson", "spearman"]
    sample_size: int = Field(ge=3)
    coefficient: FiniteFloat = Field(ge=-1, le=1)
    p_value: FiniteFloat = Field(ge=0, le=1)
    interpretation_limitations: tuple[str, ...] = (
        "Correlation does not establish causation.",
        "A non-significant result does not prove independence.",
        "The result depends on the sampling design and test assumptions.",
        "No multiple-comparison correction is applied.",
    )


class GroupComparisonResult(StatisticalResult):
    test_name: Literal["mann_whitney_u"]
    first_group_size: int = Field(ge=1)
    second_group_size: int = Field(ge=1)
    first_group_median: FiniteFloat
    second_group_median: FiniteFloat
    median_difference: FiniteFloat
    statistic: FiniteFloat = Field(ge=0)
    p_value: FiniteFloat = Field(ge=0, le=1)
    alpha: FiniteFloat = Field(gt=0, lt=1)
    statistically_significant: bool
    rank_biserial_correlation: FiniteFloat = Field(ge=-1, le=1)
    interpretation_limitations: tuple[str, ...] = (
        "Mann-Whitney tests distributions, not medians alone.",
        "Statistical significance does not establish causation.",
        "Statistical significance does not imply practical importance.",
        "Observations must be independent across and within groups.",
        "No multiple-comparison correction is applied.",
    )

    @model_validator(mode="after")
    def validate_significance_decision(self):
        if self.statistically_significant != (self.p_value < self.alpha):
            raise ValueError(
                "statistically_significant must equal p_value < alpha"
            )
        return self


def correlate(
    x: list[float] | tuple[float, ...],
    y: list[float] | tuple[float, ...],
    method: Literal["pearson", "spearman"] = "spearman",
) -> CorrelationResult:
    if method not in ("pearson", "spearman"):
        raise ValueError("method must be 'pearson' or 'spearman'")
    x_values = validate_values(x, minimum_size=3)
    y_values = validate_values(y, minimum_size=3)
    if len(x_values) != len(y_values):
        raise ValueError("x and y must have equal lengths")
    if len(set(x_values)) < 2 or len(set(y_values)) < 2:
        raise ValueError("Both variables must contain at least two distinct values")

    with np.errstate(over="ignore", invalid="ignore"):
        result = (
            pearsonr(x_values, y_values)
            if method == "pearson"
            else spearmanr(x_values, y_values)
        )
    return CorrelationResult(
        method=method,
        sample_size=len(x_values),
        coefficient=finite_result(result.statistic, "correlation coefficient"),
        p_value=finite_result(result.pvalue, "p_value"),
    )


def compare_independent_groups(
    first: list[float] | tuple[float, ...],
    second: list[float] | tuple[float, ...],
    alpha: float = 0.05,
) -> GroupComparisonResult:
    first_values = validate_values(first, minimum_size=1)
    second_values = validate_values(second, minimum_size=1)
    if isinstance(alpha, bool) or not isinstance(alpha, Real):
        raise TypeError("alpha must be a real number")
    alpha = float(alpha)
    if not isfinite(alpha) or not 0 < alpha < 1:
        raise ValueError("alpha must be finite and between 0 and 1")

    result = mannwhitneyu(
        first_values,
        second_values,
        alternative="two-sided",
        method="auto",
    )
    first_median = finite_result(np.median(first_values), "first_group_median")
    second_median = finite_result(np.median(second_values), "second_group_median")
    p_value = finite_result(result.pvalue, "p_value")
    statistic = finite_result(result.statistic, "statistic")

    return GroupComparisonResult(
        test_name="mann_whitney_u",
        first_group_size=len(first_values),
        second_group_size=len(second_values),
        first_group_median=first_median,
        second_group_median=second_median,
        median_difference=finite_result(
            second_median - first_median,
            "median_difference",
        ),
        statistic=statistic,
        p_value=p_value,
        alpha=alpha,
        statistically_significant=p_value < alpha,
        rank_biserial_correlation=rank_biserial_correlation(
            statistic,
            len(first_values),
            len(second_values),
        ),
    )