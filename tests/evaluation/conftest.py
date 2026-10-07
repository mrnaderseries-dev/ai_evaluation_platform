from __future__ import annotations

from contextlib import contextmanager
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
    Event,
    Run,
)
from schemas.events.common import EventType


def utcnow() -> datetime:
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


@pytest.fixture()
def seeded(engine):
    with Session(
        engine,
        expire_on_commit=False,
        autoflush=False,
    ) as session:
        session.add(Agent(agent_id="agent-1", name="agent-1"))
        session.add(
            AgentVersion(
                agent_version_id="av-1",
                agent_id="agent-1",
                version="1.0.0",
            )
        )
        session.add(Dataset(dataset_id="ds-1", name="ds-1"))
        session.add(Dataset(dataset_id="ds-2", name="ds-2"))
        session.add(
            DatasetVersion(
                dataset_version_id="dsv-1",
                dataset_id="ds-1",
                version="1.0.0",
            )
        )
        session.add(
            DatasetVersion(
                dataset_version_id="dsv-2",
                dataset_id="ds-2",
                version="1.0.0",
            )
        )
        session.add(
            EvaluationCase(
                case_id="case-1",
                dataset_version_id="dsv-1",
                question="What is the capital of France?",
                expected_answer="The capital of France is Paris.",
            )
        )
        session.add(
            EvaluationCase(
                case_id="case-2",
                dataset_version_id="dsv-1",
                question="What is the capital of Germany?",
                expected_answer="The capital of Germany is Berlin.",
            )
        )
        session.add(
            EvaluationCase(
                case_id="case-other",
                dataset_version_id="dsv-2",
                question="What is the capital of Spain?",
                expected_answer="The capital of Spain is Madrid.",
            )
        )
        session.add(
            EvaluationRun(
                evaluation_run_id="erun-bench",
                agent_version_id="av-1",
                dataset_version_id="dsv-1",
                status="pending",
            )
        )
        session.add(
            EvaluationRun(
                evaluation_run_id="erun-offline",
                agent_version_id="av-1",
                dataset_version_id=None,
                status="pending",
            )
        )
        session.add(
            EvaluationRun(
                evaluation_run_id="erun-other",
                agent_version_id="av-1",
                dataset_version_id=None,
                status="pending",
            )
        )
        session.add(
            Run(
                run_id="run-1",
                agent_id="agent-1",
                agent_version_id="av-1",
                input_text="What is the capital of France?",
                final_answer="The capital of France is Paris.",
                duration_ms=820,
                status="completed",
                started_at=utcnow(),
            )
        )
        session.add(
            Run(
                run_id="run-2",
                agent_id="agent-1",
                agent_version_id="av-1",
                input_text="What is the capital of Germany?",
                final_answer="The capital of Germany is Berlin.",
                duration_ms=400,
                status="completed",
                started_at=utcnow(),
            )
        )
        session.add(
            Event(
                run_id="run-1",
                sequence_number=1,
                event_type=EventType.MODEL_OUTPUT.value,
                payload={
                    "model_id": "test-model",
                    "input_tokens": 20,
                    "output_tokens": 8,
                    "total_tokens": 28,
                },
            )
        )
        session.commit()

    return "seeded"
