"""Shared fixtures for the planning test suite.

The planning core is pure and deterministic, so every fixture here builds
plain value objects. No database and no agent runtime is involved.
"""
from __future__ import annotations

import pytest

from ai_eval_platform.planning.domain import (
    AgentDescriptor,
    ChangeDescription,
    ComponentChange,
    DatasetDescriptor,
    EvaluationRequirement,
    RiskEvidence,
    make_reason_ref,
)
from ai_eval_platform.planning.planner import EvaluationPlanner, PlanRequest
from ai_eval_platform.planning.strategies import (
    AgenticRAGStrategy,
    StrategyRegistry,
)

#: Requirement types the canonical strategy derives for an agent that
#: declares every capability.
FULL_COVERAGE_REQUIREMENT_TYPES: tuple[str, ...] = (
    "answer_correctness",
    "groundedness",
    "ranking_quality",
    "retrieval_latency",
    "retrieval_recall",
)

#: Capabilities required by the requirement types above.
FULL_COVERAGE_CAPABILITIES: tuple[str, ...] = ("grounding", "retrieval")


@pytest.fixture()
def registry() -> StrategyRegistry:
    """A registry with the canonical ``agentic_rag`` strategy."""
    return StrategyRegistry([AgenticRAGStrategy()])


@pytest.fixture()
def planner(registry: StrategyRegistry) -> EvaluationPlanner:
    return EvaluationPlanner(registry)


@pytest.fixture()
def agent() -> AgentDescriptor:
    """An agentic RAG agent that declares every relevant capability."""
    return AgentDescriptor(
        agent_id="agent-1",
        agent_type="agentic_rag",
        capabilities=frozenset(
            {"retrieval", "grounding", "generation", "tool_use"}
        ),
    )


@pytest.fixture()
def changes() -> ChangeDescription:
    """A complete change description touching the retriever."""
    return ChangeDescription(
        change_id="change-1",
        agent_id="agent-1",
        from_agent_version_id="v1",
        to_agent_version_id="v2",
        changes=(
            ComponentChange(
                component="retriever",
                change_type="retriever",
                operation="modified",
                source="version_diff",
                evidence_refs=("evidence-1",),
                before_fingerprint="fp-before",
                after_fingerprint="fp-after",
                confidence=0.95,
            ),
        ),
        completeness="complete",
    )


def make_full_dataset(dataset_version_id: str, **kwargs) -> DatasetDescriptor:
    """A dataset that declares coverage for every canonical requirement type."""
    defaults = {
        "dataset_version_id": dataset_version_id,
        "capabilities_covered": frozenset(FULL_COVERAGE_CAPABILITIES),
        "requirement_types_covered": frozenset(
            FULL_COVERAGE_REQUIREMENT_TYPES
        ),
        "quality_status": "validated",
        "validation_evidence_refs": ("evidence-1",),
    }
    defaults.update(kwargs)

    return DatasetDescriptor(**defaults)


def make_request(
    agent: AgentDescriptor,
    changes: ChangeDescription,
    **kwargs,
) -> PlanRequest:
    """Build a plan request for ``agent`` and ``changes``."""
    return PlanRequest(agent=agent, changes=changes, **kwargs)


def make_dataset(
    dataset_version_id: str,
    *,
    requirement_types: tuple[str, ...] = (),
    capabilities: tuple[str, ...] = (),
    quality_status: str = "validated",
    agent_types: frozenset[str] | None = None,
    is_active: bool = True,
    estimated_cost: float | None = None,
    validation_evidence_refs: tuple[str, ...] = ("evidence-1",),
) -> DatasetDescriptor:
    """Build a dataset descriptor that declares its coverage explicitly."""
    return DatasetDescriptor(
        dataset_version_id=dataset_version_id,
        capabilities_covered=frozenset(capabilities),
        requirement_types_covered=frozenset(requirement_types),
        agent_types_supported=agent_types,
        quality_status=quality_status,
        validation_evidence_refs=validation_evidence_refs,
        is_active=is_active,
        estimated_cost=estimated_cost,
    )


def make_requirement(
    requirement_id: str,
    requirement_type: str,
    *,
    priority: str = "medium",
    reason_refs: tuple[str, ...] | None = None,
    capabilities: frozenset[str] = frozenset(),
    metrics: tuple[str, ...] = (),
    acceptance_criteria: dict | None = None,
    policy_ref: str | None = None,
) -> EvaluationRequirement:
    """Build a requirement with well-formed reason references."""
    return EvaluationRequirement(
        requirement_id=requirement_id,
        requirement_type=requirement_type,
        priority=priority,
        reason_refs=reason_refs
        or (make_reason_ref("change", "change-1"),),
        required_capabilities=capabilities,
        required_metrics=metrics,
        acceptance_criteria=acceptance_criteria or {},
        policy_ref=policy_ref,
    )


def make_evidence(
    evidence_id: str,
    requirement_type: str,
    *,
    kind: str = "signal",
    agent_id: str | None = None,
    capability: str | None = None,
    priority: str = "medium",
    rationale: str = "Observed failure in production.",
    recurrence_count: int = 1,
) -> RiskEvidence:
    """Build observed-risk evidence."""
    return RiskEvidence(
        evidence_id=evidence_id,
        kind=kind,
        requirement_type=requirement_type,
        agent_id=agent_id,
        capability=capability,
        priority=priority,
        rationale=rationale,
        recurrence_count=recurrence_count,
    )


@pytest.fixture()
def dataset_factory():
    """Factory for dataset descriptors with explicit coverage."""
    return make_dataset


@pytest.fixture()
def full_dataset_factory():
    """Factory for a dataset covering every canonical requirement type."""
    return make_full_dataset


@pytest.fixture()
def request_factory():
    """Factory for plan requests."""
    return make_request


@pytest.fixture()
def requirement_factory():
    """Factory for evaluation requirements."""
    return make_requirement


@pytest.fixture()
def evidence_factory():
    """Factory for observed-risk evidence."""
    return make_evidence

