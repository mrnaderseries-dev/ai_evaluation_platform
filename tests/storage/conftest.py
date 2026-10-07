from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from ai_eval_platform.storage.models import (
    Agent,
    AgentVersion,
    Base,
    Dataset,
    DatasetVersion,
    EvaluationCase,
    EvaluationRun,
    Run,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture()
def engine():
    """In-memory SQLite engine with all tables created."""
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
def session(engine):
    with Session(engine, expire_on_commit=False, autoflush=False) as db:
        yield db


@pytest.fixture()
def seeded_run(session) -> str:
    """Seed Agent -> AgentVersion -> Run and return the run id."""
    session.add(Agent(agent_id="agent-1", name="agent-1"))
    session.add(
        AgentVersion(agent_version_id="av-1", agent_id="agent-1", version="1.0.0")
    )
    session.add(
        Run(
            run_id="run-1",
            agent_id="agent-1",
            agent_version_id="av-1",
            started_at=utcnow(),
            status="running",
        )
    )
    session.flush()

    return "run-1"


@pytest.fixture()
def seeded_evaluation_run(session) -> str:
    """Seed the dataset/agent graph and return an evaluation run id."""
    session.add(Agent(agent_id="agent-1", name="agent-1"))
    session.add(
        AgentVersion(agent_version_id="av-1", agent_id="agent-1", version="1.0.0")
    )
    session.add(Dataset(dataset_id="ds-1", name="ds-1"))
    session.add(
        DatasetVersion(
            dataset_version_id="dsv-1",
            dataset_id="ds-1",
            version="1.0.0",
        )
    )
    session.add(
        EvaluationCase(
            case_id="case-1",
            dataset_version_id="dsv-1",
            question="What is 2+2?",
        )
    )
    session.add(
        EvaluationRun(
            evaluation_run_id="erun-1",
            agent_version_id="av-1",
            dataset_version_id="dsv-1",
            started_at=utcnow(),
            status="running",
        )
    )
    session.add(
        Run(
            run_id="run-1",
            agent_id="agent-1",
            agent_version_id="av-1",
            started_at=utcnow(),
            status="running",
        )
    )
    session.add(
        Run(
            run_id="run-2",
            agent_id="agent-1",
            agent_version_id="av-1",
            started_at=utcnow(),
            status="running",
        )
    )
    session.add(
        Run(
            run_id="run-3",
            agent_id="agent-1",
            agent_version_id="av-1",
            started_at=utcnow(),
            status="running",
        )
    )
    session.flush()

    return "erun-1"
