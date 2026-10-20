"""Tests for deterministic plan identity.

A plan must be reproducible: the same inputs always produce the same
``plan_id``, and two plans that differ in any meaningful way — including
their coverage outcome — must never share an id.
"""
from __future__ import annotations

from ai_eval_platform.planning.planner import PlanRequest


class TestDeterministicPlanId:
    def test_same_request_produces_the_same_plan_id(
        self, planner, agent, changes
    ):
        first = planner.plan(PlanRequest(agent=agent, changes=changes))
        second = planner.plan(PlanRequest(agent=agent, changes=changes))

        assert first.plan_id == second.plan_id

    def test_same_request_produces_identical_plans(
        self, planner, agent, changes, full_dataset_factory
    ):
        request = PlanRequest(
            agent=agent,
            changes=changes,
            datasets=(full_dataset_factory("dv-1"),),
        )

        first = planner.plan(request)
        second = planner.plan(request)

        assert first == second

    def test_plan_id_has_a_stable_prefix(
        self, planner, agent, changes
    ):
        plan = planner.plan(PlanRequest(agent=agent, changes=changes))

        assert plan.plan_id.startswith("plan_")
        assert len(plan.plan_id) == len("plan_") + 32

    def test_different_coverage_outcome_yields_a_different_id(
        self, planner, agent, changes, full_dataset_factory
    ):
        covered = planner.plan(
            PlanRequest(
                agent=agent,
                changes=changes,
                datasets=(full_dataset_factory("dv-1"),),
            )
        )
        blocked = planner.plan(PlanRequest(agent=agent, changes=changes))

        assert covered.plan_id != blocked.plan_id

    def test_different_dataset_version_yields_a_different_id(
        self, planner, agent, changes, full_dataset_factory
    ):
        first = planner.plan(
            PlanRequest(
                agent=agent,
                changes=changes,
                datasets=(full_dataset_factory("dv-1"),),
            )
        )
        second = planner.plan(
            PlanRequest(
                agent=agent,
                changes=changes,
                datasets=(full_dataset_factory("dv-2"),),
            )
        )

        assert first.plan_id != second.plan_id

    def test_different_policy_version_yields_a_different_id(
        self, planner, agent, changes
    ):
        first = planner.plan(
            PlanRequest(agent=agent, changes=changes, policy_version="1.0.0")
        )
        second = planner.plan(
            PlanRequest(agent=agent, changes=changes, policy_version="2.0.0")
        )

        assert first.plan_id != second.plan_id

    def test_different_target_version_yields_a_different_id(
        self, planner, agent, changes
    ):
        from ai_eval_platform.planning.domain import ChangeDescription

        other = ChangeDescription(
            change_id=changes.change_id,
            agent_id=changes.agent_id,
            from_agent_version_id="v1",
            to_agent_version_id="v3",
            changes=changes.changes,
            completeness=changes.completeness,
        )

        first = planner.plan(PlanRequest(agent=agent, changes=changes))
        second = planner.plan(PlanRequest(agent=agent, changes=other))

        assert first.plan_id != second.plan_id

    def test_different_evidence_yields_a_different_id(
        self, planner, agent, changes, evidence_factory
    ):
        without = planner.plan(PlanRequest(agent=agent, changes=changes))
        with_evidence = planner.plan(
            PlanRequest(
                agent=agent,
                changes=changes,
                evidence=(
                    evidence_factory(
                        "signal-1",
                        "abstention",
                        kind="signal",
                        agent_id="agent-1",
                    ),
                ),
            )
        )

        assert without.plan_id != with_evidence.plan_id

    def test_advisory_suggestions_do_not_change_the_plan_id(
        self, planner, agent, changes
    ):
        without = planner.plan(PlanRequest(agent=agent, changes=changes))
        with_suggestion = planner.plan(
            PlanRequest(
                agent=agent,
                changes=changes,
                advisory_suggestions=("also evaluate hallucination",),
            )
        )

        assert without.plan_id == with_suggestion.plan_id

    def test_dataset_order_does_not_change_the_plan_id(
        self, planner, agent, changes, full_dataset_factory
    ):
        first = planner.plan(
            PlanRequest(
                agent=agent,
                changes=changes,
                datasets=(
                    full_dataset_factory("dv-1"),
                    full_dataset_factory("dv-2"),
                ),
            )
        )
        second = planner.plan(
            PlanRequest(
                agent=agent,
                changes=changes,
                datasets=(
                    full_dataset_factory("dv-2"),
                    full_dataset_factory("dv-1"),
                ),
            )
        )

        assert first.plan_id == second.plan_id
