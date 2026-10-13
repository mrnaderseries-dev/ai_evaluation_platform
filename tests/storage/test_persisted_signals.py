from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError

from ai_eval_platform.monitoring.persistence import signal_to_record
from ai_eval_platform.monitoring.signals.models import Signal
from ai_eval_platform.monitoring.signals.types import (
    ErrorPatternPayload,
    RepeatedToolUsagePayload,
    SignalScope,
    SignalType,
)
from ai_eval_platform.monitoring_integration import (
    persisted_signal_from_record,
    persist_monitoring_signals,
)
from ai_eval_platform.storage.models import (
    Agent,
    AgentVersion,
    Dataset,
    DatasetVersion,
    EvaluationCase,
    EvaluationRun,
    PersistedSignal,
    Run,
)
from ai_eval_platform.storage.repositories import PersistedSignalRepository


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture()
def signal_graph(session):
    session.add(Agent(agent_id="agent-1", name="agent-1"))
    session.add(
        AgentVersion(agent_version_id="av-1", agent_id="agent-1", version="1.0.0")
    )
    session.add(
        Run(
            run_id="run-1",
            agent_id="agent-1",
            agent_version_id="av-1",
            started_at=_utcnow(),
            status="completed",
        )
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
            started_at=_utcnow(),
            status="running",
        )
    )
    session.flush()


def _run_signal(signal_id: str, detected_at: datetime) -> Signal:
    return Signal(
        signal_id=signal_id,
        agent_id="agent-1",
        signal_type=SignalType.REPEATED_TOOL_USAGE,
        scope=SignalScope.RUN,
        run_id="run-1",
        detected_at=detected_at,
        payload=RepeatedToolUsagePayload(
            tool_name="search",
            occurrence_count=2,
            sequence=["search", "search"],
            severity=0.5,
            related_event_ids=["evt-t1", "evt-t2"],
        ),
    )


def _evaluation_signal(signal_id: str, detected_at: datetime) -> Signal:
    return Signal(
        signal_id=signal_id,
        agent_id="agent-1",
        signal_type=SignalType.ERROR_PATTERN,
        scope=SignalScope.EVALUATION,
        evaluation_run_id="erun-1",
        detected_at=detected_at,
        payload=ErrorPatternPayload(error_type="timeout", occurrence_count=2),
    )


def _window_signal(signal_id: str, detected_at: datetime) -> Signal:
    return Signal(
        signal_id=signal_id,
        agent_id="agent-1",
        signal_type=SignalType.ERROR_PATTERN,
        scope=SignalScope.WINDOW,
        window_start=detected_at - timedelta(hours=1),
        window_end=detected_at,
        detected_at=detected_at,
        payload=ErrorPatternPayload(error_type="timeout", occurrence_count=2),
    )


def test_run_scope_signal_roundtrip(session, signal_graph):
    repo = PersistedSignalRepository(session)
    now = _utcnow()

    assert repo.insert_ignore_duplicates(
        [persisted_signal_from_record(signal_to_record(_run_signal("sig-1", now), "pattern"))]
    ) == 1

    row = repo.get("sig-1")
    assert row is not None
    assert row.agent_id == "agent-1"
    assert row.signal_type == "repeated_tool_usage"
    assert row.scope == "run"
    assert row.run_id == "run-1"
    assert row.evaluation_run_id is None
    assert row.window_start is None
    assert row.window_end is None
    assert row.detected_at.replace(tzinfo=timezone.utc) == now
    assert row.payload["tool_name"] == "search"
    assert row.payload["related_event_ids"] == ["evt-t1", "evt-t2"]
    assert row.payload_type == "RepeatedToolUsagePayload"
    assert row.detector_name == "pattern"
    assert row.recorded_at is not None


def test_all_scopes_persist(session, signal_graph):
    repo = PersistedSignalRepository(session)
    now = _utcnow()

    rows = [
        persisted_signal_from_record(signal_to_record(_run_signal("sig-run", now), "pattern")),
        persisted_signal_from_record(signal_to_record(_evaluation_signal("sig-eval", now))),
        persisted_signal_from_record(signal_to_record(_window_signal("sig-win", now))),
    ]

    assert repo.insert_ignore_duplicates(rows) == 3
    assert session.scalar(select(func.count()).select_from(PersistedSignal)) == 3
    assert repo.get("sig-eval").evaluation_run_id == "erun-1"
    assert repo.get("sig-win").window_start is not None


def test_duplicate_signal_is_successful_noop(session, signal_graph):
    repo = PersistedSignalRepository(session)
    now = _utcnow()

    assert repo.insert_ignore_duplicates(
        [persisted_signal_from_record(signal_to_record(_run_signal("sig-dup", now)))]
    ) == 1
    assert repo.insert_ignore_duplicates(
        [persisted_signal_from_record(signal_to_record(_run_signal("sig-dup", now)))]
    ) == 0

    assert session.scalar(select(func.count()).select_from(PersistedSignal)) == 1


def test_duplicate_signal_within_single_batch(session, signal_graph):
    repo = PersistedSignalRepository(session)
    row = persisted_signal_from_record(signal_to_record(_run_signal("sig-b", _utcnow())))

    assert repo.insert_ignore_duplicates([row, row]) == 1
    assert session.scalar(select(func.count()).select_from(PersistedSignal)) == 1


def test_new_detection_of_same_type_is_not_duplicate(session, signal_graph):
    repo = PersistedSignalRepository(session)
    now = _utcnow()

    assert repo.insert_ignore_duplicates(
        [persisted_signal_from_record(signal_to_record(_run_signal("sig-a", now)))]
    ) == 1
    assert repo.insert_ignore_duplicates(
        [persisted_signal_from_record(signal_to_record(_run_signal("sig-b2", now)))]
    ) == 1

    assert session.scalar(select(func.count()).select_from(PersistedSignal)) == 2


def test_foreign_key_definitions_reference_parent_tables(engine):
    table = PersistedSignal.__table__
    targets = {
        column.name: next(iter(column.foreign_keys)).target_fullname
        for column in table.c.values()
        if column.foreign_keys
    }

    assert targets["agent_id"] == "agents.agent_id"
    assert targets["run_id"] == "runs.run_id"
    assert targets["evaluation_run_id"] == "evaluation_runs.evaluation_run_id"


def test_list_for_run_orders_by_detected_at(session, signal_graph):
    repo = PersistedSignalRepository(session)
    base = _utcnow()

    repo.insert_ignore_duplicates(
        [
            persisted_signal_from_record(signal_to_record(_run_signal("sig-3", base + timedelta(seconds=30)))),
            persisted_signal_from_record(signal_to_record(_run_signal("sig-1", base))),
            persisted_signal_from_record(signal_to_record(_run_signal("sig-2", base + timedelta(seconds=10)))),
        ]
    )

    rows = repo.list_for_run("run-1")
    assert [row.signal_id for row in rows] == ["sig-1", "sig-2", "sig-3"]

    page = repo.list_for_run("run-1", limit=2, offset=1)
    assert [row.signal_id for row in page] == ["sig-2", "sig-3"]


def test_list_for_evaluation_run(session, signal_graph):
    repo = PersistedSignalRepository(session)
    now = _utcnow()

    repo.insert_ignore_duplicates(
        [
            persisted_signal_from_record(signal_to_record(_evaluation_signal("sig-e1", now))),
            persisted_signal_from_record(signal_to_record(_evaluation_signal("sig-e2", now))),
            persisted_signal_from_record(signal_to_record(_run_signal("sig-r1", now))),
        ]
    )

    rows = repo.list_for_evaluation_run("erun-1")
    assert [row.signal_id for row in rows] == ["sig-e1", "sig-e2"]


def test_list_for_agent_filters_by_type_and_time(session, signal_graph):
    repo = PersistedSignalRepository(session)
    base = _utcnow()

    repo.insert_ignore_duplicates(
        [
            persisted_signal_from_record(signal_to_record(_run_signal("sig-old", base))),
            persisted_signal_from_record(signal_to_record(_evaluation_signal("sig-new", base + timedelta(hours=2)))),
        ]
    )

    assert [row.signal_id for row in repo.list_for_agent("agent-1")] == ["sig-old", "sig-new"]
    assert [
        row.signal_id
        for row in repo.list_for_agent("agent-1", signal_type="error_pattern")
    ] == ["sig-new"]
    assert [
        row.signal_id
        for row in repo.list_for_agent("agent-1", detected_after=base + timedelta(hours=1))
    ] == ["sig-new"]
    assert [
        row.signal_id
        for row in repo.list_for_agent("agent-1", detected_before=base + timedelta(hours=1))
    ] == ["sig-old"]


def test_agent_isolation_between_queries(session, signal_graph):
    session.add(Agent(agent_id="agent-2", name="agent-2"))
    session.add(
        Run(
            run_id="run-2",
            agent_id="agent-2",
            started_at=_utcnow(),
            status="completed",
        )
    )
    session.flush()

    repo = PersistedSignalRepository(session)
    now = _utcnow()
    first = _run_signal("sig-a1", now)
    second = _run_signal("sig-a2", now)
    second.agent_id = "agent-2"
    second.run_id = "run-2"

    repo.insert_ignore_duplicates(
        [
            persisted_signal_from_record(signal_to_record(first)),
            persisted_signal_from_record(signal_to_record(second)),
        ]
    )

    assert [row.signal_id for row in repo.list_for_agent("agent-1")] == ["sig-a1"]
    assert [row.signal_id for row in repo.list_for_agent("agent-2")] == ["sig-a2"]
    assert [row.signal_id for row in repo.list_for_run("run-2")] == ["sig-a2"]


def test_list_for_window_queries_detected_at_range(session, signal_graph):
    repo = PersistedSignalRepository(session)
    base = _utcnow()

    repo.insert_ignore_duplicates(
        [
            persisted_signal_from_record(signal_to_record(_run_signal("sig-in", base))),
            persisted_signal_from_record(signal_to_record(_run_signal("sig-out", base + timedelta(hours=5)))),
        ]
    )

    rows = repo.list_for_window("agent-1", base - timedelta(hours=1), base + timedelta(hours=1))
    assert [row.signal_id for row in rows] == ["sig-in"]


def test_persist_monitoring_signals_skips_foreign_agent_signals(session, signal_graph):
    from ai_eval_platform.monitoring.orchestration import DetectorRun, MonitoringRun
    from ai_eval_platform.monitoring.orchestration.runner import DetectorOutcome

    monitoring_run = MonitoringRun(
        agent_id="agent-1",
        runs=[
            DetectorRun(
                detector_name="pattern",
                agent_id="agent-1",
                outcome=DetectorOutcome.SIGNALS,
                signals=[_run_signal("sig-own", _utcnow())],
            ),
            DetectorRun(
                detector_name="rogue",
                agent_id="agent-1",
                outcome=DetectorOutcome.SIGNALS,
                signals=[_run_signal("sig-rogue", _utcnow())],
            ),
        ],
    )
    monitoring_run.runs[1].signals[0].agent_id = "agent-2"

    assert persist_monitoring_signals(monitoring_run, session) == 1
    assert PersistedSignalRepository(session).get("sig-own") is not None
    assert PersistedSignalRepository(session).get("sig-rogue") is None


def test_persist_monitoring_signals_empty_run(session):
    from ai_eval_platform.monitoring.orchestration import MonitoringRun

    assert persist_monitoring_signals(MonitoringRun(agent_id="agent-1"), session) == 0


def test_detector_name_recorded_from_monitoring_run(session, signal_graph):
    from ai_eval_platform.monitoring.orchestration import DetectorRun, MonitoringRun
    from ai_eval_platform.monitoring.orchestration.runner import DetectorOutcome

    monitoring_run = MonitoringRun(
        agent_id="agent-1",
        runs=[
            DetectorRun(
                detector_name="pattern",
                agent_id="agent-1",
                outcome=DetectorOutcome.SIGNALS,
                signals=[_run_signal("sig-det", _utcnow())],
            ),
        ],
    )

    assert persist_monitoring_signals(monitoring_run, session) == 1
    assert PersistedSignalRepository(session).get("sig-det").detector_name == "pattern"



def test_repository_does_not_commit(session, seeded_run):
    repo = PersistedSignalRepository(session)

    repo.insert_ignore_duplicates(
        [persisted_signal_from_record(signal_to_record(_run_signal("sig-rb", _utcnow())))]
    )
    assert repo.get("sig-rb") is not None

    session.rollback()

    assert repo.get("sig-rb") is None


def test_insert_empty_list_returns_zero(session):
    assert PersistedSignalRepository(session).insert_ignore_duplicates([]) == 0


def test_scope_shape_check_constraint_rejects_bad_row(session, signal_graph):
    session.add(
        PersistedSignal(
            signal_id="sig-bad",
            agent_id="agent-1",
            signal_type="repeated_tool_usage",
            scope="run",
            run_id=None,
            detected_at=_utcnow(),
            payload={},
            payload_type="RepeatedToolUsagePayload",
        )
    )

    with pytest.raises(IntegrityError):
        session.flush()

    session.rollback()


def test_repository_does_not_commit(session, signal_graph):
    repo = PersistedSignalRepository(session)

    repo.insert_ignore_duplicates(
        [persisted_signal_from_record(signal_to_record(_run_signal("sig-rb", _utcnow())))]
    )
    assert repo.get("sig-rb") is not None

    session.rollback()

    assert repo.get("sig-rb") is None


def test_insert_empty_list_returns_zero(session):
    assert PersistedSignalRepository(session).insert_ignore_duplicates([]) == 0


def _detector_monitoring_run(runs):
    from ai_eval_platform.monitoring.orchestration import DetectorRun, MonitoringRun
    from ai_eval_platform.monitoring.orchestration.runner import DetectorOutcome

    return MonitoringRun(
        agent_id="agent-1",
        runs=[
            DetectorRun(
                detector_name=detector_name,
                agent_id="agent-1",
                outcome=DetectorOutcome.SIGNALS,
                signals=list(signals),
            )
            for detector_name, signals in runs
        ],
    )


def test_re_emitted_logical_detection_is_not_duplicated(session, signal_graph):
    repo = PersistedSignalRepository(session)

    assert (
        persist_monitoring_signals(
            _detector_monitoring_run([("pattern", [_run_signal("sig-retry-a", _utcnow())])]),
            session,
        )
        == 1
    )

    re_emitted = _run_signal("sig-retry-b", _utcnow() + timedelta(seconds=45))
    assert (
        persist_monitoring_signals(
            _detector_monitoring_run([("pattern", [re_emitted])]),
            session,
        )
        == 0
    )

    rows = repo.list_for_run("run-1")
    assert len(rows) == 1
    assert rows[0].signal_id == "sig-retry-a"
    assert rows[0].dedupe_key is not None


def test_distinct_detections_survive_logical_dedupe(session, signal_graph):
    session.add(
        Run(
            run_id="run-2",
            agent_id="agent-1",
            agent_version_id="av-1",
            started_at=_utcnow(),
            status="completed",
        )
    )
    session.flush()

    now = _utcnow()
    duplicate_a = _run_signal("sig-ord-a", now)
    duplicate_b = _run_signal("sig-ord-b", now)

    different_tool = _run_signal("sig-tool", now)
    different_tool.payload = RepeatedToolUsagePayload(
        tool_name="read",
        occurrence_count=2,
        sequence=["read", "read"],
        severity=0.5,
        related_event_ids=["evt-r1", "evt-r2"],
    )

    other_run = _run_signal("sig-run2", now)
    other_run.run_id = "run-2"

    window_a = _window_signal("sig-window-a", now)
    window_b = _window_signal("sig-window-b", now + timedelta(hours=2))
    shadow = _run_signal("sig-shadow", now)

    monitoring_run = _detector_monitoring_run(
        [
            (
                "pattern",
                [duplicate_a, duplicate_b, different_tool, other_run, window_a, window_b],
            ),
            ("shadow", [shadow]),
        ]
    )

    assert persist_monitoring_signals(monitoring_run, session) == 7
    assert session.scalar(select(func.count()).select_from(PersistedSignal)) == 7

    tool_again = _run_signal("sig-tool-2", now + timedelta(seconds=30))
    tool_again.payload = different_tool.payload
    run_again = _run_signal("sig-run2-2", now + timedelta(seconds=30))
    run_again.run_id = "run-2"

    re_emitted = _detector_monitoring_run(
        [
            (
                "pattern",
                [
                    _run_signal("sig-ord-a2", now + timedelta(seconds=30)),
                    _run_signal("sig-ord-b2", now + timedelta(seconds=30)),
                    tool_again,
                    run_again,
                    _window_signal("sig-window-a2", now),
                    _window_signal("sig-window-b2", now + timedelta(hours=2)),
                ],
            ),
            ("shadow", [_run_signal("sig-shadow2", now + timedelta(seconds=30))]),
        ]
    )

    assert persist_monitoring_signals(re_emitted, session) == 0
    assert session.scalar(select(func.count()).select_from(PersistedSignal)) == 7

    rows = PersistedSignalRepository(session).list_for_run("run-1")
    window_rows = [
        row for row in session.scalars(select(PersistedSignal)) if row.scope == "window"
    ]
    keys = [row.dedupe_key for row in session.scalars(select(PersistedSignal))]

    assert len({row.signal_id for row in rows}) == 4
    assert len(window_rows) == 2
    assert len(keys) == len(set(keys))
    assert all(key is not None for key in keys)


def test_dedupe_key_conflict_is_skipped_and_index_enforced(session, signal_graph):
    repo = PersistedSignalRepository(session)

    row_a = persisted_signal_from_record(signal_to_record(_run_signal("sig-uk-a", _utcnow())))
    row_a.dedupe_key = "dedupe-shared"
    row_b = persisted_signal_from_record(signal_to_record(_run_signal("sig-uk-b", _utcnow())))
    row_b.dedupe_key = "dedupe-shared"

    assert repo.insert_ignore_duplicates([row_a]) == 1
    assert repo.insert_ignore_duplicates([row_b]) == 0
    assert session.scalar(select(func.count()).select_from(PersistedSignal)) == 1

    with pytest.raises(IntegrityError):
        session.execute(
            insert(PersistedSignal).values(
                signal_id="sig-uk-c",
                agent_id="agent-1",
                signal_type="repeated_tool_usage",
                scope="run",
                run_id="run-1",
                detected_at=_utcnow(),
                payload={},
                payload_type="RepeatedToolUsagePayload",
                dedupe_key="dedupe-shared",
            )
        )

    session.rollback()


def test_list_for_run_applies_agent_filter_before_limit(session, signal_graph):
    session.add(Agent(agent_id="agent-2", name="agent-2"))
    session.add(
        AgentVersion(agent_version_id="av-2", agent_id="agent-2", version="1.0.0")
    )
    session.flush()

    repo = PersistedSignalRepository(session)
    base = _utcnow()
    rows = [
        PersistedSignal(
            signal_id=f"foreign-{i}",
            agent_id="agent-2",
            signal_type="repeated_tool_usage",
            scope="run",
            run_id="run-1",
            detected_at=base + timedelta(seconds=i),
            payload={},
            payload_type="RepeatedToolUsagePayload",
        )
        for i in range(3)
    ]
    rows += [
        PersistedSignal(
            signal_id=f"own-{i}",
            agent_id="agent-1",
            signal_type="repeated_tool_usage",
            scope="run",
            run_id="run-1",
            detected_at=base + timedelta(seconds=100 + i),
            payload={},
            payload_type="RepeatedToolUsagePayload",
        )
        for i in range(3)
    ]
    assert repo.insert_ignore_duplicates(rows) == 6

    page = repo.list_for_run("run-1", agent_id="agent-1", limit=4)
    assert [row.signal_id for row in page] == ["own-0", "own-1", "own-2"]
    assert {row.agent_id for row in page} == {"agent-1"}

    assert repo.list_for_run("run-1", agent_id="agent-3", limit=4) == []


def test_list_for_run_without_agent_filter_paginates_all_agents(session, signal_graph):
    session.add(Agent(agent_id="agent-2", name="agent-2"))
    session.add(
        AgentVersion(agent_version_id="av-2", agent_id="agent-2", version="1.0.0")
    )
    session.flush()

    repo = PersistedSignalRepository(session)
    base = _utcnow()
    repo.insert_ignore_duplicates(
        [
            PersistedSignal(
                signal_id=f"f-{i}",
                agent_id="agent-2",
                signal_type="repeated_tool_usage",
                scope="run",
                run_id="run-1",
                detected_at=base + timedelta(seconds=i),
                payload={},
                payload_type="RepeatedToolUsagePayload",
            )
            for i in range(3)
        ]
        + [
            PersistedSignal(
                signal_id="o-0",
                agent_id="agent-1",
                signal_type="repeated_tool_usage",
                scope="run",
                run_id="run-1",
                detected_at=base + timedelta(seconds=3),
                payload={},
                payload_type="RepeatedToolUsagePayload",
            )
        ]
    )

    assert [row.signal_id for row in repo.list_for_run("run-1", limit=4)] == [
        "f-0",
        "f-1",
        "f-2",
        "o-0",
    ]


def test_list_for_evaluation_run_applies_agent_filter(session, signal_graph):
    session.add(Agent(agent_id="agent-2", name="agent-2"))
    session.add(
        AgentVersion(agent_version_id="av-2", agent_id="agent-2", version="1.0.0")
    )
    session.flush()

    repo = PersistedSignalRepository(session)
    base = _utcnow()
    repo.insert_ignore_duplicates(
        [
            PersistedSignal(
                signal_id=f"e-{i}",
                agent_id="agent-1" if i < 2 else "agent-2",
                signal_type="repeated_tool_usage",
                scope="evaluation",
                evaluation_run_id="erun-1",
                detected_at=base + timedelta(seconds=i),
                payload={},
                payload_type="RepeatedToolUsagePayload",
            )
            for i in range(4)
        ]
    )

    page = repo.list_for_evaluation_run("erun-1", agent_id="agent-1", limit=4)
    assert [row.signal_id for row in page] == ["e-0", "e-1"]
    assert repo.list_for_evaluation_run("erun-1", agent_id="agent-3") == []

