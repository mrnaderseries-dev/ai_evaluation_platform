from datetime import datetime, timezone

from .models import (
    ComponentHealth,
    PlatformHealth,
    SystemStatus,
)


class SystemHealthEvaluator:

    def evaluate(
        self,
        components: list[ComponentHealth],
    ) -> PlatformHealth:

        if not components:
            return PlatformHealth(
                status=SystemStatus.UNKNOWN,
                components=[],
                reasons=[
                    "no component health available"
                ],
                evaluated_at=datetime.now(timezone.utc),
            )

        unhealthy = [
            component
            for component in components
            if component.status
            == SystemStatus.UNHEALTHY
        ]

        degraded = [
            component
            for component in components
            if component.status
            == SystemStatus.DEGRADED
        ]

        unknown = [
            component
            for component in components
            if component.status
            == SystemStatus.UNKNOWN
        ]

        if unhealthy:
            status = SystemStatus.UNHEALTHY
            reasons = [
                f"{item.component.value} is unhealthy"
                for item in unhealthy
            ]

        elif degraded:
            status = SystemStatus.DEGRADED
            reasons = [
                f"{item.component.value} is degraded"
                for item in degraded
            ]

        elif unknown:
            status = SystemStatus.UNKNOWN
            reasons = [
                f"{item.component.value} has unknown health"
                for item in unknown
            ]

        else:
            status = SystemStatus.HEALTHY
            reasons = []

        return PlatformHealth(
            status=status,
            components=components,
            reasons=reasons,
            evaluated_at=datetime.now(timezone.utc),
        )