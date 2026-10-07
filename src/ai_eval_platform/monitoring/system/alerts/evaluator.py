from datetime import datetime, timezone
from uuid import uuid4

from ..metrics.models import SystemMetric
from ..status.models import (
    ComponentHealth,
    PlatformHealth,
    SystemStatus,
)
from .models import (
    Alert,
    AlertSeverity,
    AlertStatus,
)
from .rules import MetricThresholdRule


class AlertEvaluator:

    def evaluate_metrics(
        self,
        metrics: list[SystemMetric],
        rules: list[MetricThresholdRule],
    ) -> list[Alert]:

        alerts: list[Alert] = []

        for metric in metrics:

            for rule in rules:

                if (
                    metric.component
                    != rule.component
                ):
                    continue

                if (
                    metric.metric_name
                    != rule.metric_name
                ):
                    continue

                if (
                    metric.dimension
                    != rule.dimension
                ):
                    continue

                if not rule.matches(metric.value):
                    continue

                alerts.append(
                    Alert(
                        alert_id=str(uuid4()),
                        component=metric.component,
                        severity=AlertSeverity(
                            rule.severity
                        ),
                        status=AlertStatus.FIRING,
                        condition=(
                            f"{metric.metric_name} "
                            f"{rule.comparison} "
                            f"{rule.threshold}"
                        ),
                        message=(
                            f"{metric.component.value} "
                            f"{metric.metric_name} "
                            "crossed threshold"
                        ),
                        created_at=datetime.now(
                            timezone.utc
                        ),
                    )
                )

        return alerts

    def evaluate_health(
        self,
        health: ComponentHealth | PlatformHealth,
    ) -> list[Alert]:

        component = (
            health.component
            if isinstance(
                health,
                ComponentHealth,
            )
            else None
        )

        if health.status == SystemStatus.UNHEALTHY:
            return [
                Alert(
                    alert_id=str(uuid4()),
                    component=component,
                    severity=AlertSeverity.CRITICAL,
                    status=AlertStatus.FIRING,
                    condition="health == unhealthy",
                    message="system health is unhealthy",
                    created_at=datetime.now(
                        timezone.utc
                    ),
                )
            ]

        if health.status == SystemStatus.DEGRADED:
            return [
                Alert(
                    alert_id=str(uuid4()),
                    component=component,
                    severity=AlertSeverity.WARNING,
                    status=AlertStatus.FIRING,
                    condition="health == degraded",
                    message="system health is degraded",
                    created_at=datetime.now(
                        timezone.utc
                    ),
                )
            ]

        return []