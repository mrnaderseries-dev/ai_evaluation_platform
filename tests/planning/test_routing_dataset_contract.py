"""Tests for the routing -> planning dataset-selection contract."""

from __future__ import annotations

from ai_eval_platform.planning import (
    EvaluationPlanningOrchestrator,
    EvaluationPlanner,
)
from ai_eval_platform.planning.domain import (
    ChangeDescription,
    ComponentChange,
    DatasetDescriptor,
    EvaluationPlan,
    EvaluationRequirement,
)
from ai_eval_platform.planning.planner import PlanRequest
from ai_eval_platform.planning.strategies import (
    AgenticRAGStrategy,
    StrategyRegistry,
)
from ai_eval_platform.routing import (
    AgentDescriptor as RoutingAgentDescriptor,
    AgentRoutingRegistry,
)
from tests.routing.conftest import StubStrategy


def _routing_registry():
    return AgentRoutingRegistry([StubStrategy()])


def _planning_registry():
    return StrategyRegistry([AgenticRAGStrategy()])


def _changes():
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


def _routing_descriptor():
    return RoutingAgentDescriptor(
        agent_id="agent-1",
        agent_type="agentic_rag",
        protocol="http",
        capabilities=frozenset({"retrieval", "grounding", "generation"}),
        agent_version="1.0.0",
    )


def _dataset(version_id, **kwargs):
    defaults = dict(
        dataset_version_id=version_id,
        capabilities_covered=frozenset({"retrieval", "grounding"}),
        requirement_types_covered=frozenset(
            {
                "answer_correctness",
                "groundedness",
                "ranking_quality",
                "retrieval_latency",
                "retrieval_recall",
            }
        ),
        quality_status="validated",
        validation_evidence_refs=("evidence-1",),
        is_active=True,
    )
    defaults.update(kwargs)
    return DatasetDescriptor(**defaults)


def test_routing_and_planning_agree_on_valid_dataset():
    orchestrator = EvaluationPlanningOrchestrator(
        registry=_routing_registry(),
        planner=EvaluationPlanner(_planning_registry()),
    )
    plan = orchestrator.plan(
        routing_descriptor=_routing_descriptor(),
        changes=_changes(),
        datasets=[_dataset("ds-1")],
    )
    assert isinstance(plan, EvaluationPlan)
    assert plan.metadata["allowed_dataset_version_ids"] == ["1.0.0", "ds-1"]


def test_routing_recommended_dataset_fails_coverage():
    orchestrator = EvaluationPlanningOrchestrator(
        registry=_routing_registry(),
        planner=EvaluationPlanner(_planning_registry()),
    )
    plan = orchestrator.plan(
        routing_descriptor=_routing_descriptor(),
        changes=_changes(),
        datasets=[_dataset("other-version")],
    )
    assert plan.status == "blocked"
    assert len(plan.coverage_gaps) > 0
    assert any("Routing selection" in lim for lim in plan.limitations)


def test_multiple_candidates_different_coverage():
    from ai_eval_platform.planning.dataset_matcher import DatasetMatcher
    from ai_eval_platform.planning.domain import make_reason_ref

    matcher = DatasetMatcher()
    r = EvaluationRequirement(
        requirement_id="agentic_rag.answer_correctness",
        requirement_type="answer_correctness",
        reason_refs=(make_reason_ref("change", "change-1"),),
    )
    full_ds = _dataset("ds-1", requirement_types_covered=frozenset({"answer_correctness"}))
    partial_ds = _dataset("partial", requirement_types_covered=frozenset())
    matches, gaps = matcher.match(
        [r],
        [full_ds, partial_ds],
        allowed_dataset_version_ids=("ds-1",),
    )
    assert len(matches) == 1
    assert matches[0].dataset_version_id == "ds-1"


def test_no_eligible_dataset_exists():
    orchestrator = EvaluationPlanningOrchestrator(
        registry=_routing_registry(),
        planner=EvaluationPlanner(_planning_registry()),
    )
    plan = orchestrator.plan(
        routing_descriptor=_routing_descriptor(),
        changes=_changes(),
        datasets=[],
    )
    assert plan.status == "blocked"
    assert len(plan.coverage_gaps) > 0


def test_routing_output_absent_optional_direct_planner():
    planner = EvaluationPlanner(_planning_registry())
    from ai_eval_platform.planning.domain import AgentDescriptor as PlanningAgentDescriptor

    agent = PlanningAgentDescriptor(
        agent_id="agent-1",
        agent_type="agentic_rag",
        capabilities=frozenset({"retrieval", "grounding", "generation"}),
    )
    plan = planner.plan(
        PlanRequest(
            agent=agent,
            changes=_changes(),
            datasets=[_dataset("any-version")],
        )
    )
    assert plan.metadata["allowed_dataset_version_ids"] == []


def test_deterministic_selection_tied_scores():
    from ai_eval_platform.planning.dataset_matcher import DatasetMatcher
    from ai_eval_platform.planning.domain import EvaluationRequirement, make_reason_ref

    matcher = DatasetMatcher()
    r = EvaluationRequirement(
        requirement_id="agentic_rag.answer_correctness",
        requirement_type="answer_correctness",
        reason_refs=(make_reason_ref("change", "change-1"),),
    )
    a = _dataset("a", dataset_version_id="a")
    b = _dataset("b", dataset_version_id="b")
    m1, _ = matcher.match([r], [a, b])
    m2, _ = matcher.match([r], [b, a])
    assert m1[0].dataset_version_id == m2[0].dataset_version_id == "a"


def test_routing_constraint_conflicts_with_strategy_requirements():
    orchestrator = EvaluationPlanningOrchestrator(
        registry=_routing_registry(),
        planner=EvaluationPlanner(_planning_registry()),
    )
    ds = _dataset("ds-1", requirement_types_covered=frozenset())
    plan = orchestrator.plan(
        routing_descriptor=_routing_descriptor(),
        changes=_changes(),
        datasets=[ds],
    )
    assert plan.status == "blocked"
    assert len(plan.coverage_gaps) > 0
