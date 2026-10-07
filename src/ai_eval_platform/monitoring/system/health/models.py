from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..components.types import ComponentType


class HealthStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


class CheckType(StrEnum):
    LIVENESS = "liveness"
    READINESS = "readiness"
    DEPENDENCY = "dependency"


class HealthCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component: ComponentType

    check_type: CheckType

    status: HealthStatus

    checked_at: datetime

    duration_ms: float = Field(ge=0)

    dependency: ComponentType | None = None

    timeout_ms: float = Field(gt=0)

    details: dict[str, Any] = Field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status == HealthStatus.PASS