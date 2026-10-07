from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from ..components.types import ComponentType


class Fault(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fault_id: str = Field(min_length=1)

    component: ComponentType

    reason: str = Field(min_length=1)

    confidence: float = Field(
        ge=0,
        le=1,
    )

    detected_at: datetime


class FaultImpact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fault: Fault

    affected_components: list[ComponentType] = Field(
        default_factory=list
    )

    unaffected_components: list[ComponentType] = Field(
        default_factory=list
    )

    cascading: bool = False

    shared_dependency: bool = False