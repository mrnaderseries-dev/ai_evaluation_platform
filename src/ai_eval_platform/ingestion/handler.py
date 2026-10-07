from __future__ import annotations

from typing import Protocol

from schemas.events.common import AIEvent, EventType


class HandlerError(Exception):
    """Permanent domain failure while handling a routed event (goes to DLQ)."""


class UnsupportedEventError(HandlerError):
    """A handler received an event type outside its route."""


class EventRepository(Protocol):
    """Subset of the storage event repository the handlers depend on."""

    def insert_from_envelope(
        self,
        envelope: AIEvent,
        *,
        sequence_number: int | None = ...,
    ) -> int:
        ...


class ExecutionEventHandler:
    """Persist validated execution-layer envelopes (model/tool/retrieval/answer/error/recovery)."""

    def __init__(self, repository: EventRepository) -> None:
        self._repository = repository

    def handle(self, event: AIEvent) -> None:
        match event.event_type:
            case (
                EventType.MODEL_CALLED
                | EventType.MODEL_OUTPUT
                | EventType.TOOL_CALLED
                | EventType.TOOL_COMPLETED
                | EventType.RETRIEVAL_STARTED
                | EventType.RETRIEVAL_COMPLETED
                | EventType.ANSWER_GENERATED
                | EventType.ERROR
                | EventType.RECOVERY_ATTEMPTED
                | EventType.RECOVERY_COMPLETED
            ):
                self._repository.insert_from_envelope(event)

            case _:
                raise UnsupportedEventError(
                    f"Unsupported execution event: {event.event_type.value}"
                )


class ContextEventHandler:
    """Persist validated context-layer envelopes (assembled/resource-used/updated)."""

    def __init__(self, repository: EventRepository) -> None:
        self._repository = repository

    def handle(self, event: AIEvent) -> None:
        match event.event_type:
            case (
                EventType.CONTEXT_ASSEMBLED
                | EventType.CONTEXT_RESOURCE_USED
                | EventType.CONTEXT_UPDATED
            ):
                self._repository.insert_from_envelope(event)

            case _:
                raise UnsupportedEventError(
                    f"Unsupported context event: {event.event_type.value}"
                )


class BehaviorEventHandler:
   

    def __init__(self, repository: EventRepository) -> None:
        self._repository = repository

    def handle(self, event: AIEvent) -> None:
        match event.event_type:
            case (
                EventType.AGENT_ACTION
                | EventType.VERIFICATION_ATTEMPTED
                | EventType.COUNTER_EVIDENCE_REQUESTED
                | EventType.ANSWER_REVISED
                | EventType.ABSTENTION
                | EventType.BEHAVIOR_PATTERN_DETECTED
            ):
                self._repository.insert_from_envelope(event)

            case _:
                raise UnsupportedEventError(
                    f"Unsupported behavior event: {event.event_type.value}"
                )


class EvaluationEventHandler:
    """Persist validated evaluation-completed envelopes."""

    def __init__(self, repository: EventRepository) -> None:
        self._repository = repository

    def handle(self, event: AIEvent) -> None:
        if event.event_type != EventType.EVALUATION_COMPLETED:
            raise UnsupportedEventError(
                f"Unsupported evaluation event: {event.event_type.value}"
            )

        self._repository.insert_from_envelope(event)


class AgentPolicyEventHandler:
    """Persist validated agent-policy envelopes (checks/decisions/reviews)."""

    def __init__(self, repository: EventRepository) -> None:
        self._repository = repository

    def handle(self, event: AIEvent) -> None:
        match event.event_type:
            case (
                EventType.POLICY_CHECKED
                | EventType.POLICY_ALLOWED
                | EventType.POLICY_BLOCKED
                | EventType.BEHAVIOR_RULE_TRIGGERED
                | EventType.HUMAN_REVIEW_REQUESTED
                | EventType.HUMAN_REVIEW_COMPLETED
            ):
                self._repository.insert_from_envelope(event)

            case _:
                raise UnsupportedEventError(
                    f"Unsupported agent policy event: {event.event_type.value}"
                )
