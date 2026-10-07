from collections.abc import Callable

from ..components.types import ComponentType
from .dependency import DependencyChecker
from .liveness import LivenessChecker
from .models import HealthCheckResult
from .readiness import ReadinessChecker


class HealthChecker:

    def __init__(
        self,
        liveness: LivenessChecker,
        readiness: ReadinessChecker,
        dependency_checkers: dict[
            ComponentType,
            DependencyChecker,
        ] | None = None,
    ):
        self.liveness = liveness
        self.readiness = readiness
        self.dependency_checkers = dependency_checkers or {}

    def check(
        self,
        component: ComponentType,
        dependency_probes: dict[
            ComponentType,
            Callable[[], bool],
        ] | None = None,
    ) -> list[HealthCheckResult]:

        results = [
            self.liveness.check(component),
            self.readiness.check(component),
        ]

        for dependency, probe in (
            dependency_probes or {}
        ).items():

            checker = self.dependency_checkers.get(
                dependency,
                DependencyChecker(probe),
            )

            results.append(
                checker.check(
                    component,
                    dependency,
                )
            )

        return results