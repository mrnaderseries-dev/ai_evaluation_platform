"""Shared fixtures for the routing test suite.

Routing is pure and deterministic: no database and no agent runtime is
involved. Every fixture here builds plain value objects.
"""
from __future__ import annotations

import pytest

from ai_eval_platform.routing.models import (
    AgentDescriptor,
    AgentProfile,
    DatasetSelection,
)
from ai_eval_platform.routing.protocols import AgentRoutingStrategy


@pytest.fixture()
def descriptor() -> AgentDescriptor:
    """A well-formed agent descriptor."""
    return AgentDescriptor(
        agent_id="agent-1",
        agent_type="agentic_rag",
        protocol="http",
        capabilities=frozenset({"retrieval", "grounding"}),
        agent_version="1.0.0",
    )


@pytest.fixture()
def profile() -> AgentProfile:
    """A well-formed profile for :data:`descriptor`."""
    return AgentProfile(
        agent_id="agent-1",
        agent_type="agentic_rag",
        protocol="http",
        capabilities=frozenset({"retrieval", "grounding"}),
        agent_version="1.0.0",
    )


@pytest.fixture()
def selection() -> DatasetSelection:
    """A well-formed dataset selection."""
    return DatasetSelection(
        dataset_ids=("ds-1", "ds-2"),
        dataset_version="1.0.0",
        strategy_name="agentic_rag",
        rationale=("Covers the declared requirements.",),
        limitations=(),
    )


class StubStrategy:
    """A minimal, well-behaved routing strategy.

    The stub is intentionally simple: it echoes the descriptor into a
    profile and returns a fixed dataset selection. Tests that need
    misbehaviour subclass it and override a single method.
    """

    def __init__(
        self,
        *,
        agent_types: frozenset[str] = frozenset({"agentic_rag"}),
        name: str = "stub",
    ) -> None:
        self._agent_types = agent_types
        self._name = name
        self.identified: list[AgentDescriptor] = []
        self.selected: list[AgentProfile] = []

    @property
    def supported_agent_types(self) -> frozenset[str]:
        return self._agent_types

    def identify_agent(self, agent: AgentDescriptor) -> AgentProfile:
        self.identified.append(agent)

        return AgentProfile(
            agent_id=agent.agent_id,
            agent_type=agent.agent_type,
            protocol=agent.protocol,
            capabilities=agent.capabilities,
            agent_version=agent.agent_version,
        )

    def select_dataset(self, profile: AgentProfile) -> DatasetSelection:
        self.selected.append(profile)

        return DatasetSelection(
            dataset_ids=("ds-1",),
            dataset_version="1.0.0",
            strategy_name=self._name,
            rationale=("Stub strategy.",),
        )


@pytest.fixture()
def stub_strategy() -> StubStrategy:
    """A well-behaved stub strategy."""
    return StubStrategy()


@pytest.fixture()
def strategy_factory():
    """Factory for stub strategies with custom agent types."""
    return StubStrategy


@pytest.fixture()
def strategy_base() -> type[StubStrategy]:
    """The stub strategy class, for tests that subclass it."""
    return StubStrategy


@pytest.fixture()
def strategy_type() -> type[AgentRoutingStrategy]:
    """The routing protocol, for structural checks."""
    return AgentRoutingStrategy
