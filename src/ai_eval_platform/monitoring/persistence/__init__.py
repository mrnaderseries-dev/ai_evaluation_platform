import hashlib
import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..signals.models import Signal
from ..signals.types import SignalOrigin


class SignalRecord(BaseModel):
    """Validated persistence representation of a signal."""

    model_config = ConfigDict(
        extra="forbid",
    )

    signal_id: str = Field(min_length=1)
    origin: str = SignalOrigin.DETECTOR.value
    source_event_id: str | None = None
    source_observation_id: str | None = None
    projection_version: str | None = None
    agent_id: str = Field(min_length=1)
    signal_type: str = Field(min_length=1)
    scope: str = Field(min_length=1)

    run_id: str | None = None
    evaluation_run_id: str | None = None

    window_start: datetime | None = None
    window_end: datetime | None = None

    detected_at: datetime

    payload: dict[str, Any] = Field(default_factory=dict)
    payload_type: str = Field(min_length=1)

    detector_name: str | None = None
    dedupe_key: str | None = None


def signal_to_record(
    signal: Signal,
    detector_name: str | None = None,
) -> SignalRecord:
    payload = signal.payload

    return SignalRecord(
        signal_id=signal.signal_id,
        origin=signal.origin.value,
        source_event_id=signal.source_event_id,
        source_observation_id=signal.source_observation_id,
        projection_version=signal.projection_version,
        agent_id=signal.agent_id,
        signal_type=signal.signal_type.value,
        scope=signal.scope.value,
        run_id=signal.run_id,
        evaluation_run_id=signal.evaluation_run_id,
        window_start=signal.window_start,
        window_end=signal.window_end,
        detected_at=signal.detected_at,
        payload=payload.model_dump(mode="json"),
        payload_type=type(payload).__name__,  # تم التصحيح هنا لاستخدام __name__
        detector_name=detector_name,
    )


def signal_dedupe_key(
    record: SignalRecord,
    ordinal: int | None = None,
) -> str:
    """Build a deterministic key from the signal's semantic content.

    The ordinal parameter remains for source compatibility but is deliberately
    excluded: list position is not stable identity across replays. Identity is
    defined by agent, detector, evaluation/run scope, payload and payload type,
    origin, source event/observation and projection version, signal type, and
    window bounds. Signal ID and detected/recorded timestamps are excluded so
    replayed representations of the same detection share a key.
    """
    identity = {
        "agent_id": record.agent_id,
        "detector_name": record.detector_name,
        "evaluation_run_id": record.evaluation_run_id,
        "payload": record.payload,
        "origin": record.origin,
        "source_event_id": record.source_event_id,
        "source_observation_id": record.source_observation_id,
        "projection_version": record.projection_version,
        "payload_type": record.payload_type,
        "run_id": record.run_id,
        "scope": record.scope,
        "signal_type": record.signal_type,
        "window_end": (
            record.window_end.isoformat()
            if record.window_end is not None
            else None
        ),
        "window_start": (
            record.window_start.isoformat()
            if record.window_start is not None
            else None
        ),
    }

    encoded = json.dumps(
        identity,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")

    return hashlib.sha256(encoded).hexdigest()