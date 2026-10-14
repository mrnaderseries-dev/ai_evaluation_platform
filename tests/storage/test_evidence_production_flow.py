from datetime import datetime, timedelta, timezone

from ai_eval_platform.analysis.relationship_service import RelationshipAnalysisService
from ai_eval_platform.monitoring_integration import (
    event_to_evidence_record,
    persist_signal_evidence,
    signal_to_evidence_record,
)
from ai_eval_platform.storage.models import (
    Agent,
    AgentVersion,
    Event,
    PersistedSignal,
    Run,
)
from ai_eval_platform.storage.repositories import (
    EventRepository,
    EvidenceRepository,
    PersistedSignalRepository,
)


def _utcnow():
    return datetime.now(timezone.utc)


def test_signal_to_evidence_record_roundtrip(session):
    session.add(Agent(agent_id="agent-1", name="agent-1"))
    session.add(AgentVersion(agent_version_id="av-1", agent_id="agent-1", version="1.0.0"))
    session.add(
        Run(
            run_id="run-1",
            agent_id="agent-1",
            agent_version_id="av-1",
            started_at=_utcnow(),
            status="completed",
        )
    )
    session.flush()

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
        detected_at=_utcnow(),
        payload={"error_type": "timeout", "occurrence_count": 2, "related_event_ids": ["evt-a", "evt-b"]},
        payload_type="ErrorPatternPayload",
        detector_name="pattern",
        dedupe_key="dedupe-1",
    )

    record = signal_to_evidence_record(signal)

    assert record.evidence_id != signal.signal_id
    assert len(record.evidence_id) == 64
    assert record.source == "signal"
    assert record.source_ref == "signal-1"
    assert record.evidence_kind == "error"
    assert record.domain == "monitoring"
    assert record.subject_type == "agent"
    assert record.subject_id == "agent-1"
    assert record.agent_id == "agent-1"
    assert record.run_id == "run-1"
    assert record.source_snapshot["signal_id"] == "signal-1"
    assert record.attributes["error_type"] == "timeout"


def test_signal_evidence_is_persisted_and_event_evidence_is_compatible(session):
    session.add(Agent(agent_id="agent-1", name="agent-1"))
    session.add(AgentVersion(agent_version_id="av-1", agent_id="agent-1", version="1.0.0"))
    session.add(
        Run(
            run_id="run-1",
            agent_id="agent-1",
            agent_version_id="av-1",
            started_at=_utcnow(),
            status="completed",
        )
    )
    event = Event(
        event_id="evt-1",
        run_id="run-1",
        sequence_number=1,
        event_type="tool_called",
        occurred_at=_utcnow(),
        payload={"tool_name": "search", "call_id": "call-1"},
    )
    EventRepository(session).insert_ignore_duplicates([event])
    session.flush()

    signal = PersistedSignal(
        signal_id="signal-2",
        origin="detector",
        source_event_id="evt-1",
        agent_id="agent-1",
        signal_type="repeated_tool_usage",
        scope="run",
        run_id="run-1",
        detected_at=_utcnow() + timedelta(seconds=1),
        payload={"tool_name": "search", "occurrence_count": 2, "sequence": ["search", "search"]},
        payload_type="RepeatedToolUsagePayload",
        detector_name="pattern",
        dedupe_key="dedupe-2",
    )
    PersistedSignalRepository(session).insert_ignore_duplicates([signal])
    persisted_signal = PersistedSignalRepository(session).get("signal-2")

    inserted = persist_signal_evidence(session, [persisted_signal])
    assert inserted == 1
    assert persist_signal_evidence(session, [persisted_signal]) == 0

    event_record = event_to_evidence_record(session.get(Event, "evt-1"), session=session)
    assert event_record.source == "event"
    assert event_record.evidence_id != "evt-1"
    assert event_record.source_ref == "evt-1"
    assert event_record.evidence_kind == "observation"
    assert event_record.subject_id == "agent-1"
    assert event_record.run_id == "run-1"
    colliding_source_id = signal_to_evidence_record(
        PersistedSignal(
            signal_id="evt-1",
            agent_id="agent-1",
            signal_type="error_pattern",
            scope="run",
            run_id="run-1",
            detected_at=persisted_signal.detected_at,
            payload={},
            payload_type="ErrorPatternPayload",
        )
    )
    assert colliding_source_id.evidence_id != event_record.evidence_id

    repo = EvidenceRepository(session)
    assert repo.get(signal_to_evidence_record(persisted_signal).evidence_id).source == "signal"
    assert repo.get(event_record.evidence_id).source == "event"

    result = RelationshipAnalysisService(session).analyze_subject(
        domain="monitoring",
        subject_type="agent",
        subject_id="agent-1",
    )
    assert result.evidence_count == 3
    assert any(
        relationship.relationship_type == "temporal"
        for relationship in result.relationships
    )
