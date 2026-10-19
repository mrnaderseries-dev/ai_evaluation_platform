"""Agent-agnostic evidence contracts and adapters for RCA.

This module deliberately treats all incoming artifacts as evidence candidates.
It does not infer causality from temporal proximity, detector labels, p-values,
CI gates, or discovered relationships alone.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ai_eval_platform.analysis.evidence import EvidenceRecord
from ai_eval_platform.analysis.models import AnalysisResult
from ai_eval_platform.monitoring.signals.models import Signal

from .domain import (
    PROTECTED_IDENTIFIER_KEYS,
    RCA_ATTRIBUTE_DEPTH_LIMIT,
    RCA_ATTRIBUTE_ITEMS_LIMIT,
    RCA_ATTRIBUTE_STRING_LIMIT,
    RCA_ATTRIBUTES_BYTES_LIMIT,
    REDACTION_MARKER,
    TRUNCATION_MARKER,
    EvidenceSourceType,
    RCAEvidenceReference,
    is_sensitive_key,
    scrub_secret_text,
)


class RCAArtifact(BaseModel):
    """Normalized artifact boundary for facts consumed by the RCA engine."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_id: str = Field(min_length=1)
    source_type: EvidenceSourceType
    source_ref: str = Field(min_length=1)
    observed_at: datetime | None = None
    domain: str | None = None
    subject_type: str | None = None
    subject_id: str | None = None
    agent_id: str | None = None
    system_id: str | None = None
    system_version: str | None = None
    run_id: str | None = None
    evaluation_run_id: str | None = None
    pipeline_run_id: str | None = None
    environment: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    limitations: tuple[str, ...] = ()

    @field_validator("observed_at")
    @classmethod
    def aware_time(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("observed_at must be timezone-aware")
        return value

    @field_validator(
        "agent_id",
        "system_id",
        "system_version",
        "run_id",
        "evaluation_run_id",
        "pipeline_run_id",
        "environment",
    )
    @classmethod
    def optional_non_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("scope metadata fields must not be blank")
        return value


def artifact_from_signal(signal: Signal) -> RCAArtifact:
    payload = signal.payload.model_dump(mode="json")
    return RCAArtifact(
        artifact_id=f"signal:{signal.signal_id}",
        source_type="signal",
        source_ref=signal.signal_id,
        observed_at=signal.detected_at,
        agent_id=signal.agent_id,
        run_id=signal.run_id,
        evaluation_run_id=signal.evaluation_run_id,
        attributes={
            "origin": signal.origin.value,
            "signal_type": signal.signal_type.value,
            "scope": signal.scope.value,
            "payload": payload,
            "source_event_id": signal.source_event_id,
            "source_observation_id": signal.source_observation_id,
            "projection_version": signal.projection_version,
        },
        limitations=("A signal indicates an observed condition; it does not by itself establish a cause.",),
    )


def artifact_from_evidence(record: EvidenceRecord) -> RCAArtifact:
    source_type: EvidenceSourceType = record.source
    return RCAArtifact(
        artifact_id=record.evidence_id,
        source_type=source_type,
        source_ref=record.source_ref,
        observed_at=record.observed_at,
        domain=record.domain,
        subject_type=record.subject_type,
        subject_id=record.subject_id,
        agent_id=record.agent_id,
        system_id=record.system_id,
        system_version=record.system_version,
        run_id=record.run_id,
        evaluation_run_id=record.evaluation_run_id,
        attributes={
            "evidence_kind": record.evidence_kind,
            "trace_id": record.trace_id,
            "span_id": record.span_id,
            "parent_span_id": record.parent_span_id,
            "component_id": record.component_id,
            **record.attributes,
            "source_snapshot": record.source_snapshot,
        },
        limitations=record.limitations,
    )


def artifact_from_relationship(
    relationship: Any,
    *,
    evidence_ids: Sequence[str] | None = None,
) -> RCAArtifact:
    """Adapt a relationship (in-memory or persisted) into an RCA artifact.

    Accepts the analyzer's in-memory ``EvidenceRelationship`` or the storage
    ``PersistedRelationship`` row returned by ``RelationshipAnalysisService``.
    Persisted rows keep scope lineage in their ``scope`` mapping and do not
    embed evidence links, so callers must pass the ordered evidence ids from
    ``RelationshipRepository.list_evidence_links`` via ``evidence_ids``; this
    keeps the analyzer's unresolved-source check meaningful instead of
    vacuously passing against an empty link set.
    """
    scope = getattr(relationship, "scope", None)

    if isinstance(scope, dict):
        if evidence_ids is None:
            raise ValueError(
                "Persisted relationships require evidence_ids from "
                "RelationshipRepository.list_evidence_links."
            )
        relationship_id = str(relationship.relationship_id)
        relationship_key = str(relationship.relationship_key)
        agent_id = scope.get("agent_id")
        system_id = scope.get("system_id")
        system_version = scope.get("system_version")
        run_id = scope.get("run_id")
        evaluation_run_id = scope.get("evaluation_run_id")
        resolved_evidence_ids = tuple(str(value) for value in evidence_ids)
        observed: tuple[str, ...] = ()
        attributes: dict[str, Any] = {
            "relationship_id": relationship_id,
            "relationship_key": relationship_key,
            "relationship_type": relationship.relationship_type,
            "basis": relationship.basis,
            "status": relationship.status,
            "evidence_ids": list(resolved_evidence_ids),
            "observed_at": list(observed),
            "algorithm_name": relationship.algorithm_name,
            "algorithm_version": relationship.algorithm_version,
            **dict(relationship.attributes or {}),
        }
        limitations = tuple(relationship.limitations or ())
    else:
        relationship_id = relationship.relationship_id
        agent_id = relationship.agent_id
        system_id = relationship.system_id
        system_version = relationship.system_version
        run_id = relationship.run_id
        evaluation_run_id = relationship.evaluation_run_id
        resolved_evidence_ids = tuple(relationship.evidence_ids)
        observed = tuple(relationship.observed_at)
        attributes = {
            "relationship_id": relationship_id,
            "relationship_type": relationship.relationship_type,
            "basis": relationship.basis,
            "status": relationship.status,
            "evidence_ids": list(resolved_evidence_ids),
            "observed_at": list(observed),
            **dict(relationship.attributes),
        }
        limitations = tuple(relationship.limitations)

    return RCAArtifact(
        artifact_id=f"relationship:{relationship_id}",
        source_type="relationship",
        source_ref=str(relationship_id),
        agent_id=agent_id,
        system_id=system_id,
        system_version=system_version,
        run_id=run_id,
        evaluation_run_id=evaluation_run_id,
        attributes=attributes,
        limitations=limitations + ("A discovered relationship is exploratory and is not proof of causation.",),
    )


def artifact_from_analysis(result: AnalysisResult) -> RCAArtifact:
    return RCAArtifact(
        artifact_id=f"analysis:{result.analysis_id}",
        source_type="analysis_result",
        source_ref=result.analysis_id,
        agent_id=result.agent_id,
        observed_at=result.window_end,
        attributes={
            "analysis_type": result.analysis_type,
            "algorithm_name": result.algorithm_name,
            "algorithm_version": result.algorithm_version,
            "evidence_signal_ids": list(result.evidence_signal_ids),
            "metrics": result.metrics,
            "sample_size": result.sample_size,
            "window_start": result.window_start.isoformat() if result.window_start else None,
            "window_end": result.window_end.isoformat() if result.window_end else None,
        },
        limitations=tuple(result.limitations),
    )


def reference_for(artifact: RCAArtifact, *, role: Literal["supporting", "counter", "context"], rationale: str) -> RCAEvidenceReference:
    """Build a hypothesis-scoped reference; role is never global to the artifact."""
    kwargs: dict[str, Any] = dict(
        evidence_id=artifact.artifact_id,
        source_type=artifact.source_type,
        source_ref=artifact.source_ref,
        role=role,
        rationale=rationale,
    )
    if artifact.source_type == "relationship":
        kwargs["relationship_id"] = artifact.source_ref
    if artifact.source_type == "differential_analysis":
        kwargs["differential_analysis_id"] = artifact.source_ref
    source_event_id = artifact.attributes.get("source_event_id")
    if source_event_id is None and artifact.source_type == "event":
        source_event_id = artifact.source_ref
    if source_event_id is not None:
        kwargs["source_event_id"] = str(source_event_id)
    source_observation_id = artifact.attributes.get(
        "source_observation_id"
    )
    if source_observation_id is None and artifact.source_type == "observation":
        source_observation_id = artifact.source_ref
    if source_observation_id is not None:
        kwargs["source_observation_id"] = str(source_observation_id)
    return RCAEvidenceReference(**kwargs)


def artifacts_from_differential_analysis(differential: Any) -> tuple[RCAArtifact, ...]:
    """Adapt the persisted DifferentialAnalysis row into one artifact per metric.

    The stable persisted differential-analysis ID is retained as source_ref;
    artifact IDs additionally include the metric so multi-metric comparisons do
    not collapse into one ambiguous item.
    """
    differential_id = str(differential.differential_analysis_id)
    deltas = differential.metric_deltas or {}
    tests = differential.statistical_tests or {}
    artifacts: list[RCAArtifact] = []
    for metric_name in sorted(set(deltas) | set(tests)):
        metric_delta = deltas.get(metric_name) or {}
        statistical = tests.get(metric_name) or {}
        attrs = {
            **metric_delta,
            **statistical,
            "metric_name": metric_name,
            "baseline_evaluation_run_id": differential.baseline_evaluation_run_id,
            "candidate_evaluation_run_id": differential.candidate_evaluation_run_id,
            "analysis_id": differential.analysis_id,
        }
        artifacts.append(RCAArtifact(
            artifact_id=f"differential_analysis:{differential_id}:{metric_name}",
            source_type="differential_analysis",
            source_ref=differential_id,
            evaluation_run_id=differential.candidate_evaluation_run_id,
            attributes=attrs,
            limitations=tuple(statistical.get("limitations", ())),
        ))
    return tuple(artifacts)


def artifact_from_observation(observation: Any) -> RCAArtifact:
    """Adapt the normalized Observation contract without requiring a detector."""
    observed_at = observation.observed_at
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    return RCAArtifact(
        artifact_id=f"observation:{observation.observation_id}",
        source_type="observation",
        source_ref=observation.observation_id,
        observed_at=observed_at,
        agent_id=observation.agent_id,
        system_id=observation.system_id,
        system_version=observation.system_version,
        run_id=observation.run_id,
        attributes={
            "source_event_id": observation.source_event_id,
            "projection_version": observation.projection_version,
            "event_type": observation.event_type,
            "occurred_at": observation.occurred_at.isoformat(),
            "sequence_number": observation.sequence_number,
            "parent_event_id": observation.parent_event_id,
            "correlation_id": observation.correlation_id,
            "causation_id": observation.causation_id,
            "step_id": observation.step_id,
            "attributes": observation.attributes,
        },
        limitations=("A normalized observation records a fact; causal interpretation requires additional evidence.",),
    )


def artifact_from_cicd(result: Any) -> RCAArtifact:
    """Adapt a provider-neutral CI/CD check result without inventing metadata.

    Accepts mappings (or objects with attributes) carrying any subset of:
    check/run/build/pipeline/deployment id, pipeline_run_id, decision/status,
    commit_sha/commit, agent_id/system_id/system_version, environment,
    observed_at/timestamp/decided_at, failed_tests/failed_test_names,
    test_summary, dataset_version/evaluation_run ids, baseline/candidate
    evaluation-run refs, rollback_outcome/reproduction_outcome,
    policy_version, limitations.
    Missing fields are preserved as missing and disclosed downstream; no
    value is ever fabricated.
    """
    if isinstance(result, dict):
        get = result.get
    else:
        def get(name: str, default: Any = None) -> Any:
            value = getattr(result, name, default)
            return default if callable(value) else value

    check_id = (
        get("check_id")
        or get("gate_id")
        or get("build_id")
        or get("pipeline_id")
        or get("deployment_id")
        or get("cicd_result_id")
        or "unknown"
    )
    pipeline_run_id = get("pipeline_run_id")
    if pipeline_run_id is not None:
        pipeline_run_id = str(pipeline_run_id)
    environment = get("environment")
    if environment is not None:
        environment = str(environment)
    decision = str(get("decision", get("status", "unknown")))
    observed_at = (
        get("observed_at") or get("timestamp") or get("decided_at")
    )
    attributes: dict[str, Any] = {
        "decision": decision,
        "status": decision,
        "commit_sha": get("commit_sha", get("commit")),
        "build_id": get("build_id"),
        "pipeline_id": get("pipeline_id"),
        "deployment_id": get("deployment_id"),
        "environment": get("environment"),
        "failed_tests": get("failed_tests", get("failed_test_names")),
        "test_summary": get("test_summary"),
        "dataset_version": get("dataset_version"),
        "baseline_evaluation_run_id": get("baseline_evaluation_run_id"),
        "candidate_evaluation_run_id": get("candidate_evaluation_run_id"),
        "rollback_outcome": get("rollback_outcome"),
        "reproduction_outcome": get("reproduction_outcome"),
        "policy_version": get("policy_version"),
    }
    attributes = {
        key: value for key, value in attributes.items() if value is not None
    }
    extra_limitations = get("limitations", ())
    if isinstance(extra_limitations, str):
        extra_limitations = (extra_limitations,)
    return RCAArtifact(
        artifact_id=f"ci_cd_result:{check_id}",
        source_type="ci_cd_result",
        source_ref=str(check_id),
        observed_at=observed_at,
        agent_id=get("agent_id"),
        system_id=get("system_id"),
        system_version=get("system_version"),
        run_id=get("run_id"),
        evaluation_run_id=get("evaluation_run_id", get("candidate_evaluation_run_id")),
        pipeline_run_id=pipeline_run_id,
        environment=environment,
        attributes=attributes,
        limitations=(
            "A CI/CD check outcome records a policy decision; it does not by "
            "itself identify a production root cause.",
            *tuple(extra_limitations or ()),
        ),
    )


def _serialized_size(value: Any) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")
    )


def _sanitize_value(
    value: Any,
    depth: int,
    counters: dict[str, int],
    *,
    protected: bool = False,
) -> Any:
    if depth > RCA_ATTRIBUTE_DEPTH_LIMIT:
        counters["depth"] += 1
        return TRUNCATION_MARKER

    if isinstance(value, str):
        scrubbed, secret_count = scrub_secret_text(value)
        if secret_count:
            counters["secrets"] += secret_count
            value = scrubbed
        if not protected and len(value) > RCA_ATTRIBUTE_STRING_LIMIT:
            counters["strings"] += 1
            return value[:RCA_ATTRIBUTE_STRING_LIMIT] + " " + TRUNCATION_MARKER
        return value

    if isinstance(value, dict):
        cleaned: dict[Any, Any] = {}
        kept = 0
        for key, item in value.items():
            if kept >= RCA_ATTRIBUTE_ITEMS_LIMIT:
                counters["items"] += 1
                break
            kept += 1
            key_name = str(key)
            if is_sensitive_key(key_name):
                counters["sensitive"] += 1
                cleaned[key] = REDACTION_MARKER
                continue
            key_is_protected = key_name in PROTECTED_IDENTIFIER_KEYS
            if not key_is_protected and len(key_name) > (
                RCA_ATTRIBUTE_STRING_LIMIT
            ):
                counters["strings"] += 1
                key_name = (
                    key_name[:RCA_ATTRIBUTE_STRING_LIMIT]
                    + " "
                    + TRUNCATION_MARKER
                )
            cleaned[key_name] = _sanitize_value(
                item,
                depth + 1,
                counters,
                protected=key_is_protected,
            )
        return cleaned

    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        if len(items) > RCA_ATTRIBUTE_ITEMS_LIMIT:
            counters["items"] += 1
            items = items[:RCA_ATTRIBUTE_ITEMS_LIMIT]
        cleaned_items = [
            _sanitize_value(item, depth + 1, counters, protected=protected)
            for item in items
        ]
        if isinstance(value, tuple):
            return tuple(cleaned_items)
        if isinstance(value, (set, frozenset)):
            return cleaned_items
        return cleaned_items

    return value


def _sanitize_attributes(
    attributes: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, int]]:
    counters = {
        "sensitive": 0,
        "secrets": 0,
        "strings": 0,
        "items": 0,
        "depth": 0,
        "dropped_keys": 0,
    }
    cleaned = _sanitize_value(dict(attributes), 0, counters)
    if not isinstance(cleaned, dict):
        return None, counters

    if _serialized_size(cleaned) > RCA_ATTRIBUTES_BYTES_LIMIT:
        for bulk_key in ("payload", "source_snapshot"):
            if bulk_key in cleaned:
                del cleaned[bulk_key]
                counters["dropped_keys"] += 1
        if _serialized_size(cleaned) > RCA_ATTRIBUTES_BYTES_LIMIT:
            return None, counters

    return cleaned, counters


def sanitize_artifact_for_analysis(
    artifact: RCAArtifact,
) -> tuple[RCAArtifact | None, tuple[str, ...]]:
    cleaned_attributes, counters = _sanitize_attributes(artifact.attributes)

    cleaned_limitations = tuple(
        text
        if len(text) <= RCA_ATTRIBUTE_STRING_LIMIT
        else text[:RCA_ATTRIBUTE_STRING_LIMIT] + " " + TRUNCATION_MARKER
        for text in artifact.limitations
    )
    truncated_limitations = sum(
        1
        for text, cleaned in zip(
            artifact.limitations, cleaned_limitations, strict=True
        )
        if cleaned != text
    )

    if cleaned_attributes is None:
        return None, (
            f"Excluded artifact {artifact.artifact_id}: its attribute payload "
            f"exceeded {RCA_ATTRIBUTES_BYTES_LIMIT} bytes after redaction "
            "and truncation, so it was not analyzed.",
        )

    parts: list[str] = []
    if counters["sensitive"]:
        parts.append(
            f"redacted {counters['sensitive']} sensitive attribute field(s)"
        )
    if counters["secrets"]:
        parts.append(
            f"scrubbed {counters['secrets']} secret-like attribute value(s)"
        )
    if counters["strings"] or counters["items"] or counters["depth"]:
        parts.append("truncated oversized attribute structure(s)")
    if counters["dropped_keys"]:
        parts.append("dropped oversized bulk attribute key(s)")
    if truncated_limitations:
        parts.append("truncated oversized limitation text(s)")

    if not parts and cleaned_attributes == artifact.attributes and (
        cleaned_limitations == artifact.limitations
    ):
        return artifact, ()

    notes = (
        f"Artifact {artifact.artifact_id}: " + "; ".join(parts) + ".",
    ) if parts else ()

    return artifact.model_copy(
        update={
            "attributes": cleaned_attributes,
            "limitations": cleaned_limitations,
        }
    ), notes
