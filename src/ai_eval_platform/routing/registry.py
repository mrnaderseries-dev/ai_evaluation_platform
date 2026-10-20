
from __future__ import annotations

from collections.abc import Iterable

from .protocols import AgentRoutingStrategy


class DuplicateAgentTypeError(ValueError):
    """Raised when two strategies claim the same agent type."""


class UnsupportedAgentTypeError(LookupError):
    """Raised when no routing strategy is registered for an agent type."""


class InvalidRoutingStrategyError(ValueError):
    """Raised when a strategy does not satisfy the routing protocol."""


class AgentRoutingRegistry:
    """Registry of agent-type routing strategies.

    Lookup is deterministic and explicit: an agent type maps to exactly one
    strategy, and claiming a type twice is an error rather than a silent
    override. The registry resolves strategies; it does not run them.
    """

    def __init__(
        self,
        strategies: Iterable[AgentRoutingStrategy] = (),
    ) -> None:
        self._strategies: dict[str, AgentRoutingStrategy] = {}

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

    def register(self, strategy: AgentRoutingStrategy) -> None:
        """Register a strategy for every agent type it supports."""
        try:
            agent_types = strategy.supported_agent_types
        except AttributeError as exc:
            raise InvalidRoutingStrategyError(
                "Strategy must define supported_agent_types."
            ) from exc

        if not isinstance(agent_types, frozenset) or not agent_types:
            raise InvalidRoutingStrategyError(
                "supported_agent_types must be a non-empty frozenset."
            )

        for method_name in ("identify_agent", "select_dataset"):
            if not callable(getattr(strategy, method_name, None)):
                raise InvalidRoutingStrategyError(
                    f"Strategy must implement {method_name}()."
                )

        normalized_types = {
            self._normalize_agent_type(agent_type)
            for agent_type in agent_types
        }

        if len(normalized_types) != len(agent_types):
            raise InvalidRoutingStrategyError(
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

    def get(self, agent_type: str) -> AgentRoutingStrategy:

        normalized = self._normalize_agent_type(agent_type)

        try:
            return self._strategies[normalized]
        except KeyError as exc:
            raise UnsupportedAgentTypeError(
                f"No routing strategy registered for agent type "
                f"'{normalized}'."
            ) from exc

    def supports(self, agent_type: str) -> bool:
        normalized = self._normalize_agent_type(agent_type)
        return normalized in self._strategies

    @property
    def registered_agent_types(self) -> frozenset[str]:
        return frozenset(self._strategies)
