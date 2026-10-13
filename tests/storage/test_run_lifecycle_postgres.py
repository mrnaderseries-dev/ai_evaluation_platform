from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip(
    "psycopg2",
    reason="psycopg2 is required for PostgreSQL integration tests",
)

from sqlalchemy import create_engine, func, insert, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ai_eval_platform import run_lifecycle
from ai_eval_platform.run_lifecycle import RunLifecycleService
from ai_eval_platform.storage.models import (
    Agent,
    Base,
    Event,
    PersistedSignal,
    Run,
)
from ai_eval_platform.storage.repositories import PersistedSignalRepository, RunRepository
from schemas.events.common import EventType

DATABASE_URL = os.getenv("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not configured; PostgreSQL lifecycle hardening tests skipped",
)

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "0001_run_lifecycle_hardening.sql"
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture(scope="module")
def pg_engine():
    engine = create_engine(DATABASE_URL, future=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    try:
        yield engine
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture()
def pg_session(pg_engine):
    with Session(pg_engine, expire_on_commit=False, autoflush=False) as db:
        yield db


@pytest.fixture()
def pg_session_factory(pg_engine):
    @contextmanager
    def factory():
        session = Session(
            pg_engine,
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


def _seed_run(engine, run_id, *, agent_id="agent-pg", events=()):
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
                status="running",
                started_at=_utcnow(),
            )
        )

        for event in events:
            session.add(Event(**event))

        session.commit()


def _signal_row(signal_id: str, run_id: str, dedupe_key: str | None) -> PersistedSignal:
    return PersistedSignal(
        signal_id=signal_id,
        agent_id="agent-pg",
        signal_type="repeated_tool_usage",
        scope="run",
        run_id=run_id,
        detected_at=_utcnow(),
        payload={"tool_name": "search"},
        payload_type="RepeatedToolUsagePayload",
        detector_name="pattern",
        dedupe_key=dedupe_key,
    )


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


def _migration_statements() -> list[str]:
    raw = MIGRATION_PATH.read_text(encoding="utf-8")
    return [statement.strip() for statement in raw.split(";") if statement.strip()]


def test_claim_conditional_update_allows_single_winner(pg_engine, pg_session):
    _seed_run(pg_engine, "run-claim")

    repository = RunRepository(pg_session)
    now = _utcnow()
    assert repository.claim_monitoring(
        "run-claim",
        "claim-a",
        claimed_at=now,
        expires_at=now + timedelta(minutes=5),
    )

    outcome = {}

    def contender():
        try:
            with Session(
                pg_engine,
                expire_on_commit=False,
                autoflush=False,
            ) as session:
                outcome["result"] = RunRepository(session).claim_monitoring(
                    "run-claim",
                    "claim-b",
                    claimed_at=_utcnow(),
                    expires_at=_utcnow() + timedelta(minutes=5),
                )
                session.commit()
        except Exception as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=contender)
    thread.start()
    thread.join(timeout=1.0)
    pg_session.commit()
    thread.join(timeout=30)

    assert not thread.is_alive()
    assert "error" not in outcome, outcome.get("error")
    assert outcome["result"] is False


def test_expired_claim_is_reacquirable(pg_engine, pg_session):
    _seed_run(pg_engine, "run-expired")

    repository = RunRepository(pg_session)
    assert repository.claim_monitoring(
        "run-expired",
        "claim-dead",
        claimed_at=_utcnow(),
        expires_at=_utcnow() - timedelta(seconds=5),
    )
    assert repository.claim_monitoring(
        "run-expired",
        "claim-live",
        claimed_at=_utcnow(),
        expires_at=_utcnow() + timedelta(minutes=5),
    )
    pg_session.commit()


def test_dedupe_key_uniqueness_and_conflict_skip(pg_engine, pg_session):
    _seed_run(pg_engine, "run-dedupe")
    repository = PersistedSignalRepository(pg_session)

    assert (
        repository.insert_ignore_duplicates(
            [_signal_row("sig-pg-a", "run-dedupe", "pg-key-1")]
        )
        == 1
    )
    assert (
        repository.insert_ignore_duplicates(
            [_signal_row("sig-pg-b", "run-dedupe", "pg-key-1")]
        )
        == 0
    )
    assert (
        repository.insert_ignore_duplicates(
            [_signal_row("sig-pg-a", "run-dedupe", "pg-key-2")]
        )
        == 0
    )
    assert pg_session.scalar(select(func.count()).select_from(PersistedSignal)) == 1

    with pytest.raises(IntegrityError):
        pg_session.execute(
            insert(PersistedSignal).values(
                signal_id="sig-pg-c",
                agent_id="agent-pg",
                signal_type="repeated_tool_usage",
                scope="run",
                run_id="run-dedupe",
                detected_at=_utcnow(),
                payload={},
                payload_type="RepeatedToolUsagePayload",
                dedupe_key="pg-key-1",
            )
        )

    pg_session.rollback()


def test_concurrent_complete_run_single_monitoring_on_postgres(
    pg_engine,
    pg_session_factory,
):
    _seed_run(pg_engine, "run-cc", events=_two_tool_events("run-cc", "t"))

    original = run_lifecycle.monitor_completed_run
    calls = []
    calls_lock = threading.Lock()

    def spy(run_id, *, session_factory, extra_assignments=()):
        with calls_lock:
            calls.append(run_id)

        return original(
            run_id,
            session_factory=session_factory,
            extra_assignments=extra_assignments,
        )

    run_lifecycle.monitor_completed_run = spy

    barrier = threading.Barrier(2)
    results = []
    errors = []

    def target():
        try:
            barrier.wait(timeout=10)
            service = RunLifecycleService(session_factory=pg_session_factory)
            results.append(service.complete_run("run-cc"))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=target) for _ in range(2)]

    try:
        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join(timeout=60)
    finally:
        run_lifecycle.monitor_completed_run = original

    assert not any(thread.is_alive() for thread in threads)
    assert errors == []

    completed = [r for r in results if r.monitoring == "completed"]
    deferred = [r for r in results if r.monitoring in ("skipped", "contended")]

    assert len(completed) == 1
    assert len(deferred) == 1
    assert calls == ["run-cc"]

    with Session(pg_engine, expire_on_commit=False) as session:
        assert session.scalar(select(func.count()).select_from(PersistedSignal)) == 1
        run = session.get(Run, "run-cc")

    assert run.status == "completed"
    assert run.monitoring_completed_at is not None
    assert run.monitoring_claim_id is None
    assert run.monitoring_claim_expires_at is None


def test_migration_script_applies_to_postgres_schema(pg_engine):
    with pg_engine.begin() as conn:
        conn.exec_driver_sql("DROP INDEX uq_persisted_signals_dedupe_key")
        conn.exec_driver_sql("ALTER TABLE persisted_signals DROP COLUMN dedupe_key")
        conn.exec_driver_sql("ALTER TABLE runs DROP COLUMN monitoring_claim_id")
        conn.exec_driver_sql(
            "ALTER TABLE runs DROP COLUMN monitoring_claim_expires_at"
        )
        conn.exec_driver_sql("ALTER TABLE runs DROP COLUMN monitoring_completed_at")

    with pg_engine.begin() as conn:
        for statement in _migration_statements():
            conn.exec_driver_sql(statement)

    inspector = inspect(pg_engine)
    runs_columns = {column["name"] for column in inspector.get_columns("runs")}
    signal_columns = {
        column["name"] for column in inspector.get_columns("persisted_signals")
    }
    indexes = {index["name"] for index in inspector.get_indexes("persisted_signals")}

    assert {
        "monitoring_completed_at",
        "monitoring_claim_id",
        "monitoring_claim_expires_at",
    } <= runs_columns
    assert {"dedupe_key"} <= signal_columns
    assert "uq_persisted_signals_dedupe_key" in indexes

    _seed_run(pg_engine, "run-mig-pg")

    with Session(
        pg_engine,
        expire_on_commit=False,
        autoflush=False,
    ) as session:
        repository = PersistedSignalRepository(session)

        assert (
            repository.insert_ignore_duplicates(
                [_signal_row("sig-mig-1", "run-mig-pg", "mig-key")]
            )
            == 1
        )
        assert (
            repository.insert_ignore_duplicates(
                [_signal_row("sig-mig-2", "run-mig-pg", "mig-key")]
            )
            == 0
        )
        session.commit()


