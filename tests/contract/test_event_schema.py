from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from schemas.events.common import AIEvent, EventType
from schemas.events.execution_events import ModelCalledEvent, ModelOutputEvent


def valid_common_fields() -> dict:
    return {
        "event_id": "event-001",
        "schema_version": "1.0",
        "system_id": "agent-001",
        "system_version": "1.0.0",
        "run_id": "run-001",
        "timestamp": datetime.now(timezone.utc),
    }


def test_model_called_event_is_valid():
    event = ModelCalledEvent(
        **valid_common_fields(),
        payload={"model_id": "qwen2.5:3b"},
    )
    assert event.event_type == EventType.MODEL_CALLED
    assert event.run_id == "run-001"
    assert event.payload.model_id == "qwen2.5:3b"


def test_model_output_event_is_valid():
    event = ModelOutputEvent(
        **valid_common_fields(),
        payload={"model_id": "qwen2.5:3b", "output_id": "o-1", "output_text": "Paris", "latency_ms": 820},
    )
    assert event.event_type == EventType.MODEL_OUTPUT
    assert event.payload.output_text == "Paris"
    assert event.payload.latency_ms == 820


def test_missing_run_id_is_rejected():
    fields = valid_common_fields()
    del fields["run_id"]
    with pytest.raises(ValidationError):
        ModelCalledEvent(**fields, payload={"model_id": "qwen2.5:3b"})


def test_missing_required_payload_field_is_rejected():
    with pytest.raises(ValidationError):
        ModelCalledEvent(**valid_common_fields(), payload={})
    with pytest.raises(ValidationError):
        ModelOutputEvent(
            **valid_common_fields(),
            payload={"model_id": "qwen2.5:3b", "output_text": "Paris"},
        )


def test_wrong_event_type_is_rejected():
    with pytest.raises(ValidationError):
        ModelCalledEvent(
            **valid_common_fields(),
            event_type=EventType.MODEL_OUTPUT,
            payload={"model_id": "qwen2.5:3b"},
        )


def test_extra_top_level_field_is_rejected():
    with pytest.raises(ValidationError):
        ModelCalledEvent(
            **valid_common_fields(),
            payload={"model_id": "qwen2.5:3b"},
            unexpected_field="boom",
        )


def test_naive_timestamp_is_rejected():
    fields = valid_common_fields()
    fields["timestamp"] = datetime.now()
    with pytest.raises(ValidationError):
        ModelCalledEvent(**fields, payload={"model_id": "qwen2.5:3b"})


def test_run_lifecycle_events_are_not_event_types():
    assert not hasattr(EventType, "RUN_STARTED")
    assert not hasattr(EventType, "RUN_FINISHED")


def test_different_payloads_have_different_structures():
    called = ModelCalledEvent(**valid_common_fields(), payload={"model_id": "qwen2.5:3b"})
    output = ModelOutputEvent(
        **valid_common_fields(),
        payload={"model_id": "qwen2.5:3b", "output_id": "o-1", "output_text": "Paris", "latency_ms": 820},
    )
    assert called.payload.model_id == "qwen2.5:3b"
    assert output.payload.output_text == "Paris"
    assert output.payload.latency_ms == 820


def test_common_envelope_fields_are_preserved():
    event = ModelCalledEvent(**valid_common_fields(), payload={"model_id": "qwen2.5:3b"})
    assert event.event_id == "event-001"
    assert event.schema_version == "1.0"
    assert event.system_id == "agent-001"
    assert event.system_version == "1.0.0"
    assert event.run_id == "run-001"
    assert event.timestamp.tzinfo is not None


def test_event_serialization():
    event = ModelOutputEvent(
        **valid_common_fields(),
        payload={"model_id": "qwen2.5:3b", "output_id": "o-1", "output_text": "Paris", "latency_ms": 820},
    )
    data = event.model_dump()
    assert data["event_id"] == "event-001"
    assert data["system_id"] == "agent-001"
    assert data["system_version"] == "1.0.0"
    assert data["run_id"] == "run-001"
    assert data["event_type"] == EventType.MODEL_OUTPUT
    assert "timestamp" in data
    assert data["payload"]["output_text"] == "Paris"


def test_correlation_fields_are_accepted_when_provided():
    """Storage-aligned ordering/correlation fields (models.Event columns)."""
    event = ModelCalledEvent(
        **valid_common_fields(),
        payload={"model_id": "qwen2.5:3b"},
        sequence_number=5,
        parent_event_id="event-000",
        correlation_id="corr-1",
        causation_id="event-000",
        step_id="step-1",
    )
    assert event.sequence_number == 5
    assert event.parent_event_id == "event-000"
    assert event.correlation_id == "corr-1"
    assert event.causation_id == "event-000"
    assert event.step_id == "step-1"


def test_correlation_fields_default_to_none_for_legacy_envelopes():
    event = ModelCalledEvent(**valid_common_fields(), payload={"model_id": "qwen2.5:3b"})
    assert event.sequence_number is None
    assert event.parent_event_id is None
    assert event.correlation_id is None
    assert event.causation_id is None
    assert event.step_id is None


def test_invalid_correlation_fields_are_rejected():
    with pytest.raises(ValidationError):
        ModelCalledEvent(
            **valid_common_fields(),
            payload={"model_id": "qwen2.5:3b"},
            sequence_number=-1,
        )
    with pytest.raises(ValidationError):
        ModelCalledEvent(
            **valid_common_fields(),
            payload={"model_id": "qwen2.5:3b"},
            correlation_id="",
        )
