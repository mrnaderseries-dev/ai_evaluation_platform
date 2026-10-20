"""Deterministic dataset coverage matching.

Matching is separated from the agent strategies on purpose: a strategy
declares *what* must be evaluated, and this module decides *which existing
dataset version* can evaluate it.

Rules enforced here:

* Coverage is only ever taken from explicit declarations. A dataset name,
  a dataset id, or the absence of a limitation never implies coverage.
* Inactive, invalid, or incompatible datasets are excluded before ranking.
* Ranking uses explicit, documented factors with fixed weights.
* Ties are broken deterministically, and every match records why it was
  chosen.
* Every requirement that cannot be covered is reported as a coverage gap.
* Exact dataset *version* ids are preserved, so a plan stays reproducible.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Mapping

from .domain import (
    CoverageGap,
    DatasetDescriptor,
    DatasetMatch,
    EvaluationRequirement,
)

#: Quality statuses that make a dataset version ineligible.
INELIGIBLE_QUALITY_STATUSES: frozenset[str] = frozenset(
    {"invalid", "failed", "rejected", "expired", "deprecated"}
)

#: Quality statuses that are preferred, best first.
QUALITY_RANK: Mapping[str, int] = {
    "validated": 0,
    "passed": 1,
    "approved": 2,
    "draft": 3,
    "pending": 4,
    "unknown": 5,
}

#: Weight of each ranking factor. Higher is better.
FACTOR_WEIGHTS: Mapping[str, float] = {
    "coverage": 0.5,
    "quality": 0.3,
    "cost": 0.1,
    "recency": 0.1,
}


class DatasetMatcher:
    """Matches requirements to dataset versions by declared coverage."""

    def __init__(
        self,
        *,
        coverage_weight: float = 0.5,
        quality_weight: float = 0.3,
        cost_weight: float = 0.1,
        recency_weight: float = 0.1,
    ) -> None:
        self._weights: Mapping[str, float] = {
            "coverage": coverage_weight,
            "quality": quality_weight,
            "cost": cost_weight,
            "recency": recency_weight,
        }

    def match(
        self,
        requirements: Sequence[EvaluationRequirement],
        datasets: Iterable[DatasetDescriptor],
        *,
        agent_type: str | None = None,
    ) -> tuple[tuple[DatasetMatch, ...], tuple[CoverageGap, ...]]:
        """Match requirements to datasets and report coverage gaps.

        Args:
            requirements: the requirements to cover.
            datasets: candidate dataset versions with declared coverage.
            agent_type: when given, datasets that declare a different set
                of supported agent types are excluded.

        Returns:
            A ``(matches, gaps)`` pair. A requirement appears in at most one
            of the two.
        """
        candidates = self._eligible(datasets, agent_type)
        matches: list[DatasetMatch] = []
        gaps: list[CoverageGap] = []

        for requirement in sorted(
            requirements, key=lambda item: item.requirement_id
        ):
            scored = [
                (self._score(dataset, requirement), dataset)
                for dataset in candidates
                if self._covers(dataset, requirement)
            ]

            if not scored:
                gaps.append(
                    CoverageGap(
                        requirement_id=requirement.requirement_id,
                        requirement_type=requirement.requirement_type,
                        reason=self._gap_reason(requirement),
                        considered_dataset_version_ids=tuple(
                            sorted(
                                dataset.dataset_version_id
                                for dataset in candidates
                            )
                        ),
                    )
                )
                continue

            scored.sort(key=lambda pair: self._sort_key(pair[0], pair[1]))
            best_score, best = scored[0]

            matches.append(
                DatasetMatch(
                    requirement_id=requirement.requirement_id,
                    dataset_version_id=best.dataset_version_id,
                    rationale=self._rationale(best, requirement, best_score),
                    coverage_score=round(best_score["coverage"], 6),
                    quality_status=best.quality_status,
                    matched_capabilities=frozenset(
                        capability
                        for capability in requirement.required_capabilities
                        if capability in best.capabilities_covered
                    ),
                )
            )

        return (
            tuple(sorted(matches, key=lambda item: item.requirement_id)),
            tuple(sorted(gaps, key=lambda item: item.requirement_id)),
        )

    def _eligible(
        self,
        datasets: Iterable[DatasetDescriptor],
        agent_type: str | None,
    ) -> list[DatasetDescriptor]:
        """Filter out datasets that may never be selected."""
        eligible: list[DatasetDescriptor] = []

        for dataset in datasets:
            if not dataset.is_active:
                continue

            if dataset.quality_status in INELIGIBLE_QUALITY_STATUSES:
                continue

            if (
                agent_type is not None
                and dataset.agent_types_supported is not None
                and agent_type not in dataset.agent_types_supported
            ):
                continue

            eligible.append(dataset)

        return eligible


    @staticmethod
    def _covers(
        dataset: DatasetDescriptor,
        requirement: EvaluationRequirement,
    ) -> bool:
        """Return ``True`` when the dataset explicitly covers the requirement.

        Coverage must be declared. An empty declaration never covers, and
        a dataset that declares the requirement type but not the required
        capabilities does not fully cover it either.
        """
        if requirement.requirement_type not in dataset.requirement_types_covered:
            return False

        required = requirement.required_capabilities

        if not required:
            return True

        return required.issubset(dataset.capabilities_covered)

    def _score(
        self,
        dataset: DatasetDescriptor,
        requirement: EvaluationRequirement,
    ) -> dict[str, float]:
        """Score a candidate dataset against a requirement."""
        declared_types = len(dataset.requirement_types_covered)

        coverage = 0.5

        if declared_types:
            coverage += 0.25 / declared_types

        required_capabilities = requirement.required_capabilities

        if required_capabilities:
            matched = required_capabilities.intersection(
                dataset.capabilities_covered
            )
            coverage += 0.25 * (len(matched) / len(required_capabilities))
        else:
            coverage += 0.25

        quality_rank = QUALITY_RANK.get(dataset.quality_status, 99)
        quality = max(0.0, 1.0 - (quality_rank / 10.0))

        if dataset.estimated_cost is None:
            cost = 0.5
        else:
            cost = 1.0 / (1.0 + dataset.estimated_cost)

        recency = 1.0 if dataset.validation_evidence_refs else 0.5

        return {
            "coverage": min(coverage, 1.0),
            "quality": quality,
            "cost": cost,
            "recency": recency,
        }

    def _total_score(self, scores: Mapping[str, float]) -> float:
        return sum(
            scores[factor] * weight
            for factor, weight in self._weights.items()
        )

    def _sort_key(
        self,
        scores: Mapping[str, float],
        dataset: DatasetDescriptor,
    ) -> tuple[float, str]:
        """Deterministic sort key: best score first, then dataset id.

        The dataset version id is the final tie-break so that two equally
        scoring datasets always resolve to the same choice.
        """
        return (-self._total_score(scores), dataset.dataset_version_id)

    def _rationale(
        self,
        dataset: DatasetDescriptor,
        requirement: EvaluationRequirement,
        scores: Mapping[str, float],
    ) -> str:
        """Explain, in one sentence, why this dataset was selected."""
        total = self._total_score(scores)

        return (
            f"Dataset {dataset.dataset_version_id} declares coverage of "
            f"'{requirement.requirement_type}' with quality status "
            f"'{dataset.quality_status}'; matched on coverage="
            f"{scores['coverage']:.2f}, quality={scores['quality']:.2f}, "
            f"cost={scores['cost']:.2f}, recency={scores['recency']:.2f} "
            f"(total={total:.4f})."
        )

    def _gap_reason(self, requirement: EvaluationRequirement) -> str:
        """Explain why a requirement could not be covered."""
        if requirement.required_capabilities:
            needed = ", ".join(sorted(requirement.required_capabilities))

            return (
                f"No eligible dataset version declares coverage of "
                f"'{requirement.requirement_type}' together with the "
                f"required capabilities: {needed}."
            )

        return (
            f"No eligible dataset version declares coverage of "
            f"'{requirement.requirement_type}'."
        )
