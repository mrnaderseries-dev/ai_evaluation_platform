from typing import Literal

from .confidence import ProportionConfidenceInterval, wilson_interval
from .descriptive import DescriptiveStatistics, describe
from .hypothesis import (
    CorrelationResult,
    GroupComparisonResult,
    compare_independent_groups,
    correlate,
)


class StatisticalAnalyzer:
    """Pure statistical primitives; callers must define valid sample units."""

    def describe(
        self,
        values: list[float] | tuple[float, ...],
    ) -> DescriptiveStatistics:
        return describe(values)

    def correlate(
        self,
        x: list[float] | tuple[float, ...],
        y: list[float] | tuple[float, ...],
        method: Literal["pearson", "spearman"] = "spearman",
    ) -> CorrelationResult:
        return correlate(x, y, method)

    def compare_independent_groups(
        self,
        first: list[float] | tuple[float, ...],
        second: list[float] | tuple[float, ...],
        alpha: float = 0.05,
    ) -> GroupComparisonResult:
        return compare_independent_groups(first, second, alpha)

    def proportion_interval(
        self,
        successes: int,
        sample_size: int,
        confidence_level: float = 0.95,
    ) -> ProportionConfidenceInterval:
        return wilson_interval(successes, sample_size, confidence_level)