"""Tests for requirement merging, evidence scoping, and advisory behaviour.

Merging is the step where several sources of truth (baseline checks,
change-derived checks, observed-risk evidence) collapse into one
requirement per requirement type. The rules under test:

* A merge never drops a reason reference.
* A merge never lowers the priority.
* A merge unions capabilities and metrics.
* Evidence only applies to the agent it is scoped to.
* Advisory suggestions are recorded but never become requirements.
"""
from __future__ import annotations

import pytest

from ai_eval_platform.planning.domain import (
    AgentDescriptor,
    ChangeDescription,
    ComponentChange,
    EvaluationRequirement,
    make_reason_ref,
)
from ai_eval_platform.planning.planner import EvaluationPlanner, PlanRequest
from ai_eval_platform.planning.strategies import StrategyRegistry


class StaticStrategy:
    """A strategy that returns fixed requirements.

    It exists so the merge rules can be tested with requirements that
    differ in every mergeable field, which the canonical strategy never
    produces on its own.
    """

    def __init__(self, requirements: tuple[EvaluationRequirement, ...]) -> None:
        self._requirements = tuple(requirements)

    @property
    def supported_agent_types(self) -> frozenset[str]:
        return frozenset({"custom_rag"})

    @property
    def name(self) -> str:
        return "static"

    @property
    def version(self) -> str:
        return "1.0.0"

    def requirements_for_changes(self, agent, changes):
        return ()

    def baseline_requirements(self, agent):
        return self._requirements


@pytest.fixture()
def custom_agent() -> AgentDescriptor:
    """An agent whose type is served by the static test strategy."""
    return AgentDescriptor(
        agent_id="agent-1",
        agent_type="custom_rag",
        capabilities=frozenset({"retrieval", "grounding"}),
    )


def _plan_for(agent, changes, requirements, **kwargs):
    """Plan using a static strategy that emits ``requirements``."""
    registry = StrategyRegistry([StaticStrategy(requirements)])

    return EvaluationPlanner(registry).plan(
        PlanRequest(agent=agent, changes=changes, **kwargs)
    )



class TestRequirementMerging:
    def test_same_type_requirements_merge_into_one(
        self, custom_agent, changes, requirement_factory
    ):
        first = requirement_factory(
            "custom_rag.answer_quality",
            "answer_quality",
            reason_refs=(make_reason_ref("baseline", "static"),),
        )
        second = requirement_factory(
            "custom_rag.answer_quality-2",
            "answer_quality",
            reason_refs=(make_reason_ref("change", "change-1"),),
        )

        plan = _plan_for(
            custom_agent,
            changes,
            (second, first),
        )

        assert len(plan.requirements) == 1
        assert plan.requirements[0].requirement_type == "answer_quality"

    def test_merge_preserves_every_reason_ref(
        self, custom_agent, changes, requirement_factory
    ):
        first = requirement_factory(
            "custom_rag.answer_quality",
            "answer_quality",
            reason_refs=(make_reason_ref("baseline", "static"),),
        )
        second = requirement_factory(
            "custom_rag.answer_quality-2",
            "answer_quality",
            reason_refs=(
                make_reason_ref("change", "change-1"),
                make_reason_ref("signal", "signal-1"),
            ),
        )

        plan = _plan_for(custom_agent, changes, (first, second))

        merged = plan.requirements[0]

        assert set(merged.reason_refs) == {
            "baseline:static",
            "change:change-1",
            "signal:signal-1",
        }

    def test_merge_keeps_the_highest_priority(
        self, custom_agent, changes, requirement_factory
    ):
        low = requirement_factory(
            "custom_rag.answer_quality",
            "answer_quality",
            priority="low",
            reason_refs=(make_reason_ref("baseline", "static"),),
        )
        critical = requirement_factory(
            "custom_rag.answer_quality-2",
            "answer_quality",
            priority="critical",
            reason_refs=(make_reason_ref("change", "change-1"),),
        )

        plan = _plan_for(custom_agent, changes, (low, critical))

        assert plan.requirements[0].priority == "critical"

    def test_merge_unions_capabilities_and_metrics(
        self, custom_agent, changes, requirement_factory
    ):
        first = requirement_factory(
            "custom_rag.answer_quality",
            "answer_quality",
            capabilities=frozenset({"retrieval"}),
            metrics=("mrr",),
            reason_refs=(make_reason_ref("baseline", "static"),),
        )
        second = requirement_factory(
            "custom_rag.answer_quality-2",
            "answer_quality",
            capabilities=frozenset({"grounding"}),
            metrics=("ndcg", "mrr"),
            reason_refs=(make_reason_ref("change", "change-1"),),
        )

        plan = _plan_for(custom_agent, changes, (first, second))

        merged = plan.requirements[0]

        assert merged.required_capabilities == frozenset(
            {"retrieval", "grounding"}
        )
        assert merged.required_metrics == ("mrr", "ndcg")

    def test_merge_keeps_acceptance_criteria_and_policy_ref(
        self, custom_agent, changes, requirement_factory
    ):
        first = requirement_factory(
            "custom_rag.answer_quality",
            "answer_quality",
            acceptance_criteria={"threshold": 0.9},
            reason_refs=(make_reason_ref("baseline", "static"),),
        )
        second = requirement_factory(
            "custom_rag.answer_quality-2",
            "answer_quality",
            acceptance_criteria={"reviewer": "qa"},
            policy_ref="policy-1",
            reason_refs=(make_reason_ref("change", "change-1"),),
        )

        plan = _plan_for(custom_agent, changes, (first, second))

        merged = plan.requirements[0]

        assert merged.acceptance_criteria == {
            "threshold": 0.9,
            "reviewer": "qa",
        }
        assert merged.policy_ref == "policy-1"

    def test_distinct_types_are_not_merged(
        self, custom_agent, changes, requirement_factory
    ):
        first = requirement_factory(
            "custom_rag.answer_quality",
            "answer_quality",
            reason_refs=(make_reason_ref("baseline", "static"),),
        )
        second = requirement_factory(
            "custom_rag.groundedness",
            "groundedness",
            reason_refs=(make_reason_ref("baseline", "static"),),
        )

        plan = _plan_for(custom_agent, changes, (first, second))

        assert len(plan.requirements) == 2
        assert {item.requirement_type for item in plan.requirements} == {
            "answer_quality",
            "groundedness",
        }

    def test_merged_requirements_are_ordered_by_type(
        self, custom_agent, changes, requirement_factory
    ):
        first = requirement_factory(
            "custom_rag.groundedness",
            "groundedness",
            reason_refs=(make_reason_ref("baseline", "static"),),
        )
        second = requirement_factory(
            "custom_rag.answer_quality",
            "answer_quality",
            reason_refs=(make_reason_ref("baseline", "static"),),
        )

        plan = _plan_for(custom_agent, changes, (first, second))

        assert [item.requirement_type for item in plan.requirements] == [
            "answer_quality",
            "groundedness",
        ]


class TestEvidenceScoping:
    def test_evidence_for_this_agent_creates_a_requirement(
        self, planner, agent, changes, evidence_factory
    ):
        evidence = evidence_factory(
            "signal-1",
            "abstention",
            kind="signal",
            agent_id="agent-1",
            capability="generation",
            priority="high",
        )

        plan = planner.plan(
            PlanRequest(agent=agent, changes=changes, evidence=(evidence,))
        )

        abstention = next(
            item
            for item in plan.requirements
            if item.requirement_type == "abstention"
        )

        assert abstention.priority == "high"
        assert abstention.reason_refs == ("signal:signal-1",)
        assert abstention.required_capabilities == frozenset({"generation"})

    def test_evidence_for_another_agent_is_ignored(
        self, planner, agent, changes, evidence_factory
    ):
        evidence = evidence_factory(
            "signal-1",
            "abstention",
            kind="signal",
            agent_id="agent-other",
        )

        plan = planner.plan(
            PlanRequest(agent=agent, changes=changes, evidence=(evidence,))
        )

        assert all(
            item.requirement_type != "abstention"
            for item in plan.requirements
        )

    def test_agent_agnostic_evidence_applies(
        self, planner, agent, changes, evidence_factory
    ):
        evidence = evidence_factory(
            "rca-1",
            "abstention",
            kind="rca_finding",
            agent_id=None,
        )

        plan = planner.plan(
            PlanRequest(agent=agent, changes=changes, evidence=(evidence,))
        )

        abstention = next(
            item
            for item in plan.requirements
            if item.requirement_type == "abstention"
        )

        assert abstention.reason_refs == ("rca_finding:rca-1",)

    def test_evidence_merges_with_a_strategy_requirement(
        self, planner, agent, changes, evidence_factory
    ):
        evidence = evidence_factory(
            "signal-1",
            "answer_correctness",
            kind="signal",
            agent_id="agent-1",
            priority="critical",
        )

        plan = planner.plan(
            PlanRequest(agent=agent, changes=changes, evidence=(evidence,))
        )

        merged = next(
            item
            for item in plan.requirements
            if item.requirement_type == "answer_correctness"
        )

        assert "signal:signal-1" in merged.reason_refs
        assert "baseline:agentic_rag" in merged.reason_refs
        assert merged.priority == "critical"

    def test_advisory_evidence_creates_no_requirement(
        self, planner, agent, changes, evidence_factory
    ):
        evidence = evidence_factory(
            "advisory-1",
            "abstention",
            kind="baseline",
            agent_id="agent-1",
        )

        plan = planner.plan(
            PlanRequest(agent=agent, changes=changes, evidence=(evidence,))
        )

        assert all(
            item.requirement_type != "abstention"
            for item in plan.requirements
        )


class TestAdvisorySuggestions:
    def test_advisory_suggestion_creates_no_requirement(
        self, planner, agent, changes
    ):
        plan = planner.plan(
            PlanRequest(
                agent=agent,
                changes=changes,
                advisory_suggestions=("also evaluate hallucination rate",),
            )
        )

        assert all(
            item.requirement_type != "hallucination"
            for item in plan.requirements
        )

    def test_advisory_suggestion_is_recorded_as_a_limitation(
        self, planner, agent, changes
    ):
        suggestion = "also evaluate hallucination rate"

        plan = planner.plan(
            PlanRequest(
                agent=agent,
                changes=changes,
                advisory_suggestions=(suggestion,),
            )
        )

        assert any(
            suggestion in limitation for limitation in plan.limitations
        )
        assert plan.metadata["advisory_suggestion_count"] == 1

    def test_advisory_suggestion_does_not_change_the_requirement_count(
        self, planner, agent, changes
    ):
        without = planner.plan(PlanRequest(agent=agent, changes=changes))
        with_suggestion = planner.plan(
            PlanRequest(
                agent=agent,
                changes=changes,
                advisory_suggestions=("also evaluate hallucination rate",),
            )
        )

        assert (
            len(with_suggestion.requirements) == len(without.requirements)
        )


class TestStrategyApplicability:
    def test_requirement_needing_an_undeclared_capability_is_dropped(
        self, planner, changes
    ):
        agent = AgentDescriptor(
            agent_id="agent-1",
            agent_type="agentic_rag",
            capabilities=frozenset({"generation"}),
        )

        plan = planner.plan(PlanRequest(agent=agent, changes=changes))

        types = {item.requirement_type for item in plan.requirements}

        assert "retrieval_recall" not in types
        assert "groundedness" not in types
        assert "answer_correctness" in types

    def test_unknown_change_type_adds_no_requirement(
        self, planner, agent
    ):
        changes = ChangeDescription(
            change_id="change-unknown",
            agent_id="agent-1",
            from_agent_version_id="v1",
            to_agent_version_id="v2",
            changes=(
                ComponentChange(
                    component="mystery",
                    change_type="unknown",
                    operation="modified",
                    source="version_diff",
                ),
            ),
            completeness="complete",
        )

        plan = planner.plan(PlanRequest(agent=agent, changes=changes))

        types = {item.requirement_type for item in plan.requirements}

        # Only the baseline requirements remain.
        assert types == {
            "answer_correctness",
            "groundedness",
            "retrieval_recall",
        }

    def test_change_derived_requirement_records_the_change_reason(
        self, planner, agent, changes
    ):
        plan = planner.plan(PlanRequest(agent=agent, changes=changes))

        ranking = next(
            item
            for item in plan.requirements
            if item.requirement_type == "ranking_quality"
        )

        assert "change:change-1" in ranking.reason_refs



