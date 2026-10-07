from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from schemas.events.common import EventType
from schemas.events.execution_events import ModelCalledEvent

from ai_eval_platform.storage.models import (
    Agent,
    AgentBehaviorEvent,
    AgentVersion,
    Artifact,
    ContextEvent,
    ContextResource,
    EvaluationResult,
    Event,
    ExecutionEvent,
    Lineage,
    OutboxEvent,
    Policy,
    PolicyDecision,
    PolicyVersion,
)
from ai_eval_platform.storage.repositories import (
    AgentBehaviorEventRepository,
    AgentRepository,
    ContextEventRepository,
    EntityNotFoundError,
    EvaluationResultRepository,
    EventRepository,
    ExecutionEventRepository,
    OutboxEventRepository,
    PolicyDecisionRepository,
    PolicyRepository,
    PolicyVersionRepository,
    RepositoryError,
    event_from_envelope,
)


def _event(
    event_id: str,
    run_id: str = "run-1",
    sequence_number: int = 1,
    **overrides,
) -> Event:
    fields = {
        "event_id": event_id,
        "run_id": run_id,
        "sequence_number": sequence_number,
        "event_type": "model_called",
        "schema_version": "1.0",
        "occurred_at": datetime.now(timezone.utc),
        "payload": {"model_id": "qwen"},
    }
    fields.update(overrides)
    return Event(**fields)


def _result(
    evaluation_result_id: str,
    score: float,
    metric_name: str = "correctness",
    run_id: str = "run-1",
) -> EvaluationResult:
    return EvaluationResult(
        evaluation_result_id=evaluation_result_id,
        evaluation_run_id="erun-1",
        run_id=run_id,
        case_id="case-1",
        metric_name=metric_name,
        score=score,
        passed=score >= 0.5,
    )


def _seed_artifacts(session) -> None:
    session.add(
        Artifact(artifact_id="art-1", artifact_type="report", name="input", uri="s3://in")
    )
    session.add(
        Artifact(artifact_id="art-2", artifact_type="report", name="output", uri="s3://out")
    )


def _lineage(lineage_id: str) -> Lineage:
    return Lineage(
        lineage_id=lineage_id,
        input_artifact_id="art-1",
        output_artifact_id="art-2",
        transformation="summarize",
    )


def test_crud_event_roundtrip(session, seeded_run):
    repo = EventRepository(session)
    repo.insert_ignore_duplicates([_event("evt-1")])

    assert repo.get("evt-1") is not None
    assert len(repo.list_for_run("run-1")) == 1


def test_repositories_do_not_commit(session):
    AgentRepository(session).create(
        Agent(agent_id="a-rollback", name="a-rollback")
    )

    assert session.get(Agent, "a-rollback") is not None

    session.rollback()

    assert session.get(Agent, "a-rollback") is None


def test_duplicate_event_ingestion_is_successful_noop(session, seeded_run):
    repo = EventRepository(session)

    assert repo.insert_ignore_duplicates([_event("dup-1")]) == 1
    # Second delivery of the same event_id is a no-op, not an error.
    assert repo.insert_ignore_duplicates([_event("dup-1")]) == 0

    total = session.scalar(select(func.count()).select_from(Event))
    assert total == 1


def test_duplicate_event_within_single_batch(session, seeded_run):
    repo = EventRepository(session)

    assert repo.insert_ignore_duplicates([_event("batch-1"), _event("batch-1")]) == 1
    assert session.scalar(select(func.count()).select_from(Event)) == 1


def test_evaluation_result_upsert_updates_in_place(session, seeded_evaluation_run):
    repo = EvaluationResultRepository(session)

    repo.upsert(_result("res-1", score=0.5))
    repo.upsert(_result("res-2", score=0.9))

    rows = repo.list_for_run("erun-1")
    assert len(rows) == 1
    assert rows[0].score == 0.9
    # On conflict the original primary key is preserved (row updated, not added).
    assert rows[0].evaluation_result_id == "res-1"

def test_evaluation_result_modes(session, seeded_evaluation_run):
    from ai_eval_platform.storage.models import EvaluationRun

    repo = EvaluationResultRepository(session)
    session.add(
        EvaluationRun(
            evaluation_run_id="erun-2",
            agent_version_id="av-1",
            status="running",
        )
    )
    session.flush()
    repo.upsert(
        EvaluationResult(
            evaluation_result_id="online-1",
            run_id="run-1",
            evaluation_run_id=None,
            case_id=None,
            metric_name="correctness",
            score=0.7,
            passed=True,
        )
    )
    repo.upsert(
        EvaluationResult(
            evaluation_result_id="offline-1",
            run_id="run-1",
            evaluation_run_id="erun-1",
            case_id=None,
            metric_name="correctness",
            score=0.6,
            passed=True,
        )
    )
    repo.upsert(
        EvaluationResult(
            evaluation_result_id="bench-1",
            run_id="run-2",
            evaluation_run_id="erun-1",
            case_id="case-1",
            metric_name="correctness",
            score=0.8,
            passed=True,
        )
    )
    assert session.scalar(select(func.count()).select_from(EvaluationResult)) == 3


def test_evaluation_result_multiple_metrics(session, seeded_evaluation_run):
    repo = EvaluationResultRepository(session)
    repo.upsert_many(
        [
            _result("multi-1", score=0.5, metric_name="correctness"),
            _result("multi-2", score=0.6, metric_name="groundedness"),
            _result("multi-3", score=0.7, metric_name="latency"),
        ]
    )
    rows = repo.list_for_run("erun-1")
    assert {row.metric_name for row in rows} == {
        "correctness",
        "groundedness",
        "latency",
    }


def test_evaluation_result_same_run_multiple_evaluation_runs(
    session, seeded_evaluation_run
):
    from ai_eval_platform.storage.models import EvaluationRun

    repo = EvaluationResultRepository(session)
    session.add(
        EvaluationRun(
            evaluation_run_id="erun-2",
            agent_version_id="av-1",
            status="running",
        )
    )
    session.flush()
    repo.upsert(_result("first-1", score=0.5))
    second = _result("second-1", score=0.6)
    second.evaluation_run_id = "erun-2"
    repo.upsert(second)
    assert session.scalar(select(func.count()).select_from(EvaluationResult)) == 2


def test_evaluation_result_duplicate_identity_updates(session, seeded_evaluation_run):
    from sqlalchemy.exc import IntegrityError

    repo = EvaluationResultRepository(session)
    repo.upsert(_result("dup-a", score=0.5))
    repo.upsert(_result("dup-b", score=0.9))
    rows = repo.list_for_run("erun-1")
    assert len(rows) == 1
    assert rows[0].score == 0.9
    assert rows[0].evaluation_result_id == "dup-a"
    with pytest.raises(IntegrityError):
        session.add(_result("dup-c", score=0.4))
        session.flush()
    session.rollback()


def test_evaluation_run_without_dataset_version(session, seeded_evaluation_run):
    from ai_eval_platform.storage.models import EvaluationRun
    from ai_eval_platform.storage.repositories import EvaluationRunRepository

    repo = EvaluationRunRepository(session)
    created = repo.create(
        EvaluationRun(
            evaluation_run_id="erun-nods",
            agent_version_id="av-1",
            dataset_version_id=None,
            status="running",
        )
    )
    assert created.dataset_version_id is None
    assert repo.get("erun-nods") is not None



def test_evaluation_result_unique_constraint(session, seeded_evaluation_run):
    session.add(_result("res-1", score=0.5))
    session.flush()

    session.add(_result("res-2", score=0.6))
    with pytest.raises(IntegrityError):
        session.flush()

    session.rollback()


def test_lineage_unique_constraint(session):
    _seed_artifacts(session)
    session.add(_lineage("l-1"))
    session.flush()

    session.add(_lineage("l-2"))
    with pytest.raises(IntegrityError):
        session.flush()

    session.rollback()


def test_agent_version_unique_constraint(session):
    session.add(Agent(agent_id="a1", name="a1"))
    session.add(AgentVersion(agent_version_id="av-1", agent_id="a1", version="1"))
    session.flush()

    session.add(AgentVersion(agent_version_id="av-2", agent_id="a1", version="1"))
    with pytest.raises(IntegrityError):
        session.flush()

    session.rollback()


def test_outbox_enqueue_and_publish(session):
    repo = OutboxEventRepository(session)
    repo.create(
        OutboxEvent(
            outbox_event_id="ob-1",
            event_id="evt-erun-1",
            aggregate_type="evaluation_run",
            aggregate_id="erun-1",
            topic="evaluation-events",
            message_key="erun-1",
            payload={"event_type": "evaluation_completed", "metric": "accuracy"},
        )
    )

    assert [event.outbox_event_id for event in repo.list_pending()] == ["ob-1"]

    repo.mark_published("ob-1")

    assert repo.list_pending() == []
    assert repo.get("ob-1").status == "published"


# ============================================================================
# Event ordering, relationships, correlation
# ============================================================================


def test_events_are_retrieved_in_sequence_order(session, seeded_run):
    repo = EventRepository(session)

    # deliberately inserted out of order.
    repo.create(_event("ord-3", sequence_number=3))
    repo.create(_event("ord-1", sequence_number=1))
    repo.create(_event("ord-2", sequence_number=2))

    assert [event.event_id for event in repo.list_for_run("run-1")] == [
        "ord-1",
        "ord-2",
        "ord-3",
    ]


def test_sequence_collision_is_rejected(session, seeded_run):
    """A distinct event_id reusing (run_id, sequence_number) must raise."""
    repo = EventRepository(session)
    repo.insert_ignore_duplicates([_event("seq-a", sequence_number=1)])

    with pytest.raises(IntegrityError):
        repo.insert_ignore_duplicates([_event("seq-b", sequence_number=1)])

    session.rollback()

    assert session.scalar(select(func.count()).select_from(Event)) == 0


def test_next_sequence_number(session, seeded_run):
    repo = EventRepository(session)

    assert repo.next_sequence_number("run-1") == 1

    repo.insert_ignore_duplicates(
        [_event("sq-1", sequence_number=1), _event("sq-2", sequence_number=2)]
    )

    assert repo.next_sequence_number("run-1") == 3


def test_parent_child_event_retrieval(session, seeded_run):
    repo = EventRepository(session)
    repo.create(_event("parent-1", sequence_number=1))
    # children inserted in reverse order on purpose.
    repo.create(_event("child-3", sequence_number=3, parent_event_id="parent-1"))
    repo.create(_event("child-2", sequence_number=2, parent_event_id="parent-1"))

    assert [event.event_id for event in repo.list_children("parent-1")] == [
        "child-2",
        "child-3",
    ]

    parent = repo.get_required("parent-1")
    assert {child.event_id for child in parent.child_events} == {"child-2", "child-3"}


def test_correlation_retrieval(session, seeded_run):
    repo = EventRepository(session)
    repo.create(
        _event(
            "corr-1",
            sequence_number=1,
            correlation_id="wf-1",
            step_id="step-a",
        )
    )
    repo.create(
        _event(
            "corr-2",
            sequence_number=2,
            correlation_id="wf-1",
            causation_id="corr-1",
            step_id="step-a",
        )
    )
    repo.create(_event("unrelated", sequence_number=3))

    assert [event.event_id for event in repo.list_by_correlation("wf-1")] == [
        "corr-1",
        "corr-2",
    ]
    assert repo.get_required("corr-2").causation_id == "corr-1"


def test_event_delete_cascades_to_behavior_detail(session, seeded_run):
    repo = EventRepository(session)
    event = _event("del-1", sequence_number=1)
    repo.create_with_behavior_detail(
        event, AgentBehaviorEvent(behavior_type="loop_detected")
    )

    session.delete(event)
    session.flush()

    assert session.scalar(select(func.count()).select_from(AgentBehaviorEvent)) == 0


def test_create_with_detail_rolls_back_atomically(session, seeded_run):
    """Envelope + detail + resources live and die in one transaction."""
    repo = EventRepository(session)

    repo.create_with_behavior_detail(
        _event("tx-1", sequence_number=1),
        AgentBehaviorEvent(behavior_type="loop_detected"),
    )

    context_event = ContextEvent(context_type="assembled")
    repo.create_with_context_detail(
        _event("tx-2", sequence_number=2),
        context_event,
        [ContextResource(resource_type="document", resource_id="doc-1")],
    )

    repo.create_with_execution_detail(
        _event("tx-3", sequence_number=3),
        ExecutionEvent(execution_type="tool", tool_name="search"),
    )

    assert session.scalar(select(func.count()).select_from(Event)) == 3
    assert session.scalar(select(func.count()).select_from(ContextResource)) == 1

    session.rollback()

    assert session.scalar(select(func.count()).select_from(Event)) == 0
    assert session.scalar(select(func.count()).select_from(AgentBehaviorEvent)) == 0
    assert session.scalar(select(func.count()).select_from(ContextEvent)) == 0
    assert session.scalar(select(func.count()).select_from(ContextResource)) == 0
    assert session.scalar(select(func.count()).select_from(ExecutionEvent)) == 0


# ============================================================================
# Idempotent typed-event ingestion
# ============================================================================

_DETAIL_MODELS = {
    "behavior": AgentBehaviorEvent,
    "context": ContextEvent,
    "execution": ExecutionEvent,
}


def _typed_detail(kind: str, event: Event):
    """Build ``(detail, resources)`` for one of the three 1:1 detail kinds."""
    if kind == "behavior":
        detail = AgentBehaviorEvent(
            behavior_type="loop_detected",
            action="stop",
            related_event_ids=[event.event_id],
            details={"count": 3},
        )
        detail.event = event
        return detail, []

    if kind == "context":
        detail = ContextEvent(context_type="assembled", resource_count=2)
        detail.event = event
        resources = [
            ContextResource(
                resource_type="document",
                resource_id="doc-1",
                rank=1,
                retrieved=True,
                selected=True,
                included_in_prompt=True,
            ),
            ContextResource(
                resource_type="document",
                resource_id="doc-2",
                rank=2,
                retrieved=True,
            ),
        ]
        for resource in resources:
            resource.context_event = detail
        return detail, resources

    detail = ExecutionEvent(
        execution_type="tool",
        tool_name="search",
        status="ok",
        duration_ms=12,
    )
    detail.event = event
    return detail, []


@pytest.mark.parametrize("kind", ["behavior", "context", "execution"])
def test_insert_with_detail_ignore_duplicates_is_idempotent(session, seeded_run, kind):
    repo = EventRepository(session)
    detail_model = _DETAIL_MODELS[kind]

    fresh_event = _event(f"idem-{kind}", sequence_number=1)
    fresh_detail, fresh_resources = _typed_detail(kind, fresh_event)
    assert (
        repo.insert_with_detail_ignore_duplicates(
            fresh_event, fresh_detail, fresh_resources
        )
        == 1
    )

    assert session.scalar(select(func.count()).select_from(Event)) == 1
    assert session.scalar(select(func.count()).select_from(detail_model)) == 1
    if kind == "context":
        assert session.scalar(select(func.count()).select_from(ContextResource)) == 2

    # Redelivery: fresh objects, same stable event_id -> successful no-op.
    replay_event = _event(f"idem-{kind}", sequence_number=1)
    replay_detail, replay_resources = _typed_detail(kind, replay_event)
    assert (
        repo.insert_with_detail_ignore_duplicates(
            replay_event, replay_detail, replay_resources
        )
        == 0
    )

    assert session.scalar(select(func.count()).select_from(Event)) == 1
    assert session.scalar(select(func.count()).select_from(detail_model)) == 1
    if kind == "context":
        # resources are never duplicated on replay.
        assert session.scalar(select(func.count()).select_from(ContextResource)) == 2


def test_insert_with_detail_rejects_mismatched_event(session, seeded_run):
    repo = EventRepository(session)
    detail = AgentBehaviorEvent(
        event_id="some-other-event",
        behavior_type="loop_detected",
    )

    with pytest.raises(RepositoryError):
        repo.insert_with_detail_ignore_duplicates(
            _event("mismatch", sequence_number=1), detail
        )


# ============================================================================
# Detail / policy repositories: create + retrieve
# ============================================================================


def test_context_event_repository_retrieval(session, seeded_run):
    events = EventRepository(session)
    contexts = ContextEventRepository(session)

    detail = ContextEvent(context_type="assembled", resource_count=3)
    resources = [
        ContextResource(resource_type="document", resource_id="doc-2", rank=2),
        ContextResource(resource_type="document", resource_id="doc-1", rank=1),
        ContextResource(resource_type="memory", resource_id="mem-1"),  # unranked
    ]
    events.create_with_context_detail(
        _event("ctx-1", sequence_number=1), detail, resources
    )

    assert contexts.get_required("ctx-1").resource_count == 3
    with pytest.raises(EntityNotFoundError):
        contexts.get_required("missing")

    assert [d.event_id for d in contexts.list_for_run("run-1")] == ["ctx-1"]
    assert [d.event_id for d in contexts.list_by_type("assembled")] == ["ctx-1"]

    # ranked rows first (rank asc), unranked last - deterministic on all dialects
    assert [r.resource_id for r in contexts.list_resources("ctx-1")] == [
        "doc-1",
        "doc-2",
        "mem-1",
    ]


def test_execution_event_repository_retrieval(session, seeded_run):
    events = EventRepository(session)
    executions = ExecutionEventRepository(session)
    started = datetime.now(timezone.utc)

    events.create_with_execution_detail(
        _event("exe-1", sequence_number=1),
        ExecutionEvent(
            execution_type="tool",
            tool_name="search",
            status="ok",
            started_at=started,
        ),
    )
    events.create_with_execution_detail(
        _event("exe-2", sequence_number=2),
        ExecutionEvent(execution_type="model", status="failed", started_at=started),
    )

    assert [d.event_id for d in executions.list_for_run("run-1")] == ["exe-1", "exe-2"]
    assert [d.event_id for d in executions.list_by_tool("search")] == ["exe-1"]
    assert [d.event_id for d in executions.list_by_status("failed")] == ["exe-2"]
    assert executions.get_required("exe-1").execution_type == "tool"
    with pytest.raises(EntityNotFoundError):
        executions.get_required("missing")


def test_agent_behavior_event_retrieval(session, seeded_run):
    events = EventRepository(session)
    behaviors = AgentBehaviorEventRepository(session)

    events.create_with_behavior_detail(
        _event("bvr-1", sequence_number=1),
        AgentBehaviorEvent(behavior_type="loop_detected"),
    )
    events.create_with_behavior_detail(
        _event("bvr-2", sequence_number=2),
        AgentBehaviorEvent(behavior_type="contradiction"),
    )

    assert [d.event_id for d in behaviors.list_for_run("run-1")] == ["bvr-1", "bvr-2"]
    assert [d.event_id for d in behaviors.list_by_type("loop_detected")] == ["bvr-1"]
    assert behaviors.get("bvr-1").behavior_type == "loop_detected"


def test_policy_version_lifecycle(session):
    policies = PolicyRepository(session)
    versions = PolicyVersionRepository(session)

    policy = policies.get_or_create("pii-guard", "data_security")
    again = policies.get_or_create("pii-guard", "data_security")
    assert policy.policy_id == again.policy_id

    v1 = versions.create(
        PolicyVersion(policy_id=policy.policy_id, version="1.0", rules={"r": 1})
    )
    assert (
        versions.get_by_version(policy.policy_id, "1.0").policy_version_id
        == v1.policy_version_id
    )
    assert versions.get_active(policy.policy_id) is None

    versions.activate(v1.policy_version_id)
    assert versions.get_active(policy.policy_id).policy_version_id == v1.policy_version_id

    v2 = versions.create(
        PolicyVersion(policy_id=policy.policy_id, version="2.0", rules={"r": 2})
    )
    versions.activate(v2.policy_version_id)

    assert versions.get_active(policy.policy_id).policy_version_id == v2.policy_version_id
    assert versions.get(v1.policy_version_id).is_active is False
    assert {v.version for v in versions.list_for_policy(policy.policy_id)} == {
        "1.0",
        "2.0",
    }


def test_policy_decision_roundtrip(session, seeded_run):
    policies = PolicyRepository(session)
    versions = PolicyVersionRepository(session)
    events = EventRepository(session)
    decisions = PolicyDecisionRepository(session)

    policy = policies.get_or_create("pii-guard", "data_security")
    version = versions.create(PolicyVersion(policy_id=policy.policy_id, version="1.0"))
    event = events.create(_event("dec-evt", sequence_number=1))

    decision = decisions.create(
        PolicyDecision(
            run_id="run-1",
            policy_version_id=version.policy_version_id,
            event_id=event.event_id,
            outcome="blocked",
            reason="pii_detected",
        )
    )

    assert decisions.get_required(decision.policy_decision_id).outcome == "blocked"
    assert [d.policy_decision_id for d in decisions.list_for_run("run-1")] == [
        decision.policy_decision_id
    ]
    assert len(decisions.list_for_policy_version(version.policy_version_id)) == 1
    assert [d.policy_decision_id for d in decisions.list_for_event(event.event_id)] == [
        decision.policy_decision_id
    ]
    assert (
        decisions.get_latest_for_run("run-1").policy_decision_id
        == decision.policy_decision_id
    )

    with pytest.raises(EntityNotFoundError):
        decisions.get_required("missing")


# ============================================================================
# Event Layer envelope -> storage alignment
# ============================================================================


def _envelope(event_id: str, **overrides) -> ModelCalledEvent:
    fields = {
        "event_id": event_id,
        "schema_version": "1.0",
        "system_id": "gaia-agent",
        "system_version": "1.0.0",
        "run_id": "run-1",
        "event_type": EventType.MODEL_CALLED,
        "timestamp": datetime.now(timezone.utc),
        "payload": {"model_id": "qwen"},
    }
    fields.update(overrides)
    return ModelCalledEvent(**fields)


def test_envelope_ingestion_roundtrip(session, seeded_run):
    repo = EventRepository(session)
    envelope = _envelope("env-1", correlation_id="corr-9", step_id="step-1")

    assert repo.insert_from_envelope(envelope) == 1

    stored = repo.get_required("env-1")
    assert stored.sequence_number == 1  # auto-assigned for the run
    assert stored.event_type == "model_called"
    assert stored.correlation_id == "corr-9"
    assert stored.step_id == "step-1"
    assert stored.payload == {"model_id": "qwen", "metadata": {}}
    assert (
        stored.occurred_at.replace(tzinfo=timezone.utc)
        == envelope.timestamp.astimezone(timezone.utc)
    )

    # Redelivery of the same envelope is a successful no-op.
    assert repo.insert_from_envelope(_envelope("env-1")) == 0

    # An explicit sequence on the envelope wins over auto-allocation.
    assert repo.insert_from_envelope(_envelope("env-2", sequence_number=7)) == 1
    assert repo.get_required("env-2").sequence_number == 7

    assert [event.event_id for event in repo.list_for_run("run-1")] == [
        "env-1",
        "env-2",
    ]


def test_legacy_envelope_without_correlation_fields_maps():
    """Envelopes produced before the alignment must still map cleanly."""
    legacy = SimpleNamespace(
        event_id="legacy-1",
        schema_version="1.0",
        system_id="old-producer",
        system_version=None,
        run_id="run-1",
        event_type=EventType.MODEL_CALLED,
        timestamp=datetime.now(timezone.utc),
        payload={"model_id": "qwen"},
    )

    event = event_from_envelope(legacy, sequence_number=42)

    assert event.event_id == "legacy-1"
    assert event.sequence_number == 42
    assert event.event_type == "model_called"
    assert event.correlation_id is None
    assert event.parent_event_id is None
    assert event.causation_id is None
    assert event.payload == {"model_id": "qwen"}
