from datetime import datetime, timezone

import pytest

from ai_eval_platform.statistics import EventTelemetryStatisticsService
from ai_eval_platform.storage.models import Event, Run


def _event(event_id, run_id, sequence_number, event_type, payload):
    return Event(
        event_id=event_id,
        run_id=run_id,
        sequence_number=sequence_number,
        event_type=event_type,
        occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        payload=payload,
    )


def test_event_telemetry_summarizes_only_explicit_numeric_fields_per_run(
    session,
    seeded_run,
):
    session.add_all(
        [
            _event(
                "model-output-1",
                seeded_run,
                1,
                "model_output",
                {
                    "latency_ms": 10,
                    "input_tokens": 5,
                    "output_tokens": 2,
                    "total_tokens": 7,
                },
            ),
            _event(
                "model-output-2",
                seeded_run,
                2,
                "model_output",
                {"latency_ms": 30, "input_tokens": 7},
            ),
            _event(
                "tool-completed-1",
                seeded_run,
                3,
                "tool_completed",
                {"latency_ms": 4, "success": True},
            ),
            _event(
                "retrieval-completed-1",
                seeded_run,
                4,
                "retrieval_completed",
                {"latency_ms": 20, "result_count": 3},
            ),
            _event(
                "context-assembled-1",
                seeded_run,
                5,
                "context_assembled",
                {"resource_count": 4, "token_count": 120},
            ),
            _event(
                "irrelevant-error-1",
                seeded_run,
                6,
                "error",
                {"latency_ms": 100000},
            ),
        ]
    )
    session.add(
        Run(
            run_id="other-run",
            agent_id="agent-1",
            agent_version_id="av-1",
            status="completed",
            started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    )
    session.add(
        _event(
            "model-output-other-run",
            "other-run",
            1,
            "model_output",
            {"latency_ms": 999},
        )
    )
    session.flush()

    summaries = EventTelemetryStatisticsService(session).summarize_run(seeded_run)
    by_key = {
        (summary.event_type, summary.metric_name): summary
        for summary in summaries
    }

    model_latency = by_key[("model_output", "latency_ms")]
    assert model_latency.observation_count == 2
    assert model_latency.statistics.mean == pytest.approx(20.0)
    assert model_latency.source_event_ids == (
        "model-output-1",
        "model-output-2",
    )
    assert any("No significance" in item for item in model_latency.limitations)
    assert by_key[("tool_completed", "latency_ms")].statistics.mean == 4.0
    assert by_key[("retrieval_completed", "result_count")].statistics.mean == 3.0
    assert by_key[("context_assembled", "token_count")].statistics.mean == 120.0
    assert ("error", "latency_ms") not in by_key


def test_event_telemetry_excludes_invalid_and_missing_values(session, seeded_run):
    session.add(
        _event(
            "model-output-invalid",
            seeded_run,
            1,
            "model_output",
            {"latency_ms": True},
        )
    )
    session.flush()

    summaries = EventTelemetryStatisticsService(session).summarize_run(seeded_run)
    latency = next(
        item
        for item in summaries
        if item.event_type == "model_output" and item.metric_name == "latency_ms"
    )

    assert latency.observation_count == 0
    assert latency.excluded_count == 1
    assert latency.statistics is None
    assert any("missing or invalid" in item for item in latency.limitations)


def test_event_telemetry_rejects_blank_run_id(session):
    with pytest.raises(ValueError, match="run_id"):
        EventTelemetryStatisticsService(session).summarize_run("  ")