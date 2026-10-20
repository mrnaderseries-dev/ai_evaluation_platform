"""Typed, framework-independent contracts for agent-aware change analysis.

These contracts are deliberately free of database and agent-runtime concerns.
They describe *what changed*, *what should be evaluated*, and *what plan was
produced* so that the planning core stays deterministic and reproducible.

Design rules enforced here:

* Raw configuration values are never stored on a change. Only redacted
  fingerprints are kept, because config values may contain credentials,
  personal data, or large payloads.
* Missing values are missing evidence, not removals.
* Unknown stays unknown: an unrecognised config path yields a generic
  ``configuration`` change, never a guessed semantic classification.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

ChangeType = Literal[
    "code",
    "model",
    "prompt",
    "retriever",
    "embedding",
    "vector_store",
    "chunking",
    "tool",
    "context",
    "policy",
    "dependency",
    "configuration",
    "unknown",
]

ChangeOperation = Literal["added", "removed", "modified", "unknown"]

ChangeSource = Literal["declared", "version_diff", "artifact_diff"]

ChangeCompleteness = Literal["complete", "partial", "unknown"]

RiskLevel = Literal["critical", "high", "medium", "low"]

PlanStatus = Literal["draft", "ready", "blocked"]

ReasonKind = Literal["change", "signal", "rca_finding", "baseline"]

_REASON_REF_PATTERN = re.compile(r"^(?P<kind>[a-z_]+):(?P<ref>[^\s:]+)$")

_REASON_KINDS: frozenset[str] = frozenset(
    {"change", "signal", "rca_finding", "baseline"}
)


def make_reason_ref(kind: str, ref: str) -> str:
    """Build a canonical ``kind:ref`` reason reference.

    The kind is one of ``change``, ``signal``, ``rca_finding`` or ``baseline``.
    The reference is an opaque identifier of the originating record.
    """
    if not isinstance(kind, str) or kind.strip().lower() not in _REASON_KINDS:
        raise ValueError(
            f"kind must be one of {sorted(_REASON_KINDS)}, got {kind!r}."
        )

    if not isinstance(ref, str) or not ref.strip():
        raise ValueError("ref must be a non-empty string.")

    return f"{kind.strip().lower()}:{ref.strip()}"


def parse_reason_ref(value: str) -> tuple[str, str]:
    """Split a ``kind:ref`` reason reference into its two parts."""
    if not isinstance(value, str):
        raise ValueError("reason reference must be a string.")

    match = _REASON_REF_PATTERN.match(value.strip())

    if match is None:
        raise ValueError(
            f"reason reference {value!r} must look like 'kind:ref'."
        )

    kind = match.group("kind")

    if kind not in _REASON_KINDS:
        raise ValueError(
            f"reason reference kind {kind!r} must be one of "
            f"{sorted(_REASON_KINDS)}."
        )

    return kind, match.group("ref")


class _FrozenModel(BaseModel):
    """Base for planning contracts: frozen, strict, and fully typed."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class AgentDescriptor(_FrozenModel):
    """Describes the logical agent and its declared type and capabilities.

    This is the in-memory view of an agent. Persisted identity lives in the
    ``agents`` / ``agent_versions`` storage models and is referenced by id.
    """

    agent_id: str = Field(min_length=1)
    agent_type: str = Field(min_length=1)
    capabilities: frozenset[str] = frozenset()
    agent_version_id: str | None = Field(default=None, min_length=1)
    agent_version: str | None = Field(default=None, min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("capabilities")
    @classmethod
    def capabilities_must_be_non_empty_strings(
        cls,
        value: frozenset[str],
    ) -> frozenset[str]:
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("capabilities must contain non-empty strings.")

        return value

    @field_validator("agent_type")
    @classmethod
    def agent_type_must_be_normalised(cls, value: str) -> str:
        normalised = value.strip().lower()

        if not normalised:
            raise ValueError("agent_type must not be empty.")

        return normalised


class ComponentChange(_FrozenModel):
    """One confirmed difference between two versions of the same agent.

    Raw before/after values are intentionally absent: config values may hold
    credentials, personal data, or large payloads. Only redacted
    fingerprints are kept.
    """

    component: str = Field(min_length=1)
    change_type: ChangeType
    operation: ChangeOperation = "unknown"
    source: ChangeSource = "version_diff"
    evidence_refs: tuple[str, ...] = ()
    before_fingerprint: str | None = Field(default=None, min_length=1)
    after_fingerprint: str | None = Field(default=None, min_length=1)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    detail: str | None = Field(default=None, min_length=1)

    @field_validator("component")
    @classmethod
    def component_must_be_normalised(cls, value: str) -> str:
        normalised = value.strip().lower()

        if not normalised:
            raise ValueError("component must not be empty.")

        return normalised

    @field_validator("evidence_refs")
    @classmethod
    def evidence_refs_must_be_non_empty_strings(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError(
                    "evidence_refs must contain non-empty strings."
                )

        return value


class ChangeDescription(_FrozenModel):
    """Known differences between two versions of the same agent.

    An empty ``changes`` tuple does *not* prove behavioural equivalence when
    ``completeness`` is not ``complete``; that distinction is carried
    explicitly by ``completeness`` and ``limitations``.
    """

    change_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    from_agent_version_id: str = Field(min_length=1)
    to_agent_version_id: str = Field(min_length=1)
    changes: tuple[ComponentChange, ...] = ()
    completeness: ChangeCompleteness = "unknown"
    limitations: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("changes")
    @classmethod
    def changes_must_be_deterministically_ordered(
        cls,
        value: tuple[ComponentChange, ...],
    ) -> tuple[ComponentChange, ...]:
        keys = [
            (
                change.component,
                change.change_type,
                change.operation,
                change.source,
                change.after_fingerprint or "",
            )
            for change in value
        ]

        if keys != sorted(keys):
            raise ValueError(
                "changes must be sorted deterministically by "
                "(component, change_type, operation, source, "
                "after_fingerprint)."
            )

        return value


class DatasetDescriptor(_FrozenModel):
    """What an existing dataset version is able to test.

    This mirrors — without duplicating — the persisted ``DatasetVersion``
    record. Coverage must be declared explicitly; a dataset name never
    implies coverage.
    """

    dataset_version_id: str = Field(min_length=1)
    dataset_id: str | None = Field(default=None, min_length=1)
    capabilities_covered: frozenset[str] = frozenset()
    requirement_types_covered: frozenset[str] = frozenset()
    agent_types_supported: frozenset[str] | None = None
    quality_status: str = Field(default="unknown", min_length=1)
    validation_evidence_refs: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    is_active: bool = True
    estimated_cost: float | None = Field(default=None, ge=0.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("capabilities_covered", "requirement_types_covered")
    @classmethod
    def coverage_must_be_non_empty_strings(
        cls,
        value: frozenset[str],
    ) -> frozenset[str]:
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError(
                    "coverage declarations must be non-empty strings."
                )

        return value

    @field_validator("quality_status")
    @classmethod
    def quality_status_must_be_normalised(cls, value: str) -> str:
        normalised = value.strip().lower()

        if not normalised:
            raise ValueError("quality_status must not be empty.")

        return normalised


class EvaluationRequirement(_FrozenModel):
    """One thing to evaluate.

    A requirement is neither a dataset nor an execution result. Every
    requirement records why it exists through ``reason_refs``, which are
    canonical ``kind:ref`` references (see :func:`make_reason_ref`).
    """

    requirement_id: str = Field(min_length=1)
    requirement_type: str = Field(min_length=1)
    priority: RiskLevel = "medium"
    reason_refs: tuple[str, ...] = Field(min_length=1)
    required_capabilities: frozenset[str] = frozenset()
    required_metrics: tuple[str, ...] = ()
    acceptance_criteria: dict[str, Any] = Field(default_factory=dict)
    policy_ref: str | None = Field(default=None, min_length=1)

    @field_validator("reason_refs")
    @classmethod
    def reason_refs_must_be_well_formed(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        for item in value:
            parse_reason_ref(item)

        return value

    @field_validator("requirement_type")
    @classmethod
    def requirement_type_must_be_normalised(cls, value: str) -> str:
        normalised = value.strip().lower()

        if not normalised:
            raise ValueError("requirement_type must not be empty.")

        return normalised


class DatasetMatch(_FrozenModel):
    """A dataset version selected for one requirement, with its rationale."""

    requirement_id: str = Field(min_length=1)
    dataset_version_id: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    coverage_score: float = Field(ge=0.0, le=1.0)
    quality_status: str = Field(min_length=1)
    matched_capabilities: frozenset[str] = frozenset()

    @field_validator("rationale")
    @classmethod
    def rationale_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("rationale must not be blank.")

        return value.strip()


class CoverageGap(_FrozenModel):
    """A requirement that no eligible dataset version could cover."""

    requirement_id: str = Field(min_length=1)
    requirement_type: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    considered_dataset_version_ids: tuple[str, ...] = ()

    @field_validator("reason")
    @classmethod
    def reason_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reason must not be blank.")

        return value.strip()


class EvaluationPlan(_FrozenModel):
    """A deterministic, explainable evaluation plan.

    The plan is reproducible from the recorded inputs plus the strategy and
    policy versions. It contains no execution results: planning is not
    execution.
    """

    plan_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    agent_type: str = Field(min_length=1)
    from_agent_version_id: str = Field(min_length=1)
    to_agent_version_id: str = Field(min_length=1)
    requirements: tuple[EvaluationRequirement, ...] = ()
    dataset_matches: tuple[DatasetMatch, ...] = ()
    coverage_gaps: tuple[CoverageGap, ...] = ()
    selected_dataset_version_ids: tuple[str, ...] = ()
    selection_rationale: tuple[str, ...] = ()
    strategy_name: str = Field(min_length=1)
    strategy_version: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    change_completeness: ChangeCompleteness = "unknown"
    status: PlanStatus = "draft"
    limitations: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("agent_type")
    @classmethod
    def agent_type_must_be_normalised(cls, value: str) -> str:
        normalised = value.strip().lower()

        if not normalised:
            raise ValueError("agent_type must not be empty.")

        return normalised

    @model_validator(mode="after")
    def validate_consistency(self) -> "EvaluationPlan":
        requirement_ids = {item.requirement_id for item in self.requirements}

        if len(requirement_ids) != len(self.requirements):
            raise ValueError("requirement_id values must be unique.")

        for match in self.dataset_matches:
            if match.requirement_id not in requirement_ids:
                raise ValueError(
                    "dataset match references unknown requirement "
                    f"{match.requirement_id!r}."
                )

        for gap in self.coverage_gaps:
            if gap.requirement_id not in requirement_ids:
                raise ValueError(
                    "coverage gap references unknown requirement "
                    f"{gap.requirement_id!r}."
                )

        matched_requirements = {
            match.requirement_id for match in self.dataset_matches
        }
        gapped_requirements = {gap.requirement_id for gap in self.coverage_gaps}

        overlap = matched_requirements.intersection(gapped_requirements)

        if overlap:
            raise ValueError(
                "requirements cannot be both covered and uncovered: "
                + ", ".join(sorted(overlap))
            )

        return self



class RiskEvidence(_FrozenModel):
    """Observed risk drawn from Signals or RCA findings.

    Signals and RCA findings indicate observed risk; they do not prove the
    cause of a change. This contract carries the *scope* of the evidence so
    that it can be matched against the target agent and capability, and it
    keeps the originating reference for traceability.
    """

    evidence_id: str = Field(min_length=1)
    kind: ReasonKind
    requirement_type: str = Field(min_length=1)
    agent_id: str | None = Field(default=None, min_length=1)
    capability: str | None = Field(default=None, min_length=1)
    priority: RiskLevel = "medium"
    rationale: str = Field(min_length=1)
    recurrence_count: int = Field(default=1, ge=1)

    @field_validator("requirement_type")
    @classmethod
    def requirement_type_must_be_normalised(cls, value: str) -> str:
        normalised = value.strip().lower()

        if not normalised:
            raise ValueError("requirement_type must not be empty.")

        return normalised
