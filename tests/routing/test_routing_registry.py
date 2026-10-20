"""Tests for the routing strategy registry.

The registry is the only place where agent-type knowledge is looked up, and
routing refuses to work with an agent type that has no strategy. Both
behaviours are contractual: an unsupported agent type is an explicit error,
never a silent fallback, and claiming a type twice is an error rather than
a silent override.
"""
from __future__ import annotations

import pytest

from ai_eval_platform.routing.registry import (
    AgentRoutingRegistry,
    DuplicateAgentTypeError,
    InvalidRoutingStrategyError,
    UnsupportedAgentTypeError,
)


class TestAgentRoutingRegistry:
    def test_a_registered_agent_type_is_supported(self, stub_strategy):
        registry = AgentRoutingRegistry([stub_strategy])

        assert registry.supports("agentic_rag") is True

    def test_an_unregistered_agent_type_is_not_supported(self, stub_strategy):
        registry = AgentRoutingRegistry([stub_strategy])

        assert registry.supports("other_agent") is False

    def test_lookup_is_case_insensitive(self, stub_strategy):
        registry = AgentRoutingRegistry([stub_strategy])

        assert registry.get("AGENTIC_RAG") is stub_strategy
        assert registry.get("  agentic_rag  ") is stub_strategy

    def test_get_returns_the_registered_strategy(self, stub_strategy):
        registry = AgentRoutingRegistry([stub_strategy])

        assert registry.get("agentic_rag") is stub_strategy

    def test_an_unregistered_agent_type_is_an_explicit_error(
        self, stub_strategy
    ):
        registry = AgentRoutingRegistry([stub_strategy])

        with pytest.raises(UnsupportedAgentTypeError) as excinfo:
            registry.get("other_agent")

        assert "other_agent" in str(excinfo.value)

    def test_two_strategies_for_the_same_type_are_rejected(
        self, stub_strategy, strategy_factory
    ):
        registry = AgentRoutingRegistry([stub_strategy])

        with pytest.raises(DuplicateAgentTypeError) as excinfo:
            registry.register(strategy_factory())

        assert "agentic_rag" in str(excinfo.value)

    def test_a_strategy_without_agent_types_is_rejected(self):
        class NoTypes:
            @property
            def supported_agent_types(self) -> frozenset[str]:
                return frozenset()

            def identify_agent(self, agent):
                raise AssertionError("must not be called")

            def select_dataset(self, profile):
                raise AssertionError("must not be called")

        with pytest.raises(InvalidRoutingStrategyError):
            AgentRoutingRegistry([NoTypes()])

    def test_a_strategy_missing_a_method_is_rejected(self):
        class MissingMethod:
            @property
            def supported_agent_types(self) -> frozenset[str]:
                return frozenset({"agentic_rag"})

            def identify_agent(self, agent):
                raise AssertionError("must not be called")

        with pytest.raises(InvalidRoutingStrategyError) as excinfo:
            AgentRoutingRegistry([MissingMethod()])

        assert "select_dataset" in str(excinfo.value)

    def test_a_strategy_without_the_agent_types_attribute_is_rejected(self):
        class NoAttribute:
            def identify_agent(self, agent):
                raise AssertionError("must not be called")

            def select_dataset(self, profile):
                raise AssertionError("must not be called")

        with pytest.raises(InvalidRoutingStrategyError) as excinfo:
            AgentRoutingRegistry([NoAttribute()])

        assert "supported_agent_types" in str(excinfo.value)

    def test_registered_agent_types_are_reported(self, stub_strategy):
        registry = AgentRoutingRegistry([stub_strategy])

        assert registry.registered_agent_types == frozenset({"agentic_rag"})

    def test_a_strategy_can_claim_several_agent_types(self, strategy_factory):
        strategy = strategy_factory(
            agent_types=frozenset({"agentic_rag", "chat_agent"})
        )
        registry = AgentRoutingRegistry([strategy])

        assert registry.registered_agent_types == frozenset(
            {"agentic_rag", "chat_agent"}
        )

    def test_duplicate_types_after_normalization_are_rejected(
        self, strategy_factory
    ):
        strategy = strategy_factory(
            agent_types=frozenset({"agentic_rag", "AGENTIC_RAG"})
        )

        with pytest.raises(InvalidRoutingStrategyError) as excinfo:
            AgentRoutingRegistry([strategy])

        assert "duplicate" in str(excinfo.value).lower()

    def test_an_empty_agent_type_is_rejected(self, stub_strategy):
        registry = AgentRoutingRegistry([stub_strategy])

        with pytest.raises(ValueError):
            registry.get("   ")

    def test_a_non_string_agent_type_is_rejected(self, stub_strategy):
        registry = AgentRoutingRegistry([stub_strategy])

        with pytest.raises(ValueError):
            registry.get(None)
