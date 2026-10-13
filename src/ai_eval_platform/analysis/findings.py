from datetime import datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)


class AnalysisFinding(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    finding_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)

    finding_type: Literal[
        "signal_summary",
        "trend",
        "regression",
        "anomaly",
        "correlation",
        "root_cause_hypothesis",
    ]

    severity: Literal[
        "info",
        "low",
        "medium",
        "high",
        "critical",
    ]

    title: str = Field(min_length=1)
    description: str = Field(min_length=1)

    evidence_signal_ids: tuple[str, ...]

    metrics: dict[str, Any] = Field(default_factory=dict)

    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
    )

    observed_at: datetime

    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_evidence(self) -> "AnalysisFinding":
        if (
            not self.evidence_signal_ids
            and self.finding_type != "signal_summary"
        ):
            raise ValueError(
                "Findings other than signal summaries require evidence."
            )

        if len(set(self.evidence_signal_ids)) != len(
            self.evidence_signal_ids
        ):
            raise ValueError(
                "Evidence Signal IDs must be unique."
            )

        return self