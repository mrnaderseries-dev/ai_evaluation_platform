
from __future__ import annotations

from dataclasses import dataclass

from .models import AgentDescriptor, AgentProfile, DatasetSelection
from .registry import AgentRoutingRegistry


class RoutingValidationError(ValueError):
    """Raised when a routing strategy returns an invalid result."""


@dataclass(frozen=True)
class RoutingResult:
    """The outcome of routing one agent.

    A routing result is the profile that was derived plus the dataset
    selection that was made. It records *what would be evaluated*, never
    the result of an evaluation.
    """

    profile: AgentProfile
    selection: DatasetSelection


class AgentRoutingDispatcher:
    """Routes an agent to the strategy that knows how to evaluate it.

    The dispatcher is the only place where a routing decision is made. It
    resolves the strategy from the registry, asks it for a profile and a
    dataset selection, and validates both before returning them.

    Validation is strict on purpose: a strategy that returns a profile for
    a different agent, or a selection with duplicate dataset ids, is
    rejected rather than silently accepted.
    """

    def __init__(self, registry: AgentRoutingRegistry) -> None:
        self._registry = registry

    def dispatch(self, agent: AgentDescriptor) -> RoutingResult:
        """Route ``agent`` and return its profile and dataset selection.

        Raises:
            RoutingValidationError: when the descriptor is malformed or the
                strategy returns an inconsistent result.
            UnsupportedAgentTypeError: when no strategy is registered for
                the agent's type.
        """
        self._validate_descriptor(agent)

        strategy = self._registry.get(agent.agent_type)

        profile = strategy.identify_agent(agent)
        self._validate_profile(profile, agent)

        selection = strategy.select_dataset(profile)
        self._validate_selection(selection)

        return RoutingResult(profile=profile, selection=selection)

    @staticmethod
    def _validate_descriptor(agent: AgentDescriptor) -> None:
        if not isinstance(agent, AgentDescriptor):
            raise RoutingValidationError(
                "agent must be an AgentDescriptor."
            )

        for name in ("agent_id", "agent_type", "protocol"):
            value = getattr(agent, name)

            if not isinstance(value, str) or not value.strip():
                raise RoutingValidationError(
                    f"{name} must be a non-empty string."
                )

        if not isinstance(agent.capabilities, frozenset):
            raise RoutingValidationError(
                "capabilities must be a frozenset."
            )

        if any(
            not isinstance(item, str) or not item.strip()
            for item in agent.capabilities
        ):
            raise RoutingValidationError(
                "capabilities must contain non-empty strings."
            )

    @staticmethod
    def _validate_profile(
        profile: AgentProfile,
        agent: AgentDescriptor,
    ) -> None:
        if not isinstance(profile, AgentProfile):
            raise RoutingValidationError(
                "identify_agent() must return an AgentProfile."
            )

        for name in ("agent_id", "agent_type", "protocol"):
            value = getattr(profile, name)

            if not isinstance(value, str) or not value.strip():
                raise RoutingValidationError(
                    f"Profile {name} must be a non-empty string."
                )

        if profile.agent_id != agent.agent_id:
            raise RoutingValidationError(
                "The strategy returned a profile for a different agent."
            )

        if profile.agent_type.strip().lower() != agent.agent_type.strip().lower():
            raise RoutingValidationError(
                "The strategy returned a different agent type."
            )

        if not isinstance(profile.capabilities, frozenset):
            raise RoutingValidationError(
                "Profile capabilities must be a frozenset."
            )

        if any(
            not isinstance(item, str) or not item.strip()
            for item in profile.capabilities
        ):
            raise RoutingValidationError(
                "Profile capabilities must contain non-empty strings."
            )

    @staticmethod
    def _validate_selection(selection: DatasetSelection) -> None:
        if not isinstance(selection, DatasetSelection):
            raise RoutingValidationError(
                "select_dataset() must return a DatasetSelection."
            )

        if not isinstance(selection.dataset_ids, tuple):
            raise RoutingValidationError("dataset_ids must be a tuple.")

        if any(
            not isinstance(dataset_id, str) or not dataset_id.strip()
            for dataset_id in selection.dataset_ids
        ):
            raise RoutingValidationError(
                "Dataset IDs must be non-empty strings."
            )

        if len(selection.dataset_ids) != len(set(selection.dataset_ids)):
            raise RoutingValidationError(
                "Dataset selection contains duplicate IDs."
            )

        for name in ("dataset_version", "strategy_name"):
            value = getattr(selection, name)

            if not isinstance(value, str) or not value.strip():
                raise RoutingValidationError(
                    f"{name} must be a non-empty string."
                )

        for name in ("rationale", "limitations"):
            values = getattr(selection, name)

            if not isinstance(values, tuple):
                raise RoutingValidationError(f"{name} must be a tuple.")

            if any(
                not isinstance(item, str) or not item.strip()
                for item in values
            ):
                raise RoutingValidationError(
                    f"{name} must contain non-empty strings."
                )
