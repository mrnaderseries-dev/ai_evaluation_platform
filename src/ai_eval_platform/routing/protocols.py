
from __future__ import annotations

from typing import Protocol

from .models import AgentDescriptor, AgentProfile, DatasetSelection


class AgentRoutingStrategy(Protocol):
    """The extension point for agent-specific evaluation routing.

    Implementations describe *how* an agent is profiled and which dataset
    version should evaluate it. They do not execute evaluations, and they
    never decide whether a change is acceptable.
    """

    @property
    def supported_agent_types(self) -> frozenset[str]:
        """Canonical agent types this strategy handles."""
        ...

    def identify_agent(
        self,
        agent: AgentDescriptor,
    ) -> AgentProfile:
        """Derive the evaluation profile for a concrete agent."""
        ...

    def select_dataset(
        self,
        profile: AgentProfile,
    ) -> DatasetSelection:
        """Choose the dataset version that should evaluate the agent."""
        ...
