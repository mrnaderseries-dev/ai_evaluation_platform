from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from ai_eval_platform import monitoring_integration, run_lifecycle
from ai_eval_platform.run_lifecycle import (
    RunLifecycleService,
    RunStateTransitionError,
)
from ai_eval_platform.storage.models import Agent, Base, Event, PersistedSignal, Run
from ai_eval_platform.storage.repositories import (
    EntityNotFoundError,
    PersistedSignalRepository,
    RunRepository,
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
        factory.open_sessions += 1

        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            factory.open_sessions -= 1
            session.close()

    factory.open_sessions = 0
    return factory


def _service(session_factory):
    return RunLifecycleService(session_factory=session_factory)


def _seed_run(
    engine,
    run_id,
    *,
    agent_id="agent-1",
    status="running",
    events=(),
    error_type=None,
    error_message=None,
):
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
                completed_at=_utcnow() if status in ("completed", "failed") else None,
                error_type=error_type,
                error_message=error_message,
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


def _two_tool_events(run_id, prefix):
    return [
        _tool_called(run_id, 1, f"evt-{prefix}1", "search", f"{prefix}1"),
        _tool_called(run_id, 2, f"evt-{prefix}2", "search", f"{prefix}2"),
    ]


def _read_run(engine, run_id):
    with Session(engine, expire_on_commit=False) as session:
        return RunRepository(session).get_required(run_id)


def _signal_rows(engine):
    with Session(engine, expire_on_commit=False) as session:
        return list(session.scalars(select(PersistedSignal)))


def test_complete_run_transitions_status_and_persists_monitoring_signals(
    engine,
    session_factory,
):
    _seed_run(engine, "run-1", events=_two_tool_events("run-1", "t"))

    result = _service(session_factory).complete_run("run-1")

    assert result.status == "completed"
    assert result.monitoring == "completed"
    assert result.monitoring_result is not None
    assert result.monitoring_result.run_id == "run-1"
    assert len(result.monitoring_result.signals) == 1

    run = _read_run(engine, "run-1")
    assert run.status == "completed"
    assert run.completed_at is not None
    assert run.monitoring_completed_at is not None
    assert run.monitoring_claim_id is None
    assert run.monitoring_claim_expires_at is None
    assert run.error_type is None
    assert run.error_message is None

    rows = _signal_rows(engine)
    assert len(rows) == 1
    assert rows[0].run_id == "run-1"
    assert rows[0].agent_id == "agent-1"
    assert rows[0].detector_name == "pattern"


def test_generated_signals_are_retrievable(engine, session_factory):
    _seed_run(engine, "run-1", events=_two_tool_events("run-1", "t"))

    result = _service(session_factory).complete_run("run-1")
    expected = result.monitoring_result.signals[0]

    with Session(engine, expire_on_commit=False) as session:
        repository = PersistedSignalRepository(session)
        stored = repository.get(expected.signal_id)
        for_run = repository.list_for_run("run-1")
        for_agent = repository.list_for_agent("agent-1")

    assert stored is not None
    assert stored.signal_id == expected.signal_id
    assert stored.signal_type == expected.signal_type.value
    assert stored.scope == expected.scope.value
    assert stored.payload_type is not None
    assert stored.evaluation_run_id is None
    assert [row.signal_id for row in for_run] == [expected.signal_id]
    assert [row.signal_id for row in for_agent] == [expected.signal_id]
    assert for_agent[0].run_id == "run-1"


def test_failed_execution_marks_run_failed_and_skips_monitoring(
    engine,
    session_factory,
):
    _seed_run(engine, "run-1")

    result = _service(session_factory).fail_run(
        "run-1",
        error_type="TimeoutError",
        error_message="agent timed out",
    )

    assert result.status == "failed"
    assert result.monitoring == "skipped"
    assert result.monitoring_result is None

    run = _read_run(engine, "run-1")
    assert run.status == "failed"
    assert run.completed_at is not None
    assert run.error_type == "TimeoutError"
    assert run.error_message == "agent timed out"
    assert run.monitoring_completed_at is None
    assert _signal_rows(engine) == []


def test_complete_run_refuses_failed_run(engine, session_factory):
    _seed_run(engine, "run-1", status="failed", error_type="TimeoutError")

    with pytest.raises(RunStateTransitionError):
        _service(session_factory).complete_run("run-1")

    run = _read_run(engine, "run-1")
    assert run.status == "failed"
    assert run.monitoring_completed_at is None
    assert _signal_rows(engine) == []


def test_fail_run_refuses_completed_run(engine, session_factory):
    _seed_run(engine, "run-1", status="completed")

    with pytest.raises(RunStateTransitionError):
        _service(session_factory).fail_run("run-1", error_type="ValueError")

    assert _read_run(engine, "run-1").status == "completed"


def test_fail_run_is_idempotent(engine, session_factory):
    _seed_run(engine, "run-1")

    service = _service(session_factory)
    first = service.fail_run("run-1", error_type="TimeoutError", error_message="first")
    second = service.fail_run("run-1", error_type="ValueError", error_message="second")

    assert first.status == "failed"
    assert first.monitoring == "skipped"
    assert second.status == "failed"
    assert second.monitoring == "skipped"

    run = _read_run(engine, "run-1")
    assert run.error_type == "TimeoutError"
    assert run.error_message == "first"


def test_monitoring_failure_propagates_and_run_stays_completed(
    engine,
    session_factory,
):
    _seed_run(engine, "run-1", events=_two_tool_events("run-1", "t"))

    original = PersistedSignalRepository.insert_ignore_duplicates

    def failing(self, rows):
        raise RuntimeError("persistence failure")

    PersistedSignalRepository.insert_ignore_duplicates = failing

    try:
        with pytest.raises(RuntimeError, match="persistence failure"):
            _service(session_factory).complete_run("run-1")
    finally:
        PersistedSignalRepository.insert_ignore_duplicates = original

    run = _read_run(engine, "run-1")
    assert run.status == "completed"
    assert run.completed_at is not None
    assert run.monitoring_completed_at is None
    assert _signal_rows(engine) == []

    result = _service(session_factory).complete_run("run-1")

    assert result.monitoring == "completed"
    run = _read_run(engine, "run-1")
    assert run.monitoring_completed_at is not None
    assert len(_signal_rows(engine)) == 1


def test_retry_does_not_duplicate_monitoring_or_signals(engine, session_factory):
    _seed_run(engine, "run-1", events=_two_tool_events("run-1", "t"))

    original = run_lifecycle.monitor_completed_run
    calls = []

    def spy(run_id, *, session_factory, extra_assignments=()):
        calls.append(run_id)
        return original(
            run_id,
            session_factory=session_factory,
            extra_assignments=extra_assignments,
        )

    run_lifecycle.monitor_completed_run = spy

    try:
        service = _service(session_factory)
        first = service.complete_run("run-1")
        second = service.complete_run("run-1")
    finally:
        run_lifecycle.monitor_completed_run = original

    assert first.monitoring == "completed"
    assert second.monitoring == "skipped"
    assert second.monitoring_result is None
    assert calls == ["run-1"]
    assert len(_signal_rows(engine)) == 1


def test_retry_with_zero_signals_is_still_skipped(engine, session_factory):
    _seed_run(engine, "run-quiet")

    service = _service(session_factory)
    first = service.complete_run("run-quiet")
    second = service.complete_run("run-quiet")

    assert first.monitoring == "completed"
    assert _signal_rows(engine) == []
    assert second.monitoring == "skipped"
    quiet_run = _read_run(engine, "run-quiet")
    assert quiet_run.monitoring_completed_at is not None
    assert quiet_run.monitoring_claim_id is None
    assert quiet_run.monitoring_claim_expires_at is None


def test_status_write_failure_rolls_back_and_skips_monitoring(
    engine,
    session_factory,
):
    _seed_run(engine, "run-1", events=_two_tool_events("run-1", "t"))

    original_update = RunRepository.update_status
    original_monitor = run_lifecycle.monitor_completed_run
    monitored = []

    def failing_update(
        self,
        run_id,
        status,
        completed_at=None,
        error_type=None,
        error_message=None,
    ):
        raise RuntimeError("status write failure")

    def monitor_spy(run_id, *, session_factory, extra_assignments=()):
        monitored.append(run_id)

    RunRepository.update_status = failing_update
    run_lifecycle.monitor_completed_run = monitor_spy

    try:
        with pytest.raises(RuntimeError, match="status write failure"):
            _service(session_factory).complete_run("run-1")
    finally:
        RunRepository.update_status = original_update
        run_lifecycle.monitor_completed_run = original_monitor

    run = _read_run(engine, "run-1")
    assert run.status == "running"
    assert run.completed_at is None
    assert run.monitoring_completed_at is None
    assert run.monitoring_claim_id is None
    assert run.monitoring_claim_expires_at is None
    assert monitored == []
    assert _signal_rows(engine) == []


def test_missing_run_raises_entity_not_found(engine, session_factory):
    service = _service(session_factory)

    with pytest.raises(EntityNotFoundError):
        service.complete_run("run-absent")

    with pytest.raises(EntityNotFoundError):
        service.fail_run("run-absent", error_type="TimeoutError")


def test_no_transaction_open_during_detection(engine, session_factory):
    _seed_run(engine, "run-1", events=_two_tool_events("run-1", "t"))

    original = monitoring_integration.run_monitoring
    observed = []

    def spy(agent_id, assignments):
        observed.append(session_factory.open_sessions)
        return original(agent_id, assignments)

    monitoring_integration.run_monitoring = spy

    try:
        _service(session_factory).complete_run("run-1")
    finally:
        monitoring_integration.run_monitoring = original

    assert observed == [0]


def test_agent_isolation_between_concurrent_runs(engine, session_factory):
    _seed_run(engine, "run-a", agent_id="agent-1", events=_two_tool_events("run-a", "a"))
    _seed_run(engine, "run-b", agent_id="agent-2", events=_two_tool_events("run-b", "b"))

    service = _service(session_factory)
    service.complete_run("run-a")
    service.complete_run("run-b")

    with Session(engine, expire_on_commit=False) as session:
        repository = PersistedSignalRepository(session)
        agent_one = repository.list_for_agent("agent-1")
        agent_two = repository.list_for_agent("agent-2")
        run_a = repository.list_for_run("run-a")
        run_b = repository.list_for_run("run-b")

    assert len(agent_one) == 1
    assert len(agent_two) == 1
    assert {row.agent_id for row in agent_one} == {"agent-1"}
    assert {row.run_id for row in agent_one} == {"run-a"}
    assert {row.agent_id for row in agent_two} == {"agent-2"}
    assert {row.run_id for row in agent_two} == {"run-b"}
    assert {row.run_id for row in run_a} == {"run-a"}
    assert {row.run_id for row in run_b} == {"run-b"}
    assert run_a[0].signal_id != run_b[0].signal_id
