from __future__ import annotations

from src.ai_eval_platform.evaluation.domain import (
    EvaluationContext,
    EvaluationCriteria,
    EvaluationMeasurement,
    EvaluationStatus,
    EvaluationTarget,
    TokenUsage,
)
from src.ai_eval_platform.evaluation.result_mapper import (
    measurement_to_result,
)


def make_target() -> EvaluationTarget:
    return EvaluationTarget(
        run_id="run-1",
        context=EvaluationContext(
            input="What is the capital of France?",
            output="The capital of France is Paris.",
            token_usage=TokenUsage(
                input_tokens=20,
                output_tokens=None,
                total_tokens=20,
            ),
            latency_ms=820,
        ),
        criteria=EvaluationCriteria(
            expected_output="The capital of France is Paris.",
        ),
    )


def test_maps_measurement_and_target_fields():
    measurement = EvaluationMeasurement(
        metric_name="exact_match",
        status=EvaluationStatus.SUCCESS,
        value=1.0,
        score=1.0,
        passed=True,
        feedback="Exact match.",
    )

    result = measurement_to_result(
        measurement,
        make_target(),
        evaluation_run_id="erun-1",
        case_id="case-1",
    )

    assert result.evaluation_result_id
    assert result.evaluation_run_id == "erun-1"
    assert result.case_id == "case-1"
    assert result.run_id == "run-1"
    assert result.metric_name == "exact_match"
    assert result.predicted_answer == "The capital of France is Paris."
    assert result.passed is True
    assert result.score == 1.0
    assert result.latency_ms == 820
    assert result.token_usage == {
        "input_tokens": 20,
        "total_tokens": 20,
    }
    assert result.evaluator_feedback == "Exact match."
    assert result.failure_category is None
    assert result.metrics["status"] == "success"
    assert result.metrics["value"] == 1.0


def test_online_identity_leaves_evaluation_run_and_case_null():
    measurement = EvaluationMeasurement(
        metric_name="latency",
        status=EvaluationStatus.SUCCESS,
        value=820.0,
    )

    result = measurement_to_result(measurement, make_target())

    assert result.evaluation_run_id is None
    assert result.case_id is None
    assert result.run_id == "run-1"


def test_error_measurement_maps_error_type_to_failure_category():
    measurement = EvaluationMeasurement(
        metric_name="groundedness",
        status=EvaluationStatus.ERROR,
        error_type="RuntimeError",
        feedback="Groundedness evaluation failed.",
    )

    result = measurement_to_result(measurement, make_target())

    assert result.failure_category == "RuntimeError"
    assert result.evaluator_feedback == "Groundedness evaluation failed."
    assert result.passed is None
    assert result.score is None
    assert result.metrics["status"] == "error"
    assert result.metrics["value"] is None


def test_unavailable_measurement_has_no_failure_category():
    measurement = EvaluationMeasurement(
        metric_name="exact_match",
        status=EvaluationStatus.UNAVAILABLE,
        feedback="Expected output is unavailable.",
    )

    result = measurement_to_result(measurement, make_target())

    assert result.failure_category is None
    assert result.passed is None
    assert result.score is None
    assert result.metrics["status"] == "unavailable"
    assert result.metrics["value"] is None


def test_measurement_metadata_is_persisted_in_metrics():
    measurement = EvaluationMeasurement(
        metric_name="retrieval",
        status=EvaluationStatus.SUCCESS,
        value=0.5,
        metadata={"retrieved_count": 3},
    )

    result = measurement_to_result(measurement, make_target())

    assert result.metrics["retrieved_count"] == 3
    assert result.metrics["status"] == "success"
    assert result.metrics["value"] == 0.5
