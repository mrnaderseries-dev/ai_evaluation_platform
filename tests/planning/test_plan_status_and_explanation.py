"""Tests for plan status derivation and plan explanation.

The status is a policy decision derived from the coverage outcome and the
completeness of the change description. The explanation must be derived
entirely from the plan, so it never introduces information the plan does
not already contain.
"""
from __future__ import annotations

from ai_eval_platform.planning.domain import (
    ChangeDescription,
    ComponentChange,
)
from ai_eval_platform.planning.planner import PlanRequest, explain_plan


def _partial_changes(agent) -> ChangeDescription:
    """A change description with unknown completeness."""
    return ChangeDescription(
        change_id="change-partial",
        agent_id=agent.agent_id,
        from_agent_version_id="v1",
        to_agent_version_id="v2",
        changes=(
            ComponentChange(
                component="retriever",
                change_type="retriever",
                operation="modified",
                source="version_diff",
            ),
        ),
        completeness="unknown",
    )


class TestPlanStatus:
    def test_ready_when_fully_covered_and_change_is_complete(
        self, planner, agent, changes, full_dataset_factory
    ):
        plan = planner.plan(
            PlanRequest(
                agent=agent,
                changes=changes,
                datasets=(full_dataset_factory("dv-1"),),
            )
        )

        assert plan.status == "ready"

    def test_draft_when_covered_but_change_is_incomplete(
        self, planner, agent, full_dataset_factory
    ):
        plan = planner.plan(
            PlanRequest(
                agent=agent,
                changes=_partial_changes(agent),
                datasets=(full_dataset_factory("dv-1"),),
            )
        )

        assert plan.status == "draft"

    def test_blocked_when_a_requirement_cannot_be_covered(
        self, planner, agent, changes, dataset_factory
    ):
        plan = planner.plan(
            PlanRequest(
                agent=agent,
                changes=changes,
                datasets=(dataset_factory("dv-1"),),
            )
        )

        assert plan.status == "blocked"

    def test_blocked_takes_precedence_over_incomplete_changes(
        self, planner, agent, dataset_factory
    ):
        plan = planner.plan(
            PlanRequest(
                agent=agent,
                changes=_partial_changes(agent),
                datasets=(dataset_factory("dv-1"),),
            )
        )

        assert plan.status == "blocked"

    def test_change_completeness_is_recorded_on_the_plan(
        self, planner, agent, full_dataset_factory
    ):
        plan = planner.plan(
            PlanRequest(
                agent=agent,
                changes=_partial_changes(agent),
                datasets=(full_dataset_factory("dv-1"),),
            )
        )

        assert plan.change_completeness == "unknown"


class TestPlanExplanation:
    def test_explanation_names_the_plan_and_agent(
        self, planner, agent, changes, full_dataset_factory
    ):
        plan = planner.plan(
            PlanRequest(
                agent=agent,
                changes=changes,
                datasets=(full_dataset_factory("dv-1"),),
            )
        )

        explanation = explain_plan(plan)

        assert plan.plan_id in explanation
        assert agent.agent_id in explanation
        assert "agentic_rag" in explanation

    def test_explanation_lists_requirements_and_reasons(
        self, planner, agent, changes, full_dataset_factory
    ):
        plan = planner.plan(
            PlanRequest(
                agent=agent,
                changes=changes,
                datasets=(full_dataset_factory("dv-1"),),
            )
        )

        explanation = explain_plan(plan)

        for requirement in plan.requirements:
            assert requirement.requirement_id in explanation

        assert "reasons:" in explanation

    def test_explanation_reports_dataset_matches(
        self, planner, agent, changes, full_dataset_factory
    ):
        plan = planner.plan(
            PlanRequest(
                agent=agent,
                changes=changes,
                datasets=(full_dataset_factory("dv-1"),),
            )
        )

        explanation = explain_plan(plan)

        assert "dv-1" in explanation
        assert "Dataset matches:" in explanation

    def test_explanation_reports_coverage_gaps(
        self, planner, agent, changes, dataset_factory
    ):
        plan = planner.plan(
            PlanRequest(
                agent=agent,
                changes=changes,
                datasets=(dataset_factory("dv-1"),),
            )
        )

        explanation = explain_plan(plan)

        assert "Coverage gaps:" in explanation
        assert "no eligible dataset versions" not in explanation

    def test_selection_rationale_names_strategy_and_policy(
        self, planner, agent, changes, full_dataset_factory
    ):
        plan = planner.plan(PlanRequest(agent=agent, changes=changes))

        rationale = "\n".join(plan.selection_rationale)

        assert plan.strategy_name in rationale
        assert plan.strategy_version in rationale
        assert plan.policy_version in rationale

    def test_selection_rationale_covers_every_requirement(
        self, planner, agent, changes, full_dataset_factory
    ):
        plan = planner.plan(PlanRequest(agent=agent, changes=changes))

        rationale = "\n".join(plan.selection_rationale)

        for requirement in plan.requirements:
            assert requirement.requirement_id in rationale
