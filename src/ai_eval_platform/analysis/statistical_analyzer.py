"""Backward-compatible import path for the dedicated statistics package."""

from ai_eval_platform.statistics.analyzer import StatisticalAnalyzer
from ai_eval_platform.statistics.confidence import ProportionConfidenceInterval
from ai_eval_platform.statistics.descriptive import (
    DescriptiveStatistics,
    FiniteFloat,
    StatisticalResult,
)
from ai_eval_platform.statistics.hypothesis import (
    CorrelationResult,
    GroupComparisonResult,
)

__all__ = [
    "CorrelationResult",
    "DescriptiveStatistics",
    "FiniteFloat",
    "GroupComparisonResult",
    "ProportionConfidenceInterval",
    "StatisticalAnalyzer",
    "StatisticalResult",
]