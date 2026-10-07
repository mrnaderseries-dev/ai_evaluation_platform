from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..components.types import ComponentType
from .types import MetricDimension


class SystemMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric_id: str = Field(min_length=1)

    component: ComponentType

    metric_name: str = Field(min_length=1)

    dimension: MetricDimension

    value: float

    unit: str = Field(min_length=1)

    timestamp: datetime

    metadata: dict[str, Any] = Field(default_factory=dict)