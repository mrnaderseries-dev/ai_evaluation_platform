from datetime import datetime, timedelta, timezone

import pytest

from ai_eval_platform.analysis.evidence import (
    EvidenceCollection,
    EvidenceRecord,
)
from ai_eval_platform.analysis.relationship_analyzer import (
    RelationshipAnalyzer,
)

UTC = timezone.utc
BASE = datetime(2026, 1, 1, tzinfo=UTC)


def record(evidence_id, seconds=0, **overrides):
    values = {
        "evidence_id": evidence_id,
        "source": "metric",
        "source_ref": evidence_id,
        "evidence_kind": "quantitative",
        "agent_id": "agent-a",
        "system_id": "system-a",
        "system_version": "v1",
        "evaluation_run_id": "eval-a",
        "run_id": "run-a",
        "observed_at": BASE + timedelta(seconds=seconds),
        "component_id": "cpu",
        "attributes": {},
    }
    values.update(overrides)
    return EvidenceRecord(**values)


def cumulative(evidence_id, seconds, value, **overrides):
    attributes = {
        "metric_name": "latency",
        "metric_value": value,
        "aggregation": "time_series",
        "metric_unit": "ms",
    }
    attributes.update(overrides.pop("attributes", {}))

    return record(
        evidence_id,
        seconds,
        attributes=attributes,
        **overrides,
    )


def cumulative_relationships(records):
    return tuple(
        relationship
        for relationship in RelationshipAnalyzer().analyze(
            EvidenceCollection(records=tuple(records))
        )
        if relationship.relationship_type == "cumulative"
    )


def test_temporal_relationships_do_not_cross_system_or_evaluation_scope():
    records = (
        record("a", 0),
        record("b", 10, system_id="system-b"),
        record("c", 20, evaluation_run_id="eval-b"),
        record("d", 30),
    )

    relationships = RelationshipAnalyzer().analyze(
        EvidenceCollection(records=records)
    )

    pairs = {
        relationship.evidence_ids
        for relationship in relationships
        if relationship.relationship_type == "temporal"
    }

    assert ("a", "d") in pairs
    assert ("a", "b") not in pairs
    assert ("a", "c") not in pairs


def test_execution_parent_does_not_match_span_from_another_run():
    parent = record(
        "parent",
        trace_id="trace-1",
        span_id="parent-span",
        run_id="run-one",
    )
    child = record(
        "child",
        trace_id="trace-1",
        span_id="child-span",
        parent_span_id="parent-span",
        run_id="run-two",
    )

    relationships = RelationshipAnalyzer().analyze(
        EvidenceCollection(records=(parent, child))
    )

    assert not any(
        relationship.relationship_type == "execution_parent"
        for relationship in relationships
    )


def test_execution_parent_does_not_match_incompatible_system_scope():
    parent = record(
        "parent",
        trace_id="trace-1",
        span_id="parent-span",
        system_version="v1",
    )
    child = record(
        "child",
        seconds=1,
        trace_id="trace-1",
        span_id="child-span",
        parent_span_id="parent-span",
        system_version="v2",
    )

    relationships = RelationshipAnalyzer().analyze(
        EvidenceCollection(records=(parent, child))
    )

    assert not any(
        relationship.relationship_type == "execution_parent"
        for relationship in relationships
    )


@pytest.mark.parametrize(
    ("field", "different_value"),
    [
        ("evaluation_run_id", "eval-other"),
        ("trace_id", "trace-other"),
    ],
)
def test_execution_parent_does_not_cross_evaluation_or_trace(
    field,
    different_value,
):
    parent = record(
        "parent",
        trace_id="trace-1",
        span_id="parent-span",
    )
    child_overrides = {
        "trace_id": "trace-1",
        "span_id": "child-span",
        "parent_span_id": "parent-span",
        field: different_value,
    }
    child = record(
        "child",
        seconds=1,
        **child_overrides,
    )

    relationships = RelationshipAnalyzer().analyze(
        EvidenceCollection(records=(parent, child))
    )

    assert not any(
        relationship.relationship_type == "execution_parent"
        for relationship in relationships
    )


def test_repeated_patterns_do_not_cross_system_versions():
    first = record(
        "a",
        run_id="run-one",
        attributes={"pattern_signature": "retry"},
    )
    second = record(
        "b",
        seconds=1,
        run_id="run-two",
        system_version="v2",
        attributes={"pattern_signature": "retry"},
    )

    relationships = RelationshipAnalyzer().analyze(
        EvidenceCollection(records=(first, second))
    )

    assert not any(
        relationship.relationship_type == "repeated_pattern"
        for relationship in relationships
    )


def test_cumulative_trend_does_not_mix_metric_units():
    records = (
        cumulative("a", 0, 1, attributes={"metric_unit": "ms"}),
        cumulative("b", 1, 2, attributes={"metric_unit": "seconds"}),
        cumulative("c", 2, 3, attributes={"metric_unit": "ms"}),
        cumulative("d", 3, 4, attributes={"metric_unit": "ms"}),
    )

    relationships = RelationshipAnalyzer().analyze(
        EvidenceCollection(records=records)
    )

    trends = [
        relationship
        for relationship in relationships
        if relationship.relationship_type == "cumulative"
    ]

    assert len(trends) == 1
    assert trends[0].attributes["metric_unit"] == "ms"
    assert trends[0].evidence_ids == ("a", "c", "d")


def test_cumulative_trend_ignores_nonfinite_and_boolean_values():
    records = (
        cumulative("a", 0, 1),
        cumulative("b", 1, float("nan")),
        cumulative("c", 2, 2),
        cumulative("d", 3, float("inf")),
        cumulative("e", 4, True),
        cumulative("f", 5, 3),
    )

    relationships = RelationshipAnalyzer().analyze(
        EvidenceCollection(records=records)
    )

    trends = [
        relationship
        for relationship in relationships
        if relationship.relationship_type == "cumulative"
    ]

    assert len(trends) == 1
    assert trends[0].evidence_ids == ("a", "c", "f")


def test_cumulative_requires_run_or_evaluation_scope():
    records = tuple(
        cumulative(
            f"unscoped-{index}",
            index,
            index + 1,
            run_id=None,
            evaluation_run_id=None,
        )
        for index in range(3)
    )

    assert cumulative_relationships(records) == ()


def test_cumulative_does_not_combine_distinct_run_ids():
    records = (
        cumulative("r1-a", 0, 1, run_id="run-one"),
        cumulative("r1-b", 1, 2, run_id="run-one"),
        cumulative("r2-a", 2, 3, run_id="run-two"),
    )

    assert cumulative_relationships(records) == ()


def test_cumulative_does_not_combine_distinct_evaluation_ids():
    records = (
        cumulative("e1-a", 0, 1, run_id=None, evaluation_run_id="eval-one"),
        cumulative("e1-b", 1, 2, run_id=None, evaluation_run_id="eval-one"),
        cumulative("e2-a", 2, 3, run_id=None, evaluation_run_id="eval-two"),
    )

    assert cumulative_relationships(records) == ()


@pytest.mark.parametrize(
    ("scope_field", "different_value"),
    [
        ("system_id", "system-b"),
        ("system_version", "v2"),
    ],
)
def test_cumulative_does_not_combine_distinct_system_scopes(
    scope_field,
    different_value,
):
    records = (
        cumulative("a", 0, 1),
        cumulative("b", 1, 2),
        cumulative("c", 2, 3, **{scope_field: different_value}),
    )

    assert cumulative_relationships(records) == ()


def test_cumulative_excludes_missing_or_blank_metric_units():
    missing = tuple(
        cumulative(
            f"missing-{index}",
            index,
            index + 1,
            attributes={"metric_unit": None},
        )
        for index in range(3)
    )
    blank = tuple(
        cumulative(
            f"blank-{index}",
            index,
            index + 1,
            attributes={"metric_unit": "  "},
        )
        for index in range(3)
    )

    assert cumulative_relationships(missing + blank) == ()


def test_equal_source_refs_are_not_deduplicated_without_identity_contract():
    records = tuple(
        cumulative(
            f"same-source-{index}",
            index,
            index + 1,
            source_ref="shared-source-reference",
        )
        for index in range(3)
    )

    trends = cumulative_relationships(records)

    assert len(trends) == 1
    assert trends[0].evidence_ids == (
        "same-source-0",
        "same-source-1",
        "same-source-2",
    )


@pytest.mark.parametrize(
    "seconds",
    [
        (0, 0, 0),
        (0, 0, 1),
        (0, 1, 1, 2),
    ],
    ids=["all-tied", "two-tied", "mixed-tie"],
)
def test_cumulative_requires_strictly_increasing_timestamps(seconds):
    records = tuple(
        cumulative(f"tied-{index}", second, index + 1)
        for index, second in enumerate(seconds)
    )

    assert cumulative_relationships(records) == ()


def test_cumulative_output_is_deterministic_under_input_permutation():
    records = (
        cumulative("ordered-a", 0, 1),
        cumulative("ordered-b", 1, 2),
        cumulative("ordered-c", 2, 3),
    )

    assert cumulative_relationships(records) == cumulative_relationships(
        tuple(reversed(records))
    )


def test_cumulative_ignores_non_time_series_aggregation():
    records = tuple(
        cumulative(
            f"aggregate-{index}",
            index,
            index + 1,
            attributes={"aggregation": "snapshot"},
        )
        for index in range(3)
    )

    assert cumulative_relationships(records) == ()


def test_detected_relationships_default_to_candidate():
    records = (
        record("a", 0),
        record("b", 10),
    )

    relationships = RelationshipAnalyzer().analyze(
        EvidenceCollection(records=records)
    )

    assert relationships
    assert all(
        relationship.status == "candidate"
        for relationship in relationships
    )
