from __future__ import annotations

import logging

from .domain import (
    EvaluationMeasurement,
    EvaluationStatus,
    EvaluationTarget,
)
from .errors import (
    EvaluationConfigurationError,
    EvaluationExecutionError,
    EvaluationInfrastructureError,
    InvalidEvaluationTargetError,
)
from .evaluator import Evaluator
from .target_validator import EvaluationTargetValidator

logger = logging.getLogger(__name__)


class EvaluationRunner:

    def __init__(
        self,
        target_validator: EvaluationTargetValidator | None = None,
    ) -> None:
        self._target_validator = (
            target_validator
            or EvaluationTargetValidator()
        )

    def run(
        self,
        evaluator: Evaluator,
        target: EvaluationTarget,
    ) -> EvaluationMeasurement:

        try:
            self._target_validator.validate(target)

        except InvalidEvaluationTargetError:
            raise

        try:
            return evaluator.evaluate(target)

        except EvaluationConfigurationError as exc:
            logger.exception(
                "Evaluation configuration failed: metric=%s",
                evaluator.metric_name,
            )

            return EvaluationMeasurement(
                metric_name=evaluator.metric_name,
                status=EvaluationStatus.ERROR,
                error_type=type(exc).__name__,
                feedback="Evaluator configuration is invalid.",
            )

        except EvaluationInfrastructureError as exc:
            logger.exception(
                "Evaluation infrastructure failed: metric=%s",
                evaluator.metric_name,
            )

            return EvaluationMeasurement(
                metric_name=evaluator.metric_name,
                status=EvaluationStatus.ERROR,
                error_type=type(exc).__name__,
                feedback="Evaluation infrastructure failed.",
            )

        except Exception as exc:
            logger.exception(
                "Evaluator execution failed: metric=%s",
                evaluator.metric_name,
            )

            return EvaluationMeasurement(
                metric_name=evaluator.metric_name,
                status=EvaluationStatus.ERROR,
                error_type=type(exc).__name__,
                feedback="Evaluator execution failed.",
            )