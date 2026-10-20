"""Contract tests for ``PlanRequest`` validation.

A request is the single input to the planner. If it is inconsistent the
planner must refuse it loudly instead of producing a misleading plan.
"""
from __future__ import annotations

import pytest

from ai_eval_platform.planning.domain import (
    ChangeDescription,
    ComponentChange,
)
from ai_eval_platform.planning.planner import PlanningError


class TestPlanRequestValidation:
    def test_accepts_two_different_versions_of_the_same_agent(
        self, agent, changes, request_factory
    ):
        request = request_factory(agent=agent, changes=changes)

        assert request.agent is agent
        assert request.changes is changes

    def test_rejects_same_version_on_both_sides(
        self, agent, changes, request_factory
    ):
        same = ChangeDescription(
            change_id=changes.change_id,
            agent_id=changes.agent_id,
            from_agent_version_id="v1",
            to_agent_version_id="v1",
            changes=changes.changes,
            completeness=changes.completeness,
        )

        with pytest.raises(PlanningError, match="two different versions"):
            request_factory(agent=agent, changes=same)

    def test_rejects_changes_for_a_different_agent(
        self, agent, changes, request_factory
    ):
        other = ChangeDescription(
            change_id=changes.change_id,
            agent_id="agent-other",
            from_agent_version_id="v1",
            to_agent_version_id="v2",
            changes=changes.changes,
            completeness=changes.completeness,
        )

        with pytest.raises(PlanningError, match="belongs to agent"):
            request_factory(agent=agent, changes=other)

    def test_freezes_sequences_into_tuples(
        self, agent, changes, dataset_factory, request_factory
    ):
        request = request_factory(
            agent=agent,
            changes=changes,
            datasets=[dataset_factory("dv-1")],
            evidence=[],
            advisory_suggestions=["maybe test something"],
        )

        assert isinstance(request.datasets, tuple)
        assert isinstance(request.evidence, tuple)
        assert isinstance(request.advisory_suggestions, tuple)

    def test_default_policy_version_is_recorded(
        self, agent, changes, request_factory
    ):
        request = request_factory(agent=agent, changes=changes)

        assert request.policy_version == "1.0.0"


class TestChangeDescriptionOrdering:
    def test_changes_must_be_deterministically_sorted(self):
        unsorted_changes = (
            ComponentChange(
                component="retriever",
                change_type="retriever",
                operation="modified",
                source="version_diff",
                after_fingerprint="b",
            ),
            ComponentChange(
                component="chunking",
                change_type="chunking",
                operation="modified",
                source="version_diff",
                after_fingerprint="a",
            ),
        )

        with pytest.raises(ValueError, match="sorted deterministically"):
            ChangeDescription(
                change_id="change-1",
                agent_id="agent-1",
                from_agent_version_id="v1",
                to_agent_version_id="v2",
                changes=unsorted_changes,
                completeness="complete",
            )

    def test_sorted_changes_are_accepted(self):
        ordered_changes = (
            ComponentChange(
                component="chunking",
                change_type="chunking",
                operation="modified",
                source="version_diff",
                after_fingerprint="a",
            ),
            ComponentChange(
                component="retriever",
                change_type="retriever",
                operation="modified",
                source="version_diff",
                after_fingerprint="b",
            ),
        )

        description = ChangeDescription(
            change_id="change-1",
            agent_id="agent-1",
            from_agent_version_id="v1",
            to_agent_version_id="v2",
            changes=ordered_changes,
            completeness="complete",
        )

        assert len(description.changes) == 2
