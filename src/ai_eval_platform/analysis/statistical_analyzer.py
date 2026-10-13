from math import isfinite
from numbers import Real
from typing import Annotated, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator
from scipy.stats import mannwhitneyu, pearsonr, spearmanr


FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]


class StatisticalResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DescriptiveStatistics(StatisticalResult):
    sample_size: int = Field(ge=1)
    mean: FiniteFloat
    median: FiniteFloat
    
    standard_deviation: FiniteFloat | None
    minimum: FiniteFloat
    maximum: FiniteFloat
    p95: FiniteFloat


class CorrelationResult(StatisticalResult):
    method: Literal["pearson", "spearman"]
    sample_size: int = Field(ge=3)
    coefficient: FiniteFloat = Field(ge=-1, le=1)
    p_value: FiniteFloat = Field(ge=0, le=1)
    interpretation_limitations: tuple[str, ...] = (
        "Correlation does not establish causation.",
        "A non-significant result does not prove independence.",
        "The result depends on the sampling design and test assumptions.",
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

    # Positive means the first group tends to have larger values.
    rank_biserial_correlation: FiniteFloat = Field(ge=-1, le=1)

    interpretation_limitations: tuple[str, ...] = (
        "Mann-Whitney tests distributions, not medians alone.",
        "Statistical significance does not establish causation.",
        "Statistical significance does not imply practical importance.",
        "Observations must be independent across and within groups.",
    )

    @model_validator(mode="after")
    def validate_significance_decision(self):
        expected = self.p_value < self.alpha
        if self.statistically_significant != expected:
            raise ValueError(
                "statistically_significant must equal p_value < alpha"
            )
        return self


class StatisticalAnalyzer:
    """Validated descriptive statistics and inferential tests."""

    @staticmethod
    def _validate_values(
        values: list[float] | tuple[float, ...],
        minimum_size: int,
    ) -> tuple[float, ...]:
        if not isinstance(values, (list, tuple)):
            raise TypeError("values must be a list or tuple")

        if len(values) < minimum_size:
            raise ValueError(
                f"At least {minimum_size} observations are required"
            )

        validated: list[float] = []

        for value in values:
            if isinstance(value, bool) or not isinstance(value, Real):
                raise TypeError("All observations must be real numbers")

            try:
                numeric_value = float(value)
            except (ValueError, OverflowError) as exc:
                raise ValueError(
                    "Observations must be representable as finite floats"
                ) from exc

            if not isfinite(numeric_value):
                raise ValueError("Observations must be finite")

            validated.append(numeric_value)

        return tuple(validated)

    @staticmethod
    def _finite_result(value: float, name: str) -> float:
        result = float(value)
        if not isfinite(result):
            raise ArithmeticError(
                f"{name} is not finite; inspect the input scale"
            )
        return result

    def describe(
        self,
        values: list[float] | tuple[float, ...],
    ) -> DescriptiveStatistics:
        data = self._validate_values(values, minimum_size=1)
        array = np.asarray(data, dtype=np.float64)

        with np.errstate(over="ignore", invalid="ignore"):
            mean = self._finite_result(np.mean(array), "mean")
            median = self._finite_result(np.median(array), "median")
            minimum = self._finite_result(np.min(array), "minimum")
            maximum = self._finite_result(np.max(array), "maximum")
            p95 = self._finite_result(
                np.percentile(array, 95, method="linear"),
                "p95",
            )

            std = (
                self._finite_result(
                    np.std(array, ddof=1),
                    "standard_deviation",
                )
                if len(data) > 1
                else None
            )

        return DescriptiveStatistics(
            sample_size=len(data),
            mean=mean,
            median=median,
            standard_deviation=std,
            minimum=minimum,
            maximum=maximum,
            p95=p95,
        )

    def correlate(
        self,
        x: list[float] | tuple[float, ...],
        y: list[float] | tuple[float, ...],
        method: Literal["pearson", "spearman"] = "spearman",
    ) -> CorrelationResult:
        if method not in ("pearson", "spearman"):
            raise ValueError("method must be 'pearson' or 'spearman'")

        x_values = self._validate_values(x, minimum_size=3)
        y_values = self._validate_values(y, minimum_size=3)

        if len(x_values) != len(y_values):
            raise ValueError("x and y must have equal lengths")

        if len(set(x_values)) < 2 or len(set(y_values)) < 2:
            raise ValueError(
                "Both variables must contain at least two distinct values"
            )

        with np.errstate(over="ignore", invalid="ignore"):
            if method == "pearson":
                result = pearsonr(x_values, y_values)
            else:
                result = spearmanr(x_values, y_values)

        coefficient = self._finite_result(
            result.statistic, "correlation coefficient"
        )
        p_value = self._finite_result(result.pvalue, "p_value")

        return CorrelationResult(
            method=method,
            sample_size=len(x_values),
            coefficient=coefficient,
            p_value=p_value,
        )

    def compare_independent_groups(
        self,
        first: list[float] | tuple[float, ...],
        second: list[float] | tuple[float, ...],
        alpha: float = 0.05,
    ) -> GroupComparisonResult:
        first_values = self._validate_values(first, minimum_size=1)
        second_values = self._validate_values(second, minimum_size=1)

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

        first_median = self._finite_result(
            np.median(first_values), "first_group_median"
        )
        second_median = self._finite_result(
            np.median(second_values), "second_group_median"
        )
        p_value = self._finite_result(result.pvalue, "p_value")
        statistic = self._finite_result(result.statistic, "statistic")

        denominator = len(first_values) * len(second_values)
        effect_size = self._finite_result(
            2.0 * statistic / denominator - 1.0,
            "rank_biserial_correlation",
        )

        return GroupComparisonResult(
            test_name="mann_whitney_u",
            first_group_size=len(first_values),
            second_group_size=len(second_values),
            first_group_median=first_median,
            second_group_median=second_median,
            median_difference=self._finite_result(
                second_median - first_median,
                "median_difference",
            ),
            statistic=statistic,
            p_value=p_value,
            alpha=alpha,
            statistically_significant=p_value < alpha,
            rank_biserial_correlation=effect_size,
        )