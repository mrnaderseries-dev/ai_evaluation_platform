from __future__ import annotations

from ..domain import (
    EvaluationMeasurement,
    EvaluationStatus,
    EvaluationTarget,
)
from ..evaluator import Evaluator


class RetrievalEvaluator(Evaluator):
    metric_name = "retrieval"

    def evaluate(
        self,
        target: EvaluationTarget,
    ) -> EvaluationMeasurement:

        context_source = target.context.context_source

        if context_source is None:
            return EvaluationMeasurement(
                metric_name=self.metric_name,
                status=EvaluationStatus.UNAVAILABLE,
                feedback="Retrieval context is unavailable.",
            )

        retrieved = {
            str(item)
            for item in context_source.items
        }

        criteria = target.criteria

        if criteria is None:
            return EvaluationMeasurement(
                metric_name=self.metric_name,
                status=EvaluationStatus.SUCCESS,
                value=float(len(retrieved)),
                metadata={
                    "retrieved_count": len(retrieved),
                    "expected_sources_available": False,
                },
            )

        expected = {
            str(source)
            for source in criteria.expected_context_sources
        }

        if not expected:
            return EvaluationMeasurement(
                metric_name=self.metric_name,
                status=EvaluationStatus.SUCCESS,
                value=float(len(retrieved)),
                metadata={
                    "retrieved_count": len(retrieved),
                    "expected_sources_available": False,
                },
            )

        matched = retrieved & expected

        recall = len(matched) / len(expected)

        return EvaluationMeasurement(
            metric_name=self.metric_name,
            status=EvaluationStatus.SUCCESS,
            value=recall,
            score=recall,
            passed=None,
            feedback=(
                f"Retrieved {len(matched)} of "
                f"{len(expected)} expected sources."
            ),
            metadata={
                "retrieved_count": len(retrieved),
                "expected_count": len(expected),
                "matched_count": len(matched),
                "missing_sources": sorted(expected - retrieved),
            },
        )