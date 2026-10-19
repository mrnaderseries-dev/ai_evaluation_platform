from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from ai_eval_platform.root_cause import (
    InvestigationScope,
    RCAAnalyzer,
    RCAArtifact,
    RCAReportService,
)
from ai_eval_platform.storage.models import RootCauseAnalysis
from ai_eval_platform.storage.repositories import EntityNotFoundError


NOW = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)


def _scope(**overrides):
    base = dict(domain="qa", subject_type="service", subject_id="svc-1")
    base.update(overrides)
    return InvestigationScope(**base)


def _signal(ref="s1", **overrides):
    fields = dict(
        artifact_id=f"signal:{ref}",
        source_type="signal",
        source_ref=ref,
        observed_at=NOW,
        agent_id="agent-1",
        attributes={"signal_type": "latency_anomaly", "payload": {}},
    )
    fields.update(overrides)
    return RCAArtifact(**fields)


def _report(investigation_id="investigation-1", scope=None, artifacts=None):
    return RCAAnalyzer().analyze(
        investigation_id=investigation_id,
        trigger_type="regression",
        scope=scope or _scope(),
        artifacts=artifacts if artifacts is not None else [_signal()],
    )


def test_persist_and_retrieve_round_trip(session):
    report = _report(
        scope=_scope(pipeline_run_id="pipe-run-1", environment="prod"),
        artifacts=[
            _signal(pipeline_run_id="pipe-run-1", environment="prod"),
        ],
    )
    stored = RCAReportService(session).persist_report(report)
    session.commit()
    assert stored.investigation_id == "investigation-1"
    assert stored.evidence_fingerprint == report.evidence_fingerprint
    retrieved = RCAReportService(session).get_by_investigation("investigation-1")
    assert retrieved == report
    assert retrieved.scope.pipeline_run_id == "pipe-run-1"
    assert retrieved.scope.environment == "prod"


def test_persist_duplicate_investigation_rejected(session):
    service = RCAReportService(session)
    service.persist_report(_report())
    session.flush()
    with pytest.raises(IntegrityError):
        service.persist_report(_report())
        session.flush()


def test_persist_failed_transaction_not_visible(session):
    service = RCAReportService(session)
    service.persist_report(_report())
    session.rollback()
    with pytest.raises(EntityNotFoundError):
        service.get_by_investigation("investigation-1")


def test_persist_empty_evidence_report(session):
    report = _report(artifacts=[])
    stored = RCAReportService(session).persist_report(report)
    session.commit()
    assert stored.conclusion_type == "insufficient_evidence"
    retrieved = RCAReportService(session).get_by_investigation("investigation-1")
    assert retrieved == report
    assert retrieved.hypotheses == ()


def test_persist_preserves_roles_limitations_lineage(session):
    scope = _scope(pipeline_run_id="pipe-run-1", environment="prod")
    signal = _signal(pipeline_run_id="pipe-run-1", environment="prod")
    context = RCAArtifact(
        artifact_id="log:l1",
        source_type="log",
        source_ref="l1",
        observed_at=NOW,
        agent_id="agent-1",
        run_id="run-1",
        pipeline_run_id="pipe-run-1",
        environment="prod",
        attributes={},
    )
    report = _report(scope=scope, artifacts=[signal, context])
    RCAReportService(session).persist_report(report)
    session.commit()
    retrieved = RCAReportService(session).get_by_investigation("investigation-1")
    assert retrieved.limitations == report.limitations
    assert retrieved.evidence_fingerprint == report.evidence_fingerprint
    assert retrieved.hypotheses[0].evidence[0].role == "supporting"
    assert any(ref.role == "context" for h in retrieved.hypotheses for ref in h.evidence)
    row = session.get(RootCauseAnalysis, "rca-investigation-1")
    assert row.report["scope"]["environment"] == "prod"
    assert row.report["scope"]["pipeline_run_id"] == "pipe-run-1"
    assert row.report["evidence_fingerprint"] == report.evidence_fingerprint


def test_get_unknown_investigation_raises(session):
    with pytest.raises(EntityNotFoundError):
        RCAReportService(session).get_by_investigation("missing")
