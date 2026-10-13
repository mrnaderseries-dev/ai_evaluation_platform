import sys
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from ai_eval_platform.monitoring.adapters import (
    AdapterUnavailable,
    RunRecord,
    ToolCallRecord,
    pattern_input_for_run,
)
from ai_eval_platform.monitoring.detectors.base import Detector
from ai_eval_platform.monitoring.detectors.domain import (
    ConfidenceDropDetector,
    RouteChangeDetector,
    ToolUsageDriftDetector,
    ToolUsageDetectorPolicy,
)
from ai_eval_platform.monitoring.detectors.inputs import PatternDetectionInput
from ai_eval_platform.monitoring.detectors.strategies import (
    AnomalyDetector,
    AnomalyDetectorPolicy,
    DriftDetector,
    DriftDetectorPolicy,
    PatternDetector,
    PatternDetectorPolicy,
)
from ai_eval_platform.monitoring.orchestration import DetectorOutcome, run_monitoring
from ai_eval_platform.monitoring.signals.types import SignalScope, SignalType
from ai_eval_platform.monitoring_integration import (
    RunNotCompletedError,
    build_run_records,
    load_run_monitoring_context,
    monitor_completed_run,
)
from ai_eval_platform.storage.models import Agent, Base, Event, PersistedSignal, Run
from ai_eval_platform.storage.repositories import (
    EntityNotFoundError,
    EventRepository,
    PersistedSignalRepository,
)
from schemas.events.common import EventType


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)

    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture()
def session_factory(engine):
    @contextmanager
    def factory():
        session = Session(
            engine,
            expire_on_commit=False,
            autoflush=False,
        )

        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return factory


def _seed_run(engine, run_id, *, agent_id="agent-1", status="completed", events=()):
    with Session(
        engine,
        expire_on_commit=False,
        autoflush=False,
    ) as session:
        if session.get(Agent, agent_id) is None:
            session.add(Agent(agent_id=agent_id, name=agent_id))

        session.add(
            Run(
                run_id=run_id,
                agent_id=agent_id,
                status=status,
                started_at=_utcnow(),
                completed_at=_utcnow() if status == "completed" else None,
                duration_ms=750,
                trace_id=f"trace-{run_id}",
            )
        )

        for event in events:
            session.add(Event(**event))

        session.commit()


def _tool_called(run_id, sequence_number, event_id, tool_name, call_id):
    return {
        "event_id": event_id,
        "run_id": run_id,
        "sequence_number": sequence_number,
        "event_type": EventType.TOOL_CALLED.value,
        "payload": {
            "tool_id": f"tool-{tool_name}",
            "tool_name": tool_name,
            "call_id": call_id,
        },
    }


def _tool_completed(
    run_id,
    sequence_number,
    event_id,
    tool_name,
    call_id,
    *,
    success=True,
    latency_ms=42.0,
):
    return {
        "event_id": event_id,
        "run_id": run_id,
        "sequence_number": sequence_number,
        "event_type": EventType.TOOL_COMPLETED.value,
        "payload": {
            "tool_id": f"tool-{tool_name}",
            "tool_name": tool_name,
            "call_id": call_id,
            "success": success,
            "latency_ms": latency_ms,
        },
    }


def _abstention(run_id, sequence_number, event_id, confidence):
    return {
        "event_id": event_id,
        "run_id": run_id,
        "sequence_number": sequence_number,
        "event_type": EventType.ABSTENTION.value,
        "payload": {
            "reason_code": "uncertain",
            "confidence": confidence,
        },
    }


def _model_output(run_id, sequence_number, event_id):
    return {
        "event_id": event_id,
        "run_id": run_id,
        "sequence_number": sequence_number,
        "event_type": EventType.MODEL_OUTPUT.value,
        "payload": {
            "model_id": "model-1",
            "latency_ms": 120.0,
        },
    }


def test_completed_run_reaches_signal_with_lineage(engine, session_factory):
    _seed_run(
        engine,
        "run-1",
        events=[
            _tool_called("run-1", 1, "evt-t1", "search", "c1"),
            _tool_called("run-1", 2, "evt-t2", "search", "c2"),
        ],
    )

    result = monitor_completed_run("run-1", session_factory=session_factory)

    assert result.run_id == "run-1"
    assert result.agent_id == "agent-1"
    assert result.monitoring_run.agent_id == "agent-1"

    pattern_runs = [run for run in result.monitoring_run.runs if run.detector_name == "pattern"]
    assert len(pattern_runs) == 1
    assert pattern_runs[0].outcome is DetectorOutcome.SIGNALS
    assert len(pattern_runs[0].signals) == 1

    signal = pattern_runs[0].signals[0]
    assert signal.signal_type is SignalType.REPEATED_TOOL_USAGE
    assert signal.scope is SignalScope.RUN
    assert signal.agent_id == "agent-1"
    assert signal.run_id == "run-1"
    assert signal.evaluation_run_id is None
    assert signal.window_start is None
    assert signal.window_end is None
    assert signal.payload.tool_name == "search"
    assert signal.payload.occurrence_count == 2
    assert signal.payload.related_event_ids == ["evt-t1", "evt-t2"]
    assert "anomaly" in {deferred.detector_name for deferred in result.deferred}


def test_foreign_event_cannot_produce_cross_agent_signal(engine, session_factory):
    _seed_run(
        engine,
        "run-1",
        events=[_tool_called("run-1", 1, "evt-1", "search", "c1")],
    )
    _seed_run(
        engine,
        "run-2",
        agent_id="agent-2",
        events=[
            _tool_called("run-2", 1, "evt-x1", "search", "cx1"),
            _tool_called("run-2", 2, "evt-x2", "search", "cx2"),
        ],
    )

    result = monitor_completed_run("run-1", session_factory=session_factory)
    assert all(signal.agent_id == "agent-1" for signal in result.signals)
    assert all(signal.run_id == "run-1" for signal in result.signals)
    assert all(signal.run_id != "run-2" for signal in result.signals)
    assert result.monitoring_run.runs[0].outcome is DetectorOutcome.NO_FINDING

    with session_factory() as session:
        foreign_events = EventRepository(session).list_for_run("run-2")

    with pytest.raises(AdapterUnavailable):
        build_run_records(RunRecord(agent_id="agent-1", run_id="run-1"), foreign_events)

    foreign_records, _ = build_run_records(
        RunRecord(agent_id="agent-2", run_id="run-2"),
        foreign_events,
    )
    guarded = run_monitoring(
        "agent-1",
        [
            (
                PatternDetector(PatternDetectorPolicy()),
                pattern_input_for_run("agent-2", "run-2", foreign_records),
            )
        ],
    )
    assert guarded.signals == []
    assert guarded.runs[0].outcome is DetectorOutcome.DETECTOR_FAILED
    assert guarded.runs[0].reason == "agent boundary violated"


def test_missing_confidence_is_not_fabricated(engine, session_factory):
    _seed_run(
        engine,
        "run-1",
        events=[
            _tool_called("run-1", 1, "evt-t1", "search", "c1"),
            _tool_called("run-1", 2, "evt-t2", "search", "c2"),
            _abstention("run-1", 3, "evt-a1", 0.8),
            _abstention("run-1", 4, "evt-a2", None),
        ],
    )

    result = monitor_completed_run("run-1", session_factory=session_factory)

    executed = {run.detector_name for run in result.monitoring_run.runs}
    assert "confidence_drop" not in executed
    assert "confidence_drop" in {deferred.detector_name for deferred in result.deferred}
    assert all(signal.signal_type is not SignalType.CONFIDENCE_DROP for signal in result.signals)


def test_missing_baseline_defers_anomaly(engine, session_factory):
    _seed_run(
        engine,
        "run-1",
        events=[
            _tool_called("run-1", 1, "evt-t1", "search", "c1"),
            _tool_completed("run-1", 2, "evt-t2", "search", "c1", latency_ms=120.0),
        ],
    )

    with session_factory() as session:
        context = load_run_monitoring_context("run-1", session)

    assert context.tool_calls[0].latency_ms == 120.0

    result = monitor_completed_run("run-1", session_factory=session_factory)

    executed = {run.detector_name for run in result.monitoring_run.runs}
    assert "anomaly" not in executed
    assert "anomaly" in {deferred.detector_name for deferred in result.deferred}
    assert all(signal.signal_type is not SignalType.LATENCY_ANOMALY for signal in result.signals)



def test_no_relevant_events_differs_from_no_finding(engine, session_factory):
    _seed_run(engine, "run-empty", events=[_model_output("run-empty", 1, "evt-m1")])

    empty = monitor_completed_run("run-empty", session_factory=session_factory)

    assert empty.monitoring_run.runs == []
    assert empty.signals == []
    assert "pattern" in {deferred.detector_name for deferred in empty.deferred}

    _seed_run(
        engine,
        "run-plain",
        events=[
            _tool_called("run-plain", 1, "evt-p1", "search", "c1"),
            _tool_called("run-plain", 2, "evt-p2", "write", "c2"),
        ],
    )

    plain = monitor_completed_run("run-plain", session_factory=session_factory)

    assert len(plain.monitoring_run.runs) == 1
    plain_run = plain.monitoring_run.runs[0]
    assert plain_run.detector_name == "pattern"
    assert plain_run.outcome is DetectorOutcome.NO_FINDING
    assert plain_run.signals == []
    assert empty.monitoring_run.runs != plain.monitoring_run.runs


class _FailingDetector(Detector[PatternDetectionInput, PatternDetectorPolicy]):
    def __init__(self):
        self.policy = PatternDetectorPolicy()

    @property
    def name(self) -> str:
        return "failing"

    def detect(self, input_data: PatternDetectionInput) -> list:
        raise RuntimeError("boom")


def test_detector_failure_preserves_outcome(engine, session_factory):
    _seed_run(
        engine,
        "run-1",
        events=[
            _tool_called("run-1", 1, "evt-t1", "search", "c1"),
            _tool_called("run-1", 2, "evt-t2", "search", "c2"),
        ],
    )

    failing_input = pattern_input_for_run(
        "agent-1",
        "run-1",
        [
            ToolCallRecord(agent_id="agent-1", run_id="run-1", tool_name="search", call_id="c1", event_id="evt-t1"),
            ToolCallRecord(agent_id="agent-1", run_id="run-1", tool_name="search", call_id="c2", event_id="evt-t2"),
        ],
    )

    result = monitor_completed_run(
        "run-1",
        session_factory=session_factory,
        extra_assignments=[(_FailingDetector(), failing_input)],
    )

    by_name = {run.detector_name: run for run in result.monitoring_run.runs}
    assert by_name["failing"].outcome is DetectorOutcome.DETECTOR_FAILED
    assert by_name["failing"].reason == "RuntimeError"
    assert by_name["failing"].signals == []
    assert by_name["pattern"].outcome is DetectorOutcome.SIGNALS



def test_run_scope_invariants_for_every_signal(engine, session_factory):
    _seed_run(
        engine,
        "run-1",
        events=[
            _tool_called("run-1", 1, "evt-t1", "search", "c1"),
            _tool_called("run-1", 2, "evt-t2", "search", "c2"),
            _abstention("run-1", 3, "evt-a1", 0.9),
            _abstention("run-1", 4, "evt-a2", 0.6),
        ],
    )

    result = monitor_completed_run("run-1", session_factory=session_factory)

    assert len(result.signals) >= 1
    for signal in result.signals:
        assert signal.scope is SignalScope.RUN
        assert signal.agent_id == "agent-1"
        assert signal.run_id == "run-1"
        assert signal.evaluation_run_id is None
        assert signal.window_start is None
        assert signal.window_end is None


def test_multiple_applicable_detectors_run_together(engine, session_factory):
    _seed_run(
        engine,
        "run-1",
        events=[
            _tool_called("run-1", 1, "evt-t1", "search", "c1"),
            _tool_called("run-1", 2, "evt-t2", "search", "c2"),
            _abstention("run-1", 3, "evt-a1", 0.9),
            _abstention("run-1", 4, "evt-a2", 0.6),
        ],
    )

    result = monitor_completed_run("run-1", session_factory=session_factory)

    executed = {run.detector_name for run in result.monitoring_run.runs}
    assert executed == {"pattern", "confidence_drop"}
    assert all(run.outcome is DetectorOutcome.SIGNALS for run in result.monitoring_run.runs)
    assert len(result.monitoring_run.runs) == 2
    assert len(result.signals) == 2
    assert {signal.signal_type for signal in result.signals} == {
        SignalType.REPEATED_TOOL_USAGE,
        SignalType.CONFIDENCE_DROP,
    }

    deferred = {entry.detector_name for entry in result.deferred}
    all_detectors = {
        PatternDetector(PatternDetectorPolicy()).name,
        AnomalyDetector(AnomalyDetectorPolicy()).name,
        ConfidenceDropDetector().name,
        RouteChangeDetector().name,
        DriftDetector(DriftDetectorPolicy(drift_threshold=0.1)).name,
        ToolUsageDriftDetector(ToolUsageDetectorPolicy(drift_threshold=0.1)).name,
    }
    assert executed | deferred == all_detectors
    assert executed & deferred == set()

    confidence_signal = [
        signal for signal in result.signals if signal.signal_type is SignalType.CONFIDENCE_DROP
    ][0]
    assert confidence_signal.payload.metric == "confidence"
    assert confidence_signal.payload.previous_value == 0.9
    assert confidence_signal.payload.current_value == 0.6
    assert confidence_signal.payload.baseline == 0.9



def test_monitor_completed_run_persists_signals_to_database(engine, session_factory):
    _seed_run(
        engine,
        "run-1",
        events=[
            _tool_called("run-1", 1, "evt-t1", "search", "c1"),
            _tool_called("run-1", 2, "evt-t2", "search", "c2"),
        ],
    )

    result = monitor_completed_run("run-1", session_factory=session_factory)
    assert len(result.signals) == 1

    with Session(engine, expire_on_commit=False) as session:
        rows = session.scalars(select(PersistedSignal)).all()

    assert len(rows) == 1
    stored = rows[0]
    expected = result.signals[0]
    assert stored.signal_id == expected.signal_id
    assert stored.agent_id == "agent-1"
    assert stored.run_id == "run-1"
    assert stored.signal_type == expected.signal_type.value
    assert stored.detector_name == "pattern"
    assert stored.evaluation_run_id is None
    assert stored.window_start is None
    assert stored.window_end is None
    assert stored.payload_type is not None


def test_monitor_completed_run_persistence_failure_rolls_back(engine, session_factory):
    _seed_run(
        engine,
        "run-1",
        events=[
            _tool_called("run-1", 1, "evt-t1", "search", "c1"),
            _tool_called("run-1", 2, "evt-t2", "search", "c2"),
        ],
    )

    original = PersistedSignalRepository.insert_ignore_duplicates

    def failing(self, rows):
        raise RuntimeError("persistence failure")

    PersistedSignalRepository.insert_ignore_duplicates = failing

    try:
        with pytest.raises(RuntimeError, match="persistence failure"):
            monitor_completed_run("run-1", session_factory=session_factory)
    finally:
        PersistedSignalRepository.insert_ignore_duplicates = original

    with Session(engine, expire_on_commit=False) as session:
        assert session.scalar(select(func.count()).select_from(PersistedSignal)) == 0


def test_boundary_requires_completed_run(engine, session_factory):
    _seed_run(engine, "run-live", status="running")

    with pytest.raises(RunNotCompletedError):
        monitor_completed_run("run-live", session_factory=session_factory)

    with pytest.raises(EntityNotFoundError):
        monitor_completed_run("run-absent", session_factory=session_factory)


def test_monitoring_package_imports_no_storage_or_sqlalchemy():
    import ai_eval_platform.monitoring.adapters.records
    import ai_eval_platform.monitoring.adapters.run_adapters
    import ai_eval_platform.monitoring.detectors.domain.confidence
    import ai_eval_platform.monitoring.detectors.strategies.pattern
    import ai_eval_platform.monitoring.orchestration.runner
    import ai_eval_platform.monitoring.signals.models

    for module_name, module in list(sys.modules.items()):
        if module_name != "ai_eval_platform.monitoring" and not module_name.startswith(
            "ai_eval_platform.monitoring."
        ):
            continue
        module_keys = set(vars(module))
        assert "sqlalchemy" not in module_keys
        assert "Session" not in module_keys
        assert "RunRepository" not in module_keys
        assert "EventRepository" not in module_keys

