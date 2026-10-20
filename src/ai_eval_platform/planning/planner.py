"""Deterministic, explainable evaluation planning.

The planner is the last stage of the planning core. It combines:

* the requirements an agent-type strategy derives from a change set,
* the baseline requirements that always apply to the agent type,
* observed-risk evidence (Signals, RCA findings),
* the dataset versions that can actually evaluate those requirements.

Design rules enforced here:

* The planner is agent-agnostic. All agent-type knowledge lives in the
  strategy; the planner only orchestrates.
* The same inputs always produce the same plan, including the same
  ``plan_id``. A plan is reproducible from its recorded inputs plus the
  strategy and policy versions.
* Every requirement keeps the references that caused it (``change:``,
  ``signal:``, ``rca_finding:``, ``baseline:``). Merging requirements never
  drops a reason.
* Coverage honesty: a requirement that no eligible dataset version can
  evaluate is reported as a coverage gap, never silently dropped.
* Planning is not execution. The planner never runs a test and never
  approves a deployment. Advisory suggestions (for example from an LLM)
  are recorded for review but never create requirements on their own.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .dataset_matcher import INELIGIBLE_QUALITY_STATUSES, DatasetMatcher
from .domain import (
    AgentDescriptor,
    ChangeDescription,
    DatasetDescriptor,
    EvaluationPlan,
    EvaluationRequirement,
    PlanStatus,
    RiskEvidence,
    make_reason_ref,
)
from .strategies import StrategyRegistry

#: Default policy version recorded on every plan.
DEFAULT_POLICY_VERSION = "1.0.0"

#: Priority ordering used when two requirements are merged.
PRIORITY_ORDER: Mapping[str, int] = {
    "low": 0,
    "medium": 1,
    "high": 2,
    "critical": 3,
}

#: Evidence kinds that describe *observed risk* and may add requirements.
OBSERVED_RISK_KINDS: frozenset[str] = frozenset({"signal", "rca_finding"})


class PlanningError(ValueError):
    """Base class for planning failures."""


class PlanValidationError(PlanningError):
    """Raised when a plan is internally inconsistent."""


def validate_plan(plan: EvaluationPlan) -> None:
    """Validate that a plan is internally consistent.

    A plan is only useful if every match and every gap points at a
    requirement the plan actually contains, and if no requirement is
    claimed to be both covered and uncovered.

    Raises:
        PlanValidationError: when the plan is inconsistent.
    """
    requirement_ids = {item.requirement_id for item in plan.requirements}

    if len(requirement_ids) != len(plan.requirements):
        raise PlanValidationError("requirement_id values must be unique.")

    for match in plan.dataset_matches:
        if match.requirement_id not in requirement_ids:
            raise PlanValidationError(
                "dataset match references unknown requirement "
                f"{match.requirement_id!r}."
            )

    for gap in plan.coverage_gaps:
        if gap.requirement_id not in requirement_ids:
            raise PlanValidationError(
                "coverage gap references unknown requirement "
                f"{gap.requirement_id!r}."
            )

    matched = {match.requirement_id for match in plan.dataset_matches}
    gapped = {gap.requirement_id for gap in plan.coverage_gaps}
    overlap = matched.intersection(gapped)

    if overlap:
        raise PlanValidationError(
            "requirements cannot be both covered and uncovered: "
            + ", ".join(sorted(overlap))
        )


@dataclass(frozen=True)
class PlanRequest:
    """Everything the planner needs to produce a plan.

    The request is a plain value object so that a plan can always be
    reproduced from the request plus the strategy and policy versions.
    """

    agent: AgentDescriptor
    changes: ChangeDescription
    datasets: Sequence[DatasetDescriptor] = ()
    evidence: Sequence[RiskEvidence] = ()
    advisory_suggestions: Sequence[str] = ()
    policy_version: str = DEFAULT_POLICY_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "datasets", tuple(self.datasets))
        object.__setattr__(self, "evidence", tuple(self.evidence))
        object.__setattr__(
            self, "advisory_suggestions", tuple(self.advisory_suggestions)
        )

        if self.changes.agent_id != self.agent.agent_id:
            raise PlanningError(
                "Change description belongs to agent "
                f"{self.changes.agent_id!r}, not {self.agent.agent_id!r}."
            )

        if (
            self.changes.from_agent_version_id
            == self.changes.to_agent_version_id
        ):
            raise PlanningError(
                "Change description must compare two different versions of "
                "the same agent."
            )


def _priority_rank(priority: str) -> int:
    return PRIORITY_ORDER.get(priority, PRIORITY_ORDER["medium"])


def _max_priority(priorities: Iterable[str]) -> str:
    """Return the highest priority in ``priorities`` (ties broken by name)."""
    ordered = sorted(priorities, key=lambda item: (-_priority_rank(item), item))

    if not ordered:
        return "medium"

    return ordered[0]


def _requirement_id_for(agent_type: str, requirement_type: str) -> str:
    """Build the canonical requirement id used for evidence-derived needs.

    The id mirrors the strategy convention ``<agent_type>.<requirement_type>``
    so that an evidence-derived requirement and a strategy-derived one for
    the same requirement type merge instead of duplicating.
    """
    return f"{agent_type}.{requirement_type}"


def _merge_requirements(
    requirements: Sequence[EvaluationRequirement],
) -> tuple[EvaluationRequirement, ...]:
    """Merge requirements of the same type, keeping every reason.

    Merging never drops a reason reference: the union of all
    ``reason_refs`` is preserved, and the highest priority wins.
    """
    groups: dict[str, list[EvaluationRequirement]] = {}

    for requirement in requirements:
        groups.setdefault(requirement.requirement_type, []).append(
            requirement
        )

    merged: list[EvaluationRequirement] = []

    for requirement_type in sorted(groups):
        members = sorted(
            groups[requirement_type],
            key=lambda item: (
                0 if item.reason_refs else 1,
                item.requirement_id,
            ),
        )
        chosen = members[0]

        reason_refs = tuple(
            sorted({ref for member in members for ref in member.reason_refs})
        )
        required_capabilities = frozenset().union(
            *(member.required_capabilities for member in members)
        )
        required_metrics = tuple(
            sorted(
                {
                    metric
                    for member in members
                    for metric in member.required_metrics
                }
            )
        )

        acceptance_criteria: dict[str, Any] = {}

        for member in members:
            for key, value in member.acceptance_criteria.items():
                acceptance_criteria.setdefault(key, value)

        policy_ref = next(
            (
                member.policy_ref
                for member in members
                if member.policy_ref is not None
            ),
            None,
        )

        merged.append(
            EvaluationRequirement(
                requirement_id=chosen.requirement_id,
                requirement_type=requirement_type,
                priority=_max_priority(
                    member.priority for member in members
                ),
                reason_refs=reason_refs,
                required_capabilities=required_capabilities,
                required_metrics=required_metrics,
                acceptance_criteria=acceptance_criteria,
                policy_ref=policy_ref,
            )
        )

    return tuple(merged)


def _ensure_unique_ids(
    requirements: Sequence[EvaluationRequirement],
) -> tuple[EvaluationRequirement, ...]:
    """Guarantee unique requirement ids, deterministically.

    Merging is done per requirement type, so a collision can only happen
    when two different types share an id. The first occurrence keeps the id
    and later ones are suffixed with their requirement type.
    """
    seen: set[str] = set()
    result: list[EvaluationRequirement] = []

    for requirement in requirements:
        requirement_id = requirement.requirement_id

        if requirement_id in seen:
            requirement_id = f"{requirement_id}.{requirement.requirement_type}"

        if requirement_id in seen:
            raise PlanValidationError(
                "requirement ids cannot be made unique: "
                f"{requirement.requirement_id!r}."
            )

        seen.add(requirement_id)

        if requirement_id == requirement.requirement_id:
            result.append(requirement)
        else:
            result.append(
                requirement.model_copy(
                    update={"requirement_id": requirement_id}
                )
            )

    return tuple(result)


def _evidence_requirements(
    agent: AgentDescriptor,
    evidence: Sequence[RiskEvidence],
) -> tuple[EvaluationRequirement, ...]:
    """Turn observed-risk evidence into requirements.

    Evidence only adds a requirement when its scope matches the target
    agent: either it names this agent explicitly, or it names no agent at
    all (agent-agnostic evidence). Evidence scoped to a different agent is
    ignored, because a risk observed elsewhere is not a risk of this agent.

    Advisory suggestions never appear here: they cannot create
    requirements on their own.
    """
    requirements: list[EvaluationRequirement] = []

    for item in sorted(evidence, key=lambda entry: entry.evidence_id):
        if item.kind not in OBSERVED_RISK_KINDS:
            continue

        if item.agent_id is not None and item.agent_id != agent.agent_id:
            continue

        capabilities = (
            frozenset({item.capability})
            if item.capability is not None
            else frozenset()
        )

        requirements.append(
            EvaluationRequirement(
                requirement_id=_requirement_id_for(
                    agent.agent_type, item.requirement_type
                ),
                requirement_type=item.requirement_type,
                priority=item.priority,
                reason_refs=(make_reason_ref(item.kind, item.evidence_id),),
                required_capabilities=capabilities,
                acceptance_criteria={"rationale": item.rationale},
            )
        )

    return tuple(requirements)


def _canonical_json(payload: Any) -> str:
    """Serialize a payload deterministically for hashing."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _plan_id(
    request: PlanRequest,
    strategy_name: str,
    strategy_version: str,
    requirements: Sequence[EvaluationRequirement],
    matches: Sequence[Any],
    gaps: Sequence[Any],
) -> str:
    """Compute a deterministic plan id.

    The id is a hash over the plan's *content*, not over a clock or a
    random value, so the same inputs always yield the same ``plan_id``.

    The hash covers everything that distinguishes one plan from another:
    the agent and the compared versions, the strategy and policy versions,
    the requirements (including their acceptance criteria and policy
    reference) and — crucially — the coverage outcome, meaning which
    dataset version covers which requirement and which requirements are
    left uncovered. Two plans that differ only in their coverage outcome
    are different plans and therefore must not share a ``plan_id``.
    """
    payload = {
        "agent_id": request.agent.agent_id,
        "agent_type": request.agent.agent_type,
        "from_agent_version_id": request.changes.from_agent_version_id,
        "to_agent_version_id": request.changes.to_agent_version_id,
        "change_id": request.changes.change_id,
        "change_completeness": request.changes.completeness,
        "strategy_name": strategy_name,
        "strategy_version": strategy_version,
        "policy_version": request.policy_version,
        "requirements": [
            {
                "requirement_id": item.requirement_id,
                "requirement_type": item.requirement_type,
                "priority": item.priority,
                "reason_refs": list(item.reason_refs),
                "required_capabilities": sorted(item.required_capabilities),
                "required_metrics": list(item.required_metrics),
                "acceptance_criteria": item.acceptance_criteria,
                "policy_ref": item.policy_ref,
            }
            for item in requirements
        ],
        "dataset_matches": [
            {
                "requirement_id": item.requirement_id,
                "dataset_version_id": item.dataset_version_id,
                "coverage_score": item.coverage_score,
                "quality_status": item.quality_status,
                "matched_capabilities": sorted(item.matched_capabilities),
            }
            for item in matches
        ],
        "coverage_gaps": [
            {
                "requirement_id": item.requirement_id,
                "requirement_type": item.requirement_type,
                "reason": item.reason,
                "considered_dataset_version_ids": list(
                    item.considered_dataset_version_ids
                ),
            }
            for item in gaps
        ],
    }

    digest = hashlib.sha256(
        _canonical_json(payload).encode("utf-8")
    ).hexdigest()

    return f"plan_{digest[:32]}"


class EvaluationPlanner:
    """Builds a deterministic, explainable evaluation plan.

    The planner orchestrates the deterministic pieces: it asks the agent
    type's strategy which requirements apply, adds requirements for
    observed-risk evidence, matches those requirements to dataset
    versions, and validates the result. It never executes an evaluation
    and never approves a deployment.
    """

    def __init__(
        self,
        registry: StrategyRegistry,
        matcher: DatasetMatcher | None = None,
    ) -> None:
        self._registry = registry
        self._matcher = matcher if matcher is not None else DatasetMatcher()

    @property
    def registry(self) -> StrategyRegistry:
        return self._registry

    def plan(self, request: PlanRequest) -> EvaluationPlan:
        """Produce an evaluation plan for ``request``.

        The plan is fully determined by the request and the strategy and
        policy versions; calling this twice with the same request yields
        identical plans, including the same ``plan_id``.

        Raises:
            PlanningError: when the request is inconsistent or the agent
                type has no registered strategy.
            PlanValidationError: when the produced plan is inconsistent.
        """
        strategy = self._registry.get(request.agent.agent_type)

        requirements = self._collect_requirements(request, strategy)
        requirements = _merge_requirements(requirements)
        requirements = _ensure_unique_ids(requirements)

        matches, gaps = self._matcher.match(
            requirements,
            request.datasets,
            agent_type=request.agent.agent_type,
        )

        plan = self._build_plan(
            request=request,
            strategy_name=strategy.name,
            strategy_version=strategy.version,
            requirements=requirements,
            matches=matches,
            gaps=gaps,
        )

        validate_plan(plan)

        return plan

    def _collect_requirements(
        self,
        request: PlanRequest,
        strategy: Any,
    ) -> tuple[EvaluationRequirement, ...]:
        """Gather every requirement that applies, in a deterministic order."""
        change_requirements = tuple(
            strategy.requirements_for_changes(
                request.agent, request.changes
            )
        )
        baseline_requirements = tuple(
            strategy.baseline_requirements(request.agent)
        )
        evidence_requirements = _evidence_requirements(
            request.agent, request.evidence
        )

        return (
            baseline_requirements
            + change_requirements
            + evidence_requirements
        )

    def _build_plan(
        self,
        *,
        request: PlanRequest,
        strategy_name: str,
        strategy_version: str,
        requirements: Sequence[EvaluationRequirement],
        matches: Sequence[Any],
        gaps: Sequence[Any],
    ) -> EvaluationPlan:
        """Assemble the plan and its explanations."""
        selected_dataset_version_ids = tuple(
            sorted({match.dataset_version_id for match in matches})
        )

        limitations = list(request.changes.limitations)

        for gap in gaps:
            limitations.append(
                f"Coverage gap: {gap.requirement_type} "
                f"({gap.requirement_id}) is not covered by any eligible "
                "dataset version."
            )

        for suggestion in request.advisory_suggestions:
            limitations.append(
                "Advisory suggestion recorded for review only, not applied "
                f"as a requirement: {suggestion}"
            )

        rationale = self._selection_rationale(
            requirements=requirements,
            matches=matches,
            gaps=gaps,
            strategy_name=strategy_name,
            strategy_version=strategy_version,
            policy_version=request.policy_version,
        )

        metadata: dict[str, Any] = {
            "change_id": request.changes.change_id,
            "change_completeness": request.changes.completeness,
            "change_count": len(request.changes.changes),
            "requirement_count": len(requirements),
            "covered_requirement_count": len(matches),
            "coverage_gap_count": len(gaps),
            "evidence_count": len(request.evidence),
            "advisory_suggestion_count": len(request.advisory_suggestions),
            "ineligible_dataset_count": sum(
                1
                for dataset in request.datasets
                if not dataset.is_active
                or dataset.quality_status in INELIGIBLE_QUALITY_STATUSES
            ),
        }

        return EvaluationPlan(
            plan_id=_plan_id(
                request,
                strategy_name,
                strategy_version,
                requirements,
                matches,
                gaps,
            ),
            agent_id=request.agent.agent_id,
            agent_type=request.agent.agent_type,
            from_agent_version_id=request.changes.from_agent_version_id,
            to_agent_version_id=request.changes.to_agent_version_id,
            requirements=tuple(requirements),
            dataset_matches=tuple(matches),
            coverage_gaps=tuple(gaps),
            selected_dataset_version_ids=selected_dataset_version_ids,
            selection_rationale=rationale,
            strategy_name=strategy_name,
            strategy_version=strategy_version,
            policy_version=request.policy_version,
            change_completeness=request.changes.completeness,
            status=self._status(request, gaps),
            limitations=tuple(limitations),
            metadata=metadata,
        )

    @staticmethod
    def _status(
        request: PlanRequest,
        gaps: Sequence[Any],
    ) -> PlanStatus:
        """Derive the plan status from a fixed policy.

        * ``blocked`` — at least one requirement cannot be evaluated by any
          eligible dataset version, so the plan cannot be executed as
          specified.
        * ``draft`` — every requirement is covered, but the change
          description is not complete, so the scope may still grow.
        * ``ready`` — every requirement is covered and the change
          description is complete.
        """
        if gaps:
            return "blocked"

        if request.changes.completeness != "complete":
            return "draft"

        return "ready"

    @staticmethod
    def _selection_rationale(
        *,
        requirements: Sequence[EvaluationRequirement],
        matches: Sequence[Any],
        gaps: Sequence[Any],
        strategy_name: str,
        strategy_version: str,
        policy_version: str,
    ) -> tuple[str, ...]:
        """Explain, in order, why each requirement exists and what covers it.

        The rationale is the human-readable part of the plan: it names the
        strategy and policy that produced it, and for every requirement it
        states the reasons behind it and the dataset version (or the gap)
        that resulted.
        """
        reasons_by_requirement: dict[str, list[str]] = {}

        for requirement in requirements:
            reasons_by_requirement[requirement.requirement_id] = [
                f"{requirement.requirement_type} "
                f"[{requirement.priority}] -> "
                + ", ".join(requirement.reason_refs)
            ]

        match_by_requirement = {
            match.requirement_id: match for match in matches
        }
        gap_by_requirement = {gap.requirement_id: gap for gap in gaps}

        lines: list[str] = [
            f"Strategy '{strategy_name}' version '{strategy_version}' "
            f"with policy version '{policy_version}' produced "
            f"{len(requirements)} requirement(s)."
        ]

        for requirement in requirements:
            lines.extend(reasons_by_requirement.get(requirement.requirement_id, []))

            match = match_by_requirement.get(requirement.requirement_id)

            if match is not None:
                lines.append(
                    f"  {requirement.requirement_id}: covered by dataset "
                    f"version '{match.dataset_version_id}' "
                    f"(coverage score {match.coverage_score:.2f}, quality "
                    f"'{match.quality_status}')."
                )
                continue

            gap = gap_by_requirement.get(requirement.requirement_id)

            if gap is not None:
                considered = (
                    ", ".join(gap.considered_dataset_version_ids)
                    if gap.considered_dataset_version_ids
                    else "no eligible dataset versions"
                )
                lines.append(
                    f"  {requirement.requirement_id}: coverage gap — "
                    f"{gap.reason} Considered: {considered}."
                )

        return tuple(lines)


def explain_plan(plan: EvaluationPlan) -> str:
    """Render a plan as a human-readable explanation.

    The explanation is derived entirely from the plan, so it never adds
    information the plan does not contain.
    """
    lines: list[str] = [
        f"Plan {plan.plan_id}",
        f"  Agent: {plan.agent_id} (type '{plan.agent_type}')",
        f"  Versions: {plan.from_agent_version_id} -> "
        f"{plan.to_agent_version_id}",
        f"  Strategy: {plan.strategy_name} v{plan.strategy_version}",
        f"  Policy version: {plan.policy_version}",
        f"  Change completeness: {plan.change_completeness}",
        f"  Status: {plan.status}",
        "",
        "Requirements:",
    ]

    for requirement in plan.requirements:
        lines.append(
            f"  - {requirement.requirement_id} "
            f"({requirement.requirement_type}, priority "
            f"{requirement.priority})"
        )
        lines.append(
            "      reasons: " + ", ".join(requirement.reason_refs)
        )

        if requirement.required_capabilities:
            lines.append(
                "      required capabilities: "
                + ", ".join(sorted(requirement.required_capabilities))
            )

        if requirement.required_metrics:
            lines.append(
                "      required metrics: "
                + ", ".join(requirement.required_metrics)
            )

    lines.append("")
    lines.append("Dataset matches:")

    if not plan.dataset_matches:
        lines.append("  (none)")

    for match in plan.dataset_matches:
        lines.append(
            f"  - {match.requirement_id} -> "
            f"{match.dataset_version_id} "
            f"(coverage {match.coverage_score:.2f}, quality "
            f"'{match.quality_status}')"
        )
        lines.append(f"      {match.rationale}")

    lines.append("")
    lines.append("Coverage gaps:")

    if not plan.coverage_gaps:
        lines.append("  (none)")

    for gap in plan.coverage_gaps:
        lines.append(
            f"  - {gap.requirement_id} ({gap.requirement_type}): "
            f"{gap.reason}"
        )

    if plan.selected_dataset_version_ids:
        lines.append("")
        lines.append(
            "Selected dataset versions: "
            + ", ".join(plan.selected_dataset_version_ids)
        )

    if plan.limitations:
        lines.append("")
        lines.append("Limitations:")

        for limitation in plan.limitations:
            lines.append(f"  - {limitation}")

    return "\n".join(lines)
