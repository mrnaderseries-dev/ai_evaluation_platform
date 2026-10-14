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
from ai_eval_platform.storage.models import PersistedRelationship

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
        attributes={
            "metric_name": "cpu",
            "metric_value": metric_value,
            "metric_unit": "unit",
            "aggregation": "time_series",
        },
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
        assert len(rel.relationship_id) == 64

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
            _dt(timedelta(seconds=10)),
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

        rel = next(
            relationship
            for relationship in rels
            if relationship.relationship_type == "execution_parent"
        )
        assert rel.relationship_type == "execution_parent"
        assert rel.evidence_ids == ("s1", "s2")
        assert rel.observed_at == (
            parent.observed_at.isoformat(),
            child.observed_at.isoformat(),
        )
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

    def test_parent_child_edge_is_not_rejected_for_timestamp_inversion(self):
        parent = _trace(
            _dt(timedelta(seconds=20)),
            "parent",
            trace_id="trace",
            span_id="parent-span",
        )
        child = _trace(
            _dt(timedelta(seconds=10)),
            "child",
            trace_id="trace",
            span_id="child-span",
            parent_span_id="parent-span",
        )

        relationships = RelationshipAnalyzer().analyze(
            EvidenceCollection(records=(child, parent))
        )
        parent_relationships = [
            relationship
            for relationship in relationships
            if relationship.relationship_type == "execution_parent"
        ]

        assert len(parent_relationships) == 1
        assert parent_relationships[0].evidence_ids == ("parent", "child")
        assert parent_relationships[0].observed_at == (
            parent.observed_at.isoformat(),
            child.observed_at.isoformat(),
        )

    def test_multi_node_cycles_and_edges_touching_cycle_are_excluded(self):
        span_a = _trace(
            _dt(timedelta(seconds=0)),
            "a",
            trace_id="trace",
            span_id="span-a",
            parent_span_id="span-b",
        )
        span_b = _trace(
            _dt(timedelta(seconds=1)),
            "b",
            trace_id="trace",
            span_id="span-b",
        ).model_copy(update={"parent_span_id": "span-a"})
        span_c = _trace(
            _dt(timedelta(seconds=2)),
            "c",
            trace_id="trace",
            span_id="span-c",
            parent_span_id="span-a",
        )
        independent_parent = _trace(
            _dt(timedelta(seconds=3)),
            "p",
            trace_id="trace",
            span_id="independent-parent",
        )
        independent_child = _trace(
            _dt(timedelta(seconds=4)),
            "d",
            trace_id="trace",
            span_id="independent-child",
            parent_span_id="independent-parent",
        )
        records = (
            span_a,
            span_b,
            span_c,
            independent_parent,
            independent_child,
        )

        collection = EvidenceCollection.model_construct(
            records=records,
            limitations=(),
        )
        relationships = RelationshipAnalyzer().analyze(collection)
        parent_edges = {
            relationship.evidence_ids
            for relationship in relationships
            if relationship.relationship_type == "execution_parent"
        }

        assert parent_edges == {("p", "d")}


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

    def test_missing_run_scope_skipped(self):
        records = (
            _pattern(_dt(timedelta(seconds=0)), "s1", run_id=None),
            _pattern(_dt(timedelta(seconds=1)), "s2", run_id="r2"),
        )

        relationships = RelationshipAnalyzer().analyze(
            EvidenceCollection(records=records)
        )

        assert not any(
            relationship.relationship_type == "repeated_pattern"
            for relationship in relationships
        )

    def test_multiple_occurrences_are_retained_and_permutation_is_stable(self):
        records = (
            _pattern(_dt(timedelta(seconds=0)), "r1-a", run_id="r1"),
            _pattern(_dt(timedelta(seconds=1)), "r1-b", run_id="r1"),
            _pattern(_dt(timedelta(seconds=2)), "r2-a", run_id="r2"),
        )
        analyzer = RelationshipAnalyzer()

        first = analyzer.analyze(EvidenceCollection(records=records))
        second = analyzer.analyze(
            EvidenceCollection(records=tuple(reversed(records)))
        )
        repeated_first = next(
            relationship
            for relationship in first
            if relationship.relationship_type == "repeated_pattern"
        )
        repeated_second = next(
            relationship
            for relationship in second
            if relationship.relationship_type == "repeated_pattern"
        )

        assert repeated_first == repeated_second
        assert repeated_first.evidence_ids == ("r1-a", "r1-b", "r2-a")
        assert repeated_first.attributes["distinct_run_count"] == 2


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
    def test_dependency_is_reserved_and_not_generated(self):
        records = (
            _temporal(
                _dt(timedelta(seconds=0)),
                "dependency-a",
                evidence_kind="dependency",
            ),
            _temporal(
                _dt(timedelta(seconds=1)),
                "dependency-b",
                evidence_kind="dependency",
            ),
        )

        relationships = RelationshipAnalyzer().analyze(
            EvidenceCollection(records=records)
        )

        assert "temporal" in {relationship.relationship_type for relationship in relationships}
        assert "dependency" not in {
            relationship.relationship_type for relationship in relationships
        }

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
        assert [rel.relationship_id for rel in rels] == sorted(
            rel.relationship_id for rel in rels
        )

    def test_identity_is_collision_safe_and_keeps_distinct_edges(self):
        records = (
            _temporal(_dt(timedelta(seconds=0)), "a:b", run_id="r1"),
            _temporal(_dt(timedelta(seconds=1)), "c", run_id="r1"),
            _temporal(_dt(timedelta(seconds=0)), "a", run_id="r2"),
            _temporal(_dt(timedelta(seconds=1)), "b:c", run_id="r2"),
        )

        relationships = RelationshipAnalyzer().analyze(
            EvidenceCollection(records=records)
        )
        temporal = [
            relationship
            for relationship in relationships
            if relationship.relationship_type == "temporal"
        ]

        assert {relationship.evidence_ids for relationship in temporal} == {
            ("a:b", "c"),
            ("a", "b:c"),
        }
        assert len({relationship.relationship_id for relationship in temporal}) == 2

    def test_identity_is_stable_under_input_permutation(self):
        records = (
            _temporal(_dt(timedelta(seconds=0)), "z"),
            _temporal(_dt(timedelta(seconds=10)), "a"),
        )
        analyzer = RelationshipAnalyzer()

        first = analyzer.analyze(EvidenceCollection(records=records))
        second = analyzer.analyze(
            EvidenceCollection(records=tuple(reversed(records)))
        )

        assert first == second
        relationship = first[0]
        assert relationship.evidence_ids == ("z", "a")
        assert relationship.observed_at == (
            _dt(timedelta(seconds=0)).isoformat(),
            _dt(timedelta(seconds=10)).isoformat(),
        )

    def test_identity_includes_type_and_fits_persistence_limit(self):
        parent = _trace(
            _dt(timedelta(seconds=0)),
            "x" * 300,
            trace_id="trace",
            span_id="parent",
        )
        child = _trace(
            _dt(timedelta(seconds=1)),
            "y" * 300,
            trace_id="trace",
            span_id="child",
            parent_span_id="parent",
        )

        relationships = RelationshipAnalyzer().analyze(
            EvidenceCollection(records=(parent, child))
        )
        by_type = {
            relationship.relationship_type: relationship
            for relationship in relationships
        }

        assert {"temporal", "execution_parent"} <= set(by_type)
        assert by_type["temporal"].relationship_id != by_type["execution_parent"].relationship_id
        assert all(len(relationship.relationship_id) == 64 for relationship in relationships)
        relationship_id_limit = PersistedRelationship.__table__.c.relationship_id.type.length
        relationship_key_limit = PersistedRelationship.__table__.c.relationship_key.type.length
        assert all(
            len(relationship.relationship_id)
            <= min(relationship_id_limit, relationship_key_limit)
            for relationship in relationships
        )

    def test_duplicate_span_keys_are_excluded_as_ambiguous(self):
        first_parent = _trace(
            _dt(timedelta(seconds=0)),
            "p1",
            trace_id="trace",
            span_id="parent",
        )
        second_parent = _trace(
            _dt(timedelta(seconds=1)),
            "p2",
            trace_id="trace",
            span_id="parent",
        )
        child = _trace(
            _dt(timedelta(seconds=2)),
            "child",
            trace_id="trace",
            span_id="child",
            parent_span_id="parent",
        )

        relationships = RelationshipAnalyzer().analyze(
            EvidenceCollection(records=(first_parent, second_parent, child))
        )

        assert not any(
            relationship.relationship_type == "execution_parent"
            for relationship in relationships
        )

    def test_missing_trace_metadata_remains_valid_for_other_rules(self):
        records = (
            _temporal(_dt(timedelta(seconds=0)), "s1"),
            _temporal(_dt(timedelta(seconds=10)), "s2"),
        )

        relationships = RelationshipAnalyzer().analyze(
            EvidenceCollection(records=records)
        )

        assert [relationship.relationship_type for relationship in relationships] == [
            "temporal"
        ]

    def test_malformed_self_parent_does_not_abort_unrelated_analysis(self):
        malformed = _trace(
            _dt(timedelta(seconds=0)),
            "bad-trace",
            trace_id="trace",
            span_id="same-span",
        ).model_copy(
            update={"parent_span_id": "same-span", "run_id": "other-run"}
        )
        valid_first = _temporal(_dt(timedelta(seconds=0)), "valid-1")
        valid_second = _temporal(_dt(timedelta(seconds=10)), "valid-2")

        relationships = RelationshipAnalyzer().analyze(
            EvidenceCollection.model_construct(
                records=(malformed, valid_first, valid_second),
                limitations=(),
            )
        )

        assert [relationship.relationship_type for relationship in relationships] == [
            "temporal"
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

    def test_mismatched_timestamp_count_rejected(self):
        with pytest.raises(ValidationError, match="exactly one observed_at"):
            self._make(
                ("s1", "s2"),
                observed_at=("2026-01-01T00:00:00+00:00",),
            )

