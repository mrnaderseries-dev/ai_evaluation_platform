
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from schemas.events.common import EventType

from ai_eval_platform.monitoring.adapters import (
    AdapterUnavailable,
    RunRecord,
    ToolCallRecord,
    pattern_input_for_run,
)
from ai_eval_platform.monitoring.detectors.base import Detector
from ai_eval_platform.monitoring.detectors.domain import (
    ConfidenceDetectionInput,
    ConfidenceDropDetector,
)
from ai_eval_platform.monitoring.detectors.strategies import (
    PatternDetector,
    PatternDetectorPolicy,
)
from ai_eval_platform.monitoring.orchestration import (
    MonitoringRun,
    run_monitoring,
)
from ai_eval_platform.monitoring.persistence import (
    SignalRecord,
    signal_dedupe_key,
    signal_to_record,
)
from ai_eval_platform.monitoring.signals.models import Signal
from ai_eval_platform.storage.models import Event, PersistedSignal, Run
from ai_eval_platform.storage.repositories import (
    EventRepository,
    PersistedSignalRepository,
    RunRepository,
)


class RunNotCompletedError(Exception):
    pass


class ConfidenceMeasurement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(min_length=1)
    sequence_number: int = Field(ge=0)
    value: float = Field(ge=0, le=1)


class RunMonitoringContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run: RunRecord
    tool_calls: list[ToolCallRecord]
    confidence_measurements: list[ConfidenceMeasurement]


class DeferredDetector(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    detector_name: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class RunMonitoringResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    monitoring_run: MonitoringRun
    deferred: list[DeferredDetector]

    @property
    def signals(self) -> list[Signal]:
        return self.monitoring_run.signals


def run_record_from_run(run: Run) -> RunRecord:
    return RunRecord(
        agent_id=run.agent_id,
        run_id=run.run_id,
        started_at=run.started_at,
        completed_at=run.completed_at,
        duration_ms=(
            float(run.duration_ms)
            if run.duration_ms is not None
            else None
        ),
        trace_id=run.trace_id,
    )


def build_run_records(
    run: RunRecord,
    events: Sequence[Event],
) -> tuple[list[ToolCallRecord], list[ConfidenceMeasurement]]:
    ordered_events = sorted(
        events,
        key=lambda event: (
            event.sequence_number,
            event.event_id,
        ),
    )

    call_entries: list[dict[str, Any]] = []
    call_index: dict[str, int] = {}
    measurements: list[ConfidenceMeasurement] = []

    for event in ordered_events:
        if event.run_id != run.run_id:
            raise AdapterUnavailable(
                f"Event '{event.event_id}' belongs to run "
                f"'{event.run_id}', not '{run.run_id}'."
            )

        payload = event.payload or {}

        if event.event_type == EventType.TOOL_CALLED.value:
            call_id = payload.get("call_id")

            entry = {
                "tool_name": payload.get("tool_name"),
                "call_id": call_id,
                "event_id": event.event_id,
                "latency_ms": None,
                "success": None,
            }
            call_entries.append(entry)

            if isinstance(call_id, str):
                call_index.setdefault(
                    call_id,
                    len(call_entries) - 1,
                )

        elif event.event_type == EventType.TOOL_COMPLETED.value:
            call_id = payload.get("call_id")
            index = (
                call_index.get(call_id)
                if isinstance(call_id, str)
                else None
            )

            if index is None:
                entry = {
                    "tool_name": payload.get("tool_name"),
                    "call_id": call_id,
                    "event_id": event.event_id,
                    "latency_ms": payload.get("latency_ms"),
                    "success": payload.get("success"),
                }
                call_entries.append(entry)

                if isinstance(call_id, str):
                    call_index.setdefault(
                        call_id,
                        len(call_entries) - 1,
                    )
            else:
                entry = call_entries[index]

                if entry["tool_name"] is None:
                    entry["tool_name"] = payload.get("tool_name")

                if payload.get("latency_ms") is not None:
                    entry["latency_ms"] = payload["latency_ms"]

                if payload.get("success") is not None:
                    entry["success"] = payload["success"]

        elif event.event_type in (
            EventType.ABSTENTION.value,
            EventType.BEHAVIOR_PATTERN_DETECTED.value,
        ):
            confidence = payload.get("confidence")

            if confidence is None:
                continue

            measurements.append(
                ConfidenceMeasurement(
                    event_id=event.event_id,
                    sequence_number=event.sequence_number,
                    value=float(confidence),
                )
            )

    tool_calls = [
        ToolCallRecord(
            agent_id=run.agent_id,
            run_id=run.run_id,
            tool_name=entry["tool_name"],
            call_id=entry["call_id"],
            event_id=entry["event_id"],
            latency_ms=entry["latency_ms"],
            success=entry["success"],
        )
        for entry in call_entries
    ]

    measurements.sort(
        key=lambda measurement: (
            measurement.sequence_number,
            measurement.event_id,
        )
    )

    return tool_calls, measurements


def load_run_monitoring_context(
    run_id: str,
    session: Session,
) -> RunMonitoringContext:
    run = RunRepository(session).get_required(run_id)

    if run.status != "completed":
        raise RunNotCompletedError(
            f"Run '{run_id}' has status '{run.status}', "
            "expected 'completed'."
        )

    events = EventRepository(session).list_for_run(run_id)
    run_record = run_record_from_run(run)

    tool_calls, measurements = build_run_records(
        run_record,
        events,
    )

    return RunMonitoringContext(
        run=run_record,
        tool_calls=tool_calls,
        confidence_measurements=measurements,
    )


def _confidence_input_for_run(
    context: RunMonitoringContext,
) -> ConfidenceDetectionInput:
    measurements = sorted(
        context.confidence_measurements,
        key=lambda measurement: (
            measurement.sequence_number,
            measurement.event_id,
        ),
    )

    if len(measurements) < 2:
        raise ValueError(
            "At least two confidence measurements are required."
        )

    first_value = measurements[0].value
    latest_value = measurements[-1].value

    return ConfidenceDetectionInput(
        agent_id=context.run.agent_id,
        run_id=context.run.run_id,
        metric="confidence",
        previous_value=first_value,
        current_value=latest_value,
        baseline=first_value,
    )


def select_run_assignments(
    context: RunMonitoringContext,
) -> tuple[
    list[tuple[Detector, BaseModel]],
    list[DeferredDetector],
]:
    assignments: list[tuple[Detector, BaseModel]] = []
    deferred: list[DeferredDetector] = []

    if context.tool_calls:
        assignments.append(
            (
                PatternDetector(PatternDetectorPolicy()),
                pattern_input_for_run(
                    context.run.agent_id,
                    context.run.run_id,
                    context.tool_calls,
                ),
            )
        )
    else:
        deferred.append(
            DeferredDetector(
                detector_name="pattern",
                reason="no tool call events for run",
            )
        )

    if len(context.confidence_measurements) >= 2:
        assignments.append(
            (
                ConfidenceDropDetector(),
                _confidence_input_for_run(context),
            )
        )
    else:
        deferred.append(
            DeferredDetector(
                detector_name="confidence_drop",
                reason=(
                    "fewer than two confidence measurements for run"
                ),
            )
        )

    deferred.extend(
        [
            DeferredDetector(
                detector_name="anomaly",
                reason="no baseline reference for run latency",
            ),
            DeferredDetector(
                detector_name="drift",
                reason="no baseline distribution for run",
            ),
            DeferredDetector(
                detector_name="route_change",
                reason="run scope cannot provide window history",
            ),
            DeferredDetector(
                detector_name="tool_usage_drift",
                reason="run scope cannot provide reference window",
            ),
        ]
    )

    return assignments, deferred


def persisted_signal_from_record(
    record: SignalRecord,
) -> PersistedSignal:
    return PersistedSignal(
        signal_id=record.signal_id,
        origin=record.origin,
        source_event_id=record.source_event_id,
        source_observation_id=record.source_observation_id,
        projection_version=record.projection_version,
        agent_id=record.agent_id,
        signal_type=record.signal_type,
        scope=record.scope,
        run_id=record.run_id,
        evaluation_run_id=record.evaluation_run_id,
        window_start=record.window_start,
        window_end=record.window_end,
        detected_at=record.detected_at,
        payload=record.payload,
        payload_type=record.payload_type,
        detector_name=record.detector_name,
        dedupe_key=record.dedupe_key,
    )


def persist_monitoring_signals(
    monitoring_run: MonitoringRun,
    session: Session,
) -> int:
    rows: list[PersistedSignal] = []

    for detector_run in monitoring_run.runs:
        if detector_run.agent_id != monitoring_run.agent_id:
            raise ValueError(
                "Detector run agent_id does not match monitoring run."
            )

        for signal in detector_run.signals:
            if signal.agent_id != monitoring_run.agent_id:
                continue

            record = signal_to_record(
                signal,
                detector_name=detector_run.detector_name,
            )

            record = record.model_copy(
                update={"dedupe_key": signal_dedupe_key(record)}
            )

            rows.append(
                persisted_signal_from_record(record)
            )

    return PersistedSignalRepository(
        session
    ).insert_ignore_duplicates(rows)


def monitor_completed_run(
    run_id: str,
    *,
    session_factory: Callable[
        [],
        AbstractContextManager[Session],
    ],
    extra_assignments: Sequence[
        tuple[Detector, BaseModel]
    ] = (),
) -> RunMonitoringResult:
    with session_factory() as session:
        context = load_run_monitoring_context(
            run_id,
            session,
        )

    assignments, deferred = select_run_assignments(context)
    assignments.extend(extra_assignments)

    monitoring_run = run_monitoring(
        context.run.agent_id,
        assignments,
    )

    with session_factory() as session:
        persist_monitoring_signals(
            monitoring_run,
            session,
        )

    return RunMonitoringResult(
        run_id=context.run.run_id,
        agent_id=context.run.agent_id,
        monitoring_run=monitoring_run,
        deferred=deferred,
    )


__all__ = [
    "ConfidenceMeasurement",
    "DeferredDetector",
    "RunMonitoringContext",
    "RunMonitoringResult",
    "RunNotCompletedError",
    "build_run_records",
    "load_run_monitoring_context",
    "monitor_completed_run",
    "persist_monitoring_signals",
    "persisted_signal_from_record",
    "run_record_from_run",
    "select_run_assignments",
]
