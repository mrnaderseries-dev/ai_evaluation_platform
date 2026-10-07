from __future__ import annotations


class EvaluationError(Exception):
    """Base exception for evaluation failures."""


class InvalidEvaluationTargetError(EvaluationError):
    """The evaluation target is structurally invalid."""


class MissingEvaluationDataError(EvaluationError):
    """Required evaluation data is unavailable."""


class EvaluationConfigurationError(EvaluationError):
    """Evaluator configuration is invalid."""


class EvaluationExecutionError(EvaluationError):
    """An evaluator failed while computing a measurement."""


class EvaluationInfrastructureError(EvaluationError):
    """An external dependency required by evaluation failed."""