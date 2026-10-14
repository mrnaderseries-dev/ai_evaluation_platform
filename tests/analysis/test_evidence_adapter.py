from datetime import datetime, timedelta, timezone

import pytest

from ai_eval_platform.analysis.evidence_adapter import (
    EvidenceAdaptationError,
    EvidenceAdapter,
)
from ai_eval_platform.storage.models import PersistedEvidenceRecord


def _persisted(**overrides):
    values = {
        "evidence_id": "ev-1",
        "source": "signal",
        "source_ref": "source-1",
        "evidence_kind": "quantitative",
        "domain": "monitoring",
        "subject_type": "agent",
        "subject_id": "agent-1",
        "agent_id": "agent-1",
        "system_id": "system-1",
        "system_version": "v1",
        "run_id": "run-1",
        "evaluation_run_id": None,
        "trace_id": "trace-1",
        "span_id": "span-1",
        "parent_span_id": "parent-1",
        "component_id": "retriever",
        "observed_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "attributes": {
            "metric_name": "latency",
            "metric_value": 12.0,
            "metric_unit": "ms",
            "aggregation": "time_series",
        },
        "limitations": ["source limitation"],
        "source_snapshot": {"event_version": "1.2", "payload": {"x": 1}},
        "created_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return PersistedEvidenceRecord(**values)


def test_adapter_preserves_identity_scope_attributes_and_source_provenance():
    persisted = _persisted()

    adapted = EvidenceAdapter().adapt(persisted)

    assert adapted.evidence_id == persisted.evidence_id
    assert adapted.source_ref == persisted.source_ref
    assert adapted.source == "signal"
    assert adapted.evidence_kind == "quantitative"
    assert adapted.domain == "monitoring"
    assert adapted.subject_type == "agent"
    assert adapted.subject_id == "agent-1"
    assert adapted.agent_id == "agent-1"
    assert adapted.system_id == "system-1"
    assert adapted.system_version == "v1"
    assert adapted.run_id == "run-1"
    assert adapted.evaluation_run_id is None
    assert adapted.trace_id == "trace-1"
    assert adapted.span_id == "span-1"
    assert adapted.parent_span_id == "parent-1"
    assert adapted.component_id == "retriever"
    assert adapted.observed_at == persisted.observed_at
    assert adapted.persisted_at == persisted.created_at
    assert adapted.attributes == persisted.attributes
    assert adapted.source_snapshot == persisted.source_snapshot
    assert adapted.limitations == ("source limitation",)


def test_adapter_preserves_aware_non_utc_timestamp():
    zone = timezone(timedelta(hours=3))
    persisted = _persisted(observed_at=datetime(2026, 1, 1, tzinfo=zone))

    adapted = EvidenceAdapter().adapt(persisted)

    assert adapted.observed_at.tzinfo == zone


def test_adapter_rejects_naive_timestamps_without_backend_policy():
    with pytest.raises(EvidenceAdaptationError, match="timezone-aware"):
        EvidenceAdapter().adapt(
            _persisted(observed_at=datetime(2026, 1, 1))
        )


def test_adapter_restores_sqlite_naive_timestamp_as_utc_when_configured():
    adapted = EvidenceAdapter(
        naive_timestamp_timezone=timezone.utc
    ).adapt(_persisted(observed_at=datetime(2026, 1, 1)))

    assert adapted.observed_at == datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("source", "unknown", "source must be one of"),
        ("evidence_kind", "unknown", "evidence_kind must be one of"),
        ("source_ref", "   ", "EvidenceRecord contract"),
        ("agent_id", None, "EvidenceRecord contract"),
        ("observed_at", None, "observed_at must be a datetime"),
    ],
)
def test_adapter_rejects_unmappable_required_fields(field, value, message):
    with pytest.raises(EvidenceAdaptationError, match=message):
        EvidenceAdapter().adapt(_persisted(**{field: value}))


def test_adapter_rejects_non_mapping_attributes_or_snapshot():
    with pytest.raises(EvidenceAdaptationError, match="attributes must be"):
        EvidenceAdapter().adapt(_persisted(attributes=None))

    with pytest.raises(EvidenceAdaptationError, match="source_snapshot must be"):
        EvidenceAdapter().adapt(_persisted(source_snapshot=None))