from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from ..components.types import ComponentType


class AlertSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertStatus(StrEnum):
    FIRING = "firing"
    RESOLVED = "resolved"


class Alert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alert_id: str = Field(min_length=1)

    component: ComponentType | None = None

    severity: AlertSeverity

    status: AlertStatus

    condition: str = Field(min_length=1)

    message: str = Field(min_length=1)

    created_at: datetime