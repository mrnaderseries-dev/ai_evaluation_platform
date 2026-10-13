from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


EvidenceSource = Literal[
    "signal",
    "event",
    "trace",
    "metric",
    "log",
]

EvidenceKind = Literal[
    "observation",
    "temporal",
    "dependency",
    "execution",
    "pattern",
    "error",
    "quantitative",
]


class EvidenceRecord(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    evidence_id: str
    source: EvidenceSource
    source_ref: str
    evidence_kind: EvidenceKind

    agent_id: str
    system_id: str | None = None
    system_version: str | None = None

    run_id: str | None = None
    evaluation_run_id: str | None = None

    trace_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    component_id: str | None = None

    observed_at: datetime
    attributes: dict[str, Any] = Field(default_factory=dict)

    limitations: tuple[str, ...] = ()

    @field_validator(
        "evidence_id",
        "source_ref",
        "agent_id",
    )
    @classmethod
    def validate_required_strings(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Value must not be empty")
        return value

    @field_validator("observed_at")
    @classmethod
    def validate_observed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        return value

    @field_validator("attributes")
    @classmethod
    def validate_attributes(
        cls,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        return dict(value)

    @model_validator(mode="after")
    def validate_trace_fields(self):
        if self.parent_span_id is not None and self.span_id is None:
            raise ValueError("parent_span_id requires span_id")

        if self.span_id is not None and self.trace_id is None:
            raise ValueError("span_id requires trace_id")

        return self


class EvidenceCollection(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    records: tuple[EvidenceRecord, ...] = ()
    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_unique_ids(self):
        ids = [record.evidence_id for record in self.records]

        if len(ids) != len(set(ids)):
            raise ValueError("Evidence IDs must be unique")

        return self