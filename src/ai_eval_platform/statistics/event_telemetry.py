from math import isfinite
from numbers import Real

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ai_eval_platform.storage.models import Event
from ai_eval_platform.storage.repositories import EventRepository

from .analyzer import StatisticalAnalyzer
from .descriptive import DescriptiveStatistics


_NUMERIC_EVENT_FIELDS: dict[str, tuple[str, ...]] = {
    "model_output": (
        "latency_ms",
        "input_tokens",
        "output_tokens",
        "total_tokens",
    ),
    "tool_completed": ("latency_ms",),
    "retrieval_completed": ("latency_ms", "result_count"),
    "context_assembled": ("resource_count", "token_count"),
}


class EventMetricStatistics(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    metric_name: str = Field(min_length=1)
    observation_count: int = Field(ge=0)
    excluded_count: int = Field(ge=0)
    source_event_ids: tuple[str, ...]
    statistics: DescriptiveStatistics | None = None
    limitations: tuple[str, ...]


class EventTelemetryStatisticsService:
    """Describe explicitly numeric event payload fields within one run."""

    PAGE_SIZE = 250

    def __init__(
        self,
        session: Session,
        analyzer: StatisticalAnalyzer | None = None,
    ) -> None:
        self._events = EventRepository(session)
        self._analyzer = analyzer or StatisticalAnalyzer()

    def summarize_run(self, run_id: str) -> tuple[EventMetricStatistics, ...]:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id must not be empty")

        events = self._load_all(run_id)
        groups: dict[tuple[str, str], dict[str, object]] = {}
        for event in events:
            fields = _NUMERIC_EVENT_FIELDS.get(event.event_type)
            if fields is None:
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            for metric_name in fields:
                group = groups.setdefault(
                    (event.event_type, metric_name),
                    {"values": [], "event_ids": [], "excluded": 0},
                )
                value = payload.get(metric_name)
                if (
                    isinstance(value, bool)
                    or not isinstance(value, Real)
                    or not isfinite(float(value))
                    or value < 0
                ):
                    group["excluded"] = int(group["excluded"]) + 1
                    continue
                group["values"].append(float(value))
                group["event_ids"].append(event.event_id)

        summaries: list[EventMetricStatistics] = []
        for (event_type, metric_name), group in sorted(groups.items()):
            values = group["values"]
            event_ids = group["event_ids"]
            excluded_count = int(group["excluded"])
            limitations = [
                "Descriptive within-run event observations only; repeated calls may be dependent.",
                "No significance, causal effect, or cross-run comparison is inferred.",
            ]
            if excluded_count:
                limitations.append(
                    f"Excluded {excluded_count} missing or invalid {metric_name} value(s)."
                )
            summaries.append(
                EventMetricStatistics(
                    run_id=run_id,
                    event_type=event_type,
                    metric_name=metric_name,
                    observation_count=len(values),
                    excluded_count=excluded_count,
                    source_event_ids=tuple(event_ids),
                    statistics=self._analyzer.describe(values) if values else None,
                    limitations=tuple(limitations),
                )
            )
        return tuple(summaries)

    def _load_all(self, run_id: str) -> list[Event]:
        records: list[Event] = []
        offset = 0
        while True:
            page = self._events.list_for_run(
                run_id,
                limit=self.PAGE_SIZE,
                offset=offset,
            )
            records.extend(page)
            if len(page) < self.PAGE_SIZE:
                return records
            offset += len(page)