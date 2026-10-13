from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    analysis_type: str = "signal_summary"
    algorithm_name: str = "signal_summary"
    algorithm_version: str = "1.0.0"
    evidence_signal_ids: list[str]
    metrics: dict[str, Any]
    sample_size: int = Field(ge=0)
    window_start: datetime | None = None
    window_end: datetime | None = None
    limitations: list[str] = Field(default_factory=list)


