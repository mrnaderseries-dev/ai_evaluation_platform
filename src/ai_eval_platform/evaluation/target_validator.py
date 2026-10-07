from __future__ import annotations

from .domain import EvaluationTarget
from .errors import InvalidEvaluationTargetError


class EvaluationTargetValidator:

    def validate(self, target: EvaluationTarget) -> None:
        if target is None:
            raise InvalidEvaluationTargetError(
                "Evaluation target is missing."
            )

        if not isinstance(target.run_id, str) or not target.run_id.strip():
            raise InvalidEvaluationTargetError(
                "Evaluation target has an invalid run_id."
            )

        if target.context is None:
            raise InvalidEvaluationTargetError(
                "Evaluation target is missing context."
            )