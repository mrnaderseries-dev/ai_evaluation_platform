from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from ai_eval_platform.analysis.evidence_adapter import EvidenceAdapter
from ai_eval_platform.analysis.relationship_service import RelationshipAnalysisService
from ai_eval_platform.monitoring_integration import (
    persist_signal_evidence,
)
from ai_eval_platform.root_cause import (
    InvestigationScope,
    RCAAnalyzer,
    RCAArtifact,
    RCAReportService,
    artifact_from_cicd,
    artifact_from_evidence,
    artifact_from_observation,
    artifact_from_relationship,
    artifact_from_signal,
    artifacts_from_differential_analysis,
)
from ai_eval_platform.statistics import (
    ComparisonConfig,
    EvaluationRunComparisonService,
    MetricRequest,
)
from ai_eval_platform.storage.models import (
    Agent,
    AgentVersion,
    Base,
    Dataset,
    DatasetVersion,
    EvaluationCase,
    EvaluationResult,
    EvaluationRun,
    Event,
    EventObservation,
    PersistedSignal,
    Run,
)
from ai_eval_platform.storage.repositories import (
    DifferentialAnalysisRepository,
    EventRepository,
    EvidenceRepository,
    PersistedSignalRepository,
    RelationshipRepository,
)


NOW = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)


def _engine():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


def _seed_graph(session: Session) -> None:
    session.add(Agent(agent_id="agent-1", name="agent-1"))
    session.add(
        AgentVersion(agent_version_id="av-1", agent_id="agent-1", version="1.0.0")
    )
    session.add(
        AgentVersion(agent_version_id="av-2", agent_id="agent-1", version="1.1.0")
    )
    session.add(Dataset(dataset_id="ds-1", name="ds-1"))
    session.add(
        DatasetVersion(dataset_version_id="dsv-1", dataset_id="ds-1", version="1.0.0")
    )
    session.add_all(
        EvaluationCase(
            case_id=f"case-{index}",
            dataset_version_id="dsv-1",
            question=f"question {index}",
        )
        for index in range(1, 9)
    )
    session.add(
        EvaluationRun(
            evaluation_run_id="erun-base",
            agent_version_id="av-1",
            dataset_version_id="dsv-1",
            status="completed",
        )
    )
    session.add(
        EvaluationRun(
            evaluation_run_id="erun-cand",
            agent_version_id="av-2",
            dataset_version_id="dsv-1",
            status="completed",
        )
    )
    session.add(
        Run(
            run_id="run-1",
            agent_id="agent-1",
            agent_version_id="av-1",
            started_at=NOW - timedelta(minutes=10),
            completed_at=NOW,
            status="completed",
        )
    )
    session.add(
        Run(
            run_id="run-eval-base",
            agent_id="agent-1",
            agent_version_id="av-1",
            started_at=NOW - timedelta(hours=2),
            completed_at=NOW - timedelta(hours=1),
            status="completed",
        )
    )
    session.add(
        Run(
            run_id="run-eval-cand",
            agent_id="agent-1",
            agent_version_id="av-2",
            started_at=NOW - timedelta(minutes=30),
            completed_at=NOW,
            status="completed",
        )
    )
    session.flush()


def _persist_signal(session: Session):
    signal = PersistedSignal(
        signal_id="signal-1",
        origin="detector",
        source_event_id="evt-1",
        source_observation_id="obs-1",
        projection_version="v1",
        agent_id="agent-1",
        signal_type="error_pattern",
        scope="run",
        run_id="run-1",
        detected_at=NOW,
        payload={
            "error_type": "timeout",
            "occurrence_count": 3,
            "related_event_ids": ["evt-1"],
        },
        payload_type="ErrorPatternPayload",
        detector_name="pattern",
        dedupe_key="e2e-signal-1",
    )
    assert PersistedSignalRepository(session).insert_ignore_duplicates([signal]) == 1
    session.flush()
    return PersistedSignalRepository(session).get("signal-1")


def _persist_event(session: Session):
    events = [
        Event(
            event_id="evt-1",
            run_id="run-1",
            sequence_number=1,
            event_type="tool_called",
            occurred_at=NOW - timedelta(minutes=1),
            payload={"tool_name": "search", "call_id": "call-1"},
        ),
        Event(
            event_id="evt-2",
            run_id="run-1",
            sequence_number=2,
            event_type="tool_called",
            occurred_at=NOW - timedelta(seconds=30),
            payload={"tool_name": "search", "call_id": "call-2"},
        ),
    ]
    EventRepository(session).insert_ignore_duplicates(events)
    session.flush()
    return session.get(Event, "evt-1")


def _persist_differential(session: Session):
    """Run the production evaluation-run comparison and persist its artifacts."""
    baseline_scores = [0.90, 0.92, 0.88, 0.91, 0.89, 0.93, 0.87, 0.90]
    candidate_scores = [0.55, 0.60, 0.50, 0.58, 0.52, 0.61, 0.49, 0.57]
    for prefix, evaluation_run_id, run_id, scores in (
        ("rb", "erun-base", "run-eval-base", baseline_scores),
        ("rc", "erun-cand", "run-eval-cand", candidate_scores),
    ):
        session.add_all(
            EvaluationResult(
                evaluation_result_id=f"{prefix}-{index}",
                evaluation_run_id=evaluation_run_id,
                case_id=f"case-{index}",
                run_id=run_id,
                metric_name="recall",
                score=score,
            )
            for index, score in enumerate(scores, start=1)
        )
    session.flush()

    service = EvaluationRunComparisonService(session)
    comparison = service.compare_runs(
        "erun-base",
        "erun-cand",
        [
            MetricRequest(
                metric_name="recall",
                observation_unit="case",
                metric_direction="higher_is_better",
            )
        ],
        config=ComparisonConfig(bootstrap_resamples=300, bootstrap_seed=17),
    )
    _, differential = service.save(comparison)
    session.flush()

    delta = differential.metric_deltas["recall"]
    stats = differential.statistical_tests["recall"]
    assert delta["status"] == "regressed"
    assert delta["median_difference"] < 0
    assert stats["statistically_significant"] is True
    assert stats["raw_p_value"] < 0.05
    return differential


def _build_artifacts(session: Session):
    from ai_eval_platform.monitoring.signals.models import ErrorPatternPayload, Signal

    stored = _persist_signal(session)
    event = _persist_event(session)
    differential = _persist_differential(session)
    # Verify the differential-analysis table content, not just the returned
    # object: the persisted row is what the RCA adapter must consume.
    stored_differentials = DifferentialAnalysisRepository(
        session
    ).list_for_candidate("erun-cand")
    assert len(stored_differentials) == 1
    assert stored_differentials[0].differential_analysis_id == (
        differential.differential_analysis_id
    )
    differential = stored_differentials[0]
    # Both events were persisted with their run ordering intact.
    persisted_events = (
        session.query(Event).filter_by(run_id="run-1").order_by(Event.sequence_number)
    ).all()
    assert [persisted_event.event_id for persisted_event in persisted_events] == [
        "evt-1",
        "evt-2",
    ]
    live_signal = Signal(
        signal_id=stored.signal_id,
        agent_id=stored.agent_id,
        signal_type="error_pattern",
        scope="run",
        run_id="run-1",
        detected_at=NOW,
        payload=ErrorPatternPayload(
            error_type="timeout",
            occurrence_count=3,
            related_event_ids=["evt-1"],
        ),
        source_event_id="evt-1",
        source_observation_id="obs-1",
        projection_version="v1",
    )
    persist_signal_evidence(session, [stored])
    session.flush()
    evidence_rows = EvidenceRepository(session).list_for_subject(
        "agent", "agent-1", domain="monitoring"
    )
    # 2 event + 2 base-projection + 1 detector-signal evidence rows.
    assert len(evidence_rows) == 5
    adapter = EvidenceAdapter(naive_timestamp_timezone=timezone.utc)
    records = adapter.adapt_many(evidence_rows)
    signal_records = [record for record in records if record.source == "signal"]
    assert signal_records
    detector_signal_record = next(
        record for record in records if record.source_ref == stored.signal_id
    )
    event_records = [record for record in records if record.source == "event"]
    assert len(event_records) == 2
    observation = (
        session.query(EventObservation)
        .filter_by(source_event_id=event.event_id)
        .one()
    )
    # The observation is a versioned projection of the stored event.
    assert observation.projection_version
    assert observation.sequence_number == event.sequence_number
    assert observation.event_type == event.event_type
    artifacts = [
        artifact_from_signal(live_signal),
        artifact_from_observation(observation),
        *[artifact_from_evidence(record) for record in event_records],
        artifact_from_evidence(detector_signal_record),
        *artifacts_from_differential_analysis(differential),
    ]
    relationship_result = RelationshipAnalysisService(session).analyze_subject(
        domain="monitoring",
        subject_type="agent",
        subject_id="agent-1",
    )
    assert relationship_result.relationships
    link_repository = RelationshipRepository(session)
    for persisted_relationship in relationship_result.relationships:
        links = link_repository.list_evidence_links(
            persisted_relationship.relationship_id
        )
        ordered_evidence_ids = [
            link.evidence_id
            for link in sorted(links, key=lambda item: item.evidence_order)
        ]
        artifacts.append(
            artifact_from_relationship(
                persisted_relationship,
                evidence_ids=ordered_evidence_ids,
            )
        )
    artifacts.append(
        artifact_from_cicd(
            {
                "check_id": "gate-1",
                "pipeline_run_id": "pipe-run-1",
                "environment": "prod",
                "decision": "failed",
                "commit_sha": "abc123",
                "failed_tests": ["recall_regression"],
            }
        )
    )
    return artifacts


def _scope(**overrides):
    base = dict(domain="monitoring", subject_type="agent", subject_id="agent-1")
    base.update(overrides)
    return InvestigationScope(**base)


def test_end_to_end_regression_scenario_persists_and_serves():
    from fastapi.testclient import TestClient

    from ai_eval_platform.api.main import app
    from ai_eval_platform.api.rca import _session as rca_session

    engine = _engine()
    try:
        with Session(engine, expire_on_commit=False, autoflush=False) as session:
            _seed_graph(session)
            artifacts = _build_artifacts(session)
            report = RCAAnalyzer().analyze(
                investigation_id="investigation-e2e",
                trigger_type="regression",
                scope=_scope(pipeline_run_id="pipe-run-1", environment="prod"),
                artifacts=artifacts,
            )
            assert report.hypotheses
            assert report.conclusion_level == "descriptive"
            assert report.conclusion_type == "no_supported_cause"
            # Deduplication of repeated representations of the same source:
            # submitting the same artifact twice must be rejected at the
            # analysis boundary rather than silently double-counted.
            try:
                RCAAnalyzer().analyze(
                    investigation_id="investigation-e2e-duplicate",
                    trigger_type="regression",
                    scope=_scope(pipeline_run_id="pipe-run-1", environment="prod"),
                    artifacts=[*artifacts, artifacts[0]],
                )
            except ValueError as exc:
                assert "unique" in str(exc)
            else:
                raise AssertionError(
                    "repeated representations of one artifact must be rejected"
                )
            RCAReportService(session).persist_report(report)
            session.commit()
            persisted = RCAReportService(session).get_by_investigation(
                "investigation-e2e"
            )
            assert persisted == report

        def override():
            session = Session(engine, expire_on_commit=False, autoflush=False)
            try:
                yield session
            finally:
                session.close()

        app.dependency_overrides[rca_session] = override
        try:
            client = TestClient(app)
            response = client.get("/rca/reports/investigation-e2e")
            assert response.status_code == 200
            payload = response.json()
            assert payload["schema_version"] == "1.1"
            assert payload["investigation_id"] == "investigation-e2e"
            assert payload["evidence_fingerprint"] == report.evidence_fingerprint
            assert payload["scope"]["pipeline_run_id"] == "pipe-run-1"
            assert payload["scope"]["environment"] == "prod"
            assert payload["conclusion_level"] == "descriptive"
            roles = {
                ref["role"] for hypo in payload["hypotheses"] for ref in hypo["evidence"]
            }
            assert "supporting" in roles
            assert "context" in roles

            def _refs(rule_prefix):
                hypothesis = next(
                    hypo
                    for hypo in payload["hypotheses"]
                    if hypo["rule_ids"][0].startswith(rule_prefix)
                )
                return hypothesis["evidence"]

            # Statistical evidence carries the regression as supporting
            # evidence; the CI/CD gate is a policy decision, never a cause.
            statistical_refs = _refs("statistics.differential")
            assert {
                ref["role"]
                for ref in statistical_refs
                if ref["source_type"] == "differential_analysis"
            } == {"supporting"}
            cicd_refs = _refs("ci_cd")
            assert any(
                ref["source_type"] == "ci_cd_result" and ref["role"] == "supporting"
                for ref in cicd_refs
            )
            # Discovered relationships stay exploratory context.
            relationship_refs = _refs("relationship")
            assert relationship_refs
            assert {
                ref["role"]
                for ref in relationship_refs
                if ref["source_type"] == "relationship"
            } == {"context"}
            # Validation metadata: the analyzer never mints validation, so no
            # hypothesis can present itself as validated without an explicit
            # validation record.
            assert all(hypo["validation"] is None for hypo in payload["hypotheses"])
            with Session(engine, expire_on_commit=False, autoflush=False) as fresh:
                fresh_report = RCAReportService(fresh).get_by_investigation(
                    "investigation-e2e"
                )
                assert fresh_report == report
        finally:
            app.dependency_overrides.pop(rca_session, None)
    finally:
        engine.dispose()


def test_end_to_end_scope_mismatch_and_empty_evidence():
    engine = _engine()
    try:
        with Session(engine, expire_on_commit=False, autoflush=False) as session:
            _seed_graph(session)
            artifacts = _build_artifacts(session)
            foreign = RCAArtifact(
                artifact_id="signal:foreign",
                source_type="signal",
                source_ref="foreign",
                observed_at=NOW,
                agent_id="agent-1",
                environment="staging",
                attributes={"signal_type": "latency_anomaly", "payload": {}},
            )
            report = RCAAnalyzer().analyze(
                investigation_id="investigation-scope",
                trigger_type="regression",
                scope=_scope(environment="prod"),
                artifacts=[*artifacts, foreign],
            )
            covered = {ref.evidence_id for h in report.hypotheses for ref in h.evidence}
            assert "signal:foreign" not in covered
            empty = RCAAnalyzer().analyze(
                investigation_id="investigation-empty",
                trigger_type="regression",
                scope=_scope(),
                artifacts=[],
            )
            assert empty.conclusion_type == "insufficient_evidence"
            RCAReportService(session).persist_report(empty)
            session.commit()
            assert (
                RCAReportService(session)
                .get_by_investigation("investigation-empty")
                .conclusion_level
                == "descriptive"
            )
    finally:
        engine.dispose()
