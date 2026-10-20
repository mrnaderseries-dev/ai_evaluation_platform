"""Agent-specific evaluation routing.

Routing answers one question: *given an agent, which strategy should
evaluate it, and which dataset version should that strategy use?*

The package is deliberately small and pure. It contains:

* :mod:`ai_eval_platform.routing.models` — the frozen value objects that
  describe an agent, its resolved profile, and a dataset selection.
* :mod:`ai_eval_platform.routing.protocols` — the strategy extension
  point.
* :mod:`ai_eval_platform.routing.registry` — deterministic lookup of a
  strategy by agent type.
* :mod:`ai_eval_platform.routing.dispatcher` — the single place where a
  routing decision is made and validated.

Routing never executes an evaluation and never decides whether a change is
acceptable.
"""

from .dispatcher import (
    AgentRoutingDispatcher,
    RoutingResult,
    RoutingValidationError,
)
from .models import AgentDescriptor, AgentProfile, DatasetSelection
from .protocols import AgentRoutingStrategy
from .registry import (
    AgentRoutingRegistry,
    DuplicateAgentTypeError,
    InvalidRoutingStrategyError,
    UnsupportedAgentTypeError,
)

__all__ = [
    "AgentDescriptor",
    "AgentProfile",
    "DatasetSelection",
    "AgentRoutingStrategy",
    "AgentRoutingRegistry",
    "AgentRoutingDispatcher",
    "RoutingResult",
    "RoutingValidationError",
    "DuplicateAgentTypeError",
    "InvalidRoutingStrategyError",
    "UnsupportedAgentTypeError",
]
