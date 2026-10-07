from __future__ import annotations

from ..domain import (
    EvaluationMeasurement,
    EvaluationStatus,
    EvaluationTarget,
)
from ..evaluator import Evaluator


class LatencyEvaluator(Evaluator):
    metric_name = "latency"

    def evaluate(
        self,
        target: EvaluationTarget,
    ) -> EvaluationMeasurement:

        latency_ms = target.context.latency_ms
        phases = target.context.latency_phases

        if latency_ms is None:
            return EvaluationMeasurement(
                metric_name=self.metric_name,
                status=EvaluationStatus.UNAVAILABLE,
                feedback="Total latency is unavailable.",
            )

        phase_data: dict[str, dict[str, object]] = {}

        for phase in phases:
            percentage = (
                (phase.duration_ms / latency_ms) * 100
                if latency_ms > 0
                else None
            )

            phase_data[phase.name] = {
                "duration_ms": phase.duration_ms,
                "percentage": percentage,
                "metadata": phase.metadata,
            }

        passed: bool | None = None

        if (
            target.criteria is not None
            and target.criteria.max_latency_ms is not None
        ):
            passed = latency_ms <= target.criteria.max_latency_ms

        return EvaluationMeasurement(
            metric_name=self.metric_name,
            status=EvaluationStatus.SUCCESS,
            value=float(latency_ms),
            passed=passed,
            metadata={
                "total_latency_ms": latency_ms,
                "phases": phase_data,
            },
        )