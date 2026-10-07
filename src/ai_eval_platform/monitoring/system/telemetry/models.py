from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..components.types import ComponentType


class TelemetryKind(StrEnum):
    COUNTER = "counter"
    GAUGE = "gauge"
    OBSERVATION = "observation"
    EVENT = "event"


class RawTelemetry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    telemetry_id: str = Field(min_length=1)

    component: ComponentType

    name: str = Field(min_length=1)

    kind: TelemetryKind

    value: float | None = None

    unit: str | None = None

    timestamp: datetime

    metadata: dict[str, Any] = Field(default_factory=dict)