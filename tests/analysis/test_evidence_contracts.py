"""Contract tests for the Evidence record / collection / relationship models.

Pins pydantic-level validation rules that the analysis pipeline relies on:
timezone-aware timestamps, non-empty identifiers, trace/span dependency
chains, and unique evidence ids. Deliberately uses only constructed model
instances - no repository involvement.
"""
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from ai_eval_platform.analysis.evidence import EvidenceCollection, EvidenceRecord
from ai_eval_platform.analysis.relationship_analyzer import EvidenceRelationship as AnalyzerEvidenceRelationship


def _record(**kw):
    base = dict(
        evidence_id="s1",
        source="signal",
        source_ref="r1",
        evidence_kind="observation",
        agent_id="a1",
        run_id="r1",
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    base.update(kw)
    return EvidenceRecord(**base)


class TestEvidenceRecord:
    def test_required_fields_accepted(self):
        record = _record(attributes={"user": "u", "count": 3})
        assert record.attributes == {"user": "u", "count": 3}

    def test_empty_or_whitespace_required_id_rejected(self):
        for field in ("evidence_id", "source_ref", "agent_id"):
            with pytest.raises(ValidationError, match="Value must not be empty"):
                _record(**{field: "   "})

    def test_naive_timestamp_rejected(self):
        with pytest.raises(ValidationError, match="timezone-aware"):
            _record(observed_at=datetime(2026, 1, 1))

    def test_aware_timestamp_accepted(self):
        record = _record(observed_at=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc))
        assert record.observed_at == datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

    def test_parent_span_without_span_rejected(self):
        with pytest.raises(ValidationError, match="parent_span_id requires span_id"):
            _record(parent_span_id="p-1")

    def test_span_without_trace_rejected(self):
        with pytest.raises(ValidationError, match="span_id requires trace_id"):
            _record(span_id="s-1")

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("trace_id", ""),
            ("trace_id", "  "),
            ("span_id", ""),
            ("span_id", "  "),
            ("parent_span_id", ""),
            ("parent_span_id", "  "),
        ],
    )
    def test_blank_trace_identifiers_rejected(self, field, value):
        with pytest.raises(ValidationError, match="must not be blank"):
            _record(**{field: value})

    def test_self_parent_rejected(self):
        with pytest.raises(ValidationError, match="cannot be its own parent"):
            _record(trace_id="t-1", span_id="s-1", parent_span_id="s-1")

    def test_optional_fields_default_to_none(self):
        record = _record()
        assert record.system_id is None
        assert record.system_version is None
        assert record.evaluation_run_id is None
        assert record.component_id is None
        assert record.attributes == {}

    def test_attributes_are_copied(self):
        attrs = {"k": 1}
        record = _record(attributes=attrs)
        record.attributes["k"] = 2
        assert attrs == {"k": 1}

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            _record(unknown_field="x")


class TestEvidenceCollection:
    def test_empty_collection_valid(self):
        collection = EvidenceCollection(records=())
        assert collection.records == ()

    def test_unique_ids_required(self):
        records = (_record(evidence_id="a"), _record(evidence_id="a"))
        with pytest.raises(ValidationError, match="unique"):
            EvidenceCollection(records=records)

    def test_unique_ids_across_mixed_records(self):
        records = (_record(evidence_id="a"), _record(evidence_id="b"), _record(evidence_id="c"))
        collection = EvidenceCollection(records=records)
        assert len(collection.records) == 3

    def test_record_count_preserved(self):
        records = (_record(evidence_id="a"), _record(evidence_id="b"))
        collection = EvidenceCollection(records=records)
        assert len(collection.records) == 2
        assert collection.limitations == ()


class TestEvidenceRelationship:
    def _make(self, evidence_ids, **kw):
        base = dict(
            relationship_id="temporal:s1:s2",
            relationship_type="temporal",
            basis="temporal",
            agent_id="a1",
            evidence_ids=evidence_ids,
            observed_at=tuple(
                "2026-01-01T00:00:00+00:00"
                for _ in evidence_ids
            ),
        )
        base.update(kw)
        return AnalyzerEvidenceRelationship(**base)

    def test_two_unique_ids_valid(self):
        rel = self._make(("s1", "s2"))
        assert rel.evidence_ids == ("s1", "s2")

    def test_one_id_rejected(self):
        with pytest.raises(ValidationError, match="too_short"):
            self._make(("s1",))

    def test_duplicate_ids_rejected(self):
        with pytest.raises(ValidationError, match="unique"):
            self._make(("s1", "s1"))

    def test_empty_identifier_rejected(self):
        with pytest.raises(ValidationError, match="non-empty"):
            self._make(("s1", ""))

    def test_blank_whitespace_identifier_rejected(self):
        with pytest.raises(ValidationError, match="non-empty"):
            self._make(("s1", "  "))
