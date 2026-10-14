from collections.abc import Sequence
from dataclasses import dataclass, field
from math import isfinite
from numbers import Real
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from scipy.stats import wilcoxon
from sqlalchemy.orm import Session

from ai_eval_platform.storage.models import Analysis, DifferentialAnalysis, new_id
from ai_eval_platform.storage.repositories import (
    AnalysisRepository,
    DifferentialAnalysisRepository,
    EvaluationResultRepository,
    EvaluationRunRepository,
)

from .bootstrap import (
    MIN_BOOTSTRAP_RESAMPLES,
    BootstrapInterval,
    bootstrap_median_difference_interval,
)
from .confidence import validate_confidence_level
from .descriptive import FiniteFloat
from .effect_size import matched_pairs_rank_biserial, rank_biserial_correlation
from .hypothesis import compare_independent_groups

ComparisonDesign = Literal["independent", "paired"]
# Only higher-is-better and lower-is-better metrics can be represented; a
# direction-neutral metric option is not supported (audit finding F9,
# deliberately deferred: neutral metrics must currently be labeled with one
# of the two supported polarities).
MetricDirection = Literal["higher_is_better", "lower_is_better"]
ComparisonStatus = Literal[
    "improved",
    "regressed",
    "no_clear_difference",
    "insufficient_evidence",
]
CandidateDirection = Literal[
    "candidate_higher",
    "candidate_lower",
    "no_clear_direction",
]
ObservationUnit = Literal["case", "run"]

_NON_SIGNIFICANCE_LIMITATION = (
    "A non-significant result does not establish equivalence and does not prove "
    "that no regression occurred."
)
_INDEPENDENT_CASE_DEPENDENCE_LIMITATION = (
    "Independent case-level scores computed over shared evaluation cases may be "
    "statistically dependent; a paired design with matching case keys is preferred."
)


def _validate_positive_unit_interval(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{name} must be a real number")
    value = float(value)
    if not isfinite(value) or not 0.0 < value < 1.0:
        raise ValueError(f"{name} must be finite and between 0 and 1")
    return value


def _validate_non_negative_finite(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{name} must be a real number")
    value = float(value)
    if not isfinite(value) or value < 0.0:
        raise ValueError(f"{name} must be finite and non-negative")
    return value


def _require_finite_metric_value(
    value: float | None,
    metric_name: str,
    label: str,
) -> float | None:
    """Raise a metric-scoped error when a computed statistic overflows.

    Extreme-but-finite scores (e.g. a spread of ~1e308) can overflow the
    median reductions or the candidate-minus-baseline subtraction. Failing
    here names the offending metric instead of leaking a later, opaque
    ``ArithmeticError`` from a nested statistical primitive (audit
    finding F7).
    """
    if value is not None and not isfinite(value):
        raise ValueError(
            f"metric '{metric_name}' produced a non-finite {label}; the "
            "score magnitudes are too extreme for finite difference "
            "arithmetic"
        )
    return value


class ComparisonConfig(BaseModel):
    """Comparison configuration.

    ``min_observations_per_group`` is the minimum number of observations that
    feed the statistical test itself: finite scored observations per group in
    the independent design, and matched pairs whose differences are non-zero
    in the paired design (the Wilcoxon signed-rank test discards zero
    differences, so the effective sample size can never exceed the pair
    count). ``bootstrap_resamples`` is floored at ``MIN_BOOTSTRAP_RESAMPLES``
    so that no degenerate few-draw interval can be presented as a
    decision-grade confidence interval.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    design: ComparisonDesign = "independent"
    alpha: float = 0.05
    confidence_level: float = 0.95
    min_observations_per_group: int = 5
    practical_significance_threshold: float = 0.0
    bootstrap_resamples: int = 5000
    bootstrap_seed: int = 0
    require_matching_dataset_version: bool = True

    @field_validator("alpha", mode="before")
    @classmethod
    def _validate_alpha(cls, value: float) -> float:
        return _validate_positive_unit_interval(value, "alpha")

    @field_validator("confidence_level", mode="before")
    @classmethod
    def _validate_confidence(cls, value: float) -> float:
        return validate_confidence_level(value)

    @field_validator("practical_significance_threshold", mode="before")
    @classmethod
    def _validate_threshold(cls, value: float) -> float:
        return _validate_non_negative_finite(
            value, "practical_significance_threshold"
        )

    @field_validator(
        "min_observations_per_group",
        "bootstrap_resamples",
        "bootstrap_seed",
        mode="before",
    )
    @classmethod
    def _validate_integers(cls, value: int, info) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{info.field_name} must be an integer")
        if info.field_name == "bootstrap_seed":
            minimum = 0
        elif info.field_name == "bootstrap_resamples":
            minimum = MIN_BOOTSTRAP_RESAMPLES
        else:
            minimum = 1
        if value < minimum:
            raise ValueError(f"{info.field_name} must be at least {minimum}")
        return value


class MetricRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    metric_name: str = Field(min_length=1)
    observation_unit: ObservationUnit
    metric_direction: MetricDirection


class MultipleTestingFamily(BaseModel):
    """Holm multiple-testing family with an available-case policy.

    The Holm step-down correction counts only metrics with a computable
    (finite) p-value toward the family size ``m``; that count is exposed as
    ``metrics_with_p_value`` while ``metrics`` still lists every planned
    hypothesis. Metrics reported as ``insufficient_evidence`` carry no
    p-value, do not loosen the correction for their siblings, and can never
    be interpreted as passes. This is the available-case convention; it is
    less conservative than the R ``p.adjust(..., "holm")`` default, which
    counts planned hypotheses even when their p-values are ``NA``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    family_id: str = Field(min_length=1)
    metrics: tuple[str, ...] = Field(min_length=1)
    correction: Literal["holm"]
    alpha: float
    metrics_with_p_value: int = Field(ge=0)

    @field_validator("alpha", mode="before")
    @classmethod
    def _validate_alpha(cls, value: float) -> float:
        return _validate_positive_unit_interval(value, "alpha")

    @model_validator(mode="after")
    def validate_family(self):
        if len(set(self.metrics)) != len(self.metrics):
            raise ValueError("family metrics must be unique")
        if self.metrics_with_p_value > len(self.metrics):
            raise ValueError("metrics_with_p_value cannot exceed the family size")
        return self


class MetricComparison(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    metric_name: str = Field(min_length=1)
    observation_unit: ObservationUnit
    design: ComparisonDesign
    method: Literal["mann_whitney_u", "wilcoxon_signed_rank"]
    metric_direction: MetricDirection
    direction: CandidateDirection
    status: ComparisonStatus
    baseline_sample_size: int = Field(default=0, ge=0)
    candidate_sample_size: int = Field(default=0, ge=0)
    compared_pair_count: int | None = Field(default=None, ge=1)
    excluded_missing_score_count: int = Field(default=0, ge=0)
    baseline_median: FiniteFloat | None = None
    candidate_median: FiniteFloat | None = None
    median_difference: FiniteFloat | None = None
    difference_convention: Literal["candidate_minus_baseline"]
    effect_size_definition: Literal[
        "kerby_rank_biserial_candidate_oriented",
        "matched_pairs_rank_biserial",
    ]
    rank_biserial_correlation: FiniteFloat | None = Field(default=None, ge=-1, le=1)
    raw_p_value: FiniteFloat | None = Field(default=None, ge=0, le=1)
    adjusted_p_value: FiniteFloat | None = Field(default=None, ge=0, le=1)
    decision_p_value: FiniteFloat | None = Field(default=None, ge=0, le=1)
    decision_p_value_source: Literal["adjusted_p_value"] | None = None
    statistically_significant: bool = False
    confidence_interval: BootstrapInterval | None = None
    confidence_interval_target: Literal["median_difference"] | None = None
    insufficient_evidence_reasons: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    baseline_source_result_ids: tuple[str, ...] = ()
    candidate_source_result_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_comparison(self):
        if self.median_difference is not None:
            if self.median_difference > 0.0 and self.direction != "candidate_higher":
                raise ValueError(
                    "direction must be 'candidate_higher' when median_difference is positive"
                )
            if self.median_difference < 0.0 and self.direction != "candidate_lower":
                raise ValueError(
                    "direction must be 'candidate_lower' when median_difference is negative"
                )
            if self.median_difference == 0.0 and self.direction != "no_clear_direction":
                raise ValueError(
                    "direction must be 'no_clear_direction' when median_difference is zero"
                )
        if self.status == "improved":
            if self.metric_direction == "higher_is_better" and self.direction != "candidate_higher":
                raise ValueError(
                    "improved status requires candidate_higher for higher_is_better metrics"
                )
            if self.metric_direction == "lower_is_better" and self.direction != "candidate_lower":
                raise ValueError(
                    "improved status requires candidate_lower for lower_is_better metrics"
                )
        if self.status == "regressed":
            if self.metric_direction == "higher_is_better" and self.direction != "candidate_lower":
                raise ValueError(
                    "regressed status requires candidate_lower for higher_is_better metrics"
                )
            if self.metric_direction == "lower_is_better" and self.direction != "candidate_higher":
                raise ValueError(
                    "regressed status requires candidate_higher for lower_is_better metrics"
                )
        if self.status in ("improved", "regressed"):
            if not self.statistically_significant:
                raise ValueError(f"{self.status} status requires statistical significance")
            if self.decision_p_value_source != "adjusted_p_value":
                raise ValueError(
                    f"{self.status} status requires a recorded decision p-value source"
                )
            if self.decision_p_value is None or self.adjusted_p_value is None:
                raise ValueError(
                    f"{self.status} status requires adjusted and decision p-values"
                )
            if self.decision_p_value != self.adjusted_p_value:
                raise ValueError("decision_p_value must equal adjusted_p_value")
            if self.confidence_interval is None:
                raise ValueError(f"{self.status} status requires a confidence interval")
        if self.status == "insufficient_evidence":
            if not self.insufficient_evidence_reasons:
                raise ValueError(
                    "insufficient_evidence status requires at least one recorded reason"
                )
            if self.raw_p_value is not None or self.adjusted_p_value is not None:
                raise ValueError("insufficient_evidence status must not carry p-values")
            if self.decision_p_value is not None:
                raise ValueError(
                    "insufficient_evidence status must not carry a decision p-value"
                )
        if self.status == "no_clear_difference" and self.statistically_significant:
            if not self.insufficient_evidence_reasons:
                raise ValueError(
                    "a significant no_clear_difference outcome must record its reasons"
                )
        if (
            self.raw_p_value is not None
            and self.adjusted_p_value is not None
            and self.adjusted_p_value < self.raw_p_value
        ):
            raise ValueError("adjusted_p_value must not be smaller than raw_p_value")
        if self.decision_p_value is not None:
            if self.decision_p_value_source != "adjusted_p_value":
                raise ValueError(
                    "decision_p_value requires the 'adjusted_p_value' source"
                )
            if (
                self.adjusted_p_value is None
                or self.decision_p_value != self.adjusted_p_value
            ):
                raise ValueError(
                    "decision_p_value must equal adjusted_p_value when present"
                )
        if self.compared_pair_count is not None and self.design != "paired":
            raise ValueError(
                "compared_pair_count is only valid for paired comparisons"
            )
        return self


class RunComparison(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    baseline_evaluation_run_id: str = Field(min_length=1)
    candidate_evaluation_run_id: str = Field(min_length=1)
    baseline_agent_version_id: str = Field(min_length=1)
    candidate_agent_version_id: str = Field(min_length=1)
    baseline_dataset_version_id: str | None = None
    candidate_dataset_version_id: str | None = None
    dataset_lineage_status: Literal["matched", "mismatched", "missing"]
    config: ComparisonConfig
    family: MultipleTestingFamily
    metrics: tuple[MetricComparison, ...] = Field(min_length=1)
    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_run_comparison(self):
        if self.baseline_evaluation_run_id == self.candidate_evaluation_run_id:
            raise ValueError("baseline and candidate evaluation runs must differ")
        names = tuple(metric.metric_name for metric in self.metrics)
        if len(set(names)) != len(names):
            raise ValueError("comparison metrics must be unique")
        if self.family.metrics != names:
            raise ValueError("family metrics must match the comparison metrics")
        return self


def holm_adjust(
    p_values: Sequence[float | None],
) -> tuple[float | None, ...]:
    """Holm step-down adjustment preserving ``None`` for unavailable tests.

    Available-case policy: the family size ``m`` is the number of non-``None``
    p-values, so metrics without a p-value are excluded from the correction
    and pass through as ``None``. Adjusted values are clipped to 1.0 and are
    never smaller than their raw p-values.
    """
    validated: list[tuple[int, float]] = []
    for index, value in enumerate(p_values):
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, Real):
            raise TypeError("p-values must be real numbers or None")
        number = float(value)
        if not isfinite(number) or not 0.0 <= number <= 1.0:
            raise ValueError("p-values must be finite and between 0 and 1")
        validated.append((index, number))

    adjusted: dict[int, float] = {}
    running_maximum = 0.0
    ordered = sorted(validated, key=lambda item: (item[1], item[0]))
    count = len(ordered)
    for rank, (index, p_value) in enumerate(ordered):
        candidate_value = min(1.0, (count - rank) * p_value)
        running_maximum = max(running_maximum, candidate_value)
        adjusted[index] = running_maximum

    return tuple(adjusted.get(index) for index in range(len(p_values)))


@dataclass
class _MetricEvidence:
    metric_name: str
    observation_unit: ObservationUnit
    metric_direction: MetricDirection
    method: Literal["mann_whitney_u", "wilcoxon_signed_rank"]
    effect_size_definition: Literal[
        "kerby_rank_biserial_candidate_oriented",
        "matched_pairs_rank_biserial",
    ]
    baseline_sample_size: int
    candidate_sample_size: int
    excluded_missing_score_count: int
    baseline_source_result_ids: tuple[str, ...]
    candidate_source_result_ids: tuple[str, ...]
    baseline_median: float | None = None
    candidate_median: float | None = None
    median_difference: float | None = None
    rank_biserial: float | None = None
    raw_p_value: float | None = None
    confidence_interval: BootstrapInterval | None = None
    compared_pair_count: int | None = None
    insufficient_evidence_reasons: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


class EvaluationRunComparisonService:
    ANALYSIS_TYPE = "evaluation_run_comparison"
    ALGORITHM_NAME = "evaluation_run_comparison"
    ALGORITHM_VERSION = "1.0.0"

    def __init__(self, session: Session) -> None:
        self._session = session
        self._runs = EvaluationRunRepository(session)
        self._results = EvaluationResultRepository(session)
        self._analyses = AnalysisRepository(session)
        self._differentials = DifferentialAnalysisRepository(session)

    def compare_runs(
        self,
        baseline_evaluation_run_id: str,
        candidate_evaluation_run_id: str,
        metric_requests: Sequence[MetricRequest],
        *,
        config: ComparisonConfig,
    ) -> RunComparison:
        self._validate_identifier(
            baseline_evaluation_run_id,
            "baseline_evaluation_run_id",
        )
        self._validate_identifier(
            candidate_evaluation_run_id,
            "candidate_evaluation_run_id",
        )
        if baseline_evaluation_run_id == candidate_evaluation_run_id:
            raise ValueError("baseline and candidate evaluation runs must differ")
        requests = self._validate_metric_requests(metric_requests)
        if not isinstance(config, ComparisonConfig):
            raise TypeError("config must be a ComparisonConfig")

        baseline_run = self._runs.get_required(baseline_evaluation_run_id)
        candidate_run = self._runs.get_required(candidate_evaluation_run_id)
        for side, run in (
            ("baseline", baseline_run),
            ("candidate", candidate_run),
        ):
            # The evaluation-run lifecycle (pending -> running ->
            # completed/failed) marks "completed" as the only state in which
            # the run's full case set has been evaluated; comparing the
            # partial results of a failed or in-progress run would silently
            # understate or misstate the comparison (audit finding F5).
            if run.status != "completed":
                raise ValueError(
                    f"{side} evaluation run '{run.evaluation_run_id}' has "
                    f"status '{run.status}'; only completed evaluation runs "
                    "can be compared"
                )
        dataset_lineage_status, lineage_limitations = self._assess_dataset_lineage(
            baseline_run.dataset_version_id,
            candidate_run.dataset_version_id,
            config,
        )

        baseline_groups = self._group_rows(
            self._results.list_for_run(baseline_evaluation_run_id)
        )
        candidate_groups = self._group_rows(
            self._results.list_for_run(candidate_evaluation_run_id)
        )

        evidences = [
            self._collect_evidence(request, baseline_groups, candidate_groups, config)
            for request in requests
        ]
        adjusted_p_values = holm_adjust(
            [evidence.raw_p_value for evidence in evidences]
        )
        metrics = tuple(
            self._finalize(evidence, adjusted_p_value, config)
            for evidence, adjusted_p_value in zip(evidences, adjusted_p_values)
        )
        family = MultipleTestingFamily(
            family_id=self._family_id(
                baseline_evaluation_run_id,
                candidate_evaluation_run_id,
                requests,
            ),
            metrics=tuple(request.metric_name for request in requests),
            correction="holm",
            alpha=config.alpha,
            metrics_with_p_value=sum(
                1 for p_value in adjusted_p_values if p_value is not None
            ),
        )
        limitations = list(lineage_limitations)
        if family.metrics_with_p_value < len(family.metrics):
            # Disclose the available-case Holm policy exactly when it has
            # consequences: some planned hypotheses produced no p-value, so
            # the correction counts fewer hypotheses than were planned
            # (audit finding F4).
            limitations.append(
                "Holm multiple-testing correction uses the available-case "
                "family: only the metrics with a computable p-value count "
                f"toward the family size ({family.metrics_with_p_value} of "
                f"{len(family.metrics)} planned); metrics reported as "
                "insufficient_evidence carry no p-value, are excluded from "
                "the correction, and can never be counted as passes. This is "
                "less conservative than counting every planned hypothesis."
            )

        return RunComparison(
            baseline_evaluation_run_id=baseline_evaluation_run_id,
            candidate_evaluation_run_id=candidate_evaluation_run_id,
            baseline_agent_version_id=baseline_run.agent_version_id,
            candidate_agent_version_id=candidate_run.agent_version_id,
            baseline_dataset_version_id=baseline_run.dataset_version_id,
            candidate_dataset_version_id=candidate_run.dataset_version_id,
            dataset_lineage_status=dataset_lineage_status,
            config=config,
            family=family,
            metrics=metrics,
            limitations=tuple(limitations),
        )

    def save(
        self,
        comparison: RunComparison,
    ) -> tuple[Analysis, DifferentialAnalysis]:
        if not isinstance(comparison, RunComparison):
            raise TypeError("comparison must be a RunComparison")
        payload = comparison.model_dump(mode="json")

        analysis = Analysis(
            analysis_id=new_id(),
            analysis_type=self.ANALYSIS_TYPE,
            status="completed",
            source_evaluation_run_id=comparison.candidate_evaluation_run_id,
            summary=(
                f"Baseline {comparison.baseline_evaluation_run_id} versus candidate "
                f"{comparison.candidate_evaluation_run_id} across "
                f"{len(comparison.metrics)} metric(s)"
            ),
            findings={"family": payload["family"]},
            metrics={
                "algorithm_name": self.ALGORITHM_NAME,
                "algorithm_version": self.ALGORITHM_VERSION,
                "comparison": payload,
            },
        )
        self._analyses.create(analysis)

        differential = DifferentialAnalysis(
            differential_analysis_id=new_id(),
            analysis_id=analysis.analysis_id,
            baseline_evaluation_run_id=comparison.baseline_evaluation_run_id,
            candidate_evaluation_run_id=comparison.candidate_evaluation_run_id,
            metric_deltas={
                metric["metric_name"]: {
                    "median_difference": metric["median_difference"],
                    "direction": metric["direction"],
                    "status": metric["status"],
                }
                for metric in payload["metrics"]
            },
            slice_deltas={},
            statistical_tests={
                metric["metric_name"]: metric for metric in payload["metrics"]
            },
        )
        self._differentials.create(differential)

        return analysis, differential

    def _collect_evidence(
        self,
        request: MetricRequest,
        baseline_groups,
        candidate_groups,
        config: ComparisonConfig,
    ) -> _MetricEvidence:
        baseline_group = baseline_groups.get(
            (request.metric_name, request.observation_unit)
        )
        candidate_group = candidate_groups.get(
            (request.metric_name, request.observation_unit)
        )
        if baseline_group is None:
            raise ValueError(
                f"metric '{request.metric_name}' with observation unit "
                f"'{request.observation_unit}' is missing from the baseline "
                "evaluation run"
            )
        if candidate_group is None:
            raise ValueError(
                f"metric '{request.metric_name}' with observation unit "
                f"'{request.observation_unit}' is missing from the candidate "
                "evaluation run"
            )
        if config.design == "paired":
            return self._collect_paired_evidence(
                request,
                baseline_group,
                candidate_group,
                config,
            )
        return self._collect_independent_evidence(
            request,
            baseline_group,
            candidate_group,
            config,
        )

    def _collect_independent_evidence(
        self,
        request: MetricRequest,
        baseline_group,
        candidate_group,
        config: ComparisonConfig,
    ) -> _MetricEvidence:
        baseline_values, baseline_ids, baseline_excluded = self._extract_scores(
            baseline_group,
            request.metric_name,
        )
        candidate_values, candidate_ids, candidate_excluded = self._extract_scores(
            candidate_group,
            request.metric_name,
        )
        evidence = _MetricEvidence(
            metric_name=request.metric_name,
            observation_unit=request.observation_unit,
            metric_direction=request.metric_direction,
            method="mann_whitney_u",
            effect_size_definition="kerby_rank_biserial_candidate_oriented",
            baseline_sample_size=len(baseline_values),
            candidate_sample_size=len(candidate_values),
            excluded_missing_score_count=baseline_excluded + candidate_excluded,
            baseline_source_result_ids=baseline_ids,
            candidate_source_result_ids=candidate_ids,
        )
        with np.errstate(over="ignore", invalid="ignore"):
            if baseline_values:
                evidence.baseline_median = float(np.median(baseline_values))
            if candidate_values:
                evidence.candidate_median = float(np.median(candidate_values))
        if (
            evidence.baseline_median is not None
            and evidence.candidate_median is not None
        ):
            evidence.median_difference = (
                evidence.candidate_median - evidence.baseline_median
            )
        _require_finite_metric_value(
            evidence.baseline_median, request.metric_name, "baseline median"
        )
        _require_finite_metric_value(
            evidence.candidate_median, request.metric_name, "candidate median"
        )
        _require_finite_metric_value(
            evidence.median_difference,
            request.metric_name,
            "median difference",
        )
        if baseline_excluded or candidate_excluded:
            evidence.limitations.append(
                "Evaluation results without scores were excluded from the "
                "score comparison."
            )
        if not baseline_values or not candidate_values:
            evidence.insufficient_evidence_reasons.append(
                "score observations are required for comparative inference; "
                "pass-rate comparison is not supported"
            )
            return evidence
        if len(set(baseline_values) | set(candidate_values)) == 1:
            # Every pooled score is the same constant: the Mann-Whitney U
            # statistic has zero variance and scipy returns a non-finite
            # p-value. Mirror the paired all-zero-difference handling with
            # a validated insufficient_evidence outcome instead of leaking
            # a numpy warning and an opaque ArithmeticError (audit
            # finding F2). Constant groups whose values differ have a
            # well-defined test and continue below.
            evidence.insufficient_evidence_reasons.append(
                f"metric '{request.metric_name}' records the identical "
                f"constant {baseline_values[0]!r} for every baseline and "
                "candidate observation; the Mann-Whitney U test is "
                "undefined when all scores are tied"
            )
        if len(baseline_values) < config.min_observations_per_group:
            evidence.insufficient_evidence_reasons.append(
                f"baseline has {len(baseline_values)} score observations; at "
                f"least {config.min_observations_per_group} are required"
            )
        if len(candidate_values) < config.min_observations_per_group:
            evidence.insufficient_evidence_reasons.append(
                f"candidate has {len(candidate_values)} score observations; at "
                f"least {config.min_observations_per_group} are required"
            )
        if evidence.insufficient_evidence_reasons:
            return evidence

        test = compare_independent_groups(
            baseline_values,
            candidate_values,
            alpha=config.alpha,
        )
        first_size = len(baseline_values)
        second_size = len(candidate_values)
        evidence.raw_p_value = test.p_value
        evidence.rank_biserial = rank_biserial_correlation(
            first_size * second_size - test.statistic,
            first_size,
            second_size,
        )
        try:
            evidence.confidence_interval = bootstrap_median_difference_interval(
                baseline_values,
                candidate_values,
                paired=False,
                confidence_level=config.confidence_level,
                resamples=config.bootstrap_resamples,
                seed=config.bootstrap_seed,
            )
        except ArithmeticError as exc:
            raise ValueError(
                f"metric '{request.metric_name}' overflowed while resampling "
                f"the confidence interval ({exc})"
            ) from exc
        evidence.limitations.extend(
            (
                "Mann-Whitney U tests stochastic ordering of the score "
                "distributions, not medians alone.",
                "Observations must be independent across and within both "
                "evaluation runs.",
                "The bootstrap percentile interval for the median difference "
                "is approximate and depends on the configured resamples and "
                "seed.",
            )
        )
        if request.observation_unit == "case":
            evidence.limitations.append(_INDEPENDENT_CASE_DEPENDENCE_LIMITATION)
        return evidence

    def _collect_paired_evidence(
        self,
        request: MetricRequest,
        baseline_group,
        candidate_group,
        config: ComparisonConfig,
    ) -> _MetricEvidence:
        if request.observation_unit != "case":
            raise ValueError(
                "paired comparisons require case-level observations with "
                "pairing keys"
            )
        baseline_map = self._pairing_map(baseline_group, "baseline", request)
        candidate_map = self._pairing_map(candidate_group, "candidate", request)
        missing_in_candidate = sorted(set(baseline_map) - set(candidate_map))
        missing_in_baseline = sorted(set(candidate_map) - set(baseline_map))
        if missing_in_candidate or missing_in_baseline:
            raise ValueError(
                "pairing keys do not match: "
                f"{len(missing_in_candidate)} observation(s) missing in the "
                f"candidate and {len(missing_in_baseline)} missing in the "
                "baseline; unmatched observations are not dropped"
            )

        keys = sorted(baseline_map)
        baseline_values = [baseline_map[key][0] for key in keys]
        candidate_values = [candidate_map[key][0] for key in keys]
        differences = [
            candidate - baseline
            for baseline, candidate in zip(baseline_values, candidate_values)
        ]
        non_finite_differences = [d for d in differences if not isfinite(d)]
        if non_finite_differences:
            # A finite baseline/candidate pair whose difference overflows to
            # +/-inf would silently corrupt the ranks, the bootstrap draws,
            # and the effect size (audit finding F7).
            raise ValueError(
                f"metric '{request.metric_name}' produces a non-finite "
                "paired score difference; the score magnitudes are too "
                "extreme for finite difference arithmetic"
            )
        non_zero_differences = [d for d in differences if d != 0.0]
        with np.errstate(over="ignore", invalid="ignore"):
            baseline_median = float(np.median(baseline_values))
            candidate_median = float(np.median(candidate_values))
            median_difference = float(np.median(differences))
        _require_finite_metric_value(
            baseline_median, request.metric_name, "baseline median"
        )
        _require_finite_metric_value(
            candidate_median, request.metric_name, "candidate median"
        )
        _require_finite_metric_value(
            median_difference, request.metric_name, "median difference"
        )

        evidence = _MetricEvidence(
            metric_name=request.metric_name,
            observation_unit=request.observation_unit,
            metric_direction=request.metric_direction,
            method="wilcoxon_signed_rank",
            effect_size_definition="matched_pairs_rank_biserial",
            baseline_sample_size=len(baseline_values),
            candidate_sample_size=len(candidate_values),
            excluded_missing_score_count=0,
            baseline_source_result_ids=tuple(
                sorted(baseline_map[key][1] for key in keys)
            ),
            candidate_source_result_ids=tuple(
                sorted(candidate_map[key][1] for key in keys)
            ),
            baseline_median=baseline_median,
            candidate_median=candidate_median,
            median_difference=median_difference,
            compared_pair_count=len(keys),
        )
        if len(differences) < config.min_observations_per_group:
            evidence.insufficient_evidence_reasons.append(
                f"{len(differences)} matched pairs are available; at least "
                f"{config.min_observations_per_group} are required"
            )
        elif not non_zero_differences:
            evidence.insufficient_evidence_reasons.append(
                "all paired differences are zero, so the signed-rank test "
                "cannot be computed"
            )
        elif len(non_zero_differences) < config.min_observations_per_group:
            # The signed-rank test uses only the non-zero differences, so
            # the configured floor must hold for the effective sample size
            # as well; otherwise the minimum-observation contract is
            # bypassable below the floor (audit finding F3).
            evidence.insufficient_evidence_reasons.append(
                f"{len(non_zero_differences)} of {len(differences)} matched "
                "pairs have non-zero differences and feed the signed-rank "
                f"test; at least {config.min_observations_per_group} "
                "non-zero differences are required"
            )
        if evidence.insufficient_evidence_reasons:
            return evidence

        _statistic, p_value = wilcoxon(
            non_zero_differences,
            alternative="two-sided",
            method="auto",
        )
        evidence.raw_p_value = float(p_value)
        # The two-sided Wilcoxon statistic is min(W+, W-) and cannot encode
        # the direction of the effect; W+ is recomputed from the tie-aware
        # ranks of the non-zero differences instead (audit finding F1).
        evidence.rank_biserial = matched_pairs_rank_biserial(differences)
        try:
            evidence.confidence_interval = bootstrap_median_difference_interval(
                baseline_values,
                candidate_values,
                paired=True,
                confidence_level=config.confidence_level,
                resamples=config.bootstrap_resamples,
                seed=config.bootstrap_seed,
            )
        except ArithmeticError as exc:
            raise ValueError(
                f"metric '{request.metric_name}' overflowed while resampling "
                f"the confidence interval ({exc})"
            ) from exc
        evidence.limitations.extend(
            (
                "The Wilcoxon signed-rank test assumes symmetrically "
                "distributed paired differences.",
                "Paired differences of exactly zero are discarded by the "
                "signed-rank test before computing the p-value.",
                "The bootstrap percentile interval for the median paired "
                "difference is approximate and depends on the configured "
                "resamples and seed.",
            )
        )
        return evidence

    @staticmethod
    def _extract_scores(rows, metric_name: str):
        values: list[float] = []
        result_ids: list[str] = []
        excluded = 0
        for row in rows:
            if row.score is None:
                excluded += 1
                continue
            if (
                isinstance(row.score, bool)
                or not isinstance(row.score, Real)
                or not isfinite(float(row.score))
            ):
                raise ValueError(
                    f"metric '{metric_name}' has a non-finite score in "
                    f"evaluation result '{row.evaluation_result_id}'"
                )
            values.append(float(row.score))
            result_ids.append(row.evaluation_result_id)
        return values, tuple(sorted(result_ids)), excluded

    @staticmethod
    def _pairing_map(rows, side_name: str, request: MetricRequest):
        mapping: dict[str, tuple[float, str]] = {}
        for row in rows:
            if row.case_id is None:
                raise ValueError(
                    "paired comparisons require every observation to carry a "
                    "case pairing key; evaluation result "
                    f"'{row.evaluation_result_id}' has none"
                )
            if row.score is None:
                raise ValueError(
                    "paired comparisons require a score for every pairing "
                    f"key; evaluation result '{row.evaluation_result_id}' has "
                    "no score"
                )
            if (
                isinstance(row.score, bool)
                or not isinstance(row.score, Real)
                or not isfinite(float(row.score))
            ):
                raise ValueError(
                    f"metric '{request.metric_name}' has a non-finite score "
                    f"in {side_name} evaluation result "
                    f"'{row.evaluation_result_id}'"
                )
            if row.case_id in mapping:
                raise ValueError(
                    f"duplicate pairing key '{row.case_id}' in {side_name} "
                    "evaluation results"
                )
            mapping[row.case_id] = (float(row.score), row.evaluation_result_id)
        return mapping

    @staticmethod
    def _group_rows(rows):
        groups = {}
        for row in rows:
            observation_unit: ObservationUnit = (
                "case" if row.case_id is not None else "run"
            )
            groups.setdefault((row.metric_name, observation_unit), []).append(row)
        return groups

    @staticmethod
    def _assess_dataset_lineage(
        baseline_dataset_version_id: str | None,
        candidate_dataset_version_id: str | None,
        config: ComparisonConfig,
    ):
        if (
            baseline_dataset_version_id is not None
            and candidate_dataset_version_id is not None
        ):
            if baseline_dataset_version_id == candidate_dataset_version_id:
                return "matched", []
            if config.require_matching_dataset_version:
                raise ValueError(
                    "comparability cannot be established: the baseline and "
                    "candidate evaluation runs use different dataset versions"
                )
            return (
                "mismatched",
                [
                    "The baseline and candidate evaluation runs use "
                    "different dataset versions; comparability was not "
                    "established and matching was not required."
                ],
            )
        if config.require_matching_dataset_version:
            raise ValueError(
                "comparability cannot be established: dataset version lineage "
                "is missing on the baseline and/or candidate evaluation run"
            )
        return (
            "missing",
            [
                "Dataset version lineage is missing on at least one "
                "evaluation run; comparability was not established and "
                "matching was not required."
            ],
        )

    @staticmethod
    def _family_id(
        baseline_evaluation_run_id: str,
        candidate_evaluation_run_id: str,
        requests: tuple[MetricRequest, ...],
    ) -> str:
        metric_tags = "+".join(
            f"{request.metric_name}:{request.observation_unit}"
            for request in requests
        )
        return (
            f"{baseline_evaluation_run_id}->{candidate_evaluation_run_id}:{metric_tags}"
        )

    def _finalize(
        self,
        evidence: _MetricEvidence,
        adjusted_p_value: float | None,
        config: ComparisonConfig,
    ) -> MetricComparison:
        direction: CandidateDirection = "no_clear_direction"
        if evidence.median_difference is not None:
            if evidence.median_difference > 0.0:
                direction = "candidate_higher"
            elif evidence.median_difference < 0.0:
                direction = "candidate_lower"

        reasons = list(evidence.insufficient_evidence_reasons)
        limitations = list(evidence.limitations)

        if reasons:
            status: ComparisonStatus = "insufficient_evidence"
            statistically_significant = False
            decision_p_value = None
            decision_p_value_source = None
        else:
            decision_p_value = adjusted_p_value
            decision_p_value_source = "adjusted_p_value"
            statistically_significant = adjusted_p_value < config.alpha
            if not statistically_significant:
                status = "no_clear_difference"
                limitations.append(_NON_SIGNIFICANCE_LIMITATION)
                if evidence.raw_p_value < config.alpha:
                    reasons.append(
                        "the Holm-adjusted p-value no longer meets the "
                        "configured alpha after multiple-testing correction"
                    )
            else:
                difference = evidence.median_difference
                interval = evidence.confidence_interval
                if difference is None:
                    status = "no_clear_difference"
                    reasons.append("the median difference is unavailable")
                elif difference == 0.0:
                    status = "no_clear_difference"
                    reasons.append("the median difference is exactly zero")
                elif abs(difference) < config.practical_significance_threshold:
                    status = "no_clear_difference"
                    reasons.append(
                        "the median difference is below the configured "
                        "practical significance threshold"
                    )
                elif interval is None:
                    status = "no_clear_difference"
                    reasons.append(
                        "no confidence interval is available for the median "
                        "difference"
                    )
                elif difference > 0.0 and interval.lower_bound > 0.0:
                    status = (
                        "improved"
                        if evidence.metric_direction == "higher_is_better"
                        else "regressed"
                    )
                elif difference < 0.0 and interval.upper_bound < 0.0:
                    status = (
                        "improved"
                        if evidence.metric_direction == "lower_is_better"
                        else "regressed"
                    )
                else:
                    status = "no_clear_difference"
                    reasons.append(
                        "the confidence interval for the median difference "
                        "overlaps zero"
                    )

        return MetricComparison(
            metric_name=evidence.metric_name,
            observation_unit=evidence.observation_unit,
            design=config.design,
            method=evidence.method,
            metric_direction=evidence.metric_direction,
            direction=direction,
            status=status,
            baseline_sample_size=evidence.baseline_sample_size,
            candidate_sample_size=evidence.candidate_sample_size,
            compared_pair_count=evidence.compared_pair_count,
            excluded_missing_score_count=evidence.excluded_missing_score_count,
            baseline_median=evidence.baseline_median,
            candidate_median=evidence.candidate_median,
            median_difference=evidence.median_difference,
            difference_convention="candidate_minus_baseline",
            effect_size_definition=evidence.effect_size_definition,
            rank_biserial_correlation=evidence.rank_biserial,
            raw_p_value=evidence.raw_p_value,
            adjusted_p_value=adjusted_p_value,
            decision_p_value=decision_p_value,
            decision_p_value_source=decision_p_value_source,
            statistically_significant=statistically_significant,
            confidence_interval=evidence.confidence_interval,
            confidence_interval_target=(
                "median_difference" if evidence.confidence_interval else None
            ),
            insufficient_evidence_reasons=tuple(reasons),
            limitations=tuple(limitations),
            baseline_source_result_ids=evidence.baseline_source_result_ids,
            candidate_source_result_ids=evidence.candidate_source_result_ids,
        )

    @staticmethod
    def _validate_identifier(value: str, name: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")

    @staticmethod
    def _validate_metric_requests(
        metric_requests: Sequence[MetricRequest],
    ) -> tuple[MetricRequest, ...]:
        if isinstance(metric_requests, (str, bytes)) or not isinstance(
            metric_requests, Sequence
        ):
            raise TypeError("metric_requests must be a sequence of MetricRequest")
        requests = tuple(metric_requests)
        if not requests:
            raise ValueError("at least one metric request is required")
        for request in requests:
            if not isinstance(request, MetricRequest):
                raise TypeError(
                    "metric_requests must contain only MetricRequest values"
                )
        names = [request.metric_name for request in requests]
        if len(set(names)) != len(names):
            raise ValueError(
                "each metric may be compared once per run comparison"
            )
        return requests











