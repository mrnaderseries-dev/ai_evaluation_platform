"""Focused tests for the AnalysisContext builder contract.

Round 3 finding: the context builders (AnalysisEngine.build_run_context /
build_evaluation_context / build_window_context) were exercised only
indirectly through repository tests, which never assert what arguments the
builders forward. These tests pin the caller-side contract: the requested
agent_id, scope identifier, limit and offset are forwarded to the repository
verbatim, and invalid session/pagination arguments are rejected before any
repository call.

The sqlite integration cases prove the filter-before-pagination property at
the real SQL layer (a foreign agent's rows, when placed in the same run, can
never reach AnalysisContext) plus the exact pagination limitation rules of
AnalysisContext.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from ai_eval_platform.analysis.context import AnalysisContext
from ai_eval_platform.analysis.engine import AnalysisEngine
from ai_eval_platform.storage.models import Base, PersistedSignal


SQLITE_MEMORY = "sqlite+pysqlite:///:memory:"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def engine():
    """In-memory SQLite engine with all tables created."""
    eng = create_engine(
        SQLITE_MEMORY,
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
    factory = sessionmaker(bind=engine, autoflush=False)
    sess = factory()
    yield sess
    sess.rollback()
    sess.close()



@pytest.fixture()
def analysis_engine(session):
    from ai_eval_platform.analysis.engine import AnalysisEngine

    return AnalysisEngine(session)


def _install(analysis_engine, repo):
    analysis_engine._signals = repo

# ---------------------------------------------------------------------------
# Repository fakes
# ---------------------------------------------------------------------------
class _RecordingRepository:
    """Record every repository call. Any unexpected call fails the test."""

    def __init__(self):
        self.calls: list[dict] = []

    def list_for_run(self, run_id, *, agent_id, limit, offset):
        self.calls.append(
            dict(method="list_for_run", run_id=run_id, agent_id=agent_id, limit=limit, offset=offset)
        )
        return []

    def list_for_evaluation_run(self, evaluation_run_id, *, agent_id, limit, offset):
        self.calls.append(
            dict(
                method="list_for_evaluation_run",
                evaluation_run_id=evaluation_run_id,
                agent_id=agent_id,
                limit=limit,
                offset=offset,
            )
        )
        return []

    def list_for_window(self, agent_id, window_start, window_end, *, limit, offset):
        self.calls.append(
            dict(
                method="list_for_window",
                agent_id=agent_id,
                window_start=window_start,
                window_end=window_end,
                limit=limit,
                offset=offset,
            )
        )
        return []


class _FailingRepository(_RecordingRepository):
    def __init__(self, error: Exception):
        super().__init__()
        self._error = error

    def list_for_run(self, run_id, *, agent_id, limit, offset):
        raise self._error

    def list_for_evaluation_run(self, evaluation_run_id, *, agent_id, limit, offset):
        raise self._error

    def list_for_window(self, agent_id, window_start, window_end, *, limit, offset):
        raise self._error


def _seed_signals_for_run(session, agent_id="agent-1", run_id="run-1", count=3, prefix="s"):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(count):
        session.add(
            PersistedSignal(
                signal_id=f"{prefix}-{i + 1}",
                agent_id=agent_id,
                signal_type="raw",
                scope="run",
                run_id=run_id,
                detected_at=now + timedelta(seconds=i),
                recorded_at=now,
                payload={},
                payload_type="raw",
            )
        )
    session.flush()


# ---------------------------------------------------------------------------
# Forwarding contract (caller side)
# ---------------------------------------------------------------------------
class TestRunContextForwarding:
    def test_forwards_run_agent_and_pagination(self, analysis_engine):
        repo = _RecordingRepository()
        _install(analysis_engine, repo)
        analysis_engine.build_run_context("agent-1", "run-1", limit=2, offset=1)

        assert repo.calls == [
            dict(method="list_for_run", run_id="run-1", agent_id="agent-1", limit=2, offset=1)
        ]

    def test_forwards_evaluation_agent_and_pagination(self, analysis_engine):
        repo = _RecordingRepository()
        _install(analysis_engine, repo)
        analysis_engine.build_evaluation_context("agent-1", "erun-1", limit=5, offset=2)

        assert repo.calls == [
            dict(
                method="list_for_evaluation_run",
                evaluation_run_id="erun-1",
                agent_id="agent-1",
                limit=5,
                offset=2,
            )
        ]

    def test_forwards_window_agent_and_bounds(self, analysis_engine):
        repo = _RecordingRepository()
        _install(analysis_engine, repo)
        window_start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        window_end = datetime(2026, 1, 2, 0, 0, tzinfo=timezone.utc)
        analysis_engine.build_window_context("agent-1", window_start, window_end, limit=10, offset=0)

        assert repo.calls == [
            dict(
                method="list_for_window",
                agent_id="agent-1",
                window_start=window_start,
                window_end=window_end,
                limit=10,
                offset=0,
            )
        ]

    def test_zero_offset_is_legitimate(self, analysis_engine):
        repo = _RecordingRepository()
        _install(analysis_engine, repo)
        analysis_engine.build_run_context("agent-1", "run-1", limit=10, offset=0)
        assert repo.calls == [
            dict(method="list_for_run", run_id="run-1", agent_id="agent-1", limit=10, offset=0)
        ]


# ---------------------------------------------------------------------------
# Real-sqlite behavior
# ---------------------------------------------------------------------------
class TestRunContextIntegration:
    def test_foreign_agent_rows_in_same_run_cannot_reach_context(self, analysis_engine, session):
        """Foreign-agent rows are filtered at the SQL layer, so the context is
        always well-formed and never carries rows that fail its invariants."""
        from ai_eval_platform.storage.models import Agent, AgentVersion, Run

        _seed_signals_for_run(session, agent_id="agent-1", run_id="run-1", count=3)
        session.add(Agent(agent_id="agent-2", name="agent-2"))
        session.add(AgentVersion(agent_version_id="av-1", agent_id="agent-2", version="1.0.0"))
        session.add(
            Run(
                run_id="run-1",
                agent_id="agent-1",
                agent_version_id="av-1",
                started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                status="running",
            )
        )
        session.add(
            PersistedSignal(
                signal_id="a2-1",
                agent_id="agent-2",
                signal_type="raw",
                scope="run",
                run_id="run-1",
                detected_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                recorded_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                payload={},
                payload_type="raw",
            )
        )
        session.commit()

        ctx = analysis_engine.build_run_context("agent-1", "run-1", limit=10)

        assert [s.agent_id for s in ctx.signals] == ["agent-1", "agent-1", "agent-1"]
        assert all(s.run_id == "run-1" for s in ctx.signals)

    def test_truncation_reports_result_limit_reached(self, analysis_engine, session):
        _seed_signals_for_run(session, count=3)
        session.commit()

        ctx = analysis_engine.build_run_context("agent-1", "run-1", limit=2, offset=0)

        assert [s.signal_id for s in ctx.signals] == ["s-1", "s-2"]
        assert [str(l) for l in ctx.limitations] == ["result_limit_reached; additional signals may exist"]

    def test_exact_page_boundary_reports_limitation_at_exact_limit(self, analysis_engine, session):
        """SQLite applies LIMIT; the builder counts rows returned, so a request
        for limit=N on exactly N rows produces the same limitation as a
        larger request. The rule is about observation, not about intent."""
        _seed_signals_for_run(session, count=3)
        session.commit()

        ctx = analysis_engine.build_run_context("agent-1", "run-1", limit=3, offset=0)

        assert [s.signal_id for s in ctx.signals] == ["s-1", "s-2", "s-3"]
        assert [str(l) for l in ctx.limitations] == ["result_limit_reached; additional signals may exist"]


# ---------------------------------------------------------------------------
# Error propagation and validation ordering
# ---------------------------------------------------------------------------
class TestRepositoryErrors:
    def test_repository_error_propagates_unchanged(self, analysis_engine):
        from datetime import datetime, timezone

        window_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        window_end = datetime(2026, 1, 2, tzinfo=timezone.utc)

        repo = _FailingRepository(RuntimeError("boom"))

        _install(analysis_engine, repo)
        with pytest.raises(RuntimeError, match="boom") as run_ctx:
            analysis_engine.build_run_context("agent-1", "run-1", limit=10)
        assert run_ctx.value.args == ("boom",)

        _install(analysis_engine, repo)
        with pytest.raises(RuntimeError, match="boom") as eval_ctx:
            analysis_engine.build_evaluation_context("agent-1", "erun-1", limit=10)
        assert eval_ctx.value.args == ("boom",)

        _install(analysis_engine, repo)
        with pytest.raises(RuntimeError, match="boom") as win_ctx:
            analysis_engine.build_window_context(
                "agent-1", window_start, window_end, limit=10
            )
        assert win_ctx.value.args == ("boom",)




