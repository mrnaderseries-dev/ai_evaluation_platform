from collections import defaultdict
from itertools import combinations
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_eval_platform.analysis.evidence import (
    EvidenceCollection,
    EvidenceRecord,
)


RelationshipType = Literal[
    "temporal",
    "execution_parent",
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


class EvidenceRelationship(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    relationship_id: str
    relationship_type: RelationshipType
    basis: RelationshipBasis
    agent_id: str
    evidence_ids: tuple[str, ...] = Field(min_length=2)
    observed_at: tuple[str, ...] = ()
    attributes: dict[str, object] = Field(default_factory=dict)
    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_evidence_ids(self) -> "EvidenceRelationship":
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("Evidence IDs must be unique.")

        if any(not evidence_id.strip() for evidence_id in self.evidence_ids):
            raise ValueError("Evidence IDs must be non-empty strings.")

        return self


class RelationshipAnalyzer:
    def __init__(
        self,
        max_temporal_gap_seconds: float = 300.0,
    ) -> None:
        if max_temporal_gap_seconds <= 0:
            raise ValueError(
                "max_temporal_gap_seconds must be positive."
            )

        self._max_temporal_gap_seconds = max_temporal_gap_seconds

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
                key=lambda item: item.relationship_id,
            )
        )

    def _find_temporal(
        self,
        records: tuple[EvidenceRecord, ...],
    ) -> list[EvidenceRelationship]:
        results: list[EvidenceRelationship] = []

        for first, second in combinations(records, 2):
            if first.agent_id != second.agent_id:
                continue

            if first.run_id is None or first.run_id != second.run_id:
                continue

            gap = (
                second.observed_at - first.observed_at
            ).total_seconds()

            if gap <= 0:
                continue

            if gap > self._max_temporal_gap_seconds:
                continue

            results.append(
                self._make_relationship(
                    relationship_type="temporal",
                    basis="temporal",
                    records=(first, second),
                    attributes={
                        "gap_seconds": gap,
                    },
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
        spans = {
            (record.trace_id, record.span_id): record
            for record in records
            if record.trace_id is not None
            and record.span_id is not None
        }

        results: list[EvidenceRelationship] = []

        for child in records:
            if (
                child.trace_id is None
                or child.parent_span_id is None
            ):
                continue

            parent = spans.get(
                (child.trace_id, child.parent_span_id)
            )

            if parent is None:
                continue

            if parent.agent_id != child.agent_id:
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
        groups: dict[tuple[str, str], list[EvidenceRecord]] = defaultdict(list)

        for record in records:
            signature = record.attributes.get("pattern_signature")

            if not isinstance(signature, str) or not signature.strip():
                continue

            if record.run_id is None:
                continue

            groups[(record.agent_id, signature)].append(record)

        results: list[EvidenceRelationship] = []

        for (agent_id, signature), group in groups.items():
            run_ids = {record.run_id for record in group}

            if len(run_ids) < 2:
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

            results.append(
                self._make_relationship(
                    relationship_type="repeated_pattern",
                    basis="pattern",
                    records=selected,
                    attributes={
                        "pattern_signature": signature,
                        "distinct_run_count": len(run_ids),
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
        groups: dict[
            tuple[str, str | None, str],
            list[EvidenceRecord],
        ] = defaultdict(list)

        for record in records:
            metric_name = record.attributes.get("metric_name")
            metric_value = record.attributes.get("metric_value")

            if not isinstance(metric_name, str) or not metric_name.strip():
                continue

            if isinstance(metric_value, bool):
                continue

            if not isinstance(metric_value, (int, float)):
                continue

            if record.attributes.get("aggregation") != "time_series":
                continue

            groups[
                (
                    record.agent_id,
                    record.component_id,
                    metric_name,
                )
            ].append(record)

        results: list[EvidenceRelationship] = []

        for (agent_id, component_id, metric_name), group in groups.items():
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

            values = [
                record.attributes["metric_value"]
                for record in ordered
            ]

            if not all(
                current > previous
                for previous, current in zip(values, values[1:])
            ):
                continue

            results.append(
                self._make_relationship(
                    relationship_type="cumulative",
                    basis="quantitative",
                    records=ordered,
                    attributes={
                        "metric_name": metric_name,
                        "component_id": component_id,
                        "first_value": values[0],
                        "last_value": values[-1],
                        "increase": values[-1] - values[0],
                        "observation_count": len(values),
                    },
                    limitations=(
                        "Strictly increasing samples do not prove causation.",
                        "Trend detection does not establish statistical significance.",
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
        evidence_ids = tuple(
            sorted({record.evidence_id for record in records})
        )

        relationship_id = (
            f"{relationship_type}:"
            + ":".join(evidence_ids)
        )

        return EvidenceRelationship(
            relationship_id=relationship_id,
            relationship_type=relationship_type,
            basis=basis,
            agent_id=records[0].agent_id,
            evidence_ids=evidence_ids,
            observed_at=tuple(
                record.observed_at.isoformat()
                for record in records
            ),
            attributes=attributes,
            limitations=limitations,
        )