from __future__ import annotations

from abc import ABC, abstractmethod

from .domain import EvaluationMeasurement, EvaluationTarget


class Evaluator(ABC):
    metric_name: str

    @abstractmethod
    def evaluate(
        self,
        target: EvaluationTarget,
    ) -> EvaluationMeasurement:
        ...