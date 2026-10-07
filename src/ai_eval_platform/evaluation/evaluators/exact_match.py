from __future__ import annotations

from ..domain import (
    EvaluationMeasurement,
    EvaluationStatus,
    EvaluationTarget,
)
from ..evaluator import Evaluator


class ExactMatchEvaluator(Evaluator):
    metric_name = "exact_match"

    def evaluate(
        self,
        target: EvaluationTarget,
    ) -> EvaluationMeasurement:

        output = target.context.output

        if output is None:
            return EvaluationMeasurement(
                metric_name=self.metric_name,
                status=EvaluationStatus.UNAVAILABLE,
                feedback="Model output is unavailable.",
            )

        criteria = target.criteria

        if criteria is None or criteria.expected_output is None:
            return EvaluationMeasurement(
                metric_name=self.metric_name,
                status=EvaluationStatus.UNAVAILABLE,
                feedback="Expected output is unavailable.",
            )

        passed = (
            sorted(
                word.strip(".,;:!?\"'")
                for word in output.lower().split()
            )
            == sorted(
                word.strip(".,;:!?\"'")
                for word in criteria.expected_output.lower().split()
            )
        )

        score = 1.0 if passed else 0.0

        return EvaluationMeasurement(
            metric_name=self.metric_name,
            status=EvaluationStatus.SUCCESS,
            value=score,
            score=score,
            passed=passed,
        )