from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from ai_eval_platform.api.main import app
from ai_eval_platform.api.rca import _session as rca_session
from ai_eval_platform.root_cause import InvestigationScope, RCAAnalyzer, RCAArtifact, RCAReportService
from ai_eval_platform.storage.models import Base


NOW = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)


def _client_and_engine():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    def override():
        session = Session(engine, expire_on_commit=False, autoflush=False)
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[rca_session] = override
    try:
        yield TestClient(app), engine
    finally:
        app.dependency_overrides.pop(rca_session, None)
        engine.dispose()


def _persist(engine, investigation_id="investigation-1"):
    scope = InvestigationScope(
        domain="qa",
        subject_type="service",
        subject_id="svc-1",
        pipeline_run_id="pipe-run-1",
        environment="prod",
    )
    report = RCAAnalyzer().analyze(
        investigation_id=investigation_id,
        trigger_type="regression",
        scope=scope,
        artifacts=[
            RCAArtifact(
                artifact_id="signal:s1",
                source_type="signal",
                source_ref="s1",
                observed_at=NOW,
                agent_id="agent-1",
                pipeline_run_id="pipe-run-1",
                environment="prod",
                attributes={"signal_type": "latency_anomaly", "payload": {}},
            )
        ],
    )
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        RCAReportService(session).persist_report(report)
        session.commit()
    return report


def test_rca_report_retrievable_after_session_end():
    gen = _client_and_engine()
    client, engine = next(gen)
    try:
        report = _persist(engine)
        response = client.get("/rca/reports/investigation-1")
        assert response.status_code == 200
        payload = response.json()
        assert payload["investigation_id"] == "investigation-1"
        assert payload["evidence_fingerprint"] == report.evidence_fingerprint
        assert payload["conclusion_level"] == report.conclusion_level
        assert payload["scope"]["pipeline_run_id"] == "pipe-run-1"
        assert payload["scope"]["environment"] == "prod"
        assert payload["hypotheses"][0]["evidence"][0]["role"] == "supporting"
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


def test_rca_report_not_found_is_404():
    gen = _client_and_engine()
    client, engine = next(gen)
    try:
        response = client.get("/rca/reports/missing")
        assert response.status_code == 404
        assert response.json()["detail"] == "RCA report not found"
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


def test_rca_report_for_analysis_route():
    gen = _client_and_engine()
    client, engine = next(gen)
    try:
        report = _persist(engine)
        persisted_analysis_id = client.get("/rca/reports/investigation-1").json()[
            "analysis_id"
        ]
        response = client.get(f"/rca/analyses/{persisted_analysis_id}/report")
        assert response.status_code == 200
        assert response.json()["investigation_id"] == "investigation-1"
        missing = client.get("/rca/analyses/missing/report")
        assert missing.status_code == 404
    finally:
        try:
            next(gen)
        except StopIteration:
            pass
