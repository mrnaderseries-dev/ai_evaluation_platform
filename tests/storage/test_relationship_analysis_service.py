from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from ai_eval_platform.analysis import (
    AnalysisReplayConflictError,
    EvidenceAdapter,
    EvidenceAdaptationError,
    RelationshipAnalysisService,
)
from ai_eval_platform.analysis.evidence import EvidenceCollection
from ai_eval_platform.analysis.relationship_analyzer import RelationshipAnalyzer
from ai_eval_platform.storage.models import (
    Analysis,
    PersistedEvidenceRecord,
    PersistedRelationship,
    RelationshipEvidence,
)
from ai_eval_platform.storage.repositories import EvidenceRepository, RelationshipRepository


UTC = timezone.utc
BASE = datetime(2026, 1, 1, tzinfo=UTC)


def _evidence(
    evidence_id: str,
    seconds: int,
    value: float,
    **overrides,
) -> PersistedEvidenceRecord:
    attributes = {
        "metric_name": "latency",
        "metric_value": value,
        "metric_unit": "ms",
        "aggregation": "time_series",
    }
    values = {
        "evidence_id": evidence_id,
        "source": "metric",
        "source_ref": f"source-{evidence_id}",
        "evidence_kind": "quantitative",
        "domain": "monitoring",
        "subject_type": "agent",
        "subject_id": "agent-1",
        "agent_id": "agent-1",
        "system_id": "system-1",
        "system_version": "v1",
        "run_id": "run-1",
        "evaluation_run_id": None,
        "observed_at": BASE + timedelta(seconds=seconds),
        "attributes": attributes,
        "limitations": ["fixture source limitation"],
        "source_snapshot": {"source_event_id": f"event-{evidence_id}"},
    }
    values.update(overrides)
    return PersistedEvidenceRecord(**values)


def _insert_evidence(session, rows):
    assert EvidenceRepository(session).insert_ignore_duplicates(rows) == len(rows)


def _run_service(session, **kwargs):
    return RelationshipAnalysisService(session).analyze_subject(
        domain="monitoring",
        subject_type="agent",
        subject_id="agent-1",
        **kwargs,
    )


def test_service_uses_persisted_evidence_and_preserves_link_order(session):
    _insert_evidence(
        session,
        [
            _evidence("ev-a", 0, 1),
            _evidence("ev-b", 1, 2),
            _evidence("ev-c", 2, 3),
        ],
    )
    # A fresh ORM materialization exercises SQLite's timezone loss behavior.
    session.expunge_all()

    result = _run_service(session)

    assert result.evidence_count == 3
    assert len(result.relationships) == 4
    assert all(relationship.status == "candidate" for relationship in result.relationships)
    analysis = session.get(Analysis, result.analysis_id)
    assert analysis.analysis_type == "relationship_analysis"
    assert analysis.metrics["evidence_ids"] == ["ev-a", "ev-b", "ev-c"]
    assert analysis.metrics["scope"] == {
        "domain": "monitoring",
        "subject_type": "agent",
        "subject_id": "agent-1",
        "observed_after": None,
        "observed_before": None,
    }

    adapted_records = EvidenceAdapter(naive_timestamp_timezone=UTC).adapt_many(
        EvidenceRepository(session).list_for_subject(
            "agent", "agent-1", domain="monitoring"
        )
    )
    canonical_keys = {
        relationship.relationship_id
        for relationship in RelationshipAnalyzer().analyze(
            EvidenceCollection(records=adapted_records)
        )
    }
    assert {relationship.relationship_key for relationship in result.relationships} == canonical_keys
    assert all(len(relationship.relationship_key) == 64 for relationship in result.relationships)

    evidence_by_id = {
        record.evidence_id: record
        for record in EvidenceRepository(session).list_for_subject(
            "agent", "agent-1", domain="monitoring"
        )
    }
    repository = RelationshipRepository(session)
    for relationship in result.relationships:
        links = repository.list_evidence_links(relationship.relationship_id)
        assert [link.evidence_order for link in links] == list(range(len(links)))
        assert all(link.role == "supporting" for link in links)
        linked_evidence = [evidence_by_id[link.evidence_id] for link in links]
        if len(linked_evidence) == 2:
            if relationship.relationship_type == "execution_parent":
                assert linked_evidence[0].span_id == linked_evidence[1].parent_span_id
            else:
                assert linked_evidence[0].observed_at < linked_evidence[1].observed_at
        elif len(linked_evidence) == 3:
            assert [record.observed_at for record in linked_evidence] == sorted(
                record.observed_at for record in linked_evidence
            )
        assert all(link.evidence_id in evidence_by_id for link in links)


def test_service_pages_all_evidence_without_losing_order(session):
    _insert_evidence(
        session,
        [_evidence(f"ev-{index}", index, index + 1) for index in range(5)],
    )
    session.expunge_all()

    result = RelationshipAnalysisService(session, page_size=2).analyze_subject(
        domain="monitoring",
        subject_type="agent",
        subject_id="agent-1",
    )

    assert result.evidence_count == 5
    assert session.get(Analysis, result.analysis_id).metrics["evidence_ids"] == [
        f"ev-{index}" for index in range(5)
    ]


def test_service_domain_and_run_scope_isolation(session):
    rows = [
        _evidence("run-a-1", 0, 1, run_id="run-a"),
        _evidence("run-a-2", 1, 2, run_id="run-a"),
        _evidence("run-b-1", 2, 3, run_id="run-b"),
        _evidence("run-b-2", 3, 4, run_id="run-b"),
        _evidence("other-domain-1", 4, 5, domain="evaluation"),
        _evidence("other-domain-2", 5, 6, domain="evaluation"),
        _evidence("other-domain-3", 6, 7, domain="evaluation"),
    ]
    _insert_evidence(session, rows)
    session.expunge_all()

    result = _run_service(session)

    assert result.evidence_count == 4
    assert len(result.relationships) == 2
    assert all(
        relationship.relationship_type == "temporal"
        for relationship in result.relationships
    )
    evidence_by_id = {
        record.evidence_id: record
        for record in EvidenceRepository(session).list_for_subject(
            "agent", "agent-1", domain="monitoring"
        )
    }
    repository = RelationshipRepository(session)
    for relationship in result.relationships:
        linked = [
            evidence_by_id[link.evidence_id]
            for link in repository.list_evidence_links(relationship.relationship_id)
        ]
        assert len({record.run_id for record in linked}) == 1


def test_service_replay_is_idempotent_and_new_runs_preserve_history(session):
    _insert_evidence(
        session,
        [_evidence("ev-a", 0, 1), _evidence("ev-b", 1, 2)],
    )
    session.expunge_all()
    service = RelationshipAnalysisService(session)
    first = service.analyze_subject(
        domain="monitoring", subject_type="agent", subject_id="agent-1"
    )
    first_ids = [relationship.relationship_id for relationship in first.relationships]

    replay = service.analyze_subject(
        domain="monitoring",
        subject_type="agent",
        subject_id="agent-1",
        analysis_id=first.analysis_id,
    )

    assert replay.analysis_id == first.analysis_id
    assert [relationship.relationship_id for relationship in replay.relationships] == first_ids
    assert session.scalar(select(func.count()).select_from(Analysis)) == 1
    assert session.scalar(select(func.count()).select_from(PersistedRelationship)) == len(first_ids)
    assert session.scalar(select(func.count()).select_from(RelationshipEvidence)) == 2

    historical = service.analyze_subject(
        domain="monitoring", subject_type="agent", subject_id="agent-1"
    )

    assert historical.analysis_id != first.analysis_id
    assert session.scalar(select(func.count()).select_from(Analysis)) == 2
    assert session.scalar(select(func.count()).select_from(PersistedRelationship)) == 2 * len(first_ids)


def test_replay_rejects_different_evidence_for_same_analysis_id(session):
    _insert_evidence(session, [_evidence("ev-a", 0, 1), _evidence("ev-b", 1, 2)])
    service = RelationshipAnalysisService(session)
    first = service.analyze_subject(
        domain="monitoring", subject_type="agent", subject_id="agent-1"
    )
    _insert_evidence(session, [_evidence("ev-c", 2, 3)])

    with pytest.raises(AnalysisReplayConflictError, match="different scope, evidence"):
        service.analyze_subject(
            domain="monitoring",
            subject_type="agent",
            subject_id="agent-1",
            analysis_id=first.analysis_id,
        )


def test_invalid_persisted_evidence_fails_before_creating_analysis(session):
    _insert_evidence(
        session,
        [_evidence("ev-bad", 0, 1, source="unsupported-source")],
    )

    with pytest.raises(EvidenceAdaptationError, match="source must be one of"):
        _run_service(session)

    assert session.scalar(select(func.count()).select_from(Analysis)) == 0
    assert session.scalar(select(func.count()).select_from(PersistedRelationship)) == 0


def test_service_filters_explicit_observation_window(session):
    _insert_evidence(
        session,
        [
            _evidence("ev-before", 0, 1),
            _evidence("ev-in-1", 10, 2),
            _evidence("ev-in-2", 20, 3),
            _evidence("ev-after", 30, 4),
        ],
    )
    session.expunge_all()

    result = _run_service(
        session,
        observed_after=BASE + timedelta(seconds=10),
        observed_before=BASE + timedelta(seconds=20),
    )

    assert result.evidence_count == 2
    assert len(result.relationships) == 1
    repository = RelationshipRepository(session)
    links = repository.list_evidence_links(result.relationships[0].relationship_id)
    assert [link.evidence_id for link in links] == ["ev-in-1", "ev-in-2"]


def test_service_rolls_back_partial_relationship_persistence(session):
    _insert_evidence(session, [_evidence("ev-a", 0, 1), _evidence("ev-b", 1, 2)])
    session.execute(
        text(
            """
            CREATE TRIGGER fail_second_relationship_link
            BEFORE INSERT ON relationship_evidence
            WHEN NEW.evidence_id = 'ev-b'
            BEGIN
                SELECT RAISE(ABORT, 'injected relationship evidence failure');
            END;
            """
        )
    )

    with pytest.raises(IntegrityError, match="injected relationship evidence failure"):
        _run_service(session)

    assert session.scalar(select(func.count()).select_from(Analysis)) == 0
    assert session.scalar(select(func.count()).select_from(PersistedRelationship)) == 0
    assert session.scalar(select(func.count()).select_from(RelationshipEvidence)) == 0
    assert session.scalar(select(func.count()).select_from(PersistedEvidenceRecord)) == 2


def test_service_does_not_commit_caller_transaction(session):
    _insert_evidence(session, [_evidence("ev-a", 0, 1), _evidence("ev-b", 1, 2)])
    session.commit()

    result = _run_service(session)
    assert session.get(Analysis, result.analysis_id) is not None

    session.rollback()

    assert session.get(Analysis, result.analysis_id) is None
    assert session.scalar(select(func.count()).select_from(PersistedRelationship)) == 0
