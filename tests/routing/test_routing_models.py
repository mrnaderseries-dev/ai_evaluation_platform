"""Tests for the routing agent descriptor and profile value objects.

Both objects are frozen and validate their own fields on construction.
These tests pin down that contract: an invalid object is rejected at the
point of creation rather than failing later inside the dispatcher.
"""
from __future__ import annotations

import pytest

from ai_eval_platform.routing.models import AgentDescriptor, AgentProfile


class TestAgentDescriptor:
    def test_a_valid_descriptor_is_accepted(self):
        descriptor = AgentDescriptor(
            agent_id="agent-1",
            agent_type="agentic_rag",
            protocol="http",
            capabilities=frozenset({"retrieval"}),
            agent_version="1.0.0",
        )

        assert descriptor.agent_id == "agent-1"
        assert descriptor.capabilities == frozenset({"retrieval"})

    def test_capabilities_default_to_empty(self):
        descriptor = AgentDescriptor(
            agent_id="agent-1",
            agent_type="agentic_rag",
            protocol="http",
        )

        assert descriptor.capabilities == frozenset()

    def test_an_empty_agent_id_is_rejected(self):
        with pytest.raises(ValueError) as excinfo:
            AgentDescriptor(
                agent_id="  ",
                agent_type="agentic_rag",
                protocol="http",
            )

        assert "agent_id" in str(excinfo.value)

    def test_an_empty_agent_type_is_rejected(self):
        with pytest.raises(ValueError):
            AgentDescriptor(
                agent_id="agent-1",
                agent_type="",
                protocol="http",
            )

    def test_an_empty_protocol_is_rejected(self):
        with pytest.raises(ValueError):
            AgentDescriptor(
                agent_id="agent-1",
                agent_type="agentic_rag",
                protocol="",
            )

    def test_capabilities_must_be_a_frozenset(self):
        with pytest.raises(ValueError):
            AgentDescriptor(
                agent_id="agent-1",
                agent_type="agentic_rag",
                protocol="http",
                capabilities=["retrieval"],
            )

    def test_capabilities_must_contain_non_empty_strings(self):
        with pytest.raises(ValueError):
            AgentDescriptor(
                agent_id="agent-1",
                agent_type="agentic_rag",
                protocol="http",
                capabilities=frozenset({"  "}),
            )

    def test_an_empty_agent_version_is_rejected(self):
        with pytest.raises(ValueError):
            AgentDescriptor(
                agent_id="agent-1",
                agent_type="agentic_rag",
                protocol="http",
                agent_version="  ",
            )

    def test_metadata_must_be_a_dictionary(self):
        with pytest.raises(ValueError):
            AgentDescriptor(
                agent_id="agent-1",
                agent_type="agentic_rag",
                protocol="http",
                metadata=["a"],
            )

    def test_the_descriptor_is_frozen(self):
        descriptor = AgentDescriptor(
            agent_id="agent-1",
            agent_type="agentic_rag",
            protocol="http",
        )

        with pytest.raises(Exception):
            descriptor.agent_id = "agent-2"


class TestAgentProfile:
    def test_a_valid_profile_is_accepted(self):
        profile = AgentProfile(
            agent_id="agent-1",
            agent_type="agentic_rag",
            protocol="http",
            capabilities=frozenset({"retrieval"}),
        )

        assert profile.agent_id == "agent-1"

    def test_an_empty_agent_id_is_rejected(self):
        with pytest.raises(ValueError):
            AgentProfile(
                agent_id="",
                agent_type="agentic_rag",
                protocol="http",
                capabilities=frozenset(),
            )

    def test_capabilities_must_be_a_frozenset(self):
        with pytest.raises(ValueError):
            AgentProfile(
                agent_id="agent-1",
                agent_type="agentic_rag",
                protocol="http",
                capabilities={"retrieval"},
            )

    def test_the_profile_is_frozen(self):
        profile = AgentProfile(
            agent_id="agent-1",
            agent_type="agentic_rag",
            protocol="http",
            capabilities=frozenset(),
        )

        with pytest.raises(Exception):
            profile.agent_id = "agent-2"
