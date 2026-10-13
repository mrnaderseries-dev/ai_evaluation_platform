from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Observation(BaseModel):
    """Versioned, agent-agnostic normalized projection of one source event."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    observation_id: str = Field(min_length=1)
    source_event_id: str = Field(min_length=1)
    projection_version: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    system_id: str = Field(min_length=1)
    system_version: str | None = None
    occurred_at: datetime
    observed_at: datetime
    sequence_number: int = Field(ge=0)
    parent_event_id: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    step_id: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("occurred_at", "observed_at")
    @classmethod
    def timestamps_are_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observation timestamps must be timezone-aware")
        return value
