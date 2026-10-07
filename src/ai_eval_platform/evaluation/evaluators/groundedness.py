
from __future__ import annotations

from ..domain import (
    EvaluationMeasurement,
    EvaluationStatus,
    EvaluationTarget,
)
from ..evaluator import Evaluator


class GroundednessEvaluator(Evaluator):
    metric_name = "groundedness"

    def __init__(self, groundedness_function):
        self._groundedness_function = groundedness_function

    def evaluate(
        self,
        target: EvaluationTarget,
    ) -> EvaluationMeasurement:

        output = target.context.output
        context_source = target.context.context_source

        if not output:
            return EvaluationMeasurement(
                metric_name=self.metric_name,
                status=EvaluationStatus.UNAVAILABLE,
                feedback="Model output is unavailable.",
            )

        if context_source is None or not context_source.items:
            return EvaluationMeasurement(
                metric_name=self.metric_name,
                status=EvaluationStatus.UNAVAILABLE,
                feedback="Retrieved context is unavailable.",
            )

        try:
            score = float(
                self._groundedness_function.evaluate(
                    output,
                    context_source.items,
                )
            )
        except Exception as exc:
            return EvaluationMeasurement(
                metric_name=self.metric_name,
                status=EvaluationStatus.ERROR,
                error_type=type(exc).__name__,
                feedback="Groundedness evaluation failed.",
            )

        score = max(0.0, min(1.0, score))

        return EvaluationMeasurement(
            metric_name=self.metric_name,
            status=EvaluationStatus.SUCCESS,
            value=score,
            score=score,
            passed=None,
            metadata={
                "context_items": len(context_source.items),
            },
        )