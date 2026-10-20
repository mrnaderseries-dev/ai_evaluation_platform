"""Tests for dataset coverage matching and coverage-gap reporting.

A plan must never pretend that a requirement can be evaluated when no
eligible dataset version declares coverage for it. Uncoverable
requirements become explicit coverage gaps, and the plan status reflects
that it cannot be executed as specified.
"""
from __future__ import annotations

from ai_eval_platform.planning.planner import PlanRequest

#: Every requirement type the canonical strategy derives for the canonical
#: agent with the canonical retriever change.
ALL_REQUIREMENT_TYPES: tuple[str, ...] = (
    "answer_correctness",
    "groundedness",
    "ranking_quality",
    "retrieval_latency",
    "retrieval_recall",
)

#: Capabilities needed by the requirements above.
ALL_CAPABILITIES: tuple[str, ...] = ("grounding", "retrieval")


class TestDatasetMatching:
    def test_covering_dataset_matches_every_requirement(
        self, planner, agent, changes, full_dataset_factory
    ):
        plan = planner.plan(
            PlanRequest(
                agent=agent,
                changes=changes,
                datasets=(full_dataset_factory("dv-1"),),
            )
        )

        assert len(plan.dataset_matches) == len(plan.requirements)
        assert plan.coverage_gaps == ()
        assert plan.selected_dataset_version_ids == ("dv-1",)

    def test_match_records_the_requirement_and_dataset(
        self, planner, agent, changes, full_dataset_factory
    ):
        plan = planner.plan(
            PlanRequest(
                agent=agent,
                changes=changes,
                datasets=(full_dataset_factory("dv-1"),),
            )
        )

        match = plan.dataset_matches[0]

        assert match.requirement_id in {
            item.requirement_id for item in plan.requirements
        }
        assert match.dataset_version_id == "dv-1"
        assert match.quality_status == "validated"
        assert 0.0 <= match.coverage_score <= 1.0
        assert match.rationale.strip()

    def test_dataset_without_declared_coverage_matches_nothing(
        self, planner, agent, changes, dataset_factory
    ):
        dataset = dataset_factory("dv-1")

        plan = planner.plan(
            PlanRequest(agent=agent, changes=changes, datasets=(dataset,))
        )

        assert plan.dataset_matches == ()
        assert len(plan.coverage_gaps) == len(plan.requirements)

    def test_inactive_dataset_is_ineligible(
        self, planner, agent, changes, full_dataset_factory
    ):
        dataset = full_dataset_factory("dv-1", is_active=False)

        plan = planner.plan(
            PlanRequest(agent=agent, changes=changes, datasets=(dataset,))
        )

        assert plan.dataset_matches == ()
        assert plan.metadata["ineligible_dataset_count"] == 1

    def test_invalid_quality_dataset_is_ineligible(
        self, planner, agent, changes, full_dataset_factory
    ):
        dataset = full_dataset_factory("dv-1", quality_status="invalid")

        plan = planner.plan(
            PlanRequest(agent=agent, changes=changes, datasets=(dataset,))
        )

        assert plan.dataset_matches == ()
        assert plan.metadata["ineligible_dataset_count"] == 1

    def test_dataset_for_another_agent_type_is_ineligible(
        self, planner, agent, changes, full_dataset_factory
    ):
        dataset = full_dataset_factory(
            "dv-1", agent_types_supported=frozenset({"other_type"})
        )

        plan = planner.plan(
            PlanRequest(agent=agent, changes=changes, datasets=(dataset,))
        )

        assert plan.dataset_matches == ()

    def test_dataset_for_the_agent_type_is_eligible(
        self, planner, agent, changes, full_dataset_factory
    ):
        dataset = full_dataset_factory(
            "dv-1", agent_types_supported=frozenset({"agentic_rag"})
        )

        plan = planner.plan(
            PlanRequest(agent=agent, changes=changes, datasets=(dataset,))
        )

        assert len(plan.dataset_matches) == len(plan.requirements)


class TestCoverageGaps:
    def test_uncovered_requirement_becomes_a_gap(
        self, planner, agent, changes, dataset_factory
    ):
        dataset = dataset_factory(
            "dv-1",
            requirement_types=("answer_correctness",),
            capabilities=ALL_CAPABILITIES,
        )

        plan = planner.plan(
            PlanRequest(agent=agent, changes=changes, datasets=(dataset,))
        )

        gapped = {gap.requirement_type for gap in plan.coverage_gaps}

        assert "groundedness" in gapped
        assert "answer_correctness" not in gapped

    def test_gap_records_the_considered_dataset_versions(
        self, planner, agent, changes, dataset_factory
    ):
        dataset = dataset_factory("dv-1")

        plan = planner.plan(
            PlanRequest(agent=agent, changes=changes, datasets=(dataset,))
        )

        gap = plan.coverage_gaps[0]

        assert gap.considered_dataset_version_ids == ("dv-1",)
        assert gap.reason.strip()

    def test_a_requirement_is_never_both_covered_and_gapped(
        self, planner, agent, changes, dataset_factory
    ):
        dataset = dataset_factory(
            "dv-1",
            requirement_types=("answer_correctness",),
            capabilities=ALL_CAPABILITIES,
        )

        plan = planner.plan(
            PlanRequest(agent=agent, changes=changes, datasets=(dataset,))
        )

        matched = {match.requirement_id for match in plan.dataset_matches}
        gapped = {gap.requirement_id for gap in plan.coverage_gaps}

        assert matched.isdisjoint(gapped)

    def test_gap_is_reported_as_a_limitation(
        self, planner, agent, changes, dataset_factory
    ):
        dataset = dataset_factory("dv-1")

        plan = planner.plan(
            PlanRequest(agent=agent, changes=changes, datasets=(dataset,))
        )

        assert any(
            "Coverage gap" in limitation
            for limitation in plan.limitations
        )

