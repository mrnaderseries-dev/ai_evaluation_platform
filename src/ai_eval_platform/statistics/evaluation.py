from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ai_eval_platform.storage.models import EvaluationResult
from ai_eval_platform.storage.repositories import EvaluationResultRepository

from .analyzer import StatisticalAnalyzer
from .confidence import (
    ProportionConfidenceInterval,
    validate_confidence_level,
)
from .descriptive import DescriptiveStatistics


class EvaluationMetricStatistics(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evaluation_run_id: str = Field(min_length=1)
    metric_name: str = Field(min_length=1)
    observation_unit: Literal["case", "run"]
    source_result_ids: tuple[str, ...]
    score_observation_count: int = Field(ge=0)
    score_statistics: DescriptiveStatistics | None = None
    pass_observation_count: int = Field(ge=0)
    passed_count: int = Field(ge=0)
    pass_rate: float | None = Field(default=None, ge=0, le=1)
    pass_rate_confidence_interval: ProportionConfidenceInterval | None = None
    limitations: tuple[str, ...]


class EvaluationStatisticsService:
    """Descriptive summaries scoped to one evaluation run and exact metric."""

    def __init__(
        self,
        session: Session,
        analyzer: StatisticalAnalyzer | None = None,
    ) -> None:
        self._results = EvaluationResultRepository(session)
        self._analyzer = analyzer or StatisticalAnalyzer()

    def summarize_evaluation_run(
        self,
        evaluation_run_id: str,
        *,
        confidence_level: float = 0.95,
    ) -> tuple[EvaluationMetricStatistics, ...]:
        if not isinstance(evaluation_run_id, str) or not evaluation_run_id.strip():
            raise ValueError("evaluation_run_id must not be empty")
        confidence_level = validate_confidence_level(confidence_level)

        rows = self._results.list_for_run(evaluation_run_id)
        grouped: dict[tuple[str, str], list[EvaluationResult]] = {}
        for row in rows:
            observation_unit = "case" if row.case_id is not None else "run"
            grouped.setdefault((row.metric_name, observation_unit), []).append(row)

        summaries = [
            self._summarize_group(
                evaluation_run_id,
                metric_name,
                observation_unit,
                group,
                confidence_level,
            )
            for (metric_name, observation_unit), group in sorted(grouped.items())
        ]
        return tuple(summaries)

    def _summarize_group(
        self,
        evaluation_run_id: str,
        metric_name: str,
        observation_unit: Literal["case", "run"],
        rows: list[EvaluationResult],
        confidence_level: float,
    ) -> EvaluationMetricStatistics:
        score_values = [row.score for row in rows if row.score is not None]
        pass_values = [row.passed for row in rows if row.passed is not None]
        passed_count = sum(pass_values)
        pass_count = len(pass_values)
        limitations = [
            "Summary is descriptive and scoped to one evaluation run and exact metric name.",
            "No cross-metric pooling, causal inference, or multiple-testing decision is performed.",
        ]
        if pass_count:
            limitations.append(
                "The Wilson interval assumes independent representative Bernoulli observations; case/run dependence and dataset sampling are not modeled."
            )

        return EvaluationMetricStatistics(
            evaluation_run_id=evaluation_run_id,
            metric_name=metric_name,
            observation_unit=observation_unit,
            source_result_ids=tuple(
                row.evaluation_result_id
                for row in sorted(rows, key=lambda item: item.evaluation_result_id)
            ),
            score_observation_count=len(score_values),
            score_statistics=(
                self._analyzer.describe(score_values)
                if score_values
                else None
            ),
            pass_observation_count=pass_count,
            passed_count=passed_count,
            pass_rate=(passed_count / pass_count if pass_count else None),
            pass_rate_confidence_interval=(
                self._analyzer.proportion_interval(
                    passed_count,
                    pass_count,
                    confidence_level,
                )
                if pass_count
                else None
            ),
            limitations=tuple(limitations),
        )