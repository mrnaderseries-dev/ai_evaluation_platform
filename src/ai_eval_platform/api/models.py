from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class ObservationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    observation_id: str
    source_event_id: str
    projection_version: str
    agent_id: str
    run_id: str
    event_type: str
    system_id: str
    system_version: str | None
    occurred_at: datetime
    observed_at: datetime
    sequence_number: int
    parent_event_id: str | None
    correlation_id: str | None
    causation_id: str | None
    step_id: str | None
    attributes: dict[str, Any]


class SignalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    signal_id: str
    origin: str
    source_event_id: str | None
    source_observation_id: str | None
    projection_version: str | None
    agent_id: str
    signal_type: str
    scope: str
    run_id: str | None
    evaluation_run_id: str | None
    window_start: datetime | None
    window_end: datetime | None
    detected_at: datetime
    payload: dict[str, Any]
    payload_type: str
    detector_name: str | None


class RCAReportResponse(BaseModel):
    investigation_id: str
    trigger_type: str
    status: str
    conclusion_level: str
    conclusion_type: str
    schema_version: str
    algorithm_name: str
    algorithm_version: str
    evidence_fingerprint: str
    hypotheses: list[dict[str, Any]]
    scope: dict[str, Any]
    limitations: list[str]
    analysis_id: str
    created_at: datetime
    updated_at: datetime
