from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from ..components.types import ComponentType
from ..health.models import HealthCheckResult
from ..metrics.models import SystemMetric


class SystemStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class ComponentHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component: ComponentType

    status: SystemStatus

    checks: list[HealthCheckResult] = Field(
        default_factory=list
    )

    metrics: list[SystemMetric] = Field(
        default_factory=list
    )

    reasons: list[str] = Field(
        default_factory=list
    )

    evaluated_at: datetime


class PlatformHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: SystemStatus

    components: list[ComponentHealth]

    reasons: list[str] = Field(
        default_factory=list
    )

    evaluated_at: datetime