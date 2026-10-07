from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from schemas.events.common import AIEvent, EventType


class RouterError(Exception):
    pass


class RouteConfigurationError(RouterError):
    pass


class MissingRouteError(RouterError):
    pass


class InvalidRouteError(RouteConfigurationError):
    pass


class DuplicateRouteError(RouteConfigurationError):
    pass


class EventHandler(Protocol):
    def handle(self, event: AIEvent) -> None:
        ...


class RouteKind(str, Enum):
    EXECUTION = "execution"
    CONTEXT = "context"
    BEHAVIOR = "behavior"
    EVALUATION = "evaluation"
    AGENT_POLICY = "agent_policy"


@dataclass(frozen=True, slots=True)
class EventRoute:
    event_type: EventType
    route_kind: RouteKind
    handler: EventHandler


EXPECTED_ROUTES: dict[EventType, RouteKind] = {
    EventType.MODEL_CALLED: RouteKind.EXECUTION,
    EventType.MODEL_OUTPUT: RouteKind.EXECUTION,
    EventType.TOOL_CALLED: RouteKind.EXECUTION,
    EventType.TOOL_COMPLETED: RouteKind.EXECUTION,
    EventType.RETRIEVAL_STARTED: RouteKind.EXECUTION,
    EventType.RETRIEVAL_COMPLETED: RouteKind.EXECUTION,
    EventType.ANSWER_GENERATED: RouteKind.EXECUTION,
    EventType.ERROR: RouteKind.EXECUTION,
    EventType.RECOVERY_ATTEMPTED: RouteKind.EXECUTION,
    EventType.RECOVERY_COMPLETED: RouteKind.EXECUTION,

    EventType.CONTEXT_ASSEMBLED: RouteKind.CONTEXT,
    EventType.CONTEXT_RESOURCE_USED: RouteKind.CONTEXT,
    EventType.CONTEXT_UPDATED: RouteKind.CONTEXT,

    EventType.EVALUATION_COMPLETED: RouteKind.EVALUATION,

    EventType.AGENT_ACTION: RouteKind.BEHAVIOR,
    EventType.VERIFICATION_ATTEMPTED: RouteKind.BEHAVIOR,
    EventType.COUNTER_EVIDENCE_REQUESTED: RouteKind.BEHAVIOR,
    EventType.ANSWER_REVISED: RouteKind.BEHAVIOR,
    EventType.ABSTENTION: RouteKind.BEHAVIOR,
    EventType.BEHAVIOR_PATTERN_DETECTED: RouteKind.BEHAVIOR,

    EventType.POLICY_CHECKED: RouteKind.AGENT_POLICY,
    EventType.POLICY_ALLOWED: RouteKind.AGENT_POLICY,
    EventType.POLICY_BLOCKED: RouteKind.AGENT_POLICY,
    EventType.BEHAVIOR_RULE_TRIGGERED: RouteKind.AGENT_POLICY,
    EventType.HUMAN_REVIEW_REQUESTED: RouteKind.AGENT_POLICY,
    EventType.HUMAN_REVIEW_COMPLETED: RouteKind.AGENT_POLICY,
}


class RouteRegistry:
    def __init__(self) -> None:
        self._routes: dict[EventType, EventRoute] = {}

    def register(
        self,
        event_type: EventType,
        route_kind: RouteKind,
        handler: EventHandler,
    ) -> None:
        if event_type in self._routes:
            raise DuplicateRouteError(
                f"Event type '{event_type.value}' is already registered."
            )

        self._routes[event_type] = EventRoute(
            event_type=event_type,
            route_kind=route_kind,
            handler=handler,
        )

    def get(
        self,
        event_type: EventType,
    ) -> EventRoute | None:
        return self._routes.get(event_type)

    def all_routes(self) -> dict[EventType, EventRoute]:
        return dict(self._routes)


class RouteConfigurationValidator:
    @staticmethod
    def validate(
        registry: RouteRegistry,
    ) -> None:
        routes = registry.all_routes()

        registered_event_types = set(routes)
        expected_event_types = set(EXPECTED_ROUTES)

        # A deployed registry must be able to route *every* known event type;
        # an event with no route would fail at runtime instead of at startup.
        missing_routes = expected_event_types - registered_event_types

        if missing_routes:
            names = sorted(
                event_type.value for event_type in missing_routes
            )

            raise MissingRouteError(
                "No route registered for event types: " + ", ".join(names)
            )

        unexpected_routes = registered_event_types - expected_event_types

        if unexpected_routes:
            names = sorted(
                event_type.value
                for event_type in unexpected_routes
            )

            raise RouteConfigurationError(
                "Unexpected event routes: " + ", ".join(names)
            )

        for event_type, route in routes.items():
            expected_kind = EXPECTED_ROUTES[event_type]

            if route.route_kind != expected_kind:
                raise InvalidRouteError(
                    f"Invalid route for '{event_type.value}': "
                    f"expected '{expected_kind.value}', "
                    f"got '{route.route_kind.value}'."
                )


class EventRouter:
    def __init__(
        self,
        registry: RouteRegistry,
    ) -> None:
        self._registry = registry

    def route(
        self,
        event: AIEvent,
    ) -> None:
        route = self._registry.get(event.event_type)

        if route is None:
            raise MissingRouteError(
                f"No route registered for event type "
                f"'{event.event_type.value}'."
            )

        route.handler.handle(event)


def build_route_registry(
    execution_handler: EventHandler,
    context_handler: EventHandler,
    behavior_handler: EventHandler,
    evaluation_handler: EventHandler,
    agent_policy_handler: EventHandler,
) -> RouteRegistry:
    handlers: dict[RouteKind, EventHandler] = {
        RouteKind.EXECUTION: execution_handler,
        RouteKind.CONTEXT: context_handler,
        RouteKind.BEHAVIOR: behavior_handler,
        RouteKind.EVALUATION: evaluation_handler,
        RouteKind.AGENT_POLICY: agent_policy_handler,
    }

    registry = RouteRegistry()

    for event_type, route_kind in EXPECTED_ROUTES.items():
        handler = handlers[route_kind]

        registry.register(
            event_type=event_type,
            route_kind=route_kind,
            handler=handler,
        )

    RouteConfigurationValidator.validate(registry)

    return registry


def build_event_router(
    execution_handler: EventHandler,
    context_handler: EventHandler,
    behavior_handler: EventHandler,
    evaluation_handler: EventHandler,
    agent_policy_handler: EventHandler,
) -> EventRouter:
    registry = build_route_registry(
        execution_handler=execution_handler,
        context_handler=context_handler,
        behavior_handler=behavior_handler,
        evaluation_handler=evaluation_handler,
        agent_policy_handler=agent_policy_handler,
    )

    return EventRouter(registry)