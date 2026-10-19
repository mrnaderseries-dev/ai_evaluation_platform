from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id() -> str:
    return str(uuid4())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

class Agent(Base, TimestampMixin):
    __tablename__ = "agents"

    agent_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False, default=dict)

    versions: Mapped[list[AgentVersion]] = relationship(back_populates="agent", cascade="all, delete-orphan")
    runs: Mapped[list[Run]] = relationship(back_populates="agent")
    policy_bindings: Mapped[list[AgentPolicy]] = relationship(back_populates="agent", cascade="all, delete-orphan")


class AgentVersion(Base, TimestampMixin):
    __tablename__ = "agent_versions"
    __table_args__ = (UniqueConstraint("agent_id", "version", name="uq_agent_version"),)

    agent_version_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.agent_id", ondelete="CASCADE"), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(100), nullable=False)
    code_hash: Mapped[str | None] = mapped_column(String(128))
    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    prompt_version: Mapped[str | None] = mapped_column(String(100))
    model_provider: Mapped[str | None] = mapped_column(String(100))
    model_name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")

    agent: Mapped[Agent] = relationship(back_populates="versions")
    runs: Mapped[list[Run]] = relationship(back_populates="agent_version")


class Run(Base, TimestampMixin):
    __tablename__ = "runs"
    __table_args__ = (
        Index("ix_runs_agent_started", "agent_id", "started_at"),
        Index("ix_runs_status_started", "status", "started_at"),
    )

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.agent_id", ondelete="RESTRICT"), nullable=False, index=True)
    agent_version_id: Mapped[str | None] = mapped_column(ForeignKey("agent_versions.agent_version_id", ondelete="SET NULL"), index=True)
    external_run_id: Mapped[str | None] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running", index=True)
    input_text: Mapped[str | None] = mapped_column(Text)
    final_answer: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    monitoring_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    monitoring_claim_id: Mapped[str | None] = mapped_column(String(64))
    monitoring_claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_type: Mapped[str | None] = mapped_column(String(255))
    error_message: Mapped[str | None] = mapped_column(Text)
    trace_id: Mapped[str | None] = mapped_column(String(128), index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False, default=dict)

    agent: Mapped[Agent] = relationship(back_populates="runs")
    agent_version: Mapped[AgentVersion | None] = relationship(back_populates="runs")
    events: Mapped[list[Event]] = relationship(back_populates="run", cascade="all, delete-orphan", order_by="Event.sequence_number")
    evaluation_results: Mapped[list[EvaluationResult]] = relationship(back_populates="run")

class Policy(Base, TimestampMixin):
    __tablename__ = "policies"

    policy_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    policy_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    owner: Mapped[str | None] = mapped_column(String(255))

    versions: Mapped[list[PolicyVersion]] = relationship(back_populates="policy", cascade="all, delete-orphan")


class PolicyVersion(Base, TimestampMixin):
    __tablename__ = "policy_versions"
    __table_args__ = (UniqueConstraint("policy_id", "version", name="uq_policy_version"),)

    policy_version_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    policy_id: Mapped[str] = mapped_column(ForeignKey("policies.policy_id", ondelete="CASCADE"), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(100), nullable=False)
    rules: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    content_hash: Mapped[str | None] = mapped_column(String(128))
    change_summary: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)

    policy: Mapped[Policy] = relationship(back_populates="versions")
    bindings: Mapped[list[AgentPolicy]] = relationship(back_populates="policy_version")
    decisions: Mapped[list[PolicyDecision]] = relationship(back_populates="policy_version")


class AgentPolicy(Base, TimestampMixin):
    """An effective-dated binding between an agent and a specific policy version."""
    __tablename__ = "agent_policies"
    __table_args__ = (
        UniqueConstraint("agent_id", "policy_version_id", "effective_from", name="uq_agent_policy_binding"),
        Index("ix_agent_policies_effective", "agent_id", "effective_from", "effective_to"),
    )

    agent_policy_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.agent_id", ondelete="CASCADE"), nullable=False)
    policy_version_id: Mapped[str] = mapped_column(ForeignKey("policy_versions.policy_version_id", ondelete="RESTRICT"), nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    configuration_override: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    agent: Mapped[Agent] = relationship(back_populates="policy_bindings")
    policy_version: Mapped[PolicyVersion] = relationship(back_populates="bindings")

class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence_number", name="uq_event_run_sequence"),
        Index("ix_events_run_time", "run_id", "occurred_at"),
        Index("ix_events_type_time", "event_type", "occurred_at"),
        Index("ix_events_correlation", "correlation_id"),
    )

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False, default="1.0")
    system_id: Mapped[str] = mapped_column(String(255), nullable=False, default="gaia-agent")
    system_version: Mapped[str | None] = mapped_column(String(100))
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(150), nullable=False, index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    parent_event_id: Mapped[str | None] = mapped_column(ForeignKey("events.event_id", ondelete="SET NULL"), index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(128))
    causation_id: Mapped[str | None] = mapped_column(String(64))
    step_id: Mapped[str | None] = mapped_column(String(128), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    run: Mapped[Run] = relationship(back_populates="events")
    parent_event: Mapped[Event | None] = relationship(remote_side=[event_id], back_populates="child_events")
    child_events: Mapped[list[Event]] = relationship(back_populates="parent_event")
    behavior_detail: Mapped[AgentBehaviorEvent | None] = relationship(back_populates="event", cascade="all, delete-orphan", uselist=False)
    context_detail: Mapped[ContextEvent | None] = relationship(back_populates="event", cascade="all, delete-orphan", uselist=False)
    execution_detail: Mapped[ExecutionEvent | None] = relationship(back_populates="event", cascade="all, delete-orphan", uselist=False)
    policy_decisions: Mapped[list[PolicyDecision]] = relationship(back_populates="event")


class EventObservation(Base):
    """Versioned normalized projection of a source event, never an anomaly finding."""
    __tablename__ = "event_observations"
    __table_args__ = (
        UniqueConstraint("source_event_id", "projection_version", name="uq_observation_source_projection"),
        Index("ix_observations_agent_time", "agent_id", "observed_at"),
        Index("ix_observations_run_sequence", "run_id", "sequence_number"),
        Index("ix_observations_type_time", "event_type", "occurred_at"),
    )

    observation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_event_id: Mapped[str] = mapped_column(ForeignKey("events.event_id", ondelete="CASCADE"), nullable=False, index=True)
    projection_version: Mapped[str] = mapped_column(String(32), nullable=False)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.agent_id", ondelete="RESTRICT"), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(150), nullable=False)
    system_id: Mapped[str] = mapped_column(String(255), nullable=False)
    system_version: Mapped[str | None] = mapped_column(String(100))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_event_id: Mapped[str | None] = mapped_column(String(64))
    correlation_id: Mapped[str | None] = mapped_column(String(128))
    causation_id: Mapped[str | None] = mapped_column(String(64))
    step_id: Mapped[str | None] = mapped_column(String(128))
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class AgentBehaviorEvent(Base):
    __tablename__ = "agent_behavior_events"

    event_id: Mapped[str] = mapped_column(ForeignKey("events.event_id", ondelete="CASCADE"), primary_key=True)
    behavior_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    action: Mapped[str | None] = mapped_column(String(255))
    decision_summary: Mapped[str | None] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    outcome: Mapped[str | None] = mapped_column(String(100))
    related_event_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    event: Mapped[Event] = relationship(back_populates="behavior_detail")


class ContextEvent(Base):
    __tablename__ = "context_events"

    event_id: Mapped[str] = mapped_column(ForeignKey("events.event_id", ondelete="CASCADE"), primary_key=True)
    context_type: Mapped[str] = mapped_column(String(100), nullable=False, default="assembled")
    query_text: Mapped[str | None] = mapped_column(Text)
    resource_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    selected_resource_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    included_resource_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    token_count: Mapped[int | None] = mapped_column(Integer)
    context_hash: Mapped[str | None] = mapped_column(String(128))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    event: Mapped[Event] = relationship(back_populates="context_detail")
    resources: Mapped[list[ContextResource]] = relationship(back_populates="context_event", cascade="all, delete-orphan")


class ContextResource(Base):
    """One resource considered for a context; distinguishes retrieved from actually included."""
    __tablename__ = "context_resources"
    __table_args__ = (
        Index("ix_context_resource_identity", "resource_type", "resource_id", "resource_version"),
        CheckConstraint("rank IS NULL OR rank >= 0", name="ck_context_resource_rank_nonnegative"),
    )

    context_resource_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    context_event_id: Mapped[str] = mapped_column(ForeignKey("context_events.event_id", ondelete="CASCADE"), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(512), nullable=False)
    resource_version: Mapped[str | None] = mapped_column(String(255))
    source: Mapped[str | None] = mapped_column(String(255))
    rank: Mapped[int | None] = mapped_column(Integer)
    relevance_score: Mapped[float | None] = mapped_column(Float)
    retrieved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    included_in_prompt: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    content_hash: Mapped[str | None] = mapped_column(String(128))
    token_count: Mapped[int | None] = mapped_column(Integer)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False, default=dict)

    context_event: Mapped[ContextEvent] = relationship(back_populates="resources")


class ExecutionEvent(Base):
    __tablename__ = "execution_events"
    __table_args__ = (Index("ix_execution_tool_time", "tool_name", "started_at"),)

    event_id: Mapped[str] = mapped_column(ForeignKey("events.event_id", ondelete="CASCADE"), primary_key=True)
    execution_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    tool_name: Mapped[str | None] = mapped_column(String(255), index=True)
    operation: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str | None] = mapped_column(String(32), index=True)
    attempt_number: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    input_hash: Mapped[str | None] = mapped_column(String(128))
    output_hash: Mapped[str | None] = mapped_column(String(128))
    error_type: Mapped[str | None] = mapped_column(String(255))
    error_message: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    event: Mapped[Event] = relationship(back_populates="execution_detail")


class PersistedSignal(Base):
    __tablename__ = "persisted_signals"
    __table_args__ = (
        CheckConstraint("scope IN ('run', 'evaluation', 'window')", name="ck_persisted_signal_scope"),
        CheckConstraint(
            "(scope <> 'run' OR (run_id IS NOT NULL AND evaluation_run_id IS NULL AND window_start IS NULL AND window_end IS NULL)) AND "
            "(scope <> 'evaluation' OR (evaluation_run_id IS NOT NULL AND run_id IS NULL AND window_start IS NULL AND window_end IS NULL)) AND "
            "(scope <> 'window' OR (window_start IS NOT NULL AND window_end IS NOT NULL AND window_start < window_end AND run_id IS NULL))",
            name="ck_persisted_signal_shape",
        ),
        Index("ix_persisted_signals_agent_time", "agent_id", "detected_at"),
        Index("ix_persisted_signals_type_time", "signal_type", "detected_at"),
        Index("uq_persisted_signals_dedupe_key", "dedupe_key", unique=True),
        Index("uq_persisted_signal_source_projection", "source_event_id", "origin", "projection_version", unique=True),
    )

    signal_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    origin: Mapped[str] = mapped_column(String(32), nullable=False, default="detector", server_default="detector", index=True)
    source_event_id: Mapped[str | None] = mapped_column(ForeignKey("events.event_id", ondelete="CASCADE"), index=True)
    source_observation_id: Mapped[str | None] = mapped_column(ForeignKey("event_observations.observation_id", ondelete="CASCADE"), index=True)
    projection_version: Mapped[str | None] = mapped_column(String(32))
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.agent_id", ondelete="RESTRICT"), nullable=False, index=True)
    signal_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.run_id", ondelete="CASCADE"), index=True)
    evaluation_run_id: Mapped[str | None] = mapped_column(ForeignKey("evaluation_runs.evaluation_run_id", ondelete="CASCADE"), index=True)
    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    payload_type: Mapped[str] = mapped_column(String(100), nullable=False)
    detector_name: Mapped[str | None] = mapped_column(String(255))
    dedupe_key: Mapped[str | None] = mapped_column(String(64))

class PersistedEvidenceRecord(Base):
    """Durable, agent-agnostic evidence registry."""

    __tablename__ = "evidence_records"
    __table_args__ = (
        Index("ix_evidence_domain_observed", "domain", "observed_at"),
        Index("ix_evidence_subject_observed",
              "subject_type", "subject_id", "observed_at"),
        Index("ix_evidence_agent_observed", "agent_id", "observed_at"),
        Index("ix_evidence_system_observed", "system_id", "observed_at"),
        Index("ix_evidence_run", "run_id"),
        Index("ix_evidence_evaluation_run", "evaluation_run_id"),
    )

    evidence_id: Mapped[str] = mapped_column(
        String(64), primary_key=True
    )

    # Polymorphic source: signal, event, trace, metric, log, etc.
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    evidence_kind: Mapped[str] = mapped_column(String(100), nullable=False)
    domain: Mapped[str] = mapped_column(String(100), nullable=False)

    # Evidence may describe an agent, service, model, component, or system.
    subject_type: Mapped[str] = mapped_column(String(100), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(255), nullable=False)
    agent_id: Mapped[str | None] = mapped_column(String(64), index=True)

    system_id: Mapped[str | None] = mapped_column(String(255))
    system_version: Mapped[str | None] = mapped_column(String(100))
    run_id: Mapped[str | None] = mapped_column(String(64))
    evaluation_run_id: Mapped[str | None] = mapped_column(String(64))

    trace_id: Mapped[str | None] = mapped_column(String(128))
    span_id: Mapped[str | None] = mapped_column(String(128))
    parent_span_id: Mapped[str | None] = mapped_column(String(128))
    component_id: Mapped[str | None] = mapped_column(String(255))

    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    limitations: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    source_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PersistedRelationship(Base):
    """A versioned analytical relationship; never assumed causal by default."""

    __tablename__ = "persisted_relationships"
    __table_args__ = (
        CheckConstraint(
            "status IN ('candidate', 'validated', 'rejected')",
            name="ck_persisted_relationship_status",
        ),
        UniqueConstraint(
            "analysis_id", "relationship_key",
            name="uq_relationship_analysis_key",
        ),
        Index("ix_relationship_status_discovered", "status", "discovered_at"),
        Index("ix_relationship_type_discovered",
              "relationship_type", "discovered_at"),
    )

    relationship_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=new_id
    )

    # Analysis provenance: re-analysis creates a distinct historical result.
    analysis_id: Mapped[str] = mapped_column(
        ForeignKey("analyses.analysis_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    relationship_key: Mapped[str] = mapped_column(
        String(128), nullable=False
    )

    relationship_type: Mapped[str] = mapped_column(
        String(100), nullable=False
    )
    basis: Mapped[str] = mapped_column(String(100), nullable=False)

    # Detection produces candidates; validation must be an explicit action.
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="candidate", server_default="candidate"
    )

    algorithm_name: Mapped[str] = mapped_column(
        String(255), nullable=False
    )
    algorithm_version: Mapped[str] = mapped_column(
        String(100), nullable=False
    )

    # Only common scope belongs here. Per-evidence scope stays on each record.
    scope: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    limitations: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )

    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    validated_by: Mapped[str | None] = mapped_column(String(255))
    validation_notes: Mapped[str | None] = mapped_column(Text)


class RelationshipEvidence(Base):
    """Explicit, ordered evidence links for a persisted relationship."""

    __tablename__ = "relationship_evidence"
    __table_args__ = (
        CheckConstraint(
            "role IN ('supporting', 'counter', 'context')",
            name="ck_relationship_evidence_role",
        ),
        UniqueConstraint(
            "relationship_id", "evidence_id", "role",
            name="uq_relationship_evidence_role",
        ),
        Index("ix_relationship_evidence_evidence", "evidence_id"),
    )

    relationship_evidence_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=new_id
    )
    relationship_id: Mapped[str] = mapped_column(
        ForeignKey(
            "persisted_relationships.relationship_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    evidence_id: Mapped[str] = mapped_column(
        ForeignKey("evidence_records.evidence_id", ondelete="RESTRICT"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(
        String(32), nullable=False
    )
    evidence_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
class PolicyDecision(Base):
    __tablename__ = "policy_decisions"
    __table_args__ = (
        Index("ix_policy_decisions_policy_time", "policy_version_id", "decided_at"),
        Index("ix_policy_decisions_outcome_time", "outcome", "decided_at"),
    )

    policy_decision_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    event_id: Mapped[str | None] = mapped_column(ForeignKey("events.event_id", ondelete="SET NULL"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False, index=True)
    policy_version_id: Mapped[str] = mapped_column(ForeignKey("policy_versions.policy_version_id", ondelete="RESTRICT"), nullable=False, index=True)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # allowed / blocked / review
    rule_id: Mapped[str | None] = mapped_column(String(255))
    reason: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str | None] = mapped_column(String(255))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    human_review_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    human_review_outcome: Mapped[str | None] = mapped_column(String(100))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    event: Mapped[Event | None] = relationship(back_populates="policy_decisions")
    policy_version: Mapped[PolicyVersion] = relationship(back_populates="decisions")


class Dataset(Base, TimestampMixin):
    __tablename__ = "datasets"

    dataset_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(512))
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False, default=dict)

    versions: Mapped[list[DatasetVersion]] = relationship(back_populates="dataset", cascade="all, delete-orphan")


class DatasetVersion(Base, TimestampMixin):
    __tablename__ = "dataset_versions"
    __table_args__ = (UniqueConstraint("dataset_id", "version", name="uq_dataset_version"),)

    dataset_version_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.dataset_id", ondelete="CASCADE"), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(100), nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(128))
    case_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    storage_uri: Mapped[str | None] = mapped_column(String(1024))
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False, default=dict)

    dataset: Mapped[Dataset] = relationship(back_populates="versions")
    cases: Mapped[list[EvaluationCase]] = relationship(back_populates="dataset_version", cascade="all, delete-orphan")
    evaluation_runs: Mapped[list[EvaluationRun]] = relationship(back_populates="dataset_version")


class EvaluationCase(Base, TimestampMixin):
    __tablename__ = "evaluation_cases"
    __table_args__ = (UniqueConstraint("dataset_version_id", "external_case_id", name="uq_case_external_id_per_version"),)

    case_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    dataset_version_id: Mapped[str] = mapped_column(ForeignKey("dataset_versions.dataset_version_id", ondelete="CASCADE"), nullable=False, index=True)
    external_case_id: Mapped[str | None] = mapped_column(String(255))
    question: Mapped[str] = mapped_column(Text, nullable=False)
    expected_answer: Mapped[str | None] = mapped_column(Text)
    task_type: Mapped[str | None] = mapped_column(String(100), index=True)
    difficulty: Mapped[str | None] = mapped_column(String(50))
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False, default=dict)

    dataset_version: Mapped[DatasetVersion] = relationship(back_populates="cases")
    results: Mapped[list[EvaluationResult]] = relationship(back_populates="evaluation_case")


class EvaluationRun(Base, TimestampMixin):
    __tablename__ = "evaluation_runs"
    __table_args__ = (Index("ix_evaluation_runs_status_created", "status", "created_at"),)

    evaluation_run_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    dataset_version_id: Mapped[str | None] = mapped_column(ForeignKey("dataset_versions.dataset_version_id", ondelete="RESTRICT"), index=True)
    agent_version_id: Mapped[str] = mapped_column(ForeignKey("agent_versions.agent_version_id", ondelete="RESTRICT"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    evaluator_version: Mapped[str | None] = mapped_column(String(100))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_cases: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    passed_cases: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_cases: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    dataset_version: Mapped[DatasetVersion] = relationship(back_populates="evaluation_runs")
    results: Mapped[list[EvaluationResult]] = relationship(back_populates="evaluation_run", cascade="all, delete-orphan")


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"
    __table_args__ = (
        Index(
            "uq_evaluation_result_online",
            "run_id",
            "metric_name",
            unique=True,
            postgresql_where=text("evaluation_run_id IS NULL AND case_id IS NULL"),
            sqlite_where=text("evaluation_run_id IS NULL AND case_id IS NULL"),
        ),
        Index(
            "uq_evaluation_result_offline",
            "evaluation_run_id",
            "run_id",
            "metric_name",
            unique=True,
            postgresql_where=text("evaluation_run_id IS NOT NULL AND case_id IS NULL"),
            sqlite_where=text("evaluation_run_id IS NOT NULL AND case_id IS NULL"),
        ),
        Index(
            "uq_evaluation_result_benchmark",
            "evaluation_run_id",
            "case_id",
            "metric_name",
            unique=True,
            postgresql_where=text("evaluation_run_id IS NOT NULL AND case_id IS NOT NULL"),
            sqlite_where=text("evaluation_run_id IS NOT NULL AND case_id IS NOT NULL"),
        ),
        CheckConstraint(
            "evaluation_run_id IS NOT NULL OR case_id IS NULL",
            name="ck_evaluation_result_case_requires_run",
        ),
    )

    evaluation_result_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    evaluation_run_id: Mapped[str | None] = mapped_column(ForeignKey("evaluation_runs.evaluation_run_id", ondelete="CASCADE"), index=True)
    case_id: Mapped[str | None] = mapped_column(ForeignKey("evaluation_cases.case_id", ondelete="RESTRICT"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False, index=True)
    metric_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    predicted_answer: Mapped[str | None] = mapped_column(Text)
    passed: Mapped[bool | None] = mapped_column(Boolean, index=True)
    score: Mapped[float | None] = mapped_column(Float)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    token_usage: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    evaluator_feedback: Mapped[str | None] = mapped_column(Text)
    failure_category: Mapped[str | None] = mapped_column(String(100), index=True)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    evaluation_run: Mapped[EvaluationRun] = relationship(back_populates="results")
    evaluation_case: Mapped[EvaluationCase] = relationship(back_populates="results")
    run: Mapped[Run | None] = relationship(back_populates="evaluation_results")



class MLModel(Base, TimestampMixin):
    __tablename__ = "ml_models"

    ml_model_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    model_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    task: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)

    versions: Mapped[list[ModelVersion]] = relationship(back_populates="model", cascade="all, delete-orphan")


class ModelVersion(Base, TimestampMixin):
    __tablename__ = "model_versions"
    __table_args__ = (UniqueConstraint("ml_model_id", "version", name="uq_ml_model_version"),)

    model_version_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    ml_model_id: Mapped[str] = mapped_column(ForeignKey("ml_models.ml_model_id", ondelete="CASCADE"), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(100), nullable=False)
    artifact_uri: Mapped[str | None] = mapped_column(String(1024))
    artifact_hash: Mapped[str | None] = mapped_column(String(128))
    framework: Mapped[str | None] = mapped_column(String(100))
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    stage: Mapped[str] = mapped_column(String(50), nullable=False, default="development", index=True)

    model: Mapped[MLModel] = relationship(back_populates="versions")
    training_runs: Mapped[list[TrainingRun]] = relationship(back_populates="model_version")


class PipelineVersion(Base, TimestampMixin):
    __tablename__ = "pipeline_versions"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_pipeline_version"),)

    pipeline_version_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(100), nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    code_hash: Mapped[str | None] = mapped_column(String(128))

    components: Mapped[list[PipelineComponent]] = relationship(back_populates="pipeline_version", cascade="all, delete-orphan")
    training_runs: Mapped[list[TrainingRun]] = relationship(back_populates="pipeline_version")


class PipelineComponent(Base):
    __tablename__ = "pipeline_components"
    __table_args__ = (UniqueConstraint("pipeline_version_id", "component_key", name="uq_pipeline_component_key"),)

    pipeline_component_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    pipeline_version_id: Mapped[str] = mapped_column(ForeignKey("pipeline_versions.pipeline_version_id", ondelete="CASCADE"), nullable=False, index=True)
    component_key: Mapped[str] = mapped_column(String(255), nullable=False)
    component_type: Mapped[str] = mapped_column(String(100), nullable=False)
    component_version: Mapped[str | None] = mapped_column(String(100))
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    input_keys: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    output_keys: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    pipeline_version: Mapped[PipelineVersion] = relationship(back_populates="components")


class Experiment(Base, TimestampMixin):
    __tablename__ = "experiments"

    experiment_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    hypothesis: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    training_runs: Mapped[list[TrainingRun]] = relationship(back_populates="experiment")


class TrainingRun(Base, TimestampMixin):
    __tablename__ = "training_runs"
    __table_args__ = (Index("ix_training_runs_status_started", "status", "started_at"),)

    training_run_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    experiment_id: Mapped[str | None] = mapped_column(ForeignKey("experiments.experiment_id", ondelete="SET NULL"), index=True)
    model_version_id: Mapped[str | None] = mapped_column(ForeignKey("model_versions.model_version_id", ondelete="SET NULL"), index=True)
    pipeline_version_id: Mapped[str | None] = mapped_column(ForeignKey("pipeline_versions.pipeline_version_id", ondelete="SET NULL"), index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    logs_uri: Mapped[str | None] = mapped_column(String(1024))

    experiment: Mapped[Experiment | None] = relationship(back_populates="training_runs")
    model_version: Mapped[ModelVersion | None] = relationship(back_populates="training_runs")
    pipeline_version: Mapped[PipelineVersion | None] = relationship(back_populates="training_runs")



class Analysis(Base, TimestampMixin):
    __tablename__ = "analyses"

    analysis_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    analysis_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="completed")
    source_evaluation_run_id: Mapped[str | None] = mapped_column(ForeignKey("evaluation_runs.evaluation_run_id", ondelete="SET NULL"), index=True)
    summary: Mapped[str | None] = mapped_column(Text)
    findings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class DifferentialAnalysis(Base):
    __tablename__ = "differential_analyses"

    differential_analysis_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analyses.analysis_id", ondelete="CASCADE"), nullable=False, unique=True)
    baseline_evaluation_run_id: Mapped[str] = mapped_column(ForeignKey("evaluation_runs.evaluation_run_id", ondelete="RESTRICT"), nullable=False)
    candidate_evaluation_run_id: Mapped[str] = mapped_column(ForeignKey("evaluation_runs.evaluation_run_id", ondelete="RESTRICT"), nullable=False)
    metric_deltas: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    slice_deltas: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    statistical_tests: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class DriftAnalysis(Base):
    __tablename__ = "drift_analyses"

    drift_analysis_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analyses.analysis_id", ondelete="CASCADE"), nullable=False, unique=True)
    drift_type: Mapped[str] = mapped_column(String(100), nullable=False)
    reference_window: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    current_window: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    drift_metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    detected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class RootCauseAnalysis(Base, TimestampMixin):
    __tablename__ = "root_cause_analyses"
    __table_args__ = (
        Index(
            "uq_root_cause_analyses_investigation",
            "investigation_id",
            unique=True,
        ),
        Index("ix_root_cause_analyses_fingerprint", "evidence_fingerprint"),
    )

    root_cause_analysis_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analyses.analysis_id", ondelete="CASCADE"), nullable=False, unique=True)
    investigation_id: Mapped[str | None] = mapped_column(String(64))
    trigger_type: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str | None] = mapped_column(String(32))
    conclusion_type: Mapped[str | None] = mapped_column(String(64))
    conclusion_level: Mapped[str | None] = mapped_column(String(64))
    schema_version: Mapped[str | None] = mapped_column(String(8))
    algorithm_name: Mapped[str | None] = mapped_column(String(255))
    algorithm_version: Mapped[str | None] = mapped_column(String(100))
    evidence_fingerprint: Mapped[str | None] = mapped_column(String(128))
    summary: Mapped[str | None] = mapped_column(Text)
    report: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    suspected_causes: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    evidence_event_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    affected_slices: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    confidence: Mapped[float | None] = mapped_column(Float)
    conclusion: Mapped[str | None] = mapped_column(Text)


class Artifact(Base, TimestampMixin):
    __tablename__ = "artifacts"
    __table_args__ = (Index("ix_artifacts_type_created", "artifact_type", "created_at"),)

    artifact_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    artifact_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    uri: Mapped[str] = mapped_column(String(2048), nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(128), index=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    media_type: Mapped[str | None] = mapped_column(String(255))
    created_by_run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.run_id", ondelete="SET NULL"), index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False, default=dict)

    lineage_as_output: Mapped[list[Lineage]] = relationship(foreign_keys="Lineage.output_artifact_id", back_populates="output_artifact", cascade="all, delete-orphan")
    lineage_as_input: Mapped[list[Lineage]] = relationship(foreign_keys="Lineage.input_artifact_id", back_populates="input_artifact", cascade="all, delete-orphan")


class Lineage(Base):
    __tablename__ = "lineage"
    __table_args__ = (
        UniqueConstraint("input_artifact_id", "output_artifact_id", "transformation", name="uq_lineage_edge"),
        Index("ix_lineage_output", "output_artifact_id"),
    )

    lineage_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    input_artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.artifact_id", ondelete="CASCADE"), nullable=False)
    output_artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.artifact_id", ondelete="CASCADE"), nullable=False)
    transformation: Mapped[str] = mapped_column(String(255), nullable=False)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.run_id", ondelete="SET NULL"), index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False, default=dict)

    input_artifact: Mapped[Artifact] = relationship(foreign_keys=[input_artifact_id], back_populates="lineage_as_input")
    output_artifact: Mapped[Artifact] = relationship(foreign_keys=[output_artifact_id], back_populates="lineage_as_output")


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        Index("ix_outbox_status_created", "status", "created_at"),
        Index("ix_outbox_aggregate", "aggregate_type", "aggregate_id"),
    )

    outbox_event_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    aggregate_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(64), nullable=False)
    topic: Mapped[str] = mapped_column(String(255), nullable=False)
    message_key: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


__all__ = [
    "Base", "Agent", "AgentVersion", "Run", "Policy", "PolicyVersion", "AgentPolicy",
    "Event", "AgentBehaviorEvent", "ContextEvent", "ContextResource", "ExecutionEvent", "PolicyDecision",
    "Dataset", "DatasetVersion", "EvaluationCase", "EvaluationRun", "EvaluationResult",
    "MLModel", "ModelVersion", "PipelineVersion", "PipelineComponent", "Experiment", "TrainingRun",
    "PersistedSignal",
    "PersistedEvidenceRecord", "PersistedRelationship", "RelationshipEvidence",
    "Analysis", "DifferentialAnalysis", "DriftAnalysis", "RootCauseAnalysis", "Artifact", "Lineage", "OutboxEvent",
]
