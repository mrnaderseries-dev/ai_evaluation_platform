from __future__ import annotations

from ..domain import (
    EvaluationMeasurement,
    EvaluationStatus,
    EvaluationTarget,
)
from ..evaluator import Evaluator


class CitationEvaluator(Evaluator):
    metric_name = "citation"

    def evaluate(
        self,
        target: EvaluationTarget,
    ) -> EvaluationMeasurement:

        citations = target.context.citations

        if not citations:
            return EvaluationMeasurement(
                metric_name=self.metric_name,
                status=EvaluationStatus.UNAVAILABLE,
                feedback="No citations are available.",
            )

        valid_count = 0
        invalid_count = 0

        for citation in citations:
            if citation.source.strip():
                valid_count += 1
            else:
                invalid_count += 1

        total = len(citations)

        score = valid_count / total if total else 0.0

        return EvaluationMeasurement(
            metric_name=self.metric_name,
            status=EvaluationStatus.SUCCESS,
            value=float(total),
            score=score,
            passed=None,
            feedback=(
                f"{valid_count} of {total} citations "
                "contain a valid source."
            ),
            metadata={
                "citation_count": total,
                "valid_count": valid_count,
                "invalid_count": invalid_count,
            },
        )