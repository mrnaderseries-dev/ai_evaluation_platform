"""DLQ envelope tests.

Covers required test cases 7 (DLQ publish failure) and 13 (replay data for
both a valid event and raw bytes), plus payload and security requirements.
"""

from __future__ import annotations

import base64
import json

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from ai_eval_platform.ingestion.dlq import (
    DeadLetterQueue,
    DlqPublishError,
    FailureInfo,
    FailureStage,
)
from ai_eval_platform.ingestion.event_validator import (
    EventValidator,
    ValidationCode,
    ValidationFailure,
    ValidationStage,
)
from ai_eval_platform.ingestion.kafka_record import KafkaRecord
from ai_eval_platform.ingestion.producer import RetryablePublishError

from .conftest import (
    RecordingProducer,
    make_record,
    valid_event_bytes,
    valid_event_dict,
)


def validation_failure(raw: bytes) -> ValidationFailure:
    return ValidationFailure(
        code=ValidationCode.INVALID_UTF8,
        stage=ValidationStage.DECODE,
        message="bad utf8",
        raw_payload=raw,
    )


def failure_info(**overrides) -> FailureInfo:
    values = {
        "stage": FailureStage.HANDLER,
        "code": "HANDLERERROR",
        "error_type": "HandlerError",
        "message": "permanent failure",
        "attempt": 0,
    }
    values.update(overrides)
    return FailureInfo(**values)


def test_dlq_record_for_validation_failure_keeps_raw_bytes(dlq):
    raw = b"\xff\xfe"

    envelope = dlq.build_record(
        record=make_record(raw),
        failure=failure_info(stage=FailureStage.VALIDATION),
        validation_failure=validation_failure(raw),
    )

    assert envelope.raw is not None
    assert envelope.event is None
    assert base64.b64decode(envelope.raw.raw_payload_base64) == raw
    assert envelope.source.topic == "events"
    assert envelope.source.partition == 0
    assert envelope.source.offset == 0
    assert envelope.failure.code == "INVALID_UTF8"
    assert envelope.dlq_schema_version == "1.0"
    assert envelope.replay_bytes() == raw


def test_dlq_record_for_handler_failure_keeps_event_identity(dlq):
    event = EventValidator().validate(valid_event_bytes()).event

    envelope = dlq.build_record(
        record=make_record(),
        failure=failure_info(attempt=2),
        event=event,
    )

    # Enough data to replay a valid event.
    assert envelope.event is not None
    assert envelope.event.event_id == "evt-1"
    assert envelope.event.run_id == "run-1"
    assert envelope.event.event_type == "model_called"
    assert envelope.raw is None
    assert json.loads(envelope.replay_bytes())["event_id"] == "evt-1"


def test_dlq_key_is_run_id_to_preserve_per_run_ordering(dlq, producer):
    event = EventValidator().validate(valid_event_bytes()).event

    envelope = dlq.build_record(
        record=make_record(),
        failure=failure_info(),
        event=event,
    )
    dlq.publish(envelope)

    # Keyed by run_id, not event_id: a run's DLQ records stay co-partitioned.
    assert producer.published[0]["key"] == b"run-1"


def test_dlq_publish_failure_raises_dlq_publish_error():
    producer = RecordingProducer(fail_with=RetryablePublishError("broker down"))
    dlq = DeadLetterQueue(producer, "events.dlq")

    with pytest.raises(DlqPublishError):
        dlq.publish_failure(
            record=make_record(b"bad"),
            error=ValueError("boom"),
            stage=FailureStage.VALIDATION,
            code="INVALID_JSON",
            attempt=0,
        )
def test_dlq_maps_unexpected_producer_fault_to_publish_error():
    producer = RecordingProducer(fail_with=RuntimeError("totally unexpected"))
    dlq = DeadLetterQueue(producer, "events.dlq")

    # An unexpected producer fault must still surface as DlqPublishError,
    # otherwise a consumer catching only PublishError would miss it.
    with pytest.raises(DlqPublishError):
        dlq.publish_failure(
            record=make_record(b"bad"),
            error=ValueError("boom"),
            stage=FailureStage.VALIDATION,
            code="INVALID_JSON",
            attempt=0,
        )


def test_dlq_oversize_envelope_is_rejected_not_silently_truncated():
    dlq = DeadLetterQueue(
        RecordingProducer(), "events.dlq", max_envelope_bytes=500
    )

    with pytest.raises(DlqPublishError):
        dlq.publish(
            dlq.build_record(
                record=make_record(b"z" * 2_000),
                failure=failure_info(stage=FailureStage.VALIDATION),
                validation_failure=validation_failure(b"z" * 2_000),
            )
        )


def test_dlq_envelope_stays_below_broker_record_limit(dlq, producer):
    raw = b"y" * 400_000

    envelope = dlq.build_record(
        record=make_record(raw),
        failure=failure_info(stage=FailureStage.VALIDATION),
        validation_failure=validation_failure(raw),
    )
    dlq.publish(envelope)

    # Base64 inflates 4/3; the envelope must still fit the broker limit.
    assert envelope.serialized_size() < 1_000_000


def test_dlq_error_message_excludes_sql_and_bound_parameters():
    exc = OperationalError(
        "INSERT INTO events (payload) VALUES (%(payload)s)",
        {"payload": "secret-value"},
        Exception('duplicate key value violates unique constraint "x"'),
    )

    message = DeadLetterQueue._safe_message(exc)

    # No compiled SQL or parameter tuple leaks into the DLQ record.
    assert "INSERT INTO" not in message
    assert "secret-value" not in message


def test_dlq_never_stores_stack_traces(dlq):
    envelope = dlq.build_record(
        record=make_record(b"bad"),
        failure=failure_info(message="plain message"),
    )

    serialized = envelope.serialized().decode("utf-8")

    assert "Traceback" not in serialized
    assert 'File "' not in serialized


def test_dlq_rejects_empty_topic():
    with pytest.raises(ValueError):
        DeadLetterQueue(RecordingProducer(), "")


def test_kafka_record_exposes_transport_position():
    record = KafkaRecord(
        topic="events",
        partition=2,
        offset=99,
        value=valid_event_bytes(),
        key="run-1",
    )

    assert record.position == "events[2]@99"


def test_transport_metadata_is_not_merged_into_domain_envelope():
    from schemas.events.common import AIEvent

    # AIEvent forbids extra fields, which is exactly why topic/partition/
    # offset stay on KafkaRecord instead of being merged into the event.
    with pytest.raises(ValidationError):
        AIEvent.model_validate(
            valid_event_dict(topic="events", partition=0, offset=1)
        )