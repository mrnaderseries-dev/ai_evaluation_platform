"""Integration between the routing and planning packages.

This module provides an adapter layer that translates routing results into
planning inputs, a prior-evidence provider that reads observed-risk evidence
from the storage layer, and an orchestrator that delivers an end-to-end
planning workflow.

Design choice (documented in THE_RRREPORT.md): **Choice B — adapter**.
Both public APIs are preserved unchanged. The routing package keeps its own
``AgentDescriptor`` (dataclass) and ``AgentRoutingStrategy`` protocol; the
planning package keeps its own ``AgentDescriptor`` (pydantic model) and
``AgentTypeStrategy`` protocol. A thin adapter translates between them.
No third registry or duplicated selection logic is introduced.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy.orm import Session

from ..routing import (
    AgentDescriptor as RoutingAgentDescriptor,
    AgentRoutingDispatcher,
    AgentRoutingRegistry,
)
from ..storage.models import PersistedEvidenceRecord
from ..storage.repositories import EvidenceRepository
from .domain import (
    AgentDescriptor as PlanningAgentDescriptor,
    ChangeDescription,
    EvaluationPlan,
    RiskEvidence,
    make_reason_ref,
)
from .planner import DEFAULT_POLICY_VERSION, EvaluationPlanner, PlanRequest

#: Evidence sources that describe observed risk and may add requirements.
_OBSERVED_RISK_SOURCES: frozenset[str] = frozenset({"signal", "rca_finding"})


class RoutingToPlanningAdapter:
    """Translates a routing agent descriptor into a planning agent descriptor.

    Routing's ``AgentDescriptor`` carries a ``protocol`` field that planning
    does not model. The adapter drops ``protocol`` (a routing concern) and
    preserves identity, type, capabilities, version, and metadata.
    """

    def __init__(self, dispatcher: AgentRoutingDispatcher) -> None:
        self._dispatcher = dispatcher

    def adapt(self, routing_descriptor: RoutingAgentDescriptor) -> PlanningAgentDescriptor:
        """Convert a routing descriptor to a planning descriptor.

        The ``protocol`` field is intentionally omitted: planning reasons
        about *what* to evaluate, not *how* the agent is reached.
        """
        return PlanningAgentDescriptor(
            agent_id=routing_descriptor.agent_id,
            agent_type=routing_descriptor.agent_type,
            capabilities=routing_descriptor.capabilities,
            agent_version=routing_descriptor.agent_version,
            metadata=dict(routing_descriptor.metadata),
        )


class PriorEvidenceProvider:
    """Fetches prior evaluation evidence from the storage layer.

    Wraps :class:`~ai_eval_platform.storage.repositories.EvidenceRepository`
    to convert :class:`~ai_eval_platform.storage.models.PersistedEvidenceRecord`
    rows into :class:`~ai_eval_platform.planning.domain.RiskEvidence`
    instances suitable for the planner.

    Only evidence whose ``source`` is ``signal`` or ``rca_finding`` is
    included, since those describe observed risk. Evidence explicitly
    scoped to a different agent is excluded.
    """

    def __init__(self, session: Session) -> None:
        self._repository = EvidenceRepository(session)

    def fetch_for_agent(
        self,
        agent_id: str,
        *,
        domain: str = "evaluation",
        limit: int = 100,
    ) -> tuple[RiskEvidence, ...]:
        """Return prior evidence records for an agent as planning evidence.

        Args:
            agent_id: the agent whose prior evidence should be fetched.
            domain: evidence domain filter (default ``"evaluation"``).
            limit: maximum number of records to return.

        Returns:
            A tuple of :class:`RiskEvidence` sorted by evidence id.
        """
        records = self._repository.list_for_subject(
            subject_type="agent",
            subject_id=agent_id,
            domain=domain,
            limit=limit,
        )

        evidence: list[RiskEvidence] = []

        for record in records:
            if record.source not in _OBSERVED_RISK_SOURCES:
                continue

            if record.agent_id is not None and record.agent_id != agent_id:
                continue

            try:
                evidence.append(
                    RiskEvidence(
                        evidence_id=record.evidence_id,
                        kind=record.source,  # type: ignore[arg-type]
                        requirement_type=record.evidence_kind,
                        agent_id=record.agent_id,
                        capability=record.attributes.get("capability"),
                        priority=record.attributes.get("priority", "medium"),
                        rationale=record.attributes.get("rationale", ""),
                        recurrence_count=record.attributes.get(
                            "recurrence_count", 1
                        ),
                    )
                )
            except (ValueError, TypeError):
                # Skip records whose attributes cannot be validated as
                # RiskEvidence (e.g. invalid priority or empty rationale).
                continue

        return tuple(sorted(evidence, key=lambda item: item.evidence_id))


class EvaluationPlanningOrchestrator:
    """End-to-end planning workflow combining routing and planning.

    Routes an agent through the routing dispatcher, translates the result
    to a planning descriptor, fetches prior evaluation evidence (when a
    provider is configured), and asks the planner to produce a plan.
    """

    def __init__(
        self,
        registry: AgentRoutingRegistry,
        planner: EvaluationPlanner,
        evidence_provider: PriorEvidenceProvider | None = None,
    ) -> None:
        self._dispatcher = AgentRoutingDispatcher(registry)
        self._adapter = RoutingToPlanningAdapter(self._dispatcher)
        self._planner = planner
        self._evidence_provider = evidence_provider

    def plan(
        self,
        routing_descriptor: RoutingAgentDescriptor,
        changes: ChangeDescription,
        *,
        datasets: Sequence[Any] = (),
        evidence_limit: int = 100,
        policy_version: str | None = None,
    ) -> Any:
        """Run the full routing → planning workflow.

        Steps:
        1. Route the agent through ``AgentRoutingDispatcher``.
        2. Translate the routing descriptor to a planning descriptor.
        3. Fetch prior evaluation evidence (if a provider is configured).
        4. Build a :class:`PlanRequest` and ask the planner for a plan.

        Args:
            routing_descriptor: the agent to route and plan for.
            changes: the change description between two agent versions.
            datasets: candidate dataset versions with declared coverage.
            evidence_limit: maximum prior evidence records to fetch.
            policy_version: policy version recorded on the plan.

        Returns:
            An :class:`EvaluationPlan`.
        """
        # Step 1: route the agent (validates descriptor and resolves strategy)
        routing_result = self._dispatcher.dispatch(routing_descriptor)

        # Step 2: translate to planning descriptor
        planning_agent = self._adapter.adapt(routing_descriptor)

        # Step 3: fetch prior evaluation evidence
        evidence: tuple[RiskEvidence, ...] = ()
        if self._evidence_provider is not None:
            evidence = self._evidence_provider.fetch_for_agent(
                routing_descriptor.agent_id,
                limit=evidence_limit,
            )

        # Step 4: derive the routing-level candidate restriction.
        #
        # Routing's DatasetSelection names the dataset ids / version that the
        # routing strategy permits. Planning remains the sole final selector,
        # but it may only choose within this set (the candidate-set contract).
        # An empty selection leaves planning unrestricted.
        selection = routing_result.selection
        allowed_ids = tuple(
            sorted(
                {
                    item
                    for item in (
                        *selection.dataset_ids,
                        selection.dataset_version,
                    )
                    if item
                }
            )
        )

        # Surface routing limitations (and an explicit note when the routing
        # constraint cannot be satisfied by any provided dataset) on the plan.
        routing_limitations: list[str] = list(selection.limitations)
        provided_versions = {
            getattr(dataset, "dataset_version_id", None) for dataset in datasets
        }
        if datasets and allowed_ids and not (
            provided_versions & set(allowed_ids)
        ):
            routing_limitations.append(
                "Routing selection "
                + ", ".join(allowed_ids)
                + " did not match any provided dataset version; "
                "the routing constraint cannot be satisfied, so every "
                "requirement is reported as a coverage gap."
            )

        effective_changes = changes
        if routing_limitations:
            effective_changes = changes.model_copy(
                update={
                    "limitations": tuple(
                        list(changes.limitations) + routing_limitations
                    ),
                }
            )

        # Step 5: build plan request and plan
        request = PlanRequest(
            agent=planning_agent,
            changes=effective_changes,
            datasets=tuple(datasets),
            evidence=evidence,
            allowed_dataset_version_ids=allowed_ids,
            policy_version=policy_version or DEFAULT_POLICY_VERSION,
        )

        return self._planner.plan(request)

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"evidence_provider={self._evidence_provider is not None}"
            f")"
        )