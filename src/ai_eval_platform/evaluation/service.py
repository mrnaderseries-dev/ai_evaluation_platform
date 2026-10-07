from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ai_eval_platform.storage.models import EvaluationResult, EvaluationRun
from ai_eval_platform.storage.repositories import (
    EvaluationCaseRepository,
    EvaluationResultRepository,
    EvaluationRunRepository,
    EventRepository,
    RunRepository,
)

from .domain import (
    EvaluationCriteria,
    EvaluationMeasurement,
    EvaluationTarget,
)
from .errors import (
    EvaluationConfigurationError,
    InvalidEvaluationTargetError,
)
from .evaluator import Evaluator
from .result_mapper import measurement_to_result
from .runner import EvaluationRunner
from .target_builder import EvaluationTargetBuilder


@dataclass(frozen=True)
class _RunSummary:
    total_cases: int
    passed_cases: int
    failed_cases: int
    metrics: dict[str, Any]


def _summarize(
    results: Sequence[EvaluationResult],
) -> _RunSummary:
    entities: dict[tuple[str | None, str], list[EvaluationResult]] = {}

    for result in results:
        entities.setdefault(
            (result.case_id, result.run_id),
            [],
        ).append(result)

    passed_cases = 0
    failed_cases = 0

    for group in entities.values():
        verdicts = {
            result.passed
            for result in group
            if result.passed is not None
        }

        if False in verdicts:
            failed_cases += 1
        elif True in verdicts:
            passed_cases += 1

    status_counts = {"success": 0, "unavailable": 0, "error": 0}
    metric_summaries: dict[str, dict[str, Any]] = {}

    for result in results:
        payload = result.metrics or {}
        status = payload.get("status")

        if status in status_counts:
            status_counts[status] += 1

        metric_summary = metric_summaries.setdefault(
            result.metric_name,
            {"count": 0, "passed": 0, "failed": 0, "scores": []},
        )
        metric_summary["count"] += 1

        if result.passed is True:
            metric_summary["passed"] += 1
        elif result.passed is False:
            metric_summary["failed"] += 1

        if result.score is not None:
            metric_summary["scores"].append(result.score)

    for metric_summary in metric_summaries.values():
        scores = metric_summary.pop("scores")
        metric_summary["mean_score"] = (
            sum(scores) / len(scores) if scores else None
        )

    return _RunSummary(
        total_cases=len(entities),
        passed_cases=passed_cases,
        failed_cases=failed_cases,
        metrics={
            "result_count": len(results),
            "status_counts": status_counts,
            "metric_summaries": metric_summaries,
        },
    )


class EvaluationService:
    """Coordinates target construction, evaluator execution and result persistence."""

    def __init__(
        self,
        *,
        session_factory: Callable[
            [], AbstractContextManager[Session]
        ],
        evaluators: Sequence[Evaluator] = (),
        runner: EvaluationRunner | None = None,
        target_builder: EvaluationTargetBuilder | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._evaluator_by_metric: dict[str, Evaluator] = {}

        for evaluator in evaluators:
            metric_name = evaluator.metric_name

            if metric_name in self._evaluator_by_metric:
                raise EvaluationConfigurationError(
                    f"Multiple evaluators are configured for metric '{metric_name}'."
                )

            self._evaluator_by_metric[metric_name] = evaluator

        self._runner = runner or EvaluationRunner()
        self._target_builder = target_builder or EvaluationTargetBuilder()
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def evaluate_run(
        self,
        run_id: str,
        *,
        metrics: Sequence[str] | None = None,
        evaluation_run_id: str | None = None,
        case_id: str | None = None,
        criteria: EvaluationCriteria | None = None,
    ) -> list[EvaluationMeasurement]:
        evaluators = self._resolve_evaluators(metrics)

        if case_id is not None and evaluation_run_id is None:
            raise InvalidEvaluationTargetError(
                f"Case '{case_id}' cannot be evaluated "
                "outside an evaluation run."
            )

        with self._session_factory() as session:
            target = self._build_target(
                session,
                run_id=run_id,
                evaluation_run_id=evaluation_run_id,
                case_id=case_id,
                criteria=criteria,
            )

        measurements = [
            self._runner.run(evaluator, target)
            for evaluator in evaluators
        ]

        results = [
            measurement_to_result(
                measurement,
                target,
                evaluation_run_id=evaluation_run_id,
                case_id=case_id,
            )
            for measurement in measurements
        ]

        with self._session_factory() as session:
            EvaluationResultRepository(session).upsert_many(results)

        return measurements

    def start_evaluation_run(
        self,
        evaluation_run_id: str,
    ) -> EvaluationRun:
        with self._session_factory() as session:
            return EvaluationRunRepository(session).start(
                evaluation_run_id,
                status="running",
                started_at=self._clock(),
            )

    def complete_evaluation_run(
        self,
        evaluation_run_id: str,
    ) -> EvaluationRun:
        return self._finalize_evaluation_run(
            evaluation_run_id,
            status="completed",
        )

    def fail_evaluation_run(
        self,
        evaluation_run_id: str,
    ) -> EvaluationRun:
        return self._finalize_evaluation_run(
            evaluation_run_id,
            status="failed",
        )

    def _finalize_evaluation_run(
        self,
        evaluation_run_id: str,
        *,
        status: str,
    ) -> EvaluationRun:
        with self._session_factory() as session:
            EvaluationRunRepository(session).get_required(
                evaluation_run_id
            )

            results = EvaluationResultRepository(session).list_for_run(
                evaluation_run_id
            )
            summary = _summarize(results)

            return EvaluationRunRepository(session).finalize(
                evaluation_run_id,
                status=status,
                completed_at=self._clock(),
                total_cases=summary.total_cases,
                passed_cases=summary.passed_cases,
                failed_cases=summary.failed_cases,
                metrics=summary.metrics,
            )

    def _build_target(
        self,
        session: Session,
        *,
        run_id: str,
        evaluation_run_id: str | None,
        case_id: str | None,
        criteria: EvaluationCriteria | None,
    ) -> EvaluationTarget:
        run = RunRepository(session).get_required(run_id)
        events = EventRepository(session).list_for_run(run_id)

        evaluation_run = None

        if evaluation_run_id is not None:
            evaluation_run = EvaluationRunRepository(session).get_required(
                evaluation_run_id
            )

        resolved_criteria = criteria

        if case_id is not None:
            case = EvaluationCaseRepository(session).get_required(case_id)

            if (
                evaluation_run is not None
                and evaluation_run.dataset_version_id is not None
                and case.dataset_version_id
                != evaluation_run.dataset_version_id
            ):
                raise InvalidEvaluationTargetError(
                    f"Case '{case_id}' does not belong to the dataset "
                    f"version of evaluation run '{evaluation_run_id}'."
                )

            if resolved_criteria is None:
                resolved_criteria = self._target_builder.criteria_from_case(
                    case
                )

        return self._target_builder.build(
            run,
            events,
            resolved_criteria,
        )

    def _resolve_evaluators(
        self,
        metrics: Sequence[str] | None,
    ) -> list[Evaluator]:
        if metrics is None:
            evaluators = list(self._evaluator_by_metric.values())

            if not evaluators:
                raise EvaluationConfigurationError(
                    "No evaluators are configured."
                )

            return evaluators

        resolved: list[Evaluator] = []

        for metric_name in metrics:
            evaluator = self._evaluator_by_metric.get(metric_name)

            if evaluator is None:
                raise EvaluationConfigurationError(
                    f"No evaluator is configured for metric '{metric_name}'."
                )

            resolved.append(evaluator)

        if not resolved:
            raise EvaluationConfigurationError(
                "No evaluation metrics were requested."
            )

        return resolved



