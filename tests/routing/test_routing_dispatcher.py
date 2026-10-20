"""Tests for the routing dispatcher.

The dispatcher is the single place where a routing decision is made, and it
validates everything a strategy returns. The contract under test: a
strategy that returns a profile for a different agent, or a selection with
duplicate dataset ids, is rejected rather than silently accepted.
"""
from __future__ import annotations

import pytest

from ai_eval_platform.routing.dispatcher import (
    AgentRoutingDispatcher,
    RoutingResult,
    RoutingValidationError,
)
from ai_eval_platform.routing.models import (
    AgentDescriptor,
    AgentProfile,
    DatasetSelection,
)
from ai_eval_platform.routing.registry import (
    AgentRoutingRegistry,
    UnsupportedAgentTypeError,
)


class TestAgentRoutingDispatcher:
    def test_dispatch_returns_the_profile_and_selection(
        self, descriptor, stub_strategy
    ):
        dispatcher = AgentRoutingDispatcher(
            AgentRoutingRegistry([stub_strategy])
        )

        result = dispatcher.dispatch(descriptor)

        assert isinstance(result, RoutingResult)
        assert result.profile.agent_id == descriptor.agent_id
        assert result.selection.dataset_ids == ("ds-1",)

    def test_dispatch_uses_the_registered_strategy(
        self, descriptor, stub_strategy
    ):
        dispatcher = AgentRoutingDispatcher(
            AgentRoutingRegistry([stub_strategy])
        )

        dispatcher.dispatch(descriptor)

        assert stub_strategy.identified == [descriptor]
        assert len(stub_strategy.selected) == 1

    def test_an_unregistered_agent_type_is_an_explicit_error(self, descriptor):
        dispatcher = AgentRoutingDispatcher(AgentRoutingRegistry())

        with pytest.raises(UnsupportedAgentTypeError):
            dispatcher.dispatch(descriptor)

    def test_a_non_descriptor_is_rejected(self, stub_strategy):
        dispatcher = AgentRoutingDispatcher(
            AgentRoutingRegistry([stub_strategy])
        )

        with pytest.raises(RoutingValidationError):
            dispatcher.dispatch({"agent_id": "agent-1"})

    def test_a_blank_agent_id_is_rejected(self, stub_strategy):
        dispatcher = AgentRoutingDispatcher(
            AgentRoutingRegistry([stub_strategy])
        )

        # The model validates on construction, so a blank id can only reach
        # the dispatcher if the descriptor was mutated afterwards.
        descriptor = AgentDescriptor(
            agent_id="agent-1",
            agent_type="agentic_rag",
            protocol="http",
        )
        object.__setattr__(descriptor, "agent_id", "   ")

        with pytest.raises(RoutingValidationError) as excinfo:
            dispatcher.dispatch(descriptor)

        assert "agent_id" in str(excinfo.value)

    def test_capabilities_must_be_a_frozenset(self, stub_strategy):
        dispatcher = AgentRoutingDispatcher(
            AgentRoutingRegistry([stub_strategy])
        )

        descriptor = AgentDescriptor(
            agent_id="agent-1",
            agent_type="agentic_rag",
            protocol="http",
        )
        object.__setattr__(descriptor, "capabilities", ["retrieval"])

        with pytest.raises(RoutingValidationError) as excinfo:
            dispatcher.dispatch(descriptor)

        assert "capabilities" in str(excinfo.value)


class TestProfileValidation:
    def test_a_profile_for_a_different_agent_is_rejected(
        self, descriptor, strategy_base
    ):
        class WrongAgent(strategy_base):
            def identify_agent(self, agent):
                return AgentProfile(
                    agent_id="some-other-agent",
                    agent_type=agent.agent_type,
                    protocol=agent.protocol,
                    capabilities=agent.capabilities,
                )

        dispatcher = AgentRoutingDispatcher(
            AgentRoutingRegistry([WrongAgent()])
        )

        with pytest.raises(RoutingValidationError) as excinfo:
            dispatcher.dispatch(descriptor)

        assert "different agent" in str(excinfo.value)

    def test_a_profile_with_a_different_agent_type_is_rejected(
        self, descriptor, strategy_base
    ):
        class WrongType(strategy_base):
            def identify_agent(self, agent):
                return AgentProfile(
                    agent_id=agent.agent_id,
                    agent_type="some_other_type",
                    protocol=agent.protocol,
                    capabilities=agent.capabilities,
                )

        dispatcher = AgentRoutingDispatcher(
            AgentRoutingRegistry([WrongType()])
        )

        with pytest.raises(RoutingValidationError) as excinfo:
            dispatcher.dispatch(descriptor)

        assert "agent type" in str(excinfo.value)

    def test_a_non_profile_result_is_rejected(self, descriptor, strategy_base):
        class NotAProfile(strategy_base):
            def identify_agent(self, agent):
                return {"agent_id": agent.agent_id}

        dispatcher = AgentRoutingDispatcher(
            AgentRoutingRegistry([NotAProfile()])
        )

        with pytest.raises(RoutingValidationError):
            dispatcher.dispatch(descriptor)
