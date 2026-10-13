
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ai_eval_platform.storage.models import PersistedSignal


class NormalizedSignal(BaseModel):
    """A normalized, traceable representation of a persisted signal."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    signal_id: str
    agent_id: str
    signal_type: str
    scope: str

    run_id: str | None = None
    evaluation_run_id: str | None = None

    window_start: datetime | None = None
    window_end: datetime | None = None

    detected_at: datetime
    recorded_at: datetime

    attributes: dict[str, Any] = Field(default_factory=dict)
    raw_payload: dict[str, Any] = Field(default_factory=dict)

    payload_type: str | None = None
    detector_name: str | None = None
    dedupe_key: str | None = None


class SignalNormalizer:
    """Normalize persisted signals without changing their meaning."""

    def normalize(
        self,
        signal: PersistedSignal,
    ) -> NormalizedSignal:
        payload = self._copy_payload(signal.payload)

        return NormalizedSignal(
            signal_id=str(signal.signal_id),
            agent_id=str(signal.agent_id),
            signal_type=str(signal.signal_type),
            scope=str(signal.scope),
            run_id=(
                str(signal.run_id)
                if signal.run_id is not None
                else None
            ),
            evaluation_run_id=(
                str(signal.evaluation_run_id)
                if signal.evaluation_run_id is not None
                else None
            ),
            window_start=signal.window_start,
            window_end=signal.window_end,
            detected_at=signal.detected_at,
            recorded_at=signal.recorded_at,
            attributes=self._extract_attributes(payload),
            raw_payload=payload,
            payload_type=signal.payload_type,
            detector_name=signal.detector_name,
            dedupe_key=signal.dedupe_key,
        )

    def normalize_many(
        self,
        signals: tuple[PersistedSignal, ...]
        | list[PersistedSignal],
    ) -> tuple[NormalizedSignal, ...]:
        return tuple(
            self.normalize(signal)
            for signal in signals
        )

    @staticmethod
    def _copy_payload(payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict):
            return dict(payload)

        return {"value": payload}

    @staticmethod
    def _extract_attributes(
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        excluded_keys = {
            "signal_id",
            "agent_id",
            "signal_type",
            "scope",
            "run_id",
            "evaluation_run_id",
            "window_start",
            "window_end",
            "detected_at",
            "recorded_at",
        }

        return {
            key: value
            for key, value in payload.items()
            if key not in excluded_keys
        }