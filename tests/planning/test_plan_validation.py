"""Tests for plan validation and the agent-type strategy registry.

The registry is the only place where agent-type knowledge lives, and the
planner refuses to work with an agent type that has no strategy. Both
behaviours are contractual: an unsupported agent type is an explicit
error, never a silent fallback.
"""
from __future__ import annotations

import pytest

from ai_eval_platform.planning.domain import (
    AgentDescriptor,
    ChangeDescription,
    CoverageGap,
    DatasetMatch,
    EvaluationPlan,
    EvaluationRequirement,
    make_reason_ref,
)
from ai_eval_platform.planning.planner import (
    EvaluationPlanner,
    PlanRequest,
    PlanValidationError,
    validate_plan,
)
from ai_eval_platform.planning.strategies import (
    AgenticRAGStrategy,
    DuplicateAgentTypeError,
    InvalidStrategyError,
    StrategyRegistry,
    UnsupportedAgentTypeError,
)


def _requirement(requirement_id: str) -> EvaluationRequirement:
    return EvaluationRequirement(
        requirement_id=requirement_id,
        requirement_type="answer_correctness",
        reason_refs=(make_reason_ref("baseline", "agentic_rag"),),
    )


def _match(requirement_id: str = "req-1") -> DatasetMatch:
    return DatasetMatch(
        requirement_id=requirement_id,
        dataset_version_id="dv-1",
        rationale="Because.",
        coverage_score=0.5,
        quality_status="validated",
    )


def _gap(requirement_id: str = "req-1") -> CoverageGap:
    return CoverageGap(
        requirement_id=requirement_id,
        requirement_type="answer_correctness",
        reason="Nothing covers it.",
    )


def _plan(**overrides) -> EvaluationPlan:
    defaults = {
        "plan_id": "plan_1",
        "agent_id": "agent-1",
        "agent_type": "agentic_rag",
        "from_agent_version_id": "v1",
        "to_agent_version_id": "v2",
        "requirements": (_requirement("req-1"),),
        "strategy_name": "agentic_rag",
        "strategy_version": "1.0.0",
        "policy_version": "1.0.0",
    }
    defaults.update(overrides)

    return EvaluationPlan(**defaults)


def _inconsistent_plan(**overrides) -> EvaluationPlan:
    """Build a plan that bypasses the model's own consistency check.

    ``EvaluationPlan`` rejects inconsistent plans on construction, so
    ``validate_plan`` can only be exercised against a plan that was
    assembled without validation.
    """
    defaults = {
        "plan_id": "plan_1",
        "agent_id": "agent-1",
        "agent_type": "agentic_rag",
        "from_agent_version_id": "v1",
        "to_agent_version_id": "v2",
        "requirements": (_requirement("req-1"),),
        "dataset_matches": (),
        "coverage_gaps": (),
        "selected_dataset_version_ids": (),
        "selection_rationale": (),
        "strategy_name": "agentic_rag",
        "strategy_version": "1.0.0",
        "policy_version": "1.0.0",
        "change_completeness": "complete",
        "status": "draft",
        "limitations": (),
        "metadata": {},
    }
    defaults.update(overrides)

    return EvaluationPlan.model_construct(**defaults)


class TestValidatePlan:
    def test_a_consistent_plan_validates(self):
        validate_plan(_plan())

    def test_duplicate_requirement_ids_are_rejected(self):
        with pytest.raises(PlanValidationError, match="must be unique"):
            validate_plan(
                _inconsistent_plan(
                    requirements=(
                        _requirement("req-1"),
                        _requirement("req-1"),
                    )
                )
            )

    def test_match_for_an_unknown_requirement_is_rejected(self):
        with pytest.raises(PlanValidationError, match="unknown requirement"):
            validate_plan(
                _inconsistent_plan(dataset_matches=(_match("req-unknown"),))
            )

    def test_gap_for_an_unknown_requirement_is_rejected(self):
        with pytest.raises(PlanValidationError, match="unknown requirement"):
            validate_plan(
                _inconsistent_plan(coverage_gaps=(_gap("req-unknown"),))
            )

    def test_requirement_cannot_be_both_covered_and_gapped(self):
        with pytest.raises(PlanValidationError, match="both covered"):
            validate_plan(
                _inconsistent_plan(
                    dataset_matches=(_match(),), coverage_gaps=(_gap(),)
                )
            )

    def test_the_plan_model_rejects_an_unknown_requirement_reference(self):
        with pytest.raises(ValueError, match="unknown requirement"):
            _plan(dataset_matches=(_match("req-unknown"),))

    def test_the_plan_model_rejects_a_duplicate_requirement_id(self):
        with pytest.raises(ValueError, match="must be unique"):
            _plan(
                requirements=(
                    _requirement("req-1"),
                    _requirement("req-1"),
                )
            )

    def test_the_plan_model_rejects_a_covered_and_gapped_requirement(self):
        with pytest.raises(ValueError, match="both covered"):
            _plan(dataset_matches=(_match(),), coverage_gaps=(_gap(),))



class TestStrategyRegistry:
    def test_a_registered_agent_type_is_supported(self):
        registry = StrategyRegistry([AgenticRAGStrategy()])

        assert registry.supports("agentic_rag")
        assert registry.get("agentic_rag") is not None

    def test_agent_type_lookup_is_case_insensitive(self):
        registry = StrategyRegistry([AgenticRAGStrategy()])

        assert registry.get("AGENTIC_RAG") is not None
        assert registry.supports("  Agentic_RAG  ")

    def test_an_unregistered_agent_type_is_an_explicit_error(self):
        registry = StrategyRegistry([AgenticRAGStrategy()])

        with pytest.raises(UnsupportedAgentTypeError):
            registry.get("unknown_type")

    def test_two_strategies_for_the_same_type_are_rejected(self):
        registry = StrategyRegistry([AgenticRAGStrategy()])

        with pytest.raises(DuplicateAgentTypeError):
            registry.register(AgenticRAGStrategy())

    def test_a_strategy_without_agent_types_is_rejected(self):
        class Broken:
            supported_agent_types = frozenset()

            def requirements_for_changes(self, agent, changes):
                return ()

            def baseline_requirements(self, agent):
                return ()

        with pytest.raises(InvalidStrategyError):
            StrategyRegistry([Broken()])

    def test_a_strategy_missing_a_method_is_rejected(self):
        class Broken:
            supported_agent_types = frozenset({"custom"})

            def baseline_requirements(self, agent):
                return ()

        with pytest.raises(InvalidStrategyError):
            StrategyRegistry([Broken()])

    def test_registered_agent_types_are_reported(self):
        registry = StrategyRegistry([AgenticRAGStrategy()])

        assert registry.registered_agent_types == frozenset({"agentic_rag"})


class TestUnsupportedAgentType:
    def test_planning_an_unknown_agent_type_fails_loudly(
        self, registry, changes
    ):
        agent = AgentDescriptor(
            agent_id="agent-1",
            agent_type="mystery_type",
            capabilities=frozenset({"retrieval"}),
        )

        planner = EvaluationPlanner(registry)

        with pytest.raises(UnsupportedAgentTypeError):
            planner.plan(PlanRequest(agent=agent, changes=changes))

    def test_an_empty_change_set_still_produces_baseline_requirements(
        self, planner, agent
    ):
        changes = ChangeDescription(
            change_id="change-1",
            agent_id="agent-1",
            from_agent_version_id="v1",
            to_agent_version_id="v2",
            changes=(),
            completeness="complete",
        )

        plan = planner.plan(PlanRequest(agent=agent, changes=changes))

        types = {item.requirement_type for item in plan.requirements}

        assert types == {
            "answer_correctness",
            "groundedness",
            "retrieval_recall",
        }

