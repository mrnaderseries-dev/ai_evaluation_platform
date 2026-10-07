from collections.abc import Callable
from datetime import datetime, timezone
from time import monotonic

from ..components.types import ComponentType
from .models import (
    CheckType,
    HealthCheckResult,
    HealthStatus,
)


class DependencyChecker:

    def __init__(
        self,
        probe: Callable[[], bool],
        timeout_ms: float = 1000,
    ):
        self.probe = probe
        self.timeout_ms = timeout_ms

    def check(
        self,
        component: ComponentType,
        dependency: ComponentType,
    ) -> HealthCheckResult:

        started = monotonic()

        try:
            healthy = self.probe()

            elapsed = (monotonic() - started) * 1000

            if elapsed > self.timeout_ms:
                status = HealthStatus.TIMEOUT
            elif healthy:
                status = HealthStatus.PASS
            else:
                status = HealthStatus.FAIL

            return HealthCheckResult(
                component=component,
                check_type=CheckType.DEPENDENCY,
                dependency=dependency,
                status=status,
                checked_at=datetime.now(timezone.utc),
                duration_ms=elapsed,
                timeout_ms=self.timeout_ms,
            )

        except Exception as exc:
            elapsed = (monotonic() - started) * 1000

            return HealthCheckResult(
                component=component,
                check_type=CheckType.DEPENDENCY,
                dependency=dependency,
                status=HealthStatus.FAIL,
                checked_at=datetime.now(timezone.utc),
                duration_ms=elapsed,
                timeout_ms=self.timeout_ms,
                details={
                    "error_type": type(exc).__name__,
                },
            )