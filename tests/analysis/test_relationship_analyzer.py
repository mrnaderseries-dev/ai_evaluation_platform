"""Behavioral contract tests for RelationshipAnalyzer.

Each test maps to a rule in RelationshipAnalyzer._find_temporal /
_find_execution_parent / _find_repeated_patterns / _find_cumulative and to
the pydantic rules of EvidenceRelationship. No repository involved.
"""
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from ai_eval_platform.analysis.evidence import EvidenceCollection, EvidenceRecord
from ai_eval_platform.analysis.relationship_analyzer import (
    EvidenceRelationship as AnalyzerEvidenceRelationship,
    RelationshipAnalyzer,
)

FIXED = timezone.utc


def _temporal(observed_at, evidence_id, **kw):
    base = dict(
        evidence_id=evidence_id, source="signal", source_ref="r", evidence_kind="observation",
        agent_id="a1", run_id="r1", observed_at=observed_at, attributes={"kind": "temporal"},
    )
    base.update(kw)
    return EvidenceRecord(**base)


def _trace(observed_at, evidence_id, **kw):
    base = dict(
        evidence_id=evidence_id, source="trace", source_ref="t", evidence_kind="execution",
        agent_id="a1", run_id="r1", observed_at=observed_at, attributes={},
    )
    base.update(kw)
    return EvidenceRecord(**base)


def _pattern(observed_at, evidence_id, **kw):
    base = dict(
        evidence_id=evidence_id, source="metric", source_ref="m", evidence_kind="quantitative",
        agent_id="a1", run_id="r1", observed_at=observed_at, attributes={"pattern_signature": "load"},
    )
    base.update(kw)
    return EvidenceRecord(**base)


def _cumulative(observed_at, evidence_id, metric_value, **kw):
    base = dict(
        evidence_id=evidence_id, source="metric", source_ref="m", evidence_kind="quantitative",
        agent_id="a1", component_id="cpu", run_id="r1", observed_at=observed_at,
        attributes={"metric_name": "cpu", "metric_value": metric_value, "aggregation": "time_series"},
    )
    base.update(kw)
    return EvidenceRecord(**base)


analyzer = RelationshipAnalyzer()


def _dt(offset):
    return datetime(2026, 1, 1, tzinfo=FIXED) + offset

# ---------------------------------------------------------------------------
# Temporal
# ---------------------------------------------------------------------------
class TestTemporalRelationships:
    def test_within_gap_added(self):
        analyzer = RelationshipAnalyzer(max_temporal_gap_seconds=300.0)
        records = (
            _temporal(_dt(timedelta(seconds=0)), "s1"),
            _temporal(_dt(timedelta(seconds=100)), "s2"),
        )
        rels = analyzer.analyze(EvidenceCollection(records=records))

        assert len(rels) == 1
        rel = rels[0]
        assert isinstance(rel, AnalyzerEvidenceRelationship)
        assert rel.relationship_type == "temporal"
        assert rel.attributes["gap_seconds"] == 100
        assert rel.relationship_id == "temporal:s1:s2"

    def test_gap_boundary_included(self):
        analyzer = RelationshipAnalyzer(max_temporal_gap_seconds=300.0)
        records = (
            _temporal(_dt(timedelta(seconds=0)), "s1"),
            _temporal(_dt(timedelta(seconds=300)), "s2"),
        )
        rels = analyzer.analyze(EvidenceCollection(records=records))
        assert len(rels) == 1

    def test_exceeding_gap_skipped(self):
        analyzer = RelationshipAnalyzer(max_temporal_gap_seconds=300.0)
        records = (
            _temporal(_dt(timedelta(seconds=0)), "s1"),
            _temporal(_dt(timedelta(seconds=301)), "s2"),
        )
        assert analyzer.analyze(EvidenceCollection(records=records)) == ()

    def test_different_agents_skipped(self):
        records = (
            _temporal(_dt(timedelta(seconds=0)), "s1", agent_id="a1"),
            _temporal(_dt(timedelta(seconds=10)), "s2", agent_id="a2"),
        )
        assert analyzer.analyze(EvidenceCollection(records=records)) == ()

    def test_different_runs_skipped(self):
        records = (
            _temporal(_dt(timedelta(seconds=0)), "s1", run_id="r1"),
            _temporal(_dt(timedelta(seconds=10)), "s2", run_id="r2"),
        )
        assert analyzer.analyze(EvidenceCollection(records=records)) == ()

    def test_zero_gap_skipped(self):
        records = (
            _temporal(_dt(timedelta(seconds=0)), "s1"),
            _temporal(_dt(timedelta(seconds=0)), "s2"),
        )
        assert analyzer.analyze(EvidenceCollection(records=records)) == ()


# ---------------------------------------------------------------------------
# Execution parent
# ---------------------------------------------------------------------------
class TestExecutionParent:
    def test_direct_parent_child_pair(self):
        analyzer = RelationshipAnalyzer()
        parent = _trace(
            _dt(timedelta(seconds=-3600)),
            "s1",
            trace_id="T1",
            span_id="P1",
        )
        child = _trace(
            _dt(timedelta(seconds=0)),
            "s2",
            trace_id="T1",
            span_id="S2",
            parent_span_id="P1",
        )
        rels = analyzer.analyze(EvidenceCollection(records=(parent, child)))

        assert len(rels) == 1
        rel = rels[0]
        assert rel.relationship_type == "execution_parent"
        assert rel.evidence_ids == ("s1", "s2")
        assert rel.attributes["trace_id"] == "T1"
        assert rel.attributes["parent_span_id"] == "P1"
        assert rel.attributes["child_span_id"] == "S2"

    def test_child_missing_trace_or_parent_span_skipped(self):
        analyzer = RelationshipAnalyzer()
        parent = _trace(
            _dt(timedelta(seconds=-3600)),
            "s1",
            trace_id="T1",
            span_id="P1",
        )
        child = _trace(
            _dt(timedelta(seconds=0)),
            "s2",
            trace_id=None,
            span_id=None,
            parent_span_id=None,
        )
        assert analyzer.analyze(EvidenceCollection(records=(parent, child))) == ()

    def test_parent_without_matching_child_skipped(self):
        analyzer = RelationshipAnalyzer()
        parent = _trace(
            _dt(timedelta(seconds=-3600)),
            "s1",
            trace_id="T1",
            span_id="P1",
        )
        child = _trace(
            _dt(timedelta(seconds=0)),
            "s2",
            trace_id="T1",
            span_id="S2",
            parent_span_id="P9",
        )
        assert analyzer.analyze(EvidenceCollection(records=(parent, child))) == ()

    def test_mismatched_agents_skipped(self):
        analyzer = RelationshipAnalyzer()
        parent = _trace(
            _dt(timedelta(seconds=-3600)),
            "s1",
            trace_id="T1",
            span_id="P1",
            agent_id="a1",
        )
        child = _trace(
            _dt(timedelta(seconds=0)),
            "s2",
            trace_id="T1",
            span_id="S2",
            parent_span_id="P1",
            agent_id="a2",
        )
        assert analyzer.analyze(EvidenceCollection(records=(parent, child))) == ()


# ---------------------------------------------------------------------------
# Repeated pattern
# ---------------------------------------------------------------------------
class TestRepeatedPattern:
    def test_across_two_runs(self):
        analyzer = RelationshipAnalyzer()
        records = (
            _pattern(_dt(timedelta(seconds=0)), "s1", run_id="r1"),
            _pattern(_dt(timedelta(seconds=1)), "s2", run_id="r2"),
        )
        rels = analyzer.analyze(EvidenceCollection(records=records))

        assert len(rels) == 1
        assert rels[0].relationship_type == "repeated_pattern"
        assert rels[0].attributes["pattern_signature"] == "load"
        assert rels[0].attributes["distinct_run_count"] == 2

    def test_same_run_skipped(self):
        records = (
            _pattern(_dt(timedelta(seconds=0)), "s1", run_id="r1"),
            _pattern(_dt(timedelta(seconds=1)), "s2", run_id="r1"),
        )
        rels = analyzer.analyze(EvidenceCollection(records=records))
        assert not any(r.relationship_type == "repeated_pattern" for r in rels)

    def test_missing_pattern_signature_skipped(self):
        records = (
            _temporal(_dt(timedelta(seconds=0)), "s1"),
            _temporal(_dt(timedelta(seconds=1)), "s2"),
        )
        rels = analyzer.analyze(EvidenceCollection(records=records))
        assert not any(r.relationship_type == "repeated_pattern" for r in rels)


# ---------------------------------------------------------------------------
# Cumulative
# ---------------------------------------------------------------------------
class TestCumulative:
    def test_strictly_increasing_three_points(self):
        analyzer = RelationshipAnalyzer()
        records = (
            _cumulative(_dt(timedelta(seconds=0)), "s1", 1.0),
            _cumulative(_dt(timedelta(seconds=400)), "s2", 2.0),
            _cumulative(_dt(timedelta(seconds=800)), "s3", 3.0),
        )
        rels = analyzer.analyze(EvidenceCollection(records=records))

        assert len(rels) == 1
        rel = rels[0]
        assert rel.relationship_type == "cumulative"
        assert rel.attributes["metric_name"] == "cpu"
        assert rel.attributes["component_id"] == "cpu"
        assert rel.attributes["first_value"] == 1.0
        assert rel.attributes["last_value"] == 3.0
        assert rel.attributes["increase"] == 2.0
        assert rel.attributes["observation_count"] == 3

    def test_non_increasing_skipped(self):
        analyzer = RelationshipAnalyzer()
        records = (
            _cumulative(_dt(timedelta(seconds=0)), "s1", 3.0),
            _cumulative(_dt(timedelta(seconds=400)), "s2", 2.0),
            _cumulative(_dt(timedelta(seconds=800)), "s3", 1.0),
        )
        assert analyzer.analyze(EvidenceCollection(records=records)) == ()

    def test_fewer_than_three_records_skipped(self):
        analyzer = RelationshipAnalyzer()
        records = (
            _cumulative(_dt(timedelta(seconds=0)), "s1", 1.0),
            _cumulative(_dt(timedelta(seconds=400)), "s2", 2.0),
        )
        assert analyzer.analyze(EvidenceCollection(records=records)) == ()

    def test_boolean_metric_value_skipped(self):
        analyzer = RelationshipAnalyzer()
        records = (
            _cumulative(_dt(timedelta(seconds=0)), "s1", True),
            _cumulative(_dt(timedelta(seconds=400)), "s2", 2.0),
            _cumulative(_dt(timedelta(seconds=800)), "s3", 3.0),
        )
        assert analyzer.analyze(EvidenceCollection(records=records)) == ()


# ---------------------------------------------------------------------------
# Deterministic ordering and deduplication
# ---------------------------------------------------------------------------
class TestAnalyzeContract:
    def test_relationship_ids_unique_and_deterministic(self):
        analyzer = RelationshipAnalyzer()
        records = (
            _pattern(_dt(timedelta(seconds=0)), "s1", run_id="r1"),
            _pattern(_dt(timedelta(seconds=1)), "s2", run_id="r2"),
            _temporal(_dt(timedelta(seconds=0)), "s3", run_id="r3"),
            _temporal(_dt(timedelta(seconds=10)), "s4", run_id="r3"),
        )
        rels = analyzer.analyze(EvidenceCollection(records=records))

        assert len(rels) == 2
        assert [rel.relationship_id for rel in rels] == [
            "repeated_pattern:s1:s2",
            "temporal:s3:s4",
        ]

    def test_analyzer_rejects_non_positive_max_gap(self):
        with pytest.raises(ValueError, match="positive"):
            RelationshipAnalyzer(max_temporal_gap_seconds=0)


# ---------------------------------------------------------------------------
# EvidenceRelationship pydantic rules
# ---------------------------------------------------------------------------
class TestEvidenceRelationshipContracts:
    def _make(self, evidence_ids, **kw):
        base = dict(
            relationship_id="temporal:s1:s2",
            relationship_type="temporal",
            basis="temporal",
            agent_id="a1",
            evidence_ids=evidence_ids,
            observed_at=("2026-01-01T00:00:00+00:00",),
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

