from collections.abc import Iterable
from statistics import quantiles

from ..telemetry.models import RawTelemetry
from .models import SystemMetric
from .types import MetricDimension


class MetricCalculator:

    def counter_rate(
        self,
        telemetry: Iterable[RawTelemetry],
        *,
        metric_id: str,
        component,
        name: str,
        dimension: MetricDimension,
        unit: str = "per_second",
    ) -> SystemMetric:

        items = list(telemetry)

        if len(items) < 2:
            raise ValueError(
                "at least two telemetry points are required"
            )

        first = items[0]
        last = items[-1]

        if first.value is None or last.value is None:
            raise ValueError(
                "counter telemetry requires values"
            )

        elapsed = (
            last.timestamp - first.timestamp
        ).total_seconds()

        if elapsed <= 0:
            raise ValueError(
                "telemetry timestamps must increase"
            )

        rate = (
            last.value - first.value
        ) / elapsed

        return SystemMetric(
            metric_id=metric_id,
            component=component,
            metric_name=name,
            dimension=dimension,
            value=rate,
            unit=unit,
            timestamp=last.timestamp,
        )

    def percentile(
        self,
        telemetry: Iterable[RawTelemetry],
        *,
        metric_id: str,
        component,
        name: str,
        dimension: MetricDimension,
        percentile: float = 0.95,
        unit: str = "ms",
    ) -> SystemMetric:
        items = list(telemetry)

        values = [
            item.value
            for item in items
            if item.value is not None
        ]

        if not values:
            raise ValueError(
                "no numeric telemetry"
            )

        if not 0 < percentile <= 1:
            raise ValueError(
                "percentile must be in (0, 1]"
            )

        if len(values) == 1:
            value = values[0]
        else:
            cuts = quantiles(
                values,
                n=100,
                method="inclusive",
            )

            index = max(
                0,
                min(98, int(percentile * 100) - 1),
            )

            value = cuts[index]

        return SystemMetric(
            metric_id=metric_id,
            component=component,
            metric_name=name,
            dimension=dimension,
            value=float(value),
            unit=unit,
            timestamp=max(
                item.timestamp
                for item in items
            ),
        )