"""Agent-type strategies for turning changes into evaluation requirements.

A strategy is the *only* place where agent-type knowledge lives. The
planner itself is agent-agnostic: it asks the strategy for requirements and
lets the strategy decide which components of an agent are worth evaluating.

Strategies must be deterministic. The same change description, agent
descriptor, and evidence set must always yield the same requirements in the
same order, so that plans are reproducible and reviewable.

Naming follows the repository's existing convention in
:mod:`ai_eval_platform.routing.protocols`: a strategy declares the agent
types it supports via ``supported_agent_types``.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Mapping, Protocol, runtime_checkable

from .domain import (
    AgentDescriptor,
    ChangeDescription,
    ComponentChange,
    EvaluationRequirement,
    RiskLevel,
    make_reason_ref,
)


@runtime_checkable
class AgentTypeStrategy(Protocol):
    """The extension point for agent-type-specific planning.

    Implementations describe *what* to evaluate for a given agent type. They
    do not select datasets, and they never execute evaluations.
    """

    @property
    def supported_agent_types(self) -> frozenset[str]:
        """Canonical agent types this strategy handles."""
        ...

    @property
    def name(self) -> str:
        """Stable, human-readable strategy name."""
        ...

    @property
    def version(self) -> str:
        """Strategy version, recorded on every plan it produces."""
        ...

    def requirements_for_changes(
        self,
        agent: AgentDescriptor,
        changes: ChangeDescription,
    ) -> Sequence[EvaluationRequirement]:
        """Derive the requirements to evaluate for a set of changes."""
        ...

    def baseline_requirements(
        self,
        agent: AgentDescriptor,
    ) -> Sequence[EvaluationRequirement]:
        """Derive the requirements to always evaluate for this agent type."""
        ...


class DuplicateAgentTypeError(ValueError):
    """Raised when two strategies claim the same agent type."""


class UnsupportedAgentTypeError(LookupError):
    """Raised when no strategy is registered for an agent type."""


class InvalidStrategyError(ValueError):
    """Raised when a strategy does not satisfy the strategy protocol."""


class StrategyRegistry:
    """Registry of agent-type strategies.

    Lookup is deterministic and explicit: an agent type maps to exactly one
    strategy, and claiming a type twice is an error rather than a silent
    override. The registry resolves strategies; it does not run them.
    """

    def __init__(self, strategies: Iterable[AgentTypeStrategy] = ()) -> None:
        self._strategies: dict[str, AgentTypeStrategy] = {}

        for strategy in strategies:
            self.register(strategy)

    @staticmethod
    def _normalize_agent_type(agent_type: str) -> str:
        if not isinstance(agent_type, str):
            raise ValueError("Agent type must be a string.")

        normalized = agent_type.strip().lower()

        if not normalized:
            raise ValueError("Agent type must not be empty.")

        return normalized

    def register(self, strategy: AgentTypeStrategy) -> None:
        """Register a strategy for every agent type it supports."""
        try:
            agent_types = strategy.supported_agent_types
        except AttributeError as exc:
            raise InvalidStrategyError(
                "Strategy must define supported_agent_types."
            ) from exc

        if not isinstance(agent_types, frozenset) or not agent_types:
            raise InvalidStrategyError(
                "supported_agent_types must be a non-empty frozenset."
            )

        for method_name in (
            "requirements_for_changes",
            "baseline_requirements",
        ):
            if not callable(getattr(strategy, method_name, None)):
                raise InvalidStrategyError(
                    f"Strategy must implement {method_name}()."
                )

        normalized_types = {
            self._normalize_agent_type(agent_type)
            for agent_type in agent_types
        }

        if len(normalized_types) != len(agent_types):
            raise InvalidStrategyError(
                "Strategy declares duplicate agent types after normalization."
            )

        duplicates = normalized_types.intersection(self._strategies)

        if duplicates:
            names = ", ".join(sorted(duplicates))
            raise DuplicateAgentTypeError(
                f"Agent types already registered: {names}"
            )

        for agent_type in normalized_types:
            self._strategies[agent_type] = strategy

    def get(self, agent_type: str) -> AgentTypeStrategy:
        """Return the strategy registered for ``agent_type``.

        Raises:
            UnsupportedAgentTypeError: when the agent type is not
                registered. An unsupported type is always an explicit
                error; a mismatched strategy is never used silently.
        """
        normalized = self._normalize_agent_type(agent_type)

        try:
            return self._strategies[normalized]
        except KeyError as exc:
            raise UnsupportedAgentTypeError(
                f"No strategy registered for agent type '{normalized}'."
            ) from exc

    def supports(self, agent_type: str) -> bool:
        """Return ``True`` when a strategy is registered for ``agent_type``."""
        normalized = self._normalize_agent_type(agent_type)

        return normalized in self._strategies

    @property
    def registered_agent_types(self) -> frozenset[str]:
        """Every agent type with a registered strategy."""
        return frozenset(self._strategies)

    def all(self) -> tuple[AgentTypeStrategy, ...]:
        """Every registered strategy, in a deterministic order."""
        return tuple(
            self._strategies[agent_type]
            for agent_type in sorted(self._strategies)
        )




#: Change type -> candidate requirement types for an agentic RAG agent.
#: These are *candidate* checks. Applicability is additionally filtered by
#: the agent's declared capabilities, so an agent without the ``retrieval``
#: capability never gets retrieval requirements.
AGENTIC_RAG_CHANGE_REQUIREMENTS: Mapping[str, tuple[str, ...]] = {
    "embedding": ("retrieval_recall", "semantic_matching", "latency"),
    "chunking": ("context_coverage", "retrieval_quality", "groundedness"),
    "retriever": ("retrieval_recall", "ranking_quality", "retrieval_latency"),
    "vector_store": (
        "retrieval_correctness",
        "availability",
        "retrieval_latency",
    ),
    "prompt": ("answer_correctness", "groundedness", "abstention"),
    "model": (
        "answer_quality",
        "groundedness",
        "latency",
        "token_cost",
    ),
    "code": ("answer_correctness", "groundedness"),
    "context": ("context_coverage", "groundedness"),
}

#: Capability required for each candidate requirement type.
AGENTIC_RAG_REQUIREMENT_CAPABILITIES: Mapping[str, frozenset[str]] = {
    "retrieval_recall": frozenset({"retrieval"}),
    "retrieval_quality": frozenset({"retrieval"}),
    "retrieval_correctness": frozenset({"retrieval"}),
    "retrieval_latency": frozenset({"retrieval"}),
    "ranking_quality": frozenset({"retrieval"}),
    "semantic_matching": frozenset({"retrieval"}),
    "context_coverage": frozenset({"retrieval"}),
    "groundedness": frozenset({"grounding"}),
    "answer_correctness": frozenset(),
    "answer_quality": frozenset(),
    "abstention": frozenset(),
    "latency": frozenset(),
    "token_cost": frozenset(),
    "availability": frozenset(),
}


#: Metrics measured for each candidate requirement type.
AGENTIC_RAG_REQUIREMENT_METRICS: Mapping[str, tuple[str, ...]] = {
    "retrieval_recall": ("recall_at_k",),
    "retrieval_quality": ("ndcg", "mrr"),
    "retrieval_correctness": ("precision_at_k",),
    "retrieval_latency": ("p95_latency_ms",),
    "ranking_quality": ("mrr", "ndcg"),
    "semantic_matching": ("semantic_match_rate",),
    "context_coverage": ("context_coverage_ratio",),
    "groundedness": ("groundedness_score", "citation_accuracy"),
    "answer_correctness": ("exact_match", "answer_correctness_score"),
    "answer_quality": ("answer_quality_score",),
    "abstention": ("abstention_accuracy",),
    "latency": ("p95_latency_ms", "mean_latency_ms"),
    "token_cost": ("prompt_tokens", "completion_tokens", "cost_usd"),
    "availability": ("availability_ratio",),
}


class AgenticRAGStrategy:
    """Strategy for canonical ``agentic_rag`` agents.

    The strategy maps confirmed component changes to candidate evaluation
    requirements. It is deliberately conservative:

    * A change type with no mapping adds nothing; an unrelated change must
      not add irrelevant requirements.
    * A requirement whose required capability is not declared by the agent
      is dropped, so an agent without ``retrieval`` never receives
      retrieval checks.
    * Requirements are derived from *confirmed* changes only. A change with
      unknown semantics contributes no requirement rather than a guessed one.
    """

    def __init__(
        self,
        *,
        name: str = "agentic_rag",
        version: str = "1.0.0",
    ) -> None:
        self._name = name
        self._version = version

    @property
    def supported_agent_types(self) -> frozenset[str]:
        return frozenset({"agentic_rag"})

    def requirements_for_changes(
        self,
        agent: AgentDescriptor,
        changes: ChangeDescription,
    ) -> tuple[EvaluationRequirement, ...]:
        """Map the confirmed changes onto candidate requirements."""
        requirements: list[EvaluationRequirement] = []
        seen: set[str] = set()

        for change in changes.changes:
            if change.change_type == "unknown":
                continue

            candidates = AGENTIC_RAG_CHANGE_REQUIREMENTS.get(
                change.change_type, ()
            )

            for requirement_type in candidates:
                if not self._is_applicable(agent, requirement_type):
                    continue

                requirement_id = self._requirement_id(requirement_type)

                if requirement_id in seen:
                    continue

                seen.add(requirement_id)
                requirements.append(
                    EvaluationRequirement(
                        requirement_id=requirement_id,
                        requirement_type=requirement_type,
                        priority=self._priority(change),
                        reason_refs=(
                            make_reason_ref("change", changes.change_id),
                        ),
                        required_capabilities=(
                            AGENTIC_RAG_REQUIREMENT_CAPABILITIES.get(
                                requirement_type, frozenset()
                            )
                        ),
                        required_metrics=(
                            AGENTIC_RAG_REQUIREMENT_METRICS.get(
                                requirement_type, ()
                            )
                        ),
                    )
                )

        return tuple(
            sorted(requirements, key=lambda item: item.requirement_id)
        )

    def baseline_requirements(
        self,
        agent: AgentDescriptor,
    ) -> tuple[EvaluationRequirement, ...]:
        """Return the checks that always apply to this agent type.

        Baseline requirements are produced even when change data is
        incomplete, because missing change metadata must not silently reduce
        the evaluation scope.
        """
        candidates = (
            "answer_correctness",
            "groundedness",
            "retrieval_recall",
        )
        requirements: list[EvaluationRequirement] = []

        for requirement_type in candidates:
            if not self._is_applicable(agent, requirement_type):
                continue

            requirements.append(
                EvaluationRequirement(
                    requirement_id=self._requirement_id(requirement_type),
                    requirement_type=requirement_type,
                    priority="medium",
                    reason_refs=(
                        make_reason_ref("baseline", self._name),
                    ),
                    required_capabilities=(
                        AGENTIC_RAG_REQUIREMENT_CAPABILITIES.get(
                            requirement_type, frozenset()
                        )
                    ),
                    required_metrics=(
                        AGENTIC_RAG_REQUIREMENT_METRICS.get(
                            requirement_type, ()
                        )
                    ),
                )
            )

        return tuple(
            sorted(requirements, key=lambda item: item.requirement_id)
        )

    @staticmethod
    def _requirement_id(requirement_type: str) -> str:
        return f"agentic_rag.{requirement_type}"

    @staticmethod
    def _is_applicable(
        agent: AgentDescriptor,
        requirement_type: str,
    ) -> bool:
        """Return ``True`` when the agent declares the needed capability."""
        required = AGENTIC_RAG_REQUIREMENT_CAPABILITIES.get(
            requirement_type, frozenset()
        )

        if not required:
            return True

        return required.issubset(agent.capabilities)

    @staticmethod
    def _priority(change: ComponentChange) -> RiskLevel:
        """Derive a requirement priority from the confidence of a change.

        A change observed with full confidence matters more than one that is
        only half-observed, but the priority never claims more certainty
        than the evidence supports.
        """
        if change.confidence is None:
            return "medium"

        if change.confidence >= 0.9:
            return "high"

        if change.confidence >= 0.6:
            return "medium"

        return "low"

    @property
    def name(self) -> str:
        return self._name

    @property
    def version(self) -> str:
        return self._version
