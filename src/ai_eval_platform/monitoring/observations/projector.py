from __future__ import annotations
import hashlib
from datetime import datetime, timezone
from typing import Any
from ..signals.models import Signal
from ..signals.types import (
    EventObservationPayload,
    SignalOrigin,
    SignalScope,
    SignalType,
)
from .models import Observation
PROJECTION_VERSION = "1.0"
def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256(
        "|".join(parts).encode("utf-8")
    ).hexdigest()
    return f"{prefix}_{digest[:56]}"


def project_event(
    event: Any,
    *,
    agent_id: str,
    observed_at: datetime | None = None,
) -> tuple[Observation, Signal]:
    """
    Project one persisted event into:
      1. A normalized Observation.
      2. A Base Signal representing the observed event.

    This function does not depend on detector results.
    The caller is responsible for persisting both outputs in the
    same transaction as the source event.
    """
    if not agent_id or not agent_id.strip():
        raise ValueError("agent_id must be a non-empty string")

    event_id = getattr(event, "event_id", None)
    if not isinstance(event_id, str) or not event_id.strip():
        raise ValueError("event.event_id must be a non-empty string")

    run_id = getattr(event, "run_id", None)
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("event.run_id must be a non-empty string")

    event_type = getattr(event, "event_type", None)
    if hasattr(event_type, "value"):
        event_type = event_type.value
    if not isinstance(event_type, str) or not event_type.strip():
        raise ValueError("event.event_type must be a non-empty string")

    occurred_at = getattr(event, "occurred_at", None)
    if not isinstance(occurred_at, datetime):
        raise ValueError("Persisted event must have a datetime occurred_at")
    if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)

    when_observed = observed_at or datetime.now(timezone.utc)
    if (
        when_observed.tzinfo is None
        or when_observed.utcoffset() is None
    ):
        raise ValueError("observed_at must be timezone-aware")

    sequence_number = getattr(event, "sequence_number", None)
    if (
        not isinstance(sequence_number, int)
        or isinstance(sequence_number, bool)
        or sequence_number < 0
    ):
        raise ValueError(
            "Persisted event must have a non-negative sequence_number"
        )

    payload = getattr(event, "payload", None) or {}
    if not isinstance(payload, dict):
        raise ValueError("event.payload must be a dictionary")

    projection_version = PROJECTION_VERSION

    observation_id = _stable_id(
        "obs", event_id, projection_version
    )

    observation = Observation(
        observation_id=observation_id,
        source_event_id=event_id,
        projection_version=projection_version,
        agent_id=agent_id,
        run_id=run_id,
        event_type=event_type,
        system_id=event.system_id,
        system_version=event.system_version,
        occurred_at=occurred_at,
        observed_at=when_observed,
        sequence_number=sequence_number,
        parent_event_id=event.parent_event_id,
        correlation_id=event.correlation_id,
        causation_id=event.causation_id,
        step_id=event.step_id,
        attributes=dict(payload),
    )

    signal_id = _stable_id(
        "sig", event_id, projection_version
    )

    base_signal = Signal(
        signal_id=signal_id,
        origin=SignalOrigin.BASE_OBSERVATION,
        source_event_id=event_id,
        source_observation_id=observation_id,
        projection_version=projection_version,
        agent_id=agent_id,
        signal_type=SignalType.EVENT_OBSERVATION,
        scope=SignalScope.RUN,
        run_id=run_id,
        detected_at=when_observed,
        payload=EventObservationPayload(
            observation_id=observation_id,
            source_event_id=event_id,
            event_type=event_type,
            occurred_at=occurred_at,
            sequence_number=sequence_number,
        ),
    )

    return observation, base_signal