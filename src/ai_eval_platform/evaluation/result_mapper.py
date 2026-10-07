from __future__ import annotations

from typing import Any

from ai_eval_platform.storage.models import EvaluationResult, new_id

from .domain import EvaluationMeasurement, EvaluationTarget, TokenUsage


def _token_usage_payload(
    token_usage: TokenUsage | None,
) -> dict[str, Any]:
    if token_usage is None:
        return {}

    return {
        name: value
        for name, value in (
            ("input_tokens", token_usage.input_tokens),
            ("output_tokens", token_usage.output_tokens),
            ("total_tokens", token_usage.total_tokens),
        )
        if value is not None
    }


def _metrics_payload(
    measurement: EvaluationMeasurement,
) -> dict[str, Any]:
    return {
        **measurement.metadata,
        "status": measurement.status.value,
        "value": measurement.value,
    }


def measurement_to_result(
    measurement: EvaluationMeasurement,
    target: EvaluationTarget,
    *,
    evaluation_run_id: str | None = None,
    case_id: str | None = None,
) -> EvaluationResult:
    return EvaluationResult(
        evaluation_result_id=new_id(),
        evaluation_run_id=evaluation_run_id,
        case_id=case_id,
        run_id=target.run_id,
        metric_name=measurement.metric_name,
        predicted_answer=target.context.output,
        passed=measurement.passed,
        score=measurement.score,
        latency_ms=target.context.latency_ms,
        token_usage=_token_usage_payload(target.context.token_usage),
        evaluator_feedback=measurement.feedback,
        failure_category=measurement.error_type,
        metrics=_metrics_payload(measurement),
    )
