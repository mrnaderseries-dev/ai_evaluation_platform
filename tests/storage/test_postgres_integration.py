"""
PostgreSQL integration tests.

These tests exercise the real PostgreSQL dialect (DDL, ON CONFLICT, upsert,
transaction rollback) against the database named by ``DATABASE_URL``. They
are skipped automatically when psycopg2 or DATABASE_URL is unavailable, so
the default SQLite-based suite remains dependency-free.

Run locally with, for example:

    createdb ai_eval_storage_test
    DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/ai_eval_storage_test
        python -m pytest tests/storage/test_postgres_integration.py -q

The target database is schema-managed by these tests (drop_all/create_all):
point them at a SCRATCH database only.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip(
    "psycopg2",
    reason="psycopg2 is required for PostgreSQL integration tests",
)

from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ai_eval_platform.storage.models import (
    Agent,
    AgentVersion,
    Base,
    ContextEvent,
    ContextResource,
    Event,
    EvaluationResult,
    PersistedSignal,
    Run,
    utc_now,
)
from ai_eval_platform.storage.repositories import (
    ContextEventRepository,
    EventRepository,
    EvaluationResultRepository,
    PersistedSignalRepository,
)

DATABASE_URL = os.getenv("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not configured; PostgreSQL integration tests skipped",
)


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
def pg_run(pg_session):
    pg_session.add(Agent(agent_id="agent-pg", name="agent-pg"))
    pg_session.add(
        AgentVersion(agent_version_id="av-pg", agent_id="agent-pg", version="1.0.0")
    )
    pg_session.add(
        Run(
            run_id="run-pg",
            agent_id="agent-pg",
            agent_version_id="av-pg",
            started_at=utc_now(),
            status="running",
        )
    )
    pg_session.flush()
    return "run-pg"


def _event(event_id: str, sequence_number: int = 1) -> Event:
    return Event(
        event_id=event_id,
        run_id="run-pg",
        sequence_number=sequence_number,
        event_type="model_called",
        schema_version="1.0",
        occurred_at=utc_now(),
        payload={"model": "qwen"},
    )


def test_create_all_creates_full_schema_on_postgres(pg_engine):
    tables = set(inspect(pg_engine).get_table_names())
    assert "events" in tables
    assert "persisted_signals" in tables
    assert "context_resources" in tables
    assert "policy_versions" in tables
    assert len(tables) == 31

    # The renamed metadata column exists with its DB column name.
    agent_columns = {c["name"] for c in inspect(pg_engine).get_columns("agents")}
    assert "metadata" in agent_columns


def test_idempotent_event_ingestion_on_postgres(pg_session, pg_run):
    repo = EventRepository(pg_session)

    assert repo.insert_ignore_duplicates([_event("pg-evt-1")]) == 1
    # redelivery: same event_id (also same run/sequence) -> no-op, no error
    assert repo.insert_ignore_duplicates([_event("pg-evt-1")]) == 0
    # same-batch duplicate (fresh sequence) -> exactly one row
    assert (
        repo.insert_ignore_duplicates(
            [_event("pg-batch", sequence_number=2), _event("pg-batch", sequence_number=2)]
        )
        == 1
    )

    total = pg_session.scalar(select(func.count()).select_from(Event))
    assert total == 2

    # a distinct event_id reusing (run_id, sequence_number) must raise
    with pytest.raises(IntegrityError):
        repo.insert_ignore_duplicates([_event("pg-evt-2", sequence_number=1)])

    pg_session.rollback()

    assert pg_session.scalar(select(func.count()).select_from(Event)) == 0


def test_evaluation_result_upsert_on_postgres(pg_session, pg_run):
    from ai_eval_platform.storage.models import (
        Dataset,
        DatasetVersion,
        EvaluationCase,
        EvaluationRun,
    )

    pg_session.add(Dataset(dataset_id="ds-pg", name="ds-pg"))
    pg_session.add(
        DatasetVersion(dataset_version_id="dsv-pg", dataset_id="ds-pg", version="1")
    )
    pg_session.add(
        EvaluationCase(case_id="case-pg", dataset_version_id="dsv-pg", question="q")
    )
    pg_session.add(
        EvaluationRun(
            evaluation_run_id="erun-pg",
            dataset_version_id="dsv-pg",
            agent_version_id="av-pg",
            status="running",
        )
    )
    pg_session.flush()

    repo = EvaluationResultRepository(pg_session)
    repo.upsert(
        EvaluationResult(
            evaluation_result_id="res-pg-1",
            evaluation_run_id="erun-pg",
            run_id="run-pg",
            case_id="case-pg",
            metric_name="correctness",
            score=0.4,
            passed=False,
        )
    )
    repo.upsert(
        EvaluationResult(
            evaluation_result_id="res-pg-2",
            evaluation_run_id="erun-pg",
            run_id="run-pg",
            case_id="case-pg",
            metric_name="correctness",
            score=0.9,
            passed=True,
        )
    )

    rows = repo.list_for_run("erun-pg")
    assert len(rows) == 1
    assert rows[0].score == 0.9
    # On conflict the original primary key is preserved.
    assert rows[0].evaluation_result_id == "res-pg-1"

    pg_session.rollback()


def test_signal_idempotency_and_foreign_keys_on_postgres(pg_session, pg_run):
    repo = PersistedSignalRepository(pg_session)

    row = PersistedSignal(
        signal_id="pg-sig-1",
        agent_id="agent-pg",
        signal_type="repeated_tool_usage",
        scope="run",
        run_id="run-pg",
        detected_at=utc_now(),
        payload={"tool_name": "search"},
        payload_type="RepeatedToolUsagePayload",
        detector_name="pattern",
    )

    assert repo.insert_ignore_duplicates([row]) == 1
    assert repo.insert_ignore_duplicates([row]) == 0
    assert pg_session.scalar(select(func.count()).select_from(PersistedSignal)) == 1

    stored = repo.get("pg-sig-1")
    assert stored.detector_name == "pattern"
    assert stored.payload == {"tool_name": "search"}

    pg_session.add(
        PersistedSignal(
            signal_id="pg-sig-orphan",
            agent_id="agent-pg",
            signal_type="repeated_tool_usage",
            scope="run",
            run_id="run-absent",
            detected_at=utc_now(),
            payload={},
            payload_type="RepeatedToolUsagePayload",
        )
    )

    with pytest.raises(IntegrityError):
        pg_session.flush()

    pg_session.rollback()

    pg_session.add(
        PersistedSignal(
            signal_id="pg-sig-stray",
            agent_id="agent-absent",
            signal_type="repeated_tool_usage",
            scope="run",
            run_id="run-pg",
            detected_at=utc_now(),
            payload={},
            payload_type="RepeatedToolUsagePayload",
        )
    )

    with pytest.raises(IntegrityError):
        pg_session.flush()

    pg_session.rollback()


def test_typed_event_idempotency_and_rollback_on_postgres(pg_session, pg_run):
    repo = EventRepository(pg_session)

    detail = ContextEvent(context_type="assembled", resource_count=1)
    resource = ContextResource(resource_type="document", resource_id="doc-1", rank=1)
    assert repo.insert_with_detail_ignore_duplicates(_event("pg-typed-1"), detail, [resource]) == 1

    replay_detail = ContextEvent(context_type="assembled", resource_count=1)
    replay_resource = ContextResource(
        resource_type="document", resource_id="doc-1", rank=1
    )
    assert (
        repo.insert_with_detail_ignore_duplicates(
            _event("pg-typed-1"), replay_detail, [replay_resource]
        )
        == 0
    )

    assert pg_session.scalar(select(func.count()).select_from(Event)) == 1
    assert pg_session.scalar(select(func.count()).select_from(ContextEvent)) == 1
    assert pg_session.scalar(select(func.count()).select_from(ContextResource)) == 1

    # repositories only flush: rollback removes everything atomically
    pg_session.rollback()

    assert pg_session.scalar(select(func.count()).select_from(Event)) == 0
    assert pg_session.scalar(select(func.count()).select_from(ContextEvent)) == 0
    assert pg_session.scalar(select(func.count()).select_from(ContextResource)) == 0


def test_context_resource_retrieval_order_on_postgres(pg_session, pg_run):
    repo = EventRepository(pg_session)
    contexts = ContextEventRepository(pg_session)

    detail = ContextEvent(context_type="assembled")
    resources = [
        ContextResource(resource_type="document", resource_id="doc-2", rank=2),
        ContextResource(resource_type="document", resource_id="doc-1", rank=1),
        ContextResource(resource_type="memory", resource_id="mem-1"),  # unranked
    ]
    repo.create_with_context_detail(_event("pg-ctx-1"), detail, resources)

    # ranked first (rank asc), unranked last - same as on SQLite
    assert [r.resource_id for r in contexts.list_resources("pg-ctx-1")] == [
        "doc-1",
        "doc-2",
        "mem-1",
    ]

    pg_session.rollback()