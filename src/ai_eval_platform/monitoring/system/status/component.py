from datetime import datetime, timezone

from ..components.types import ComponentType
from ..health.models import (
    HealthCheckResult,
    HealthStatus,
)
from ..metrics.models import SystemMetric
from ..metrics.types import MetricDimension
from .models import ComponentHealth, SystemStatus


class ComponentHealthEvaluator:

    def evaluate(
        self,
        component: ComponentType,
        checks: list[HealthCheckResult],
        metrics: list[SystemMetric] | None = None,
    ) -> ComponentHealth:

        metrics = metrics or []

        reasons: list[str] = []

        failed = any(
            check.status == HealthStatus.FAIL
            for check in checks
        )

        timed_out = any(
            check.status == HealthStatus.TIMEOUT
            for check in checks
        )

        if failed:
            status = SystemStatus.UNHEALTHY
            reasons.append(
                "one or more health checks failed"
            )

        elif timed_out:
            status = SystemStatus.DEGRADED
            reasons.append(
                "one or more health checks timed out"
            )

        else:
            performance_degraded = any(
                metric.dimension
                == MetricDimension.PERFORMANCE
                and metric.metadata.get("degraded") is True
                for metric in metrics
            )

            scalability_degraded = any(
                metric.dimension
                == MetricDimension.SCALABILITY
                and metric.metadata.get("degraded") is True
                for metric in metrics
            )

            if performance_degraded:
                status = SystemStatus.DEGRADED
                reasons.append(
                    "performance degradation detected"
                )

            elif scalability_degraded:
                status = SystemStatus.DEGRADED
                reasons.append(
                    "scalability degradation detected"
                )

            elif not checks:
                status = SystemStatus.UNKNOWN
                reasons.append(
                    "no health checks available"
                )

            else:
                status = SystemStatus.HEALTHY

        return ComponentHealth(
            component=component,
            status=status,
            checks=checks,
            metrics=metrics,
            reasons=reasons,
            evaluated_at=datetime.now(timezone.utc),
        )