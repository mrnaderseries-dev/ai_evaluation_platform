from __future__ import annotations

from ..domain import (
    EvaluationMeasurement,
    EvaluationStatus,
    EvaluationTarget,
)
from ..evaluator import Evaluator
from ..engines.embedding_similarity import EmbeddingSimilarityEngine


class SemanticSimilarityEvaluator(Evaluator):
    metric_name = "semantic_similarity"

    def __init__(self, engine: EmbeddingSimilarityEngine):
        self._engine = engine

    def evaluate(
        self,
        target: EvaluationTarget,
    ) -> EvaluationMeasurement:

        output = target.context.output

        if not output:
            return EvaluationMeasurement(
                metric_name=self.metric_name,
                status=EvaluationStatus.UNAVAILABLE,
                feedback="Model output is unavailable.",
            )

        if target.criteria is None:
            return EvaluationMeasurement(
                metric_name=self.metric_name,
                status=EvaluationStatus.UNAVAILABLE,
                feedback="Evaluation criteria are unavailable.",
            )

        expected = target.criteria.expected_output

        if not expected:
            return EvaluationMeasurement(
                metric_name=self.metric_name,
                status=EvaluationStatus.UNAVAILABLE,
                feedback="Expected output is unavailable.",
            )

        try:
            similarity = self._engine.compare(
                output,
                expected,
            )
        except Exception as exc:
            return EvaluationMeasurement(
                metric_name=self.metric_name,
                status=EvaluationStatus.ERROR,
                error_type=type(exc).__name__,
                feedback="Semantic similarity computation failed.",
            )

        return EvaluationMeasurement(
            metric_name=self.metric_name,
            status=EvaluationStatus.SUCCESS,
            value=similarity,
            score=similarity,
        )