from dataclasses import dataclass

from ..components.types import ComponentType
from ..metrics.types import MetricDimension
from .models import AlertSeverity


@dataclass(frozen=True)
class MetricThresholdRule:

    component: ComponentType

    metric_name: str

    dimension: MetricDimension

    threshold: float

    severity: AlertSeverity

    comparison: str = "gt"

    def matches(
        self,
        value: float,
    ) -> bool:

        if self.comparison == "gt":
            return value > self.threshold

        if self.comparison == "gte":
            return value >= self.threshold

        if self.comparison == "lt":
            return value < self.threshold

        if self.comparison == "lte":
            return value <= self.threshold

        raise ValueError(
            f"unsupported comparison: {self.comparison}"
        )