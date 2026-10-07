from typing import Any, Literal

from pydantic import BaseModel, Field

from schemas.events.common import AIEvent, EventType


class ContextResource(BaseModel):
    resource_id: str = Field(..., min_length=1)
    resource_type: str = Field(..., min_length=1)
    source: str | None = None
    relevance_score: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContextAssembledPayload(BaseModel):
    context_id: str = Field(..., min_length=1)
    resource_count: int = Field(..., ge=0)
    resources: list[ContextResource] = Field(default_factory=list)
    token_count: int | None = Field(default=None, ge=0)


class ContextResourceUsedPayload(BaseModel):
    context_id: str = Field(..., min_length=1)
    resource_id: str = Field(..., min_length=1)
    resource_type: str = Field(..., min_length=1)
    usage_type: str = Field(..., min_length=1)


class ContextUpdatedPayload(BaseModel):
    context_id: str = Field(..., min_length=1)
    previous_resource_count: int = Field(..., ge=0)
    current_resource_count: int = Field(..., ge=0)
    reason: str | None = None


class ContextAssembledEvent(AIEvent):
    event_type: Literal[EventType.CONTEXT_ASSEMBLED] = EventType.CONTEXT_ASSEMBLED
    payload: ContextAssembledPayload


class ContextResourceUsedEvent(AIEvent):
    event_type: Literal[EventType.CONTEXT_RESOURCE_USED] = EventType.CONTEXT_RESOURCE_USED
    payload: ContextResourceUsedPayload


class ContextUpdatedEvent(AIEvent):
    event_type: Literal[EventType.CONTEXT_UPDATED] = EventType.CONTEXT_UPDATED
    payload: ContextUpdatedPayload