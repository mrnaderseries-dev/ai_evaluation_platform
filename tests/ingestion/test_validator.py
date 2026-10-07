"""Validator and size-limit tests.

Covers required test cases 2 (invalid UTF-8), 3 (invalid JSON), 4 (schema
failure / machine-readable code) and 11 (oversized payload).
"""

from __future__ import annotations

import json

import pytest

from ai_eval_platform.ingestion.event_validator import (
    EventValidator,
    ValidationCode,
    ValidationFailure,
    ValidationStage,
)

from .conftest import make_record, valid_event_bytes, valid_event_dict


def test_valid_event_returns_success():
    result = EventValidator().validate(valid_event_bytes())

    assert result.event.event_id == "evt-1"
    assert result.event.run_id == "run-1"
    assert result.raw_payload == valid_event_bytes()


def test_invalid_utf8_preserves_raw_bytes():
    raw = b"\xff\xfe\x00bad"

    result = EventValidator().validate(raw)

    assert isinstance(result, ValidationFailure)
    assert result.code is ValidationCode.INVALID_UTF8
    assert result.stage is ValidationStage.DECODE
    # The exact original bytes must survive for the DLQ.
    assert result.raw_payload == raw


def test_invalid_json_preserves_raw_bytes():
    raw = b"{not valid json"

    result = EventValidator().validate(raw)

    assert isinstance(result, ValidationFailure)
    assert result.code is ValidationCode.INVALID_JSON
    assert result.stage is ValidationStage.PARSE
    assert result.raw_payload == raw


def test_non_object_json_is_rejected():
    result = EventValidator().validate(b"[1,2,3]")

    assert result.code is ValidationCode.NOT_A_JSON_OBJECT


def test_schema_validation_failure_has_machine_readable_code():
    raw = json.dumps({"event_id": "evt-1"}).encode("utf-8")

    result = EventValidator().validate(raw)

    assert isinstance(result, ValidationFailure)
    assert result.code is ValidationCode.INVALID_ENVELOPE
    assert result.raw_payload == raw


def test_concrete_schema_violation_is_distinct_from_envelope_error():
    event = valid_event_dict(payload={"wrong_field": "x"})

    result = EventValidator().validate(json.dumps(event).encode("utf-8"))

    assert result.code is ValidationCode.SCHEMA_VALIDATION_ERROR


def test_unsupported_schema_version_is_reported():
    validator = EventValidator(supported_schema_versions={"1.0"})

    result = validator.validate(
        json.dumps(valid_event_dict(schema_version="9.9")).encode("utf-8")
    )

    assert result.code is ValidationCode.UNSUPPORTED_SCHEMA_VERSION


def test_validator_never_raises_on_malformed_input():
    validator = EventValidator()

    for raw in (b"", b"\x00", b"{", b"[]", b"null", b'"str"'):
        result = validator.validate(raw)
        assert isinstance(result, ValidationFailure), raw


def test_oversized_payload_is_rejected_and_truncated_not_dropped():
    validator = EventValidator(max_message_bytes=1_000)

    result = validator.validate(b"x" * 5_000)

    assert result.code is ValidationCode.MESSAGE_TOO_LARGE
    # It must not silently disappear, and what is kept must stay small
    # enough that a DLQ envelope around it is still publishable.
    assert len(result.raw_payload) == 1_000


def test_validator_rejects_limit_that_cannot_be_dead_lettered():
    # Guards the size-inversion defect: a record accepted at the limit must
    # still fit in a DLQ envelope after base64 inflation (4/3). Otherwise an
    # oversize poison record could never be dead-lettered and, under the
    # no-commit-on-DLQ-failure rule, would be redelivered forever.
    with pytest.raises(ValueError):
        EventValidator(max_message_bytes=1_048_576)

    with pytest.raises(ValueError):
        EventValidator(max_message_bytes=900_000)


def test_maximum_accepted_record_fits_in_a_dlq_envelope(dlq):
    # The invariant itself: a maximum-size accepted record must produce a
    # publishable envelope. This is the case that used to loop forever.
    validator = EventValidator()
    raw = b"x" * validator.max_message_bytes

    # At the limit, so not rejected for size - but still dead-letterable.
    result = validator.validate(raw)
    assert isinstance(result, ValidationFailure)

    envelope = dlq.build_record(
        record=make_record(raw),
        failure=dlq_failure_info(),
        validation_failure=result,
    )

    assert envelope.serialized_size() < 1_000_000


def dlq_failure_info():
    from ai_eval_platform.ingestion.dlq import FailureInfo, FailureStage

    return FailureInfo(
        stage=FailureStage.VALIDATION,
        code="MESSAGE_TOO_LARGE",
        error_type="ValidationFailure",
        message="too large",
        attempt=0,
    )