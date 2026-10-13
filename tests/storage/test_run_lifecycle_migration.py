from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ai_eval_platform.run_lifecycle import RunLifecycleService
from ai_eval_platform.storage.models import Agent, Run

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "0001_run_lifecycle_hardening.sql"
)

RUNS_NEW_COLUMNS = {
    "monitoring_completed_at",
    "monitoring_claim_id",
    "monitoring_claim_expires_at",
}
SIGNAL_NEW_COLUMNS = {"dedupe_key"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _runs_columns(engine) -> set[str]:
    return {column["name"] for column in inspect(engine).get_columns("runs")}


def _signal_columns(engine) -> set[str]:
    return {
        column["name"] for column in inspect(engine).get_columns("persisted_signals")
    }


def _signal_indexes(engine) -> set[str]:
    return {index["name"] for index in inspect(engine).get_indexes("persisted_signals")}


def _drop_hardening_schema(engine) -> None:
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP INDEX uq_persisted_signals_dedupe_key")
        conn.exec_driver_sql("ALTER TABLE persisted_signals DROP COLUMN dedupe_key")
        conn.exec_driver_sql("ALTER TABLE runs DROP COLUMN monitoring_claim_id")
        conn.exec_driver_sql(
            "ALTER TABLE runs DROP COLUMN monitoring_claim_expires_at"
        )
        conn.exec_driver_sql("ALTER TABLE runs DROP COLUMN monitoring_completed_at")


def _migration_statements() -> list[str]:
    raw = MIGRATION_PATH.read_text(encoding="utf-8")
    return [statement.strip() for statement in raw.split(";") if statement.strip()]


def _apply_migration(engine) -> None:
    with engine.begin() as conn:
        for statement in _migration_statements():
            conn.exec_driver_sql(statement)


def _session_factory(engine):
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


def test_fresh_schema_contains_hardening_columns(engine):
    assert RUNS_NEW_COLUMNS <= _runs_columns(engine)
    assert SIGNAL_NEW_COLUMNS <= _signal_columns(engine)
    assert "uq_persisted_signals_dedupe_key" in _signal_indexes(engine)


def test_migration_script_declares_expected_statements():
    statements = _migration_statements()

    assert len(statements) == 5
    assert any(
        "ALTER TABLE runs ADD COLUMN monitoring_completed_at" in s for s in statements
    )
    assert any(
        "ALTER TABLE runs ADD COLUMN monitoring_claim_id" in s for s in statements
    )
    assert any(
        "ALTER TABLE runs ADD COLUMN monitoring_claim_expires_at" in s
        for s in statements
    )
    assert any(
        "ALTER TABLE persisted_signals ADD COLUMN dedupe_key" in s
        for s in statements
    )
    assert any(
        "CREATE UNIQUE INDEX uq_persisted_signals_dedupe_key" in s
        for s in statements
    )


def test_migration_upgrades_existing_schema(engine):
    _drop_hardening_schema(engine)

    assert not RUNS_NEW_COLUMNS & _runs_columns(engine)
    assert not SIGNAL_NEW_COLUMNS & _signal_columns(engine)
    assert "uq_persisted_signals_dedupe_key" not in _signal_indexes(engine)

    _apply_migration(engine)

    assert RUNS_NEW_COLUMNS <= _runs_columns(engine)
    assert SIGNAL_NEW_COLUMNS <= _signal_columns(engine)
    assert "uq_persisted_signals_dedupe_key" in _signal_indexes(engine)

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO agents (agent_id, name, status, metadata, created_at, updated_at) "
                "VALUES ('agent-m', 'agent-m', 'active', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO runs (run_id, agent_id, status, started_at, metadata, created_at, updated_at) "
                "VALUES ('run-m', 'agent-m', 'completed', CURRENT_TIMESTAMP, '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO persisted_signals (signal_id, agent_id, signal_type, scope, run_id, detected_at, payload, payload_type, dedupe_key) "
                "VALUES ('sig-m1', 'agent-m', 'repeated_tool_usage', 'run', 'run-m', CURRENT_TIMESTAMP, '{}', 'RepeatedToolUsagePayload', 'dedupe-m')"
            )
        )

    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO persisted_signals (signal_id, agent_id, signal_type, scope, run_id, detected_at, payload, payload_type, dedupe_key) "
                    "VALUES ('sig-m2', 'agent-m', 'repeated_tool_usage', 'run', 'run-m', CURRENT_TIMESTAMP, '{}', 'RepeatedToolUsagePayload', 'dedupe-m')"
                )
            )


def test_lifecycle_behaves_on_migrated_schema(engine):
    _drop_hardening_schema(engine)
    _apply_migration(engine)

    with Session(
        engine,
        expire_on_commit=False,
        autoflush=False,
    ) as session:
        session.add(Agent(agent_id="agent-mig", name="agent-mig"))
        session.add(
            Run(
                run_id="run-mig",
                agent_id="agent-mig",
                status="running",
                started_at=_utcnow(),
            )
        )
        session.commit()

    service = RunLifecycleService(session_factory=_session_factory(engine))

    first = service.complete_run("run-mig")
    second = service.complete_run("run-mig")

    assert first.monitoring == "completed"
    assert second.monitoring == "skipped"

    with Session(engine, expire_on_commit=False) as session:
        run = session.get(Run, "run-mig")

    assert run.status == "completed"
    assert run.monitoring_completed_at is not None
    assert run.monitoring_claim_id is None
    assert run.monitoring_claim_expires_at is None

