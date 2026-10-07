from datetime import datetime, timezone

from ..components.types import ComponentType
from ..health.models import (
    HealthCheckResult,
    HealthStatus,
)
from .dependency_graph import DependencyGraph
from .models import Fault, FaultImpact


class FaultIsolationAnalyzer:

    def __init__(
        self,
        graph: DependencyGraph,
    ):
        self.graph = graph

    def analyze(
        self,
        checks: list[HealthCheckResult],
        fault_id: str,
    ) -> list[FaultImpact]:

        failed_components = {
            check.component
            for check in checks
            if check.status
            in {
                HealthStatus.FAIL,
                HealthStatus.TIMEOUT,
            }
        }

        results: list[FaultImpact] = []

        for component in failed_components:

            dependents = set(
                self.graph.dependents_of(component)
            )

            affected = sorted(
                failed_components
                | (
                    dependents
                    & failed_components
                ),
                key=lambda item: item.value,
            )

            unaffected = sorted(
                set(self.graph.registry.components())
                - set(affected),
                key=lambda item: item.value,
            )

            fault = Fault(
                fault_id=fault_id,
                component=component,
                reason="component health failure observed",
                confidence=0.5,
                detected_at=datetime.now(timezone.utc),
            )

            results.append(
                FaultImpact(
                    fault=fault,
                    affected_components=affected,
                    unaffected_components=unaffected,
                    cascading=len(affected) > 1,
                    shared_dependency=len(
                        failed_components
                    ) > 1,
                )
            )

        return results