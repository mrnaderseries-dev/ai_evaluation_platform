from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session

from ai_eval_platform import run_lifecycle
from ai_eval_platform.run_lifecycle import (
    MonitoringClaimLostError,
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
def engine(tmp_path):
    eng = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'run_lifecycle.db'}",
        future=True,
        connect_args={"check_same_thread": False, "timeout": 30},
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


def _service(session_factory, *, clock=None, monitoring_lease=None):
    kwargs = {"session_factory": session_factory}

    if clock is not None:
        kwargs["clock"] = clock

    if monitoring_lease is not None:
        kwargs["monitoring_lease"] = monitoring_lease

    return RunLifecycleService(**kwargs)


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


def _set_claim(engine, run_id, claim_id, expires_at):
    with Session(
        engine,
        expire_on_commit=False,
        autoflush=False,
    ) as session:
        run = RunRepository(session).get_required(run_id)
        run.monitoring_claim_id = claim_id
        run.monitoring_claim_expires_at = expires_at
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


def _run_concurrently(targets):
    barrier = threading.Barrier(len(targets))
    results = []
    errors = []

    def runner(target):
        try:
            barrier.wait(timeout=10)
            results.append(target())
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=runner, args=(target,)) for target in targets]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join(timeout=30)

    assert not any(thread.is_alive() for thread in threads)

    return results, errors


class _MonitorSpy:

    def __init__(self):
        self.calls = []
        self._original = run_lifecycle.monitor_completed_run

    def __enter__(self):
        def spy(run_id, *, session_factory, extra_assignments=()):
            self.calls.append(run_id)
            return self._original(
                run_id,
                session_factory=session_factory,
                extra_assignments=extra_assignments,
            )

        run_lifecycle.monitor_completed_run = spy
        return self

    def __exit__(self, exc_type, exc, tb):
        run_lifecycle.monitor_completed_run = self._original
        return False


def test_concurrent_complete_run_same_run_executes_monitoring_once(
    engine,
    session_factory,
):
    _seed_run(engine, "run-1", events=_two_tool_events("run-1", "t"))

    with _MonitorSpy() as spy:
        results, errors = _run_concurrently(
            [
                lambda: _service(session_factory).complete_run("run-1"),
                lambda: _service(session_factory).complete_run("run-1"),
            ]
        )

    assert errors == []
    assert len(results) == 2

    completed = [r for r in results if r.monitoring == "completed"]
    deferred = [r for r in results if r.monitoring in ("skipped", "contended")]

    assert len(completed) == 1
    assert len(deferred) == 1
    assert spy.calls == ["run-1"]
    assert len(_signal_rows(engine)) == 1

    run = _read_run(engine, "run-1")
    assert run.status == "completed"
    assert run.monitoring_completed_at is not None
    assert run.monitoring_claim_id is None
    assert run.monitoring_claim_expires_at is None


def test_concurrent_complete_run_different_runs_both_monitor(
    engine,
    session_factory,
):
    _seed_run(engine, "run-a", agent_id="agent-1", events=_two_tool_events("run-a", "a"))
    _seed_run(engine, "run-b", agent_id="agent-2", events=_two_tool_events("run-b", "b"))

    with _MonitorSpy() as spy:
        results, errors = _run_concurrently(
            [
                lambda: _service(session_factory).complete_run("run-a"),
                lambda: _service(session_factory).complete_run("run-b"),
            ]
        )

    assert errors == []
    assert len(results) == 2
    assert [r.monitoring for r in results] == ["completed", "completed"]
    assert sorted(spy.calls) == ["run-a", "run-b"]

    run_a = _read_run(engine, "run-a")
    run_b = _read_run(engine, "run-b")
    assert run_a.monitoring_completed_at is not None
    assert run_b.monitoring_completed_at is not None
    assert run_a.monitoring_claim_id is None
    assert run_b.monitoring_claim_id is None

    rows_a = [row for row in _signal_rows(engine) if row.run_id == "run-a"]
    rows_b = [row for row in _signal_rows(engine) if row.run_id == "run-b"]
    assert len(rows_a) == 1
    assert len(rows_b) == 1
    assert rows_a[0].agent_id == "agent-1"
    assert rows_b[0].agent_id == "agent-2"


def test_active_foreign_claim_returns_contended_without_monitoring(
    engine,
    session_factory,
):
    _seed_run(engine, "run-1", status="completed", events=_two_tool_events("run-1", "t"))
    _set_claim(engine, "run-1", "foreign-claim", _utcnow() + timedelta(minutes=10))

    with _MonitorSpy() as spy:
        result = _service(session_factory).complete_run("run-1")

    assert result.monitoring == "contended"
    assert result.monitoring_result is None
    assert spy.calls == []
    assert _signal_rows(engine) == []

    run = _read_run(engine, "run-1")
    assert run.status == "completed"
    assert run.monitoring_completed_at is None
    assert run.monitoring_claim_id == "foreign-claim"
    assert run.monitoring_claim_expires_at is not None


def test_expired_foreign_claim_is_recovered(engine, session_factory):
    _seed_run(engine, "run-1", status="completed", events=_two_tool_events("run-1", "t"))
    _set_claim(engine, "run-1", "dead-claim", _utcnow() - timedelta(seconds=1))

    with _MonitorSpy() as spy:
        result = _service(session_factory).complete_run("run-1")

    assert result.monitoring == "completed"
    assert spy.calls == ["run-1"]
    assert len(_signal_rows(engine)) == 1

    run = _read_run(engine, "run-1")
    assert run.monitoring_completed_at is not None
    assert run.monitoring_claim_id is None
    assert run.monitoring_claim_expires_at is None


def test_contended_attempt_recovers_after_lease_expiry(engine, session_factory):
    _seed_run(engine, "run-1", status="completed", events=_two_tool_events("run-1", "t"))

    current = [_utcnow()]
    _set_claim(engine, "run-1", "foreign-claim", current[0] + timedelta(seconds=30))

    service = _service(
        session_factory,
        clock=lambda: current[0],
        monitoring_lease=timedelta(seconds=30),
    )

    first = service.complete_run("run-1")
    assert first.monitoring == "contended"

    current[0] = current[0] + timedelta(seconds=31)

    second = service.complete_run("run-1")
    assert second.monitoring == "completed"

    run = _read_run(engine, "run-1")
    assert run.monitoring_completed_at is not None
    assert run.monitoring_claim_id is None
    assert len(_signal_rows(engine)) == 1


def test_marker_failure_after_signal_commit_is_retry_safe(
    engine,
    session_factory,
):
    _seed_run(engine, "run-1", events=_two_tool_events("run-1", "t"))

    original_mark = RunRepository.mark_monitoring_completed
    attempts = {"count": 0}

    def flaky_mark(self, run_id, monitoring_completed_at, claim_id):
        attempts["count"] += 1

        if attempts["count"] == 1:
            raise RuntimeError("marker write failure")

        return original_mark(self, run_id, monitoring_completed_at, claim_id)

    RunRepository.mark_monitoring_completed = flaky_mark

    with _MonitorSpy() as spy:
        try:
            with pytest.raises(RuntimeError, match="marker write failure"):
                _service(session_factory).complete_run("run-1")
        finally:
            RunRepository.mark_monitoring_completed = original_mark

        rows_after_first = _signal_rows(engine)
        assert len(rows_after_first) == 1
        assert rows_after_first[0].dedupe_key is not None
        first_signal_id = rows_after_first[0].signal_id

        run = _read_run(engine, "run-1")
        assert run.status == "completed"
        assert run.monitoring_completed_at is None
        assert run.monitoring_claim_id is None
        assert run.monitoring_claim_expires_at is None

        result = _service(session_factory).complete_run("run-1")

        assert result.monitoring == "completed"
        assert spy.calls == ["run-1", "run-1"]

        rows = _signal_rows(engine)
        assert len(rows) == 1
        assert rows[0].signal_id == first_signal_id
        assert rows[0].dedupe_key == rows_after_first[0].dedupe_key
        assert _read_run(engine, "run-1").monitoring_completed_at is not None


def test_failed_monitoring_attempt_releases_claim_for_immediate_retry(
    engine,
    session_factory,
):
    _seed_run(engine, "run-1", events=_two_tool_events("run-1", "t"))

    original_insert = PersistedSignalRepository.insert_ignore_duplicates
    attempts = {"count": 0}

    def flaky_insert(self, rows):
        attempts["count"] += 1

        if attempts["count"] == 1:
            raise RuntimeError("persistence failure")

        return original_insert(self, rows)

    PersistedSignalRepository.insert_ignore_duplicates = flaky_insert

    with _MonitorSpy() as spy:
        try:
            with pytest.raises(RuntimeError, match="persistence failure"):
                _service(session_factory).complete_run("run-1")
        finally:
            PersistedSignalRepository.insert_ignore_duplicates = original_insert

        run = _read_run(engine, "run-1")
        assert run.status == "completed"
        assert run.monitoring_completed_at is None
        assert run.monitoring_claim_id is None
        assert run.monitoring_claim_expires_at is None
        assert _signal_rows(engine) == []

        result = _service(session_factory).complete_run("run-1")

        assert result.monitoring == "completed"
        assert spy.calls == ["run-1", "run-1"]
        assert len(_signal_rows(engine)) == 1
        assert _read_run(engine, "run-1").monitoring_completed_at is not None


def test_lost_claim_during_marker_write_raises_and_stays_retryable(
    engine,
    session_factory,
):
    _seed_run(engine, "run-1", events=_two_tool_events("run-1", "t"))

    original_mark = RunRepository.mark_monitoring_completed

    def losing_mark(self, run_id, monitoring_completed_at, claim_id):
        return False

    RunRepository.mark_monitoring_completed = losing_mark

    with _MonitorSpy() as spy:
        try:
            with pytest.raises(MonitoringClaimLostError):
                _service(session_factory).complete_run("run-1")
        finally:
            RunRepository.mark_monitoring_completed = original_mark

        run = _read_run(engine, "run-1")
        assert run.status == "completed"
        assert run.monitoring_completed_at is None
        assert run.monitoring_claim_id is None
        assert len(_signal_rows(engine)) == 1

        result = _service(session_factory).complete_run("run-1")

        assert result.monitoring == "completed"
        assert spy.calls == ["run-1", "run-1"]
        assert len(_signal_rows(engine)) == 1
        assert _read_run(engine, "run-1").monitoring_completed_at is not None


def test_marker_set_by_other_attempt_returns_skipped(engine, session_factory):
    _seed_run(engine, "run-1", events=_two_tool_events("run-1", "t"))

    original_mark = RunRepository.mark_monitoring_completed

    def stolen_mark(self, run_id, monitoring_completed_at, claim_id):
        with Session(
            engine,
            expire_on_commit=False,
            autoflush=False,
        ) as other:
            run = RunRepository(other).get_required(run_id)
            run.monitoring_completed_at = monitoring_completed_at
            run.monitoring_claim_id = None
            run.monitoring_claim_expires_at = None
            other.commit()

        return False

    RunRepository.mark_monitoring_completed = stolen_mark

    with _MonitorSpy() as spy:
        try:
            result = _service(session_factory).complete_run("run-1")
        finally:
            RunRepository.mark_monitoring_completed = original_mark

    assert result.monitoring == "skipped"
    assert result.monitoring_result is None
    assert spy.calls == ["run-1"]

    run = _read_run(engine, "run-1")
    assert run.monitoring_completed_at is not None
    assert run.monitoring_claim_id is None
    assert len(_signal_rows(engine)) == 1


def test_complete_run_refuses_run_failed_by_concurrent_writer(
    engine,
    session_factory,
):
    _seed_run(engine, "run-1", events=_two_tool_events("run-1", "t"))

    original_get = RunRepository.get_required
    fired = {"done": False}

    def racing_get(self, run_id):
        run = original_get(self, run_id)

        if not fired["done"]:
            fired["done"] = True
            self.session.execute(
                update(Run)
                .where(Run.run_id == run_id)
                .values(status="failed")
                .execution_options(synchronize_session=False)
            )
            self.session.flush()

        return run

    RunRepository.get_required = racing_get

    with _MonitorSpy() as spy:
        try:
            with pytest.raises(RunStateTransitionError):
                _service(session_factory).complete_run("run-1")
        finally:
            RunRepository.get_required = original_get

    assert spy.calls == []

    run = _read_run(engine, "run-1")
    assert run.status == "running"
    assert run.monitoring_claim_id is None
    assert run.monitoring_completed_at is None
    assert _signal_rows(engine) == []


def test_fail_run_refuses_run_completed_by_concurrent_writer(
    engine,
    session_factory,
):
    _seed_run(engine, "run-1")

    original_get = RunRepository.get_required
    fired = {"done": False}

    def racing_get(self, run_id):
        run = original_get(self, run_id)

        if not fired["done"]:
            fired["done"] = True
            self.session.execute(
                update(Run)
                .where(Run.run_id == run_id)
                .values(status="completed")
                .execution_options(synchronize_session=False)
            )
            self.session.flush()

        return run

    RunRepository.get_required = racing_get

    try:
        with pytest.raises(RunStateTransitionError):
            _service(session_factory).fail_run("run-1", error_type="ValueError")
    finally:
        RunRepository.get_required = original_get

    run = _read_run(engine, "run-1")
    assert run.status == "running"
    assert run.error_type is None
    assert run.error_message is None


def test_repository_claim_flushes_without_committing(engine):
    _seed_run(engine, "run-1")

    with Session(
        engine,
        expire_on_commit=False,
        autoflush=False,
    ) as session:
        repository = RunRepository(session)
        claimed = repository.claim_monitoring(
            "run-1",
            "claim-uncommitted",
            claimed_at=_utcnow(),
            expires_at=_utcnow() + timedelta(minutes=5),
        )

        assert claimed is True
        assert session.in_transaction() is True

        with Session(
            engine,
            expire_on_commit=False,
            autoflush=False,
        ) as reader:
            observed = RunRepository(reader).get_required("run-1")
            assert observed.monitoring_claim_id is None

        session.rollback()

    run = _read_run(engine, "run-1")
    assert run.monitoring_claim_id is None
    assert run.monitoring_claim_expires_at is None


def test_claim_is_visible_while_monitoring_executes(engine, session_factory):
    _seed_run(engine, "run-1", status="completed", events=_two_tool_events("run-1", "t"))

    original_monitor = run_lifecycle.monitor_completed_run
    observed = {}

    def spy(run_id, *, session_factory, extra_assignments=()):
        with Session(
            engine,
            expire_on_commit=False,
            autoflush=False,
        ) as reader:
            run = RunRepository(reader).get_required(run_id)
            observed["status"] = run.status
            observed["claim_id"] = run.monitoring_claim_id

        return original_monitor(
            run_id,
            session_factory=session_factory,
            extra_assignments=extra_assignments,
        )

    run_lifecycle.monitor_completed_run = spy

    try:
        result = _service(session_factory).complete_run("run-1")
    finally:
        run_lifecycle.monitor_completed_run = original_monitor

    assert result.monitoring == "completed"
    assert observed["status"] == "completed"
    assert observed["claim_id"] is not None
    assert _read_run(engine, "run-1").monitoring_claim_id is None


def test_missing_run_raises_entity_not_found(engine, session_factory):
    service = _service(session_factory)

    with pytest.raises(EntityNotFoundError):
        service.complete_run("run-absent")

    with pytest.raises(EntityNotFoundError):
        service.fail_run("run-absent", error_type="TimeoutError")




