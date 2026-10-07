from __future__ import annotations

from ..domain import (
    EvaluationMeasurement,
    EvaluationStatus,
    EvaluationTarget,
)
from ..evaluator import Evaluator


class ToolUsageEvaluator(Evaluator):
    metric_name = "tool_usage"

    def evaluate(
        self,
        target: EvaluationTarget,
    ) -> EvaluationMeasurement:

        tool_names = {
            call.name
            for call in target.context.tool_calls
        }

        criteria = target.criteria

        if criteria is None:
            return EvaluationMeasurement(
                metric_name=self.metric_name,
                status=EvaluationStatus.SUCCESS,
                value=float(len(tool_names)),
            )

        required = set(criteria.required_tools)
        forbidden = set(criteria.forbidden_tools)

        missing = required - tool_names
        used_forbidden = forbidden & tool_names

        passed = not missing and not used_forbidden

        feedback = None

        if not passed:
            feedback = (
                f"Missing required tools: {sorted(missing)}; "
                f"Forbidden tools used: {sorted(used_forbidden)}"
            )

        return EvaluationMeasurement(
            metric_name=self.metric_name,
            status=EvaluationStatus.SUCCESS,
            value=float(len(tool_names)),
            score=1.0 if passed else 0.0,
            passed=passed,
            feedback=feedback,
        )