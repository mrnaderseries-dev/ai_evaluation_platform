
from collections import defaultdict
from hashlib import sha256
import json
from math import isfinite
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_eval_platform.analysis.evidence import (
    EvidenceCollection,
    EvidenceRecord,
)


RelationshipType = Literal[
    "temporal",
    "execution_parent",
    # Reserved for a future explicit dependency-evidence contract.
    "dependency",
    "repeated_pattern",
    "cumulative",
]

RelationshipBasis = Literal[
    "direct",
    "temporal",
    "pattern",
    "quantitative",
]

RelationshipStatus = Literal[
    "candidate",
    "validated",
    "rejected",
]


class EvidenceRelationship(BaseModel):
    """An exploratory relationship between evidence records.

    Detected relationships remain candidates until an explicit validation
    process changes their status. They must not directly control deployment.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    relationship_id: str
    relationship_type: RelationshipType
    basis: RelationshipBasis
    status: RelationshipStatus = "candidate"

    agent_id: str
    system_id: str | None = None
    system_version: str | None = None
    evaluation_run_id: str | None = None
    run_id: str | None = None

    evidence_ids: tuple[str, ...] = Field(min_length=2)
    observed_at: tuple[str, ...] = ()

    attributes: dict[str, object] = Field(default_factory=dict)
    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_evidence_ids(self) -> "EvidenceRelationship":
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("Evidence IDs must be unique.")

        if any(not evidence_id.strip() for evidence_id in self.evidence_ids):
            raise ValueError(
                "Evidence IDs must be non-empty strings."
            )

        if len(self.observed_at) != len(self.evidence_ids):
            raise ValueError(
                "Each evidence ID must have exactly one observed_at timestamp."
            )

        return self


class RelationshipAnalyzer:
    """Generate descriptive candidates from explicit evidence fields.

    ``dependency`` remains a reserved relationship type: no generator is
    enabled until EvidenceRecord can carry a trustworthy dependency reference.
    """

    def __init__(
        self,
        max_temporal_gap_seconds: float = 300.0,
    ) -> None:
        if (
            not isfinite(max_temporal_gap_seconds)
            or max_temporal_gap_seconds <= 0
        ):
            raise ValueError(
                "max_temporal_gap_seconds must be positive and finite."
            )

        self._max_temporal_gap_seconds = max_temporal_gap_seconds

    @staticmethod
    def _scope(record: EvidenceRecord) -> tuple[object, ...]:
        """Return the complete scope used to isolate temporal evidence."""
        return (
            record.agent_id,
            record.system_id,
            record.system_version,
            record.evaluation_run_id,
            record.run_id,
        )

    @staticmethod
    def _common(
        records: tuple[EvidenceRecord, ...],
        field: str,
    ) -> str | None:
        values = {getattr(record, field) for record in records}

        if len(values) != 1:
            return None

        return next(iter(values))

    def analyze(
        self,
        collection: EvidenceCollection,
    ) -> tuple[EvidenceRelationship, ...]:
        records = tuple(
            sorted(
                collection.records,
                key=lambda record: (
                    record.observed_at,
                    record.evidence_id,
                ),
            )
        )

        relationships: list[EvidenceRelationship] = []
        relationships.extend(self._find_temporal(records))
        relationships.extend(self._find_execution_parent(records))
        relationships.extend(self._find_repeated_patterns(records))
        relationships.extend(self._find_cumulative(records))

        unique = {
            relationship.relationship_id: relationship
            for relationship in relationships
        }

        return tuple(
            sorted(
                unique.values(),
                key=lambda relationship: relationship.relationship_id,
            )
        )

    def _find_temporal(
        self,
        records: tuple[EvidenceRecord, ...],
    ) -> list[EvidenceRelationship]:
        groups: dict[
            tuple[object, ...],
            list[EvidenceRecord],
        ] = defaultdict(list)

        for record in records:
            # Without a run ID, records are not eligible for this relationship.
            if record.run_id is None:
                continue

            groups[self._scope(record)].append(record)

        results: list[EvidenceRelationship] = []

        for group in groups.values():
            group.sort(
                key=lambda record: (
                    record.observed_at,
                    record.evidence_id,
                )
            )

            for index, first in enumerate(group):
                for second_index in range(index + 1, len(group)):
                    second = group[second_index]
                    gap = (
                        second.observed_at - first.observed_at
                    ).total_seconds()

                    # The group is sorted, so later records can be skipped.
                    if gap > self._max_temporal_gap_seconds:
                        break

                    if gap <= 0:
                        continue

                    results.append(
                        self._make_relationship(
                            relationship_type="temporal",
                            basis="temporal",
                            records=(first, second),
                            attributes={"gap_seconds": gap},
                            limitations=(
                                "Temporal order does not establish causation.",
                            ),
                        )
                    )

        return results

    def _find_execution_parent(
        self,
        records: tuple[EvidenceRecord, ...],
    ) -> list[EvidenceRelationship]:
        span_groups: dict[tuple[object, ...], list[EvidenceRecord]] = defaultdict(list)
        for record in records:
            if record.trace_id is None or record.span_id is None:
                continue

            span_groups[(
                *self._scope(record),
                record.trace_id,
                record.span_id,
            )].append(record)

        ambiguous_span_keys = {
            key
            for key, matches in span_groups.items()
            if len(matches) > 1
        }
        spans = {
            key: matches[0]
            for key, matches in span_groups.items()
            if len(matches) == 1
        }

        # Parent references define a hierarchy; exclude cyclic nodes and their edges.
        parent_by_child: dict[tuple[object, ...], tuple[object, ...]] = {}
        for child_key, child in spans.items():
            if child.parent_span_id is None or child.parent_span_id == child.span_id:
                continue

            parent_key = (
                *self._scope(child),
                child.trace_id,
                child.parent_span_id,
            )
            if parent_key in spans:
                parent_by_child[child_key] = parent_key

        cyclic_span_keys: set[tuple[object, ...]] = set()
        resolved_span_keys: set[tuple[object, ...]] = set()
        for start_key in parent_by_child:
            path: list[tuple[object, ...]] = []
            path_positions: dict[tuple[object, ...], int] = {}
            current_key = start_key

            while (
                current_key in parent_by_child
                and current_key not in resolved_span_keys
                and current_key not in path_positions
            ):
                path_positions[current_key] = len(path)
                path.append(current_key)
                current_key = parent_by_child[current_key]

            if current_key in path_positions:
                cyclic_span_keys.update(path[path_positions[current_key]:])

            resolved_span_keys.update(path)

        results: list[EvidenceRelationship] = []

        for child in records:
            if (
                child.trace_id is None
                or child.span_id is None
                or child.parent_span_id is None
                or child.span_id == child.parent_span_id
            ):
                continue

            child_key = (
                *self._scope(child),
                child.trace_id,
                child.span_id,
            )
            if child_key in ambiguous_span_keys:
                continue

            parent_key = (
                *self._scope(child),
                child.trace_id,
                child.parent_span_id,
            )
            parent = spans.get(parent_key)

            if parent is None:
                continue

            if child_key in cyclic_span_keys or parent_key in cyclic_span_keys:
                continue

            results.append(
                self._make_relationship(
                    relationship_type="execution_parent",
                    basis="direct",
                    records=(parent, child),
                    attributes={
                        "trace_id": child.trace_id,
                        "parent_span_id": child.parent_span_id,
                        "child_span_id": child.span_id,
                    },
                    limitations=(
                        "Trace hierarchy does not prove root cause.",
                    ),
                )
            )

        return results

    def _find_repeated_patterns(
        self,
        records: tuple[EvidenceRecord, ...],
    ) -> list[EvidenceRelationship]:
        groups: dict[
            tuple[object, ...],
            list[EvidenceRecord],
        ] = defaultdict(list)

        for record in records:
            signature = record.attributes.get("pattern_signature")

            if (
                not isinstance(signature, str)
                or not signature.strip()
                or record.run_id is None
            ):
                continue

            key = (
                record.agent_id,
                record.system_id,
                record.system_version,
                record.evaluation_run_id,
                signature,
            )
            groups[key].append(record)

        results: list[EvidenceRelationship] = []

        for key, group in groups.items():
            distinct_runs = {
                record.run_id for record in group
            }

            if len(distinct_runs) < 2:
                continue

            selected = tuple(
                sorted(
                    group,
                    key=lambda record: (
                        record.observed_at,
                        record.evidence_id,
                    ),
                )
            )

            signature = str(key[-1])

            results.append(
                self._make_relationship(
                    relationship_type="repeated_pattern",
                    basis="pattern",
                    records=selected,
                    attributes={
                        "pattern_signature": signature,
                        "distinct_run_count": len(distinct_runs),
                    },
                    limitations=(
                        "Repeated pattern does not establish a shared cause.",
                    ),
                )
            )

        return results

    def _find_cumulative(
        self,
        records: tuple[EvidenceRecord, ...],
    ) -> list[EvidenceRelationship]:
        """Find monotone samples within one explicit run/evaluation and unit.

        ``source_ref`` is provenance, not a declared observation-uniqueness key;
        records sharing it remain separate observations. Missing units and
        records with no run/evaluation scope are excluded.
        """
        groups: dict[
            tuple[object, ...],
            list[EvidenceRecord],
        ] = defaultdict(list)

        for record in records:
            attributes = record.attributes
            metric_name = attributes.get("metric_name")
            metric_value = attributes.get("metric_value")
            metric_unit = attributes.get("metric_unit")

            if (
                not isinstance(metric_name, str)
                or not metric_name.strip()
            ):
                continue

            if (
                not isinstance(metric_unit, str)
                or not metric_unit.strip()
            ):
                continue

            if record.run_id is None and record.evaluation_run_id is None:
                continue

            # bool is a subclass of int in Python; exclude it explicitly.
            if isinstance(metric_value, bool):
                continue

            if not isinstance(metric_value, (int, float)):
                continue

            if not isfinite(float(metric_value)):
                continue

            if attributes.get("aggregation") != "time_series":
                continue

            key = (
                record.agent_id,
                record.system_id,
                record.system_version,
                record.evaluation_run_id,
                record.run_id,
                record.component_id,
                metric_name,
                metric_unit,
            )
            groups[key].append(record)

        results: list[EvidenceRelationship] = []

        for key, group in groups.items():
            ordered = tuple(
                sorted(
                    group,
                    key=lambda record: (
                        record.observed_at,
                        record.evidence_id,
                    ),
                )
            )

            if len(ordered) < 3:
                continue

            if any(
                current.observed_at <= previous.observed_at
                for previous, current in zip(ordered, ordered[1:])
            ):
                continue

            values = [
                float(record.attributes["metric_value"])
                for record in ordered
            ]

            if not all(
                current > previous
                for previous, current in zip(values, values[1:])
            ):
                continue

            (
                _agent_id,
                _system_id,
                _system_version,
                _evaluation_run_id,
                _run_id,
                component_id,
                metric_name,
                metric_unit,
            ) = key

            results.append(
                self._make_relationship(
                    relationship_type="cumulative",
                    basis="quantitative",
                    records=ordered,
                    attributes={
                        "metric_name": metric_name,
                        "metric_unit": metric_unit,
                        "component_id": component_id,
                        "first_value": values[0],
                        "last_value": values[-1],
                        "increase": values[-1] - values[0],
                        "observation_count": len(values),
                    },
                    limitations=(
                        "Strictly increasing samples do not prove causation.",
                        "Trend detection does not establish statistical significance.",
                        "Cumulative samples require one run/evaluation scope and an explicit metric unit.",
                        "Cumulative samples require strictly increasing observation timestamps.",
                    ),
                )
            )

        return results

    @staticmethod
    def _make_relationship(
        relationship_type: RelationshipType,
        basis: RelationshipBasis,
        records: tuple[EvidenceRecord, ...],
        attributes: dict[str, object],
        limitations: tuple[str, ...],
    ) -> EvidenceRelationship:
        # Call sites pass chronological order, except execution_parent, whose
        # semantic order is explicitly parent then child.
        ordered_records = records
        evidence_ids = tuple(
            record.evidence_id
            for record in ordered_records
        )
        identity_payload = json.dumps(
            {
                "relationship_type": relationship_type,
                "evidence_ids": evidence_ids,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        relationship_id = sha256(identity_payload).hexdigest()

        return EvidenceRelationship(
            relationship_id=relationship_id,
            relationship_type=relationship_type,
            basis=basis,
            status="candidate",
            agent_id=records[0].agent_id,
            system_id=RelationshipAnalyzer._common(
                records, "system_id"
            ),
            system_version=RelationshipAnalyzer._common(
                records, "system_version"
            ),
            evaluation_run_id=RelationshipAnalyzer._common(
                ordered_records, "evaluation_run_id"
            ),
            run_id=RelationshipAnalyzer._common(
                ordered_records, "run_id"
            ),
            evidence_ids=evidence_ids,
            observed_at=tuple(
                record.observed_at.isoformat()
                for record in ordered_records
            ),
            attributes=attributes,
            limitations=limitations,
        )
