"""Statistical primitives and evaluation-result summaries.

The package is deliberately separate from signal and relationship analysis.
It does not infer causal effects or automatically compare agents/runs.
"""

from .analyzer import StatisticalAnalyzer
from .bootstrap import BootstrapInterval, bootstrap_median_difference_interval
from .confidence import ProportionConfidenceInterval, wilson_interval
from .comparison import (
	ComparisonConfig,
	ComparisonStatus,
	EvaluationRunComparisonService,
	MetricComparison,
	MetricDirection,
	MetricRequest,
	MultipleTestingFamily,
	RunComparison,
	holm_adjust,
)
from .descriptive import DescriptiveStatistics
from .evaluation import (
	EvaluationMetricStatistics,
	EvaluationStatisticsService,
)
from .event_telemetry import (
	EventMetricStatistics,
	EventTelemetryStatisticsService,
)
from .hypothesis import CorrelationResult, GroupComparisonResult

__all__ = [
	"BootstrapInterval",
	"ComparisonConfig",
	"ComparisonStatus",
	"CorrelationResult",
	"DescriptiveStatistics",
	"EvaluationMetricStatistics",
	"EvaluationRunComparisonService",
	"EvaluationStatisticsService",
	"EventMetricStatistics",
	"EventTelemetryStatisticsService",
	"GroupComparisonResult",
	"MetricComparison",
	"MetricDirection",
	"MetricRequest",
	"MultipleTestingFamily",
	"ProportionConfidenceInterval",
	"RunComparison",
	"StatisticalAnalyzer",
	"bootstrap_median_difference_interval",
	"holm_adjust",
	"wilson_interval",
]
