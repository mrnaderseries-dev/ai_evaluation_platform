from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SignalRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    signal_type: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    run_id: str | None = None
    evaluation_run_id: str | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    detected_at: datetime
    payload: dict[str, object] = Field(default_factory=dict)
    payload_type: str = Field(min_length=1)


def signal_to_record(signal: BaseModel) -> SignalRecord:
    payload = signal.payload
    return SignalRecord(
        signal_id=signal.signal_id,
        agent_id=signal.agent_id,
        signal_type=signal.signal_type.value,
        scope=signal.scope.value,
        run_id=signal.run_id,
        evaluation_run_id=signal.evaluation_run_id,
        window_start=signal.window_start,
        window_end=signal.window_end,
        detected_at=signal.detected_at,
        payload=payload.model_dump(mode="json"),
        payload_type=type(payload).__name__,
    )
