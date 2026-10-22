"""Tests for the routing–planning integration layer."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from ai_eval_platform.planning import (
    EvaluationPlanningOrchestrator,
    PriorEvidenceProvider,
    RoutingToPlanningAdapter,
)
from ai_eval_platform.planning.domain import (
    AgentDescriptor,
    ChangeDescription,
    ComponentChange,
    EvaluationPlan,
    RiskEvidence,
)
from ai_eval_platform.planning.planner import (
    DEFAULT_POLICY_VERSION,
    EvaluationPlanner,
    PlanRequest,
)
from ai_eval_platform.planning.strategies import (
    AgenticRAGStrategy,
    StrategyRegistry,
)
from ai_eval_platform.routing import (
    AgentDescriptor as RoutingAgentDescriptor,
    AgentRoutingRegistry,
)
from ai_eval_platform.routing.dispatcher import AgentRoutingDispatcher
from ai_eval_platform.storage.models import PersistedEvidenceRecord
from tests.routing.conftest import StubStrategy


class TestRoutingToPlanningAdapter:
    def test_adapt_preserves_identity_and_capabilities(self):
        routing = RoutingAgentDescriptor(
            agent_id="agent-1",
            agent_type="agentic_rag",
            protocol="http",
            capabilities=frozenset({"retrieval", "grounding"}),
            agent_version="1.0.0",
            metadata={"key": "value"},
        )
        dispatcher = AgentRoutingDispatcher(
            AgentRoutingRegistry([StubStrategy()])
        )
        adapter = RoutingToPlanningAdapter(dispatcher)
        planning = adapter.adapt(routing)
        assert planning.agent_id == "agent-1"
        assert planning.agent_type == "agentic_rag"
        assert planning.capabilities == frozenset({"retrieval", "grounding"})
        assert planning.agent_version == "1.0.0"
        assert planning.metadata == {"key": "value"}

    def test_adapt_drops_protocol_field(self):
        routing = RoutingAgentDescriptor(
            agent_id="agent-1",
            agent_type="agentic_rag",
            protocol="grpc",
            capabilities=frozenset(),
        )
        dispatcher = AgentRoutingDispatcher(
            AgentRoutingRegistry([StubStrategy()])
        )
        adapter = RoutingToPlanningAdapter(dispatcher)
        planning = adapter.adapt(routing)
        assert not hasattr(planning, "protocol")

    def test_adapt_returns_planning_agent_descriptor_type(self):
        routing = RoutingAgentDescriptor(
            agent_id="agent-1",
            agent_type="agentic_rag",
            protocol="http",
        )
        dispatcher = AgentRoutingDispatcher(
            AgentRoutingRegistry([StubStrategy()])
        )
        adapter = RoutingToPlanningAdapter(dispatcher)
        planning = adapter.adapt(routing)
        assert isinstance(planning, AgentDescriptor)


class TestPriorEvidenceProvider:
    @staticmethod
    def _make_record(
        evidence_id: str,
        source: str,
        evidence_kind: str,
        agent_id: str | None = "agent-1",
        *,
        attributes: dict | None = None,
    ) -> MagicMock:
        record = MagicMock(spec=PersistedEvidenceRecord)
        record.evidence_id = evidence_id
        record.source = source
        record.source_ref = evidence_id
        record.evidence_kind = evidence_kind
        record.domain = "evaluation"
        record.subject_type = "agent"
        record.subject_id = agent_id or "agent-1"
        record.agent_id = agent_id
        record.observed_at = datetime.now(timezone.utc)
        record.attributes = attributes or {}
        return record


    def test_converts_signal_record_to_risk_evidence(self):
        record = self._make_record(
            evidence_id="ev-1",
            source="signal",
            evidence_kind="answer_correctness",
            agent_id="agent-1",
            attributes={
                "capability": "grounding",
                "priority": "high",
                "rationale": "Low scores on production traffic.",
                "recurrence_count": 3,
            },
        )
        provider = PriorEvidenceProvider.__new__(PriorEvidenceProvider)
        provider._repository = MagicMock()
        provider._repository.list_for_subject.return_value = [record]
        evidence = provider.fetch_for_agent("agent-1")
        assert len(evidence) == 1
        assert evidence[0].evidence_id == "ev-1"
        assert evidence[0].kind == "signal"
        assert evidence[0].requirement_type == "answer_correctness"
        assert evidence[0].agent_id == "agent-1"
        assert evidence[0].capability == "grounding"
        assert evidence[0].priority == "high"
        assert evidence[0].rationale == "Low scores on production traffic."
        assert evidence[0].recurrence_count == 3

    def test_converts_rca_finding_record(self):
        record = self._make_record(
            evidence_id="ev-2",
            source="rca_finding",
            evidence_kind="retrieval_recall",
            agent_id="agent-1",
            attributes={"rationale": "Retriever drift detected."},
        )
        provider = PriorEvidenceProvider.__new__(PriorEvidenceProvider)
        provider._repository = MagicMock()
        provider._repository.list_for_subject.return_value = [record]
        evidence = provider.fetch_for_agent("agent-1")
        assert len(evidence) == 1
        assert evidence[0].kind == "rca_finding"
        assert evidence[0].requirement_type == "retrieval_recall"

    def test_excludes_non_observed_risk_sources(self):
        event_record = self._make_record(
            evidence_id="ev-3",
            source="event",
            evidence_kind="answer_correctness",
            agent_id="agent-1",
        )
        trace_record = self._make_record(
            evidence_id="ev-4",
            source="trace",
            evidence_kind="retrieval_recall",
            agent_id="agent-1",
        )
        provider = PriorEvidenceProvider.__new__(PriorEvidenceProvider)
        provider._repository = MagicMock()
        provider._repository.list_for_subject.return_value = [
            event_record,
            trace_record,
        ]
        evidence = provider.fetch_for_agent("agent-1")
        assert evidence == ()

    def test_excludes_evidence_scoped_to_different_agent(self):
        other_agent = self._make_record(
            evidence_id="ev-5",
            source="signal",
            evidence_kind="answer_correctness",
            agent_id="agent-other",
        )
        provider = PriorEvidenceProvider.__new__(PriorEvidenceProvider)
        provider._repository = MagicMock()
        provider._repository.list_for_subject.return_value = [other_agent]
        evidence = provider.fetch_for_agent("agent-1")
        assert evidence == ()

    def test_includes_agent_agnostic_evidence(self):
        record = self._make_record(
            evidence_id="ev-6",
            source="signal",
            evidence_kind="groundedness",
            agent_id=None,
            attributes={"rationale": "Agent-agnostic prior finding."},
        )
        provider = PriorEvidenceProvider.__new__(PriorEvidenceProvider)
        provider._repository = MagicMock()
        provider._repository.list_for_subject.return_value = [record]
        evidence = provider.fetch_for_agent("agent-1")
        assert len(evidence) == 1
        assert evidence[0].agent_id is None

    def test_skips_records_with_invalid_attributes(self):
        record = self._make_record(
            evidence_id="ev-7",
            source="signal",
            evidence_kind="answer_correctness",
            agent_id="agent-1",
            attributes={"priority": "not_a_valid_level"},
        )
        provider = PriorEvidenceProvider.__new__(PriorEvidenceProvider)
        provider._repository = MagicMock()
        provider._repository.list_for_subject.return_value = [record]
        evidence = provider.fetch_for_agent("agent-1")
        assert evidence == ()

    def test_returns_empty_when_no_records(self):
        provider = PriorEvidenceProvider.__new__(PriorEvidenceProvider)
        provider._repository = MagicMock()
        provider._repository.list_for_subject.return_value = []
        evidence = provider.fetch_for_agent("agent-1")
        assert evidence == ()

    def test_sorts_by_evidence_id(self):
        records = [
            self._make_record(
                evidence_id="ev-b",
                source="signal",
                evidence_kind="groundedness",
                agent_id="agent-1",
                attributes={"rationale": "Prior finding B."},
            ),
            self._make_record(
                evidence_id="ev-a",
                source="signal",
                evidence_kind="answer_correctness",
                agent_id="agent-1",
                attributes={"rationale": "Prior finding A."},
            ),
        ]
        provider = PriorEvidenceProvider.__new__(PriorEvidenceProvider)
        provider._repository = MagicMock()
        provider._repository.list_for_subject.return_value = records
        evidence = provider.fetch_for_agent("agent-1")
        assert [e.evidence_id for e in evidence] == ["ev-a", "ev-b"]


class TestEvaluationPlanningOrchestrator:
    @staticmethod
    def _make_routing_registry() -> AgentRoutingRegistry:
        return AgentRoutingRegistry([StubStrategy()])

    @staticmethod
    def _make_planning_registry() -> StrategyRegistry:
        return StrategyRegistry([AgenticRAGStrategy()])

    @staticmethod
    def _make_changes() -> ChangeDescription:
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

    @staticmethod
    def _make_routing_descriptor() -> RoutingAgentDescriptor:
        return RoutingAgentDescriptor(
            agent_id="agent-1",
            agent_type="agentic_rag",
            protocol="http",
            capabilities=frozenset({"retrieval", "grounding", "generation"}),
            agent_version="1.0.0",
        )

    def test_end_to_end_without_evidence_provider(self):
        routing_registry = self._make_routing_registry()
        planning_registry = self._make_planning_registry()
        planner = EvaluationPlanner(planning_registry)

        orchestrator = EvaluationPlanningOrchestrator(
            registry=routing_registry,
            planner=planner,
        )

        plan = orchestrator.plan(
            routing_descriptor=self._make_routing_descriptor(),
            changes=self._make_changes(),
        )

        assert isinstance(plan, EvaluationPlan)
        assert plan.agent_id == "agent-1"
        assert plan.agent_type == "agentic_rag"
        assert plan.strategy_name == "agentic_rag"
        assert plan.strategy_version == "1.0.0"
        assert plan.policy_version == DEFAULT_POLICY_VERSION
        assert len(plan.requirements) > 0

    def test_end_to_end_with_evidence_provider(self):
        routing_registry = self._make_routing_registry()
        planning_registry = self._make_planning_registry()
        planner = EvaluationPlanner(planning_registry)

        signal_evidence = RiskEvidence(
            evidence_id="prior-signal-1",
            kind="signal",
            requirement_type="answer_correctness",
            agent_id="agent-1",
            priority="high",
            rationale="Prior production failure.",
            recurrence_count=2,
        )

        provider = MagicMock(spec=PriorEvidenceProvider)
        provider.fetch_for_agent.return_value = (signal_evidence,)

        orchestrator = EvaluationPlanningOrchestrator(
            registry=routing_registry,
            planner=planner,
            evidence_provider=provider,
        )

        plan = orchestrator.plan(
            routing_descriptor=self._make_routing_descriptor(),
            changes=self._make_changes(),
        )

        assert isinstance(plan, EvaluationPlan)
        assert any(
            "signal:prior-signal-1" in req.reason_refs
            for req in plan.requirements
        )

    def test_unsupported_agent_type_raises(self):
        routing_registry = AgentRoutingRegistry()
        planning_registry = self._make_planning_registry()
        planner = EvaluationPlanner(planning_registry)

        orchestrator = EvaluationPlanningOrchestrator(
            registry=routing_registry,
            planner=planner,
        )

        descriptor = RoutingAgentDescriptor(
            agent_id="agent-1",
            agent_type="unknown_type",
            protocol="http",
        )

        with pytest.raises(Exception):
            orchestrator.plan(
                routing_descriptor=descriptor,
                changes=self._make_changes(),
            )

    def test_plan_request_records_evidence_count(self):
        routing_registry = self._make_routing_registry()
        planning_registry = self._make_planning_registry()
        planner = EvaluationPlanner(planning_registry)

        signal_evidence = RiskEvidence(
            evidence_id="prior-signal-1",
            kind="signal",
            requirement_type="answer_correctness",
            agent_id="agent-1",
            priority="high",
            rationale="Prior production failure.",
        )

        provider = MagicMock(spec=PriorEvidenceProvider)
        provider.fetch_for_agent.return_value = (signal_evidence,)

        orchestrator = EvaluationPlanningOrchestrator(
            registry=routing_registry,
            planner=planner,
            evidence_provider=provider,
        )

        plan = orchestrator.plan(
            routing_descriptor=self._make_routing_descriptor(),
            changes=self._make_changes(),
        )

        assert plan.metadata["evidence_count"] >= 1
