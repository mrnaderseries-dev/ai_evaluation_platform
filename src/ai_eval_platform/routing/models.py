
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _require_non_empty_string(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string.")


def _validate_string_set(value: object, field_name: str) -> None:
    if not isinstance(value, frozenset):
        raise ValueError(f"{field_name} must be a frozenset.")

    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(
            f"{field_name} must contain only non-empty strings."
        )


def _validate_string_tuple(value: object, field_name: str) -> None:
    if not isinstance(value, tuple):
        raise ValueError(f"{field_name} must be a tuple.")

    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(
            f"{field_name} must contain only non-empty strings."
        )


@dataclass(frozen=True)
class AgentDescriptor:
    """The caller-supplied description of an agent to route.

    This is the *input* to routing: it states what the agent is and what it
    can do, without stating how it should be evaluated.
    """

    agent_id: str
    agent_type: str
    protocol: str
    capabilities: frozenset[str] = frozenset()
    agent_version: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty_string(self.agent_id, "agent_id")
        _require_non_empty_string(self.agent_type, "agent_type")
        _require_non_empty_string(self.protocol, "protocol")
        _validate_string_set(self.capabilities, "capabilities")

        if self.agent_version is not None:
            _require_non_empty_string(self.agent_version, "agent_version")

        if not isinstance(self.metadata, dict):
            raise ValueError("metadata must be a dictionary.")


@dataclass(frozen=True)
class AgentProfile:
    """The resolved evaluation profile for an agent.

    A profile is produced by a strategy from an
    :class:`AgentDescriptor`. It must describe the *same* agent that was
    routed; the dispatcher rejects a profile for a different agent.
    """

    agent_id: str
    agent_type: str
    protocol: str
    capabilities: frozenset[str]
    agent_version: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty_string(self.agent_id, "agent_id")
        _require_non_empty_string(self.agent_type, "agent_type")
        _require_non_empty_string(self.protocol, "protocol")
        _validate_string_set(self.capabilities, "capabilities")

        if self.agent_version is not None:
            _require_non_empty_string(self.agent_version, "agent_version")

        if not isinstance(self.metadata, dict):
            raise ValueError("metadata must be a dictionary.")


@dataclass(frozen=True)
class DatasetSelection:
    """The dataset version chosen to evaluate an agent.

    The selection records the exact dataset *version* ids plus the reasons
    and limitations behind the choice, so a routing decision can be
    reviewed and reproduced.
    """

    dataset_ids: tuple[str, ...]
    dataset_version: str
    strategy_name: str
    rationale: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_string_tuple(self.dataset_ids, "dataset_ids")
        _require_non_empty_string(self.dataset_version, "dataset_version")
        _require_non_empty_string(self.strategy_name, "strategy_name")
        _validate_string_tuple(self.rationale, "rationale")
        _validate_string_tuple(self.limitations, "limitations")

        if len(self.dataset_ids) != len(set(self.dataset_ids)):
            raise ValueError("dataset_ids must not contain duplicates.")
