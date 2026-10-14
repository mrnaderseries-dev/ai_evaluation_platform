from __future__ import annotations

from datetime import datetime
from typing import Any, TypeVar

from sqlalchemy import and_, func, inspect, or_, select, update
from sqlalchemy.orm import Session

from .models import (
    Agent,
    AgentBehaviorEvent,
    AgentPolicy,
    AgentVersion,
    Analysis,
    Artifact,
    ContextEvent,
    ContextResource,
    Dataset,
    DatasetVersion,
    DifferentialAnalysis,
    DriftAnalysis,
    EvaluationCase,
    EvaluationResult,
    EvaluationRun,
    Event,
    EventObservation,
    ExecutionEvent,
    Experiment,
    Lineage,
    MLModel,
    ModelVersion,
    OutboxEvent,
    PersistedSignal,
    PipelineComponent,
    PipelineVersion,
    Policy,
    PolicyDecision,
    PolicyVersion,
    RootCauseAnalysis,
    Run,
    TrainingRun,
    utc_now,
    PersistedEvidenceRecord,
    PersistedRelationship,
    RelationshipEvidence,
)


T = TypeVar("T")


class RepositoryError(Exception):
    """Base repository exception."""


class EntityNotFoundError(RepositoryError):
    """Raised when an entity does not exist."""


def _dialect_insert(session: Session):
    """
    Return the dialect-specific INSERT construct for the session's bind.

    PostgreSQL is the production target and provides native
    ``ON CONFLICT DO NOTHING`` / ``ON CONFLICT DO UPDATE``. SQLite is
    supported for tests because it exposes the same ``ON CONFLICT``
    semantics. Both are the same operation; a check-then-insert is never
    used, which avoids the TOCTOU race between a SELECT and an INSERT.
    """
    dialect = session.get_bind().dialect.name

    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as dialect_insert

        return dialect_insert

    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as dialect_insert

        return dialect_insert

    raise RepositoryError(
        f"Idempotent insert/upsert is not supported for dialect '{dialect}'."
    )


def _snapshot_row(obj: Any) -> dict[str, Any]:
    row: dict[str, Any] = {}

    for attribute in inspect(type(obj)).mapper.column_attrs:
        value = getattr(obj, attribute.key)

        if value is None:
            continue

        for column in attribute.columns:
            row[column.key] = value

    return row


def _signal_row(row: PersistedSignal) -> dict[str, Any]:
    values = _snapshot_row(row)
    values.setdefault("run_id", None)
    values.setdefault("evaluation_run_id", None)
    values.setdefault("window_start", None)
    values.setdefault("window_end", None)
    values.setdefault("detector_name", None)
    values.setdefault("payload", {})
    values.setdefault("origin", "detector")
    values.setdefault("source_event_id", None)
    values.setdefault("source_observation_id", None)
    values.setdefault("projection_version", None)

    return values


# ============================================================================
# Agent
# ============================================================================


class AgentRepository:

    def __init__(self, session: Session):
        self.session = session

    def create(self, agent: Agent) -> Agent:
        self.session.add(agent)
        self.session.flush()
        return agent

    def get(self, agent_id: str) -> Agent | None:
        return self.session.get(Agent, agent_id)

    def get_required(self, agent_id: str) -> Agent:
        agent = self.get(agent_id)

        if agent is None:
            raise EntityNotFoundError(
                f"Agent '{agent_id}' does not exist."
            )

        return agent

    def get_by_name(self, name: str) -> Agent | None:
        statement = select(Agent).where(Agent.name == name)
        return self.session.scalar(statement)

    def list(self, limit: int = 100, offset: int = 0) -> list[Agent]:
        statement = (
            select(Agent)
            .order_by(Agent.created_at.desc())
            .limit(limit)
            .offset(offset)
        )

        return list(self.session.scalars(statement))

    def delete(self, agent_id: str) -> None:
        agent = self.get_required(agent_id)
        self.session.delete(agent)
        self.session.flush()


# ============================================================================
# Agent Version
# ============================================================================


class AgentVersionRepository:

    def __init__(self, session: Session):
        self.session = session

    def create(self, version: AgentVersion) -> AgentVersion:
        self.session.add(version)
        self.session.flush()
        return version

    def get(self, agent_version_id: str) -> AgentVersion | None:
        return self.session.get(AgentVersion, agent_version_id)

    def get_by_version(
        self,
        agent_id: str,
        version: str,
    ) -> AgentVersion | None:
        statement = select(AgentVersion).where(
            AgentVersion.agent_id == agent_id,
            AgentVersion.version == version,
        )

        return self.session.scalar(statement)

    def list_for_agent(
        self,
        agent_id: str,
        limit: int = 100,
    ) -> list[AgentVersion]:
        statement = (
            select(AgentVersion)
            .where(AgentVersion.agent_id == agent_id)
            .order_by(AgentVersion.created_at.desc())
            .limit(limit)
        )

        return list(self.session.scalars(statement))


# ============================================================================
# Run
# ============================================================================


class RunRepository:

    def __init__(self, session: Session):
        self.session = session

    def create(self, run: Run) -> Run:
        self.session.add(run)
        self.session.flush()
        return run

    def get(self, run_id: str) -> Run | None:
        return self.session.get(Run, run_id)

    def get_required(self, run_id: str) -> Run:
        run = self.get(run_id)

        if run is None:
            raise EntityNotFoundError(
                f"Run '{run_id}' does not exist."
            )

        return run

    def list_for_agent_version(
        self,
        agent_version_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Run]:
        statement = (
            select(Run)
            .where(Run.agent_version_id == agent_version_id)
            .order_by(Run.started_at.desc())
            .limit(limit)
            .offset(offset)
        )

        return list(self.session.scalars(statement))

    def update_status(
        self,
        run_id: str,
        status: str,
        completed_at: datetime | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> Run:
        run = self.get_required(run_id)

        run.status = status

        if completed_at is not None:
            run.completed_at = completed_at

        if error_type is not None:
            run.error_type = error_type

        if error_message is not None:
            run.error_message = error_message

        self.session.flush()

        return run

    def claim_monitoring(
        self,
        run_id: str,
        claim_id: str,
        *,
        claimed_at: datetime,
        expires_at: datetime,
    ) -> bool:
        statement = (
            update(Run)
            .where(
                Run.run_id == run_id,
                Run.status != "failed",
                Run.monitoring_completed_at.is_(None),
                or_(
                    Run.monitoring_claim_expires_at.is_(None),
                    Run.monitoring_claim_expires_at <= claimed_at,
                ),
            )
            .values(
                monitoring_claim_id=claim_id,
                monitoring_claim_expires_at=expires_at,
            )
            .execution_options(synchronize_session=False)
        )

        result = self.session.execute(statement)
        self.session.flush()

        return bool(result.rowcount and result.rowcount > 0)

    def release_monitoring_claim(self, run_id: str, claim_id: str) -> bool:
        statement = (
            update(Run)
            .where(
                Run.run_id == run_id,
                Run.monitoring_claim_id == claim_id,
            )
            .values(
                monitoring_claim_id=None,
                monitoring_claim_expires_at=None,
            )
            .execution_options(synchronize_session=False)
        )

        result = self.session.execute(statement)
        self.session.flush()

        return bool(result.rowcount and result.rowcount > 0)

    def mark_monitoring_completed(
        self,
        run_id: str,
        monitoring_completed_at: datetime,
        claim_id: str,
    ) -> bool:
        statement = (
            update(Run)
            .where(
                Run.run_id == run_id,
                Run.monitoring_completed_at.is_(None),
                Run.monitoring_claim_id == claim_id,
            )
            .values(
                monitoring_completed_at=monitoring_completed_at,
                monitoring_claim_id=None,
                monitoring_claim_expires_at=None,
            )
            .execution_options(synchronize_session=False)
        )

        result = self.session.execute(statement)
        self.session.flush()

        return bool(result.rowcount and result.rowcount > 0)

    def mark_failed(
        self,
        run_id: str,
        *,
        completed_at: datetime,
        error_type: str,
        error_message: str | None = None,
    ) -> bool:
        values: dict[str, Any] = {
            "status": "failed",
            "completed_at": completed_at,
            "error_type": error_type,
        }

        if error_message is not None:
            values["error_message"] = error_message

        statement = (
            update(Run)
            .where(
                Run.run_id == run_id,
                Run.status.notin_(("completed", "failed")),
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )

        result = self.session.execute(statement)
        self.session.flush()

        return bool(result.rowcount and result.rowcount > 0)


# ============================================================================
# Event
# ============================================================================


def event_from_envelope(envelope: Any, *, sequence_number: int) -> Event:
    """
    Map an Event Layer envelope (``schemas.events.common.AIEvent``) onto a
    storage ``Event`` row.

    Alignment (Event Layer field -> ``models.Event`` column)::

        event_id        -> event_id          stable idempotency key / primary key
        schema_version  -> schema_version
        system_id       -> system_id
        system_version  -> system_version
        run_id          -> run_id
        event_type      -> event_type        enum stored as its value string
        timestamp       -> occurred_at       tz-aware producer time
        payload         -> payload           typed payload serialized to a dict
        sequence_number -> sequence_number   per-run ordering (caller-supplied)
        parent_event_id / correlation_id / causation_id / step_id
                          -> same-named columns

    ``recorded_at`` is assigned by the database (``server_default=func.now()``).

    Pure mapping - no database I/O - so it can be unit-tested without a
    session. Optional envelope fields are read with ``getattr`` defaults so
    envelopes produced before the alignment still map (those columns stay
    NULL).
    """
    payload = getattr(envelope, "payload", None)

    if hasattr(payload, "model_dump"):
        # exclude_none: store only fields the producer actually provided;
        # unset optionals (provider=None, ...) are noise in the stored blob.
        payload = payload.model_dump(mode="json", exclude_none=True)
    elif payload is not None and not isinstance(payload, dict):
        payload = dict(payload)

    event_type = getattr(envelope, "event_type")
    event_type = getattr(event_type, "value", event_type)
    timestamp = getattr(envelope, "timestamp", None) or utc_now()

    return Event(
        event_id=envelope.event_id,
        schema_version=envelope.schema_version,
        system_id=envelope.system_id,
        system_version=getattr(envelope, "system_version", None),
        run_id=envelope.run_id,
        sequence_number=sequence_number,
        event_type=event_type,
        occurred_at=timestamp,
        parent_event_id=getattr(envelope, "parent_event_id", None),
        correlation_id=getattr(envelope, "correlation_id", None),
        causation_id=getattr(envelope, "causation_id", None),
        step_id=getattr(envelope, "step_id", None),
        payload=payload or {},
    )


class EventRepository:

    def __init__(self, session: Session):
        self.session = session

    def create(self, event: Event) -> Event:
        self.session.add(event)
        self.session.flush()
        self._project_event_by_id(event.event_id)
        self._persist_event_evidence([event.event_id])
        return event

    def create_many(self, events: list[Event]) -> list[Event]:
        self.session.add_all(events)
        self.session.flush()
        for event in events:
            self._project_event_by_id(event.event_id)
        self._persist_event_evidence([event.event_id for event in events])
        return events

    def insert_ignore_duplicates(self, events: list[Event]) -> int:
        """
        Idempotently insert events.

        Uses ``INSERT ... ON CONFLICT DO NOTHING`` on the natural idempotency
        key ``event_id``. Duplicate Kafka deliveries are therefore successful
        no-ops: they raise no exception and never abort the enclosing
        transaction. ``event_id`` remains the primary key; no surrogate id is
        introduced.

        The conflict target is deliberately ``event_id``: a row conflicting
        with an existing row on *both* ``event_id`` and
        ``uq_event_run_sequence`` is skipped (verified on PostgreSQL 17 and
        SQLite), while a *different* ``event_id`` reusing a run's
        ``(run_id, sequence_number)`` still raises ``IntegrityError`` - a
        genuine ordering violation must not be swallowed.

        Returns the number of rows actually inserted.
        """
        if not events:
            return 0

        rows = [self._to_row(event) for event in events]

        statement = (
            _dialect_insert(self.session)(Event)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["event_id"])
        )

        result = self.session.execute(statement)
        self.session.flush()

        # Projection is coupled to the repository's event write path, not to
        # detector execution. Canonical stored rows are used on redelivery.
        for event in events:
            self._project_event_by_id(event.event_id)
        self._persist_event_evidence([event.event_id for event in events])

        rowcount = result.rowcount

        return int(rowcount) if rowcount is not None and rowcount >= 0 else 0

    @staticmethod
    def _to_row(event: Event) -> dict[str, Any]:
        """
        Snapshot an event for Core INSERT.

        Delegates to :meth:`_core_row`: ``None``-valued columns are omitted so
        Python-side model defaults apply (e.g. ``system_id="gaia-agent"``),
        matching ORM flush semantics. Nullable columns left unset still end up
        NULL in the database.
        """
        return EventRepository._core_row(event)

    def insert_from_envelope(
        self,
        envelope: Any,
        *,
        sequence_number: int | None = None,
    ) -> int:
        """
        Idempotently persist an Event Layer envelope as an ordered event.

        ``sequence_number`` precedence: the envelope's own ``sequence_number``,
        then the explicit argument, then ``next_sequence_number(run_id)``.
        Allocating from ``max()+1`` is only safe with a single writer per run;
        concurrent ingest should assign the sequence upstream - the
        ``uq_event_run_sequence`` constraint is the final arbiter.

        Returns 1 when the event was inserted, 0 when the same ``event_id``
        was already stored (successful no-op).
        """
        sequence = getattr(envelope, "sequence_number", None)

        if sequence is None:
            sequence = sequence_number
        if sequence is None:
            sequence = self.next_sequence_number(envelope.run_id)

        event = event_from_envelope(envelope, sequence_number=sequence)
        return self.insert_ignore_duplicates([event])

    def _project_event_by_id(self, event_id: str) -> None:
        """Idempotently persist an event's Observation and base Signal."""
        canonical_event = self.get(event_id)
        if canonical_event is None:
            raise RepositoryError(
                f"Event '{event_id}' was not available after persistence."
            )
        run = self.session.get(Run, canonical_event.run_id)
        if run is None:
            raise RepositoryError(
                f"Run '{canonical_event.run_id}' for event '{canonical_event.event_id}' does not exist."
            )

        from ..monitoring.observations.projector import project_event
        from ..monitoring.persistence import signal_to_record

        observation, base_signal = project_event(canonical_event, agent_id=run.agent_id)
        self._persist_observation(observation)
        record = signal_to_record(base_signal)
        record = record.model_copy(
            update={"dedupe_key": self._base_signal_dedupe_key(record)}
        )
        self._persist_base_signal(record)

    @staticmethod
    def _base_signal_dedupe_key(record: Any) -> str:
        from ..monitoring.persistence import signal_dedupe_key

        return signal_dedupe_key(record)

    def _persist_observation(self, observation: Any) -> None:
        values = {
            "observation_id": observation.observation_id,
            "source_event_id": observation.source_event_id,
            "projection_version": observation.projection_version,
            "agent_id": observation.agent_id,
            "run_id": observation.run_id,
            "event_type": observation.event_type,
            "system_id": observation.system_id,
            "system_version": observation.system_version,
            "occurred_at": observation.occurred_at,
            "observed_at": observation.observed_at,
            "sequence_number": observation.sequence_number,
            "parent_event_id": observation.parent_event_id,
            "correlation_id": observation.correlation_id,
            "causation_id": observation.causation_id,
            "step_id": observation.step_id,
            "attributes": observation.attributes,
        }
        statement = (
            _dialect_insert(self.session)(EventObservation)
            .values(values)
            .on_conflict_do_nothing(index_elements=["observation_id"])
        )
        self.session.execute(statement)
        self.session.flush()

    def _persist_base_signal(self, record: Any) -> None:
        from ..monitoring_integration import persisted_signal_from_record

        row = persisted_signal_from_record(record)
        values = _signal_row(row)
        statement = (
            _dialect_insert(self.session)(PersistedSignal)
            .values(values)
            .on_conflict_do_nothing(index_elements=["signal_id"])
        )
        self.session.execute(statement)
        self.session.flush()

        from ..monitoring_integration import persist_signal_evidence

        persist_signal_evidence(self.session, [row])

    def _persist_event_evidence(self, event_ids: list[str]) -> None:
        from ..monitoring_integration import persist_event_evidence

        canonical_events = [
            self.get(event_id)
            for event_id in event_ids
        ]
        persist_event_evidence(
            self.session,
            [event for event in canonical_events if event is not None],
        )

    def get(self, event_id: str) -> Event | None:
        return self.session.get(Event, event_id)

    def get_required(self, event_id: str) -> Event:
        event = self.get(event_id)

        if event is None:
            raise EntityNotFoundError(
                f"Event '{event_id}' does not exist."
            )

        return event

    def list_for_run(
        self,
        run_id: str,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[Event]:
        statement = (
            select(Event)
            .where(Event.run_id == run_id)
            .order_by(Event.sequence_number.asc())
            .limit(limit)
            .offset(offset)
        )

        return list(self.session.scalars(statement))

    def list_children(self, parent_event_id: str) -> list[Event]:
        statement = (
            select(Event)
            .where(Event.parent_event_id == parent_event_id)
            .order_by(Event.sequence_number.asc())
        )

        return list(self.session.scalars(statement))

    def list_by_correlation(self, correlation_id: str) -> list[Event]:
        statement = (
            select(Event)
            .where(Event.correlation_id == correlation_id)
            .order_by(Event.sequence_number.asc())
        )

        return list(self.session.scalars(statement))

    def next_sequence_number(self, run_id: str) -> int:
        """
        Return the next per-run event sequence number.

        Event ordering is ``(run_id, sequence_number)``; assigning the sequence
        per run keeps ordering deterministic and independent of wall-clock time,
        which is required to preserve event ordering on ingestion.
        """
        statement = (
            select(func.coalesce(func.max(Event.sequence_number), 0))
            .where(Event.run_id == run_id)
        )

        current = self.session.scalar(statement) or 0

        return int(current) + 1

    def create_with_behavior_detail(
        self,
        event: Event,
        detail: AgentBehaviorEvent,
    ) -> tuple[Event, AgentBehaviorEvent]:
        """Persist an event and its agent-behavior detail atomically."""
        detail.event = event
        self.session.add(event)
        self.session.add(detail)
        self.session.flush()

        return event, detail

    def create_with_context_detail(
        self,
        event: Event,
        detail: ContextEvent,
        resources: list[ContextResource] | None = None,
    ) -> tuple[Event, ContextEvent]:
        """Persist a context event, its detail and resources atomically."""
        detail.event = event

        for resource in resources or []:
            resource.context_event = detail

        self.session.add(event)
        self.session.add(detail)
        self.session.flush()

        return event, detail

    def create_with_execution_detail(
        self,
        event: Event,
        detail: ExecutionEvent,
    ) -> tuple[Event, ExecutionEvent]:
        """Persist an event and its execution detail atomically."""
        detail.event = event
        self.session.add(event)
        self.session.add(detail)
        self.session.flush()

        return event, detail

    @staticmethod
    def _core_row(obj: Any) -> dict[str, Any]:
        """
        Snapshot a mapped object into Core-INSERT parameters.

        Keys are column *names* (an attribute renamed via
        ``mapped_column("metadata", ...)`` maps to its column name).
        Attributes still at ``None`` are omitted so the column's Python-side
        or server-side default applies, exactly as it would on flush.
        """
        return _snapshot_row(obj)

    def insert_with_detail_ignore_duplicates(
        self,
        event: Event,
        detail: AgentBehaviorEvent | ContextEvent | ExecutionEvent,
        resources: list[ContextResource] | None = None,
    ) -> int:
        """
        Idempotently persist a typed event (envelope + 1:1 detail row, plus
        context resources when present) in a single flush.

        Every row is written with ``INSERT ... ON CONFLICT (event_id) DO
        NOTHING`` on its own primary key, so redelivering the same event is a
        successful no-op for envelope *and* detail - never an
        ``IntegrityError``. ``context_resources`` has no unique constraint of
        its own, so resources are inserted only when the context detail row
        was actually inserted; a replay can never duplicate them.

        Core INSERTs (not ``session.add``) are used deliberately: relationship
        cascades on transient parent objects would otherwise re-INSERT the
        envelope. The method flushes only - atomicity comes from the caller's
        transaction (``session_scope`` is the commit point).

        Returns the number of envelope rows inserted (1 = fresh, 0 =
        duplicate).
        """
        if not isinstance(detail, (AgentBehaviorEvent, ContextEvent, ExecutionEvent)):
            raise RepositoryError(
                f"Unsupported event detail type: {type(detail).__name__}."
            )

        if detail.event_id is not None and detail.event_id != event.event_id:
            raise RepositoryError(
                f"Detail event_id '{detail.event_id}' does not match "
                f"event '{event.event_id}'."
            )

        inserted = self.insert_ignore_duplicates([event])

        detail_row = self._core_row(detail)
        detail_row["event_id"] = event.event_id

        detail_statement = (
            _dialect_insert(self.session)(type(detail))
            .values(detail_row)
            .on_conflict_do_nothing(index_elements=["event_id"])
        )

        detail_result = self.session.execute(detail_statement)
        detail_rowcount = detail_result.rowcount
        detail_inserted = (
            int(detail_rowcount)
            if detail_rowcount is not None and detail_rowcount >= 0
            else 0
        )

        if detail_inserted and resources:
            resource_rows = []

            for resource in resources:
                resource_row = self._core_row(resource)
                resource_row["context_event_id"] = event.event_id
                resource_rows.append(resource_row)

            self.session.execute(
                _dialect_insert(self.session)(ContextResource).values(resource_rows)
            )

        self.session.flush()

        return inserted

    def list_by_type(
        self,
        event_type: str,
        limit: int = 1000,
    ) -> list[Event]:
        statement = (
            select(Event)
            .where(Event.event_type == event_type)
            .order_by(Event.occurred_at.desc())
            .limit(limit)
        )

        return list(self.session.scalars(statement))


# ============================================================================
# Persisted Signal
# ============================================================================


class PersistedSignalRepository:

    def __init__(self, session: Session):
        self.session = session

    def insert_ignore_duplicates(self, rows: list[PersistedSignal]) -> int:
        if not rows:
            return 0

        values = [_signal_row(row) for row in rows]

        statement = (
            _dialect_insert(self.session)(PersistedSignal)
            .values(values)
            .on_conflict_do_nothing()
        )

        result = self.session.execute(statement)
        self.session.flush()

        rowcount = result.rowcount

        return int(rowcount) if rowcount is not None and rowcount >= 0 else 0

    def get(self, signal_id: str) -> PersistedSignal | None:
        return self.session.get(PersistedSignal, signal_id)

    def resolve_canonical_rows(
        self,
        attempted_rows: list[PersistedSignal],
    ) -> list[PersistedSignal]:
        """Resolve each attempted row to exactly one canonical stored row."""
        if not attempted_rows:
            return []

        canonical_by_id: dict[str, PersistedSignal] = {}
        for attempted in attempted_rows:
            identities = [PersistedSignal.signal_id == attempted.signal_id]
            if attempted.dedupe_key is not None:
                identities.append(
                    PersistedSignal.dedupe_key == attempted.dedupe_key
                )
            if (
                attempted.source_event_id is not None
                and attempted.origin is not None
                and attempted.projection_version is not None
            ):
                identities.append(
                    and_(
                        PersistedSignal.source_event_id
                        == attempted.source_event_id,
                        PersistedSignal.origin == attempted.origin,
                        PersistedSignal.projection_version
                        == attempted.projection_version,
                    )
                )

            matches = list(
                self.session.scalars(
                    select(PersistedSignal).where(or_(*identities))
                )
            )
            matches_by_id = {row.signal_id: row for row in matches}
            if len(matches_by_id) != 1:
                reason = (
                    "no canonical persisted row"
                    if not matches_by_id
                    else "multiple canonical persisted rows"
                )
                raise RepositoryError(
                    f"Signal '{attempted.signal_id}' resolved to {reason} "
                    "across its unique identities."
                )

            canonical_id, canonical_row = next(iter(matches_by_id.items()))
            canonical_by_id[canonical_id] = canonical_row

        return [canonical_by_id[key] for key in sorted(canonical_by_id)]

    def list_for_run(
        self,
        run_id: str,
        *,
        agent_id: str | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[PersistedSignal]:
        statement = select(PersistedSignal).where(PersistedSignal.run_id == run_id)

        if agent_id is not None:
            statement = statement.where(PersistedSignal.agent_id == agent_id)

        statement = (
            statement.order_by(PersistedSignal.detected_at.asc(), PersistedSignal.signal_id.asc())
            .limit(limit)
            .offset(offset)
        )

        return list(self.session.scalars(statement))

    def list_for_evaluation_run(
        self,
        evaluation_run_id: str,
        *,
        agent_id: str | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[PersistedSignal]:
        statement = select(PersistedSignal).where(
            PersistedSignal.evaluation_run_id == evaluation_run_id
        )

        if agent_id is not None:
            statement = statement.where(PersistedSignal.agent_id == agent_id)

        statement = (
            statement.order_by(PersistedSignal.detected_at.asc(), PersistedSignal.signal_id.asc())
            .limit(limit)
            .offset(offset)
        )

        return list(self.session.scalars(statement))

    def list_for_agent(
        self,
        agent_id: str,
        *,
        signal_type: str | None = None,
        detected_after: datetime | None = None,
        detected_before: datetime | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[PersistedSignal]:
        statement = select(PersistedSignal).where(PersistedSignal.agent_id == agent_id)

        if signal_type is not None:
            statement = statement.where(PersistedSignal.signal_type == signal_type)

        if detected_after is not None:
            statement = statement.where(PersistedSignal.detected_at >= detected_after)

        if detected_before is not None:
            statement = statement.where(PersistedSignal.detected_at <= detected_before)

        statement = (
            statement.order_by(PersistedSignal.detected_at.asc(), PersistedSignal.signal_id.asc())
            .limit(limit)
            .offset(offset)
        )

        return list(self.session.scalars(statement))

    def list_for_window(
        self,
        agent_id: str,
        window_start: datetime,
        window_end: datetime,
        *,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[PersistedSignal]:
        statement = (
            select(PersistedSignal)
            .where(
                PersistedSignal.agent_id == agent_id,
                PersistedSignal.detected_at >= window_start,
                PersistedSignal.detected_at < window_end,
            )
            .order_by(PersistedSignal.detected_at.asc(), PersistedSignal.signal_id.asc())
            .limit(limit)
            .offset(offset)
        )

        return list(self.session.scalars(statement))
# ============================================================================
# Evidence and Relationship Persistence
# ============================================================================


class EvidenceRepository:
    """Persistence for reusable, agent-agnostic evidence."""

    def __init__(self, session: Session):
        self.session = session

    def insert_ignore_duplicates(
        self,
        records: list[PersistedEvidenceRecord],
    ) -> int:
        if not records:
            return 0

        inserted = 0
        for record in records:
            statement = (
                _dialect_insert(self.session)(PersistedEvidenceRecord)
                .values(_snapshot_row(record))
                .on_conflict_do_nothing()
            )
            result = self.session.execute(statement)
            if result.rowcount is not None and result.rowcount > 0:
                inserted += int(result.rowcount)

        self.session.flush()
        return inserted

    def get(self, evidence_id: str) -> PersistedEvidenceRecord | None:
        return self.session.get(PersistedEvidenceRecord, evidence_id)

    def list_for_subject(
        self,
        subject_type: str,
        subject_id: str,
        *,
        domain: str | None = None,
        observed_after: datetime | None = None,
        observed_before: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[PersistedEvidenceRecord]:
        if limit < 1 or offset < 0:
            raise ValueError("limit must be positive and offset non-negative")

        statement = select(PersistedEvidenceRecord).where(
            PersistedEvidenceRecord.subject_type == subject_type,
            PersistedEvidenceRecord.subject_id == subject_id,
        )

        if domain is not None:
            statement = statement.where(
                PersistedEvidenceRecord.domain == domain
            )

        if observed_after is not None:
            statement = statement.where(
                PersistedEvidenceRecord.observed_at >= observed_after
            )

        if observed_before is not None:
            statement = statement.where(
                PersistedEvidenceRecord.observed_at <= observed_before
            )

        statement = (
            statement
            .order_by(
                PersistedEvidenceRecord.observed_at.asc(),
                PersistedEvidenceRecord.evidence_id.asc(),
            )
            .limit(limit)
            .offset(offset)
        )

        return list(self.session.scalars(statement))


class RelationshipRepository:
    """Persistence and retrieval for analytical relationships."""

    VALID_STATUSES = {"candidate", "validated", "rejected"}
    VALID_EVIDENCE_ROLES = {"supporting", "counter", "context"}

    def __init__(self, session: Session):
        self.session = session

    def get(self, relationship_id: str) -> PersistedRelationship | None:
        return self.session.get(PersistedRelationship, relationship_id)

    def get_by_analysis_key(
        self,
        analysis_id: str,
        relationship_key: str,
    ) -> PersistedRelationship | None:
        statement = select(PersistedRelationship).where(
            PersistedRelationship.analysis_id == analysis_id,
            PersistedRelationship.relationship_key == relationship_key,
        )
        return self.session.scalar(statement)

    def insert_with_evidence_ignore_duplicates(
        self,
        relationship: PersistedRelationship,
        evidence_links: list[RelationshipEvidence],
    ) -> tuple[PersistedRelationship, bool]:
        """
        Persist a relationship and its evidence links in the caller's
        transaction.

        Returns (persisted_relationship, inserted).
        A replay reuses the existing relationship and inserts any missing
        evidence links. This method flushes but never commits.
        """
        if relationship.status != "candidate":
            raise RepositoryError(
                "New relationships must be inserted as candidates; "
                "use set_validation_status() for reviewed status changes."
            )

        for link in evidence_links:
            if link.role not in self.VALID_EVIDENCE_ROLES:
                raise ValueError(
                    f"Unsupported evidence role: {link.role}"
                )

            if (
                link.relationship_id
                and link.relationship_id != relationship.relationship_id
            ):
                raise ValueError(
                    "Evidence link references a different relationship"
                )

        values = _snapshot_row(relationship)

        statement = (
            _dialect_insert(self.session)(PersistedRelationship)
            .values(values)
            .on_conflict_do_nothing()
        )

        result = self.session.execute(statement)
        self.session.flush()

        rowcount = result.rowcount
        inserted = rowcount == 1

        # A conflict may mean the same analysis/key was already persisted
        # with a different generated relationship_id.
        persisted = self.get_by_analysis_key(
            relationship.analysis_id,
            relationship.relationship_key,
        )

        if persisted is None:
            raise RepositoryError(
                "Relationship insert did not produce a retrievable row"
            )

        if not inserted and persisted.status in {"validated", "rejected"}:
            same_relationship = (
                persisted.relationship_type == relationship.relationship_type
                and persisted.basis == relationship.basis
                and persisted.algorithm_name == relationship.algorithm_name
                and persisted.algorithm_version == relationship.algorithm_version
                and dict(persisted.scope or {}) == dict(relationship.scope or {})
                and dict(persisted.attributes or {})
                == dict(relationship.attributes or {})
                and list(persisted.limitations or [])
                == list(relationship.limitations or [])
            )
            existing_links = {
                (link.evidence_id, link.role, link.evidence_order)
                for link in self.list_evidence_links(persisted.relationship_id)
            }
            replayed_links = {
                (link.evidence_id, link.role, link.evidence_order)
                for link in evidence_links
            }

            if not same_relationship or replayed_links != existing_links:
                raise RepositoryError(
                    "Cannot change a validated or rejected relationship "
                    "or its evidence links during replay."
                )

        for link in evidence_links:
            link_values = _snapshot_row(link)
            link_values["relationship_id"] = persisted.relationship_id

            link_statement = (
                _dialect_insert(self.session)(RelationshipEvidence)
                .values(link_values)
                .on_conflict_do_nothing()
            )
            self.session.execute(link_statement)

        self.session.flush()
        return persisted, inserted

    def list_for_analysis(
        self,
        analysis_id: str,
        *,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[PersistedRelationship]:
        if limit < 1 or offset < 0:
            raise ValueError("limit must be positive and offset non-negative")

        statement = select(PersistedRelationship).where(
            PersistedRelationship.analysis_id == analysis_id
        )

        if status is not None:
            if status not in self.VALID_STATUSES:
                raise ValueError(f"Unsupported relationship status: {status}")

            statement = statement.where(
                PersistedRelationship.status == status
            )

        statement = (
            statement
            .order_by(
                PersistedRelationship.discovered_at.desc(),
                PersistedRelationship.relationship_id.asc(),
            )
            .limit(limit)
            .offset(offset)
        )

        return list(self.session.scalars(statement))

    def list_evidence_links(
        self,
        relationship_id: str,
    ) -> list[RelationshipEvidence]:
        statement = (
            select(RelationshipEvidence)
            .where(
                RelationshipEvidence.relationship_id == relationship_id
            )
            .order_by(
                RelationshipEvidence.evidence_order.asc(),
                RelationshipEvidence.relationship_evidence_id.asc(),
            )
        )
        return list(self.session.scalars(statement))

    def set_validation_status(
        self,
        relationship_id: str,
        *,
        status: str,
        validated_by: str,
        notes: str | None = None,
    ) -> PersistedRelationship:
        """
        Explicit human/application validation action.
        Detection itself must never call this method implicitly.
        """
        if status not in {"validated", "rejected"}:
            raise ValueError(
                "Validation status must be 'validated' or 'rejected'"
            )

        if not validated_by.strip():
            raise ValueError("validated_by must not be empty")

        relationship = self.get(relationship_id)
        if relationship is None:
            raise EntityNotFoundError(
                f"Relationship '{relationship_id}' does not exist"
            )

        relationship.status = status
        relationship.validated_at = utc_now()
        relationship.validated_by = validated_by
        relationship.validation_notes = notes

        self.session.flush()
        return relationship
# ============================================================================
# Dataset
# ============================================================================


class DatasetRepository:

    def __init__(self, session: Session):
        self.session = session

    def create(self, dataset: Dataset) -> Dataset:
        self.session.add(dataset)
        self.session.flush()
        return dataset

    def get(self, dataset_id: str) -> Dataset | None:
        return self.session.get(Dataset, dataset_id)

    def get_by_name(self, name: str) -> Dataset | None:
        statement = select(Dataset).where(Dataset.name == name)
        return self.session.scalar(statement)


class DatasetVersionRepository:

    def __init__(self, session: Session):
        self.session = session

    def create(self, version: DatasetVersion) -> DatasetVersion:
        self.session.add(version)
        self.session.flush()
        return version

    def get(self, dataset_version_id: str) -> DatasetVersion | None:
        return self.session.get(DatasetVersion, dataset_version_id)

    def get_by_version(
        self,
        dataset_id: str,
        version: str,
    ) -> DatasetVersion | None:
        statement = select(DatasetVersion).where(
            DatasetVersion.dataset_id == dataset_id,
            DatasetVersion.version == version,
        )

        return self.session.scalar(statement)

    def list_versions(
        self,
        dataset_id: str,
        limit: int = 100,
    ) -> list[DatasetVersion]:
        statement = (
            select(DatasetVersion)
            .where(DatasetVersion.dataset_id == dataset_id)
            .order_by(DatasetVersion.created_at.desc())
            .limit(limit)
        )

        return list(self.session.scalars(statement))


# ============================================================================
# Evaluation
# ============================================================================


class EvaluationCaseRepository:

    def __init__(self, session: Session):
        self.session = session

    def create(self, case: EvaluationCase) -> EvaluationCase:
        self.session.add(case)
        self.session.flush()
        return case

    def create_many(
        self,
        cases: list[EvaluationCase],
    ) -> list[EvaluationCase]:
        self.session.add_all(cases)
        self.session.flush()
        return cases

    def get(self, case_id: str) -> EvaluationCase | None:
        return self.session.get(EvaluationCase, case_id)

    def get_required(self, case_id: str) -> EvaluationCase:
        case = self.get(case_id)

        if case is None:
            raise EntityNotFoundError(
                f"Evaluation case '{case_id}' does not exist."
            )

        return case

    def list_for_dataset(
        self,
        dataset_version_id: str,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[EvaluationCase]:
        statement = (
            select(EvaluationCase)
            .where(
                EvaluationCase.dataset_version_id
                == dataset_version_id
            )
            .order_by(EvaluationCase.case_id)
            .limit(limit)
            .offset(offset)
        )

        return list(self.session.scalars(statement))


class EvaluationRunRepository:

    def __init__(self, session: Session):
        self.session = session

    def create(self, run: EvaluationRun) -> EvaluationRun:
        self.session.add(run)
        self.session.flush()
        return run

    def get(self, run_id: str) -> EvaluationRun | None:
        return self.session.get(EvaluationRun, run_id)

    def get_required(self, run_id: str) -> EvaluationRun:
        run = self.get(run_id)

        if run is None:
            raise EntityNotFoundError(
                f"Evaluation run '{run_id}' does not exist."
            )

        return run

    def list_for_agent_version(
        self,
        agent_version_id: str,
        limit: int = 100,
    ) -> list[EvaluationRun]:
        statement = (
            select(EvaluationRun)
            .where(
                EvaluationRun.agent_version_id
                == agent_version_id
            )
            .order_by(EvaluationRun.started_at.desc())
            .limit(limit)
        )

        return list(self.session.scalars(statement))

    def update_status(
        self,
        run_id: str,
        status: str,
        completed_at: datetime | None = None,
    ) -> EvaluationRun:
        run = self.get_required(run_id)

        run.status = status

        if completed_at is not None:
            run.completed_at = completed_at

        self.session.flush()

        return run

    def start(
        self,
        run_id: str,
        *,
        status: str,
        started_at: datetime,
    ) -> EvaluationRun:
        run = self.get_required(run_id)

        run.status = status

        if run.started_at is None:
            run.started_at = started_at

        self.session.flush()

        return run

    def finalize(
        self,
        run_id: str,
        *,
        status: str,
        completed_at: datetime,
        total_cases: int,
        passed_cases: int,
        failed_cases: int,
        metrics: dict[str, Any],
    ) -> EvaluationRun:
        run = self.get_required(run_id)

        run.status = status
        run.completed_at = completed_at
        run.total_cases = total_cases
        run.passed_cases = passed_cases
        run.failed_cases = failed_cases
        run.metrics = metrics

        self.session.flush()

        return run


class EvaluationResultRepository:

    def __init__(self, session: Session):
        self.session = session

    def create(self, result: EvaluationResult) -> EvaluationResult:
        self.session.add(result)
        self.session.flush()
        return result

    def create_many(
        self,
        results: list[EvaluationResult],
    ) -> list[EvaluationResult]:
        self.session.add_all(results)
        self.session.flush()
        return results

    NATURAL_KEY = ("evaluation_run_id", "case_id", "metric_name")
    UPDATABLE_COLUMNS = (
        "run_id",
        "predicted_answer",
        "passed",
        "score",
        "latency_ms",
        "token_usage",
        "evaluator_feedback",
        "failure_category",
        "metrics",
    )

    def upsert(self, result: EvaluationResult) -> int:
        """
        Idempotently upsert a single evaluation result.
        """
        return self.upsert_many([result])

    def upsert_many(self, results: list[EvaluationResult]) -> int:
        """
        Idempotently upsert evaluation results on the natural key
        ``(evaluation_run_id, case_id, metric_name)``.

        Re-ingesting the same result (for example a replayed
        ``evaluation_completed`` event) updates the existing row in place
        instead of creating a duplicate.

        Returns the number of rows affected.
        """
        if not results:
            return 0

        total = 0

        for key, group in self._group_by_identity(results):
            rows = [self._to_row(result) for result in group]

            base = _dialect_insert(self.session)(EvaluationResult)

            statement = base.values(rows).on_conflict_do_update(
                index_elements=list(key),
                index_where=self._conflict_predicate(key),
                set_={
                    column: getattr(base.excluded, column)
                    for column in self.UPDATABLE_COLUMNS
                },
            )

            executed = self.session.execute(statement)
            self.session.flush()

            rowcount = executed.rowcount

            if rowcount is not None and rowcount >= 0:
                total += int(rowcount)

        return total

    @staticmethod
    def _group_by_identity(
        results: list[EvaluationResult],
    ) -> list[tuple[tuple[str, ...], list[EvaluationResult]]]:
        groups: dict[tuple[str, ...], list[EvaluationResult]] = {}

        for result in results:
            if result.evaluation_run_id is None:
                key = ("run_id", "metric_name")
            elif result.case_id is None:
                key = ("evaluation_run_id", "run_id", "metric_name")
            else:
                key = ("evaluation_run_id", "case_id", "metric_name")

            groups.setdefault(key, []).append(result)

        return list(groups.items())

    @staticmethod
    def _conflict_predicate(key: tuple[str, ...]) -> Any:
        if key == ("run_id", "metric_name"):
            return (EvaluationResult.evaluation_run_id.is_(None)) & (
                EvaluationResult.case_id.is_(None)
            )

        if key == ("evaluation_run_id", "run_id", "metric_name"):
            return (EvaluationResult.evaluation_run_id.is_not(None)) & (
                EvaluationResult.case_id.is_(None)
            )

        return (EvaluationResult.evaluation_run_id.is_not(None)) & (
            EvaluationResult.case_id.is_not(None)
        )

    @staticmethod
    def _to_row(result: EvaluationResult) -> dict[str, Any]:
        return {
            "evaluation_result_id": result.evaluation_result_id,
            "evaluation_run_id": result.evaluation_run_id,
            "case_id": result.case_id,
            "run_id": result.run_id,
            "metric_name": result.metric_name,
            "predicted_answer": result.predicted_answer,
            "passed": result.passed,
            "score": result.score,
            "latency_ms": result.latency_ms,
            "token_usage": result.token_usage or {},
            "evaluator_feedback": result.evaluator_feedback,
            "failure_category": result.failure_category,
            "metrics": result.metrics or {},
        }

    def list_for_run(
        self,
        evaluation_run_id: str,
    ) -> list[EvaluationResult]:

        statement = (
            select(EvaluationResult)
            .where(EvaluationResult.evaluation_run_id == evaluation_run_id)
            .order_by(EvaluationResult.case_id)
        )

        return list(self.session.scalars(statement))



class MLModelRepository:

    def __init__(self, session: Session):
        self.session = session

    def create(self, model: MLModel) -> MLModel:
        self.session.add(model)
        self.session.flush()
        return model

    def get(self, model_id: str) -> MLModel | None:
        return self.session.get(MLModel, model_id)

    def get_by_name(self, name: str) -> MLModel | None:
        statement = select(MLModel).where(MLModel.name == name)
        return self.session.scalar(statement)


class ModelVersionRepository:

    def __init__(self, session: Session):
        self.session = session

    def create(self, version: ModelVersion) -> ModelVersion:
        self.session.add(version)
        self.session.flush()
        return version

    def get(self, model_version_id: str) -> ModelVersion | None:
        return self.session.get(ModelVersion, model_version_id)

    def get_by_version(
        self,
        model_id: str,
        version: str,
    ) -> ModelVersion | None:
        statement = select(ModelVersion).where(
            ModelVersion.ml_model_id == model_id,
            ModelVersion.version == version,
        )

        return self.session.scalar(statement)

    def list_versions(
        self,
        model_id: str,
        limit: int = 100,
    ) -> list[ModelVersion]:
        statement = (
            select(ModelVersion)
            .where(ModelVersion.ml_model_id == model_id)
            .order_by(ModelVersion.created_at.desc())
            .limit(limit)
        )

        return list(self.session.scalars(statement))



class PipelineRepository:

    def __init__(self, session: Session):
        self.session = session

    def create(self, pipeline: PipelineVersion) -> PipelineVersion:
        self.session.add(pipeline)
        self.session.flush()
        return pipeline

    def get(
        self,
        pipeline_version_id: str,
    ) -> PipelineVersion | None:
        return self.session.get(
            PipelineVersion,
            pipeline_version_id,
        )

    def add_component(
        self,
        component: PipelineComponent,
    ) -> PipelineComponent:
        self.session.add(component)
        self.session.flush()
        return component

    def list_components(
        self,
        pipeline_version_id: str,
    ) -> list[PipelineComponent]:
        statement = (
            select(PipelineComponent)
            .where(
                PipelineComponent.pipeline_version_id
                == pipeline_version_id
            )
            .order_by(PipelineComponent.component_key.asc())
        )

        return list(self.session.scalars(statement))



class ExperimentRepository:

    def __init__(self, session: Session):
        self.session = session

    def create(self, experiment: Experiment) -> Experiment:
        self.session.add(experiment)
        self.session.flush()
        return experiment

    def get(self, experiment_id: str) -> Experiment | None:
        return self.session.get(Experiment, experiment_id)

    def get_by_name(self, name: str) -> Experiment | None:
        statement = select(Experiment).where(
            Experiment.name == name
        )

        return self.session.scalar(statement)


class TrainingRunRepository:

    def __init__(self, session: Session):
        self.session = session

    def create(self, run: TrainingRun) -> TrainingRun:
        self.session.add(run)
        self.session.flush()
        return run

    def get(self, training_run_id: str) -> TrainingRun | None:
        return self.session.get(TrainingRun, training_run_id)

    def list_for_model(
        self,
        model_version_id: str,
        limit: int = 100,
    ) -> list[TrainingRun]:
        statement = (
            select(TrainingRun)
            .where(
                TrainingRun.model_version_id
                == model_version_id
            )
            .order_by(TrainingRun.started_at.desc())
            .limit(limit)
        )

        return list(self.session.scalars(statement))

    def list_for_experiment(
        self,
        experiment_id: str,
    ) -> list[TrainingRun]:
        statement = (
            select(TrainingRun)
            .where(
                TrainingRun.experiment_id == experiment_id
            )
            .order_by(TrainingRun.started_at.asc())
        )

        return list(self.session.scalars(statement))

    def update_status(
        self,
        training_run_id: str,
        status: str,
        completed_at: datetime | None = None,
    ) -> TrainingRun:
        run = self.get(training_run_id)

        if run is None:
            raise EntityNotFoundError(
                f"Training run '{training_run_id}' does not exist."
            )

        run.status = status

        if completed_at is not None:
            run.completed_at = completed_at

        self.session.flush()

        return run




class AnalysisRepository:

    def __init__(self, session: Session):
        self.session = session

    def create(self, analysis: Analysis) -> Analysis:
        self.session.add(analysis)
        self.session.flush()
        return analysis

    def get(self, analysis_id: str) -> Analysis | None:
        return self.session.get(Analysis, analysis_id)

    def list_for_evaluation_run(
        self,
        source_evaluation_run_id: str,
        analysis_type: str | None = None,
    ) -> list[Analysis]:
        """List analyses produced from a given evaluation run."""

        statement = select(Analysis).where(
            Analysis.source_evaluation_run_id == source_evaluation_run_id,
        )

        if analysis_type is not None:
            statement = statement.where(
                Analysis.analysis_type == analysis_type
            )

        statement = statement.order_by(Analysis.created_at.desc())

        return list(self.session.scalars(statement))

    def list_by_type(
        self,
        analysis_type: str,
        limit: int = 100,
    ) -> list[Analysis]:
        statement = (
            select(Analysis)
            .where(Analysis.analysis_type == analysis_type)
            .order_by(Analysis.created_at.desc())
            .limit(limit)
        )

        return list(self.session.scalars(statement))


class DifferentialAnalysisRepository:

    def __init__(self, session: Session):
        self.session = session

    def create(
        self,
        analysis: DifferentialAnalysis,
    ) -> DifferentialAnalysis:
        self.session.add(analysis)
        self.session.flush()
        return analysis

    def get(
        self,
        analysis_id: str,
    ) -> DifferentialAnalysis | None:
        return self.session.get(
            DifferentialAnalysis,
            analysis_id,
        )

    def list_for_candidate(
        self,
        candidate_evaluation_run_id: str,
    ) -> list[DifferentialAnalysis]:
        statement = (
            select(DifferentialAnalysis)
            .where(
                DifferentialAnalysis.candidate_evaluation_run_id
                == candidate_evaluation_run_id
            )
            .order_by(DifferentialAnalysis.differential_analysis_id)
        )

        return list(self.session.scalars(statement))

    def get_for_analysis(self, analysis_id: str) -> DifferentialAnalysis | None:
        statement = select(DifferentialAnalysis).where(
            DifferentialAnalysis.analysis_id == analysis_id
        )

        return self.session.scalar(statement)


class DriftAnalysisRepository:

    def __init__(self, session: Session):
        self.session = session

    def create(
        self,
        analysis: DriftAnalysis,
    ) -> DriftAnalysis:
        self.session.add(analysis)
        self.session.flush()
        return analysis

    def get(self, drift_id: str) -> DriftAnalysis | None:
        return self.session.get(
            DriftAnalysis,
            drift_id,
        )

    def list_by_type(
        self,
        drift_type: str,
        limit: int = 100,
    ) -> list[DriftAnalysis]:
        statement = (
            select(DriftAnalysis)
            .where(DriftAnalysis.drift_type == drift_type)
            .order_by(DriftAnalysis.drift_analysis_id)
            .limit(limit)
        )

        return list(self.session.scalars(statement))

    def get_for_analysis(self, analysis_id: str) -> DriftAnalysis | None:
        statement = select(DriftAnalysis).where(
            DriftAnalysis.analysis_id == analysis_id
        )

        return self.session.scalar(statement)


class RootCauseAnalysisRepository:

    def __init__(self, session: Session):
        self.session = session

    def create(
        self,
        analysis: RootCauseAnalysis,
    ) -> RootCauseAnalysis:
        self.session.add(analysis)
        self.session.flush()
        return analysis

    def get(
        self,
        analysis_id: str,
    ) -> RootCauseAnalysis | None:
        return self.session.get(
            RootCauseAnalysis,
            analysis_id,
        )

    def get_for_analysis(self, analysis_id: str) -> RootCauseAnalysis | None:
        statement = select(RootCauseAnalysis).where(
            RootCauseAnalysis.analysis_id == analysis_id
        )

        return self.session.scalar(statement)



class ArtifactRepository:

    def __init__(self, session: Session):
        self.session = session

    def create(self, artifact: Artifact) -> Artifact:
        self.session.add(artifact)
        self.session.flush()
        return artifact

    def get(self, artifact_id: str) -> Artifact | None:
        return self.session.get(Artifact, artifact_id)

    def get_by_content_hash(
        self,
        content_hash: str,
    ) -> Artifact | None:
        statement = select(Artifact).where(
            Artifact.content_hash == content_hash
        )

        return self.session.scalar(statement)

    def list_by_type(
        self,
        artifact_type: str,
        limit: int = 100,
    ) -> list[Artifact]:
        statement = (
            select(Artifact)
            .where(
                Artifact.artifact_type == artifact_type
            )
            .order_by(Artifact.created_at.desc())
            .limit(limit)
        )

        return list(self.session.scalars(statement))


# ============================================================================
# Lineage
# ============================================================================


class LineageRepository:

    def __init__(self, session: Session):
        self.session = session

    def create(self, lineage: Lineage) -> Lineage:
        self.session.add(lineage)
        self.session.flush()
        return lineage

    def get(self, lineage_id: str) -> Lineage | None:
        return self.session.get(Lineage, lineage_id)

    def list_for_output(
        self,
        output_artifact_id: str,
    ) -> list[Lineage]:
        """Edges whose output is the given artifact (its upstream inputs)."""
        statement = (
            select(Lineage)
            .where(Lineage.output_artifact_id == output_artifact_id)
            .order_by(Lineage.lineage_id)
        )

        return list(self.session.scalars(statement))

    def list_for_input(
        self,
        input_artifact_id: str,
    ) -> list[Lineage]:
        """Edges whose input is the given artifact (its downstream outputs)."""
        statement = (
            select(Lineage)
            .where(Lineage.input_artifact_id == input_artifact_id)
            .order_by(Lineage.lineage_id)
        )

        return list(self.session.scalars(statement))


# ============================================================================
# Policy
# ============================================================================


class PolicyDecisionRepository:

    def __init__(self, session: Session):
        self.session = session

    def create(
        self,
        decision: PolicyDecision,
    ) -> PolicyDecision:
        self.session.add(decision)
        self.session.flush()
        return decision

    def get(
        self,
        decision_id: str,
    ) -> PolicyDecision | None:
        return self.session.get(
            PolicyDecision,
            decision_id,
        )

    def list_for_run(
        self,
        run_id: str,
    ) -> list[PolicyDecision]:
        statement = (
            select(PolicyDecision)
            .where(PolicyDecision.run_id == run_id)
            .order_by(PolicyDecision.decided_at.desc())
        )

        return list(self.session.scalars(statement))

    def list_for_policy_version(
        self,
        policy_version_id: str,
        limit: int = 100,
    ) -> list[PolicyDecision]:
        statement = (
            select(PolicyDecision)
            .where(PolicyDecision.policy_version_id == policy_version_id)
            .order_by(PolicyDecision.decided_at.desc())
            .limit(limit)
        )

        return list(self.session.scalars(statement))

    def get_latest_for_run(
        self,
        run_id: str,
    ) -> PolicyDecision | None:
        statement = (
            select(PolicyDecision)
            .where(PolicyDecision.run_id == run_id)
            .order_by(PolicyDecision.decided_at.desc())
            .limit(1)
        )

        return self.session.scalar(statement)

    def get_required(
        self,
        decision_id: str,
    ) -> PolicyDecision:
        decision = self.get(decision_id)

        if decision is None:
            raise EntityNotFoundError(
                f"Policy decision '{decision_id}' does not exist."
            )

        return decision

    def list_for_event(
        self,
        event_id: str,
        limit: int = 100,
    ) -> list[PolicyDecision]:
        """Decisions attached to a stored event (e.g. a policy_* event)."""
        statement = (
            select(PolicyDecision)
            .where(PolicyDecision.event_id == event_id)
            .order_by(PolicyDecision.decided_at.desc())
            .limit(limit)
        )

        return list(self.session.scalars(statement))


# ============================================================================
# Transactional Outbox
# ============================================================================


class OutboxEventRepository:
    """
    Minimal persistence for the transactional outbox.

    Callers enqueue an outbox record in the SAME transaction as the domain
    change it represents. A relay (not implemented here) later reads pending
    records and publishes them to Kafka.
    """

    def __init__(self, session: Session):
        self.session = session

    def create(self, event: OutboxEvent) -> OutboxEvent:
        self.session.add(event)
        self.session.flush()
        return event

    def get(self, outbox_event_id: str) -> OutboxEvent | None:
        return self.session.get(OutboxEvent, outbox_event_id)

    def list_pending(
        self,
        limit: int = 100,
    ) -> list[OutboxEvent]:
        statement = (
            select(OutboxEvent)
            .where(OutboxEvent.status == "pending")
            .order_by(OutboxEvent.created_at.asc())
            .limit(limit)
        )

        return list(self.session.scalars(statement))

    def mark_published(
        self,
        outbox_event_id: str,
        published_at: datetime | None = None,
    ) -> OutboxEvent:
        event = self.get(outbox_event_id)

        if event is None:
            raise EntityNotFoundError(
                f"Outbox event '{outbox_event_id}' does not exist."
            )

        event.status = "published"
        event.published_at = published_at or utc_now()
        self.session.flush()

        return event
# ============================================================================
# Policy (versioned policy definitions, bindings, decisions)
# ============================================================================


class PolicyRepository:
    """Persistence for logical policy definitions."""

    def __init__(self, session: Session):
        self.session = session

    def create(self, policy: Policy) -> Policy:
        self.session.add(policy)
        self.session.flush()
        return policy

    def get(self, policy_id: str) -> Policy | None:
        return self.session.get(Policy, policy_id)

    def get_required(self, policy_id: str) -> Policy:
        policy = self.get(policy_id)

        if policy is None:
            raise EntityNotFoundError(f"Policy '{policy_id}' does not exist.")

        return policy

    def get_by_name(self, name: str) -> Policy | None:
        statement = select(Policy).where(Policy.name == name)
        return self.session.scalar(statement)

    def get_or_create(
        self,
        name: str,
        policy_type: str,
        description: str | None = None,
    ) -> Policy:
        """Idempotent lookup-or-create keyed on the unique policy name."""
        policy = self.get_by_name(name)

        if policy is None:
            policy = Policy(
                name=name,
                policy_type=policy_type,
                description=description,
            )
            self.session.add(policy)
            self.session.flush()

        return policy

    def list(self, limit: int = 100, offset: int = 0) -> list[Policy]:
        statement = (
            select(Policy)
            .order_by(Policy.created_at.desc())
            .limit(limit)
            .offset(offset)
        )

        return list(self.session.scalars(statement))


class PolicyVersionRepository:
    """Persistence for immutable policy versions."""

    def __init__(self, session: Session):
        self.session = session

    def create(self, version: PolicyVersion) -> PolicyVersion:
        self.session.add(version)
        self.session.flush()
        return version

    def get(self, policy_version_id: str) -> PolicyVersion | None:
        return self.session.get(PolicyVersion, policy_version_id)

    def get_required(self, policy_version_id: str) -> PolicyVersion:
        version = self.get(policy_version_id)

        if version is None:
            raise EntityNotFoundError(
                f"Policy version '{policy_version_id}' does not exist."
            )

        return version

    def get_by_version(
        self,
        policy_id: str,
        version: str,
    ) -> PolicyVersion | None:
        statement = select(PolicyVersion).where(
            PolicyVersion.policy_id == policy_id,
            PolicyVersion.version == version,
        )

        return self.session.scalar(statement)

    def list_for_policy(
        self,
        policy_id: str,
        limit: int = 100,
    ) -> list[PolicyVersion]:
        statement = (
            select(PolicyVersion)
            .where(PolicyVersion.policy_id == policy_id)
            .order_by(PolicyVersion.created_at.desc())
            .limit(limit)
        )

        return list(self.session.scalars(statement))

    def get_active(self, policy_id: str) -> PolicyVersion | None:
        statement = (
            select(PolicyVersion)
            .where(
                PolicyVersion.policy_id == policy_id,
                PolicyVersion.is_active.is_(True),
            )
            .order_by(PolicyVersion.created_at.desc())
            .limit(1)
        )

        return self.session.scalar(statement)

    def activate(self, policy_version_id: str) -> PolicyVersion:
        """
        Make one policy version the active version for its policy.

        Deactivates sibling versions first so ``is_active`` stays unique per
        policy in practice.
        """
        version = self.get_required(policy_version_id)

        siblings = select(PolicyVersion).where(
            PolicyVersion.policy_id == version.policy_id,
            PolicyVersion.policy_version_id != policy_version_id,
        )

        for sibling in self.session.scalars(siblings):
            if sibling.is_active:
                sibling.is_active = False

        version.is_active = True
        self.session.flush()

        return version
class AgentPolicyRepository:
    """Persistence for effective-dated agent <-> policy version bindings."""

    def __init__(self, session: Session):
        self.session = session

    def create(self, binding: AgentPolicy) -> AgentPolicy:
        self.session.add(binding)
        self.session.flush()
        return binding

    def get(self, agent_policy_id: str) -> AgentPolicy | None:
        return self.session.get(AgentPolicy, agent_policy_id)

    def list_for_agent(
        self,
        agent_id: str,
        enabled_only: bool = True,
    ) -> list[AgentPolicy]:
        statement = select(AgentPolicy).where(
            AgentPolicy.agent_id == agent_id,
        )

        if enabled_only:
            statement = statement.where(AgentPolicy.enabled.is_(True))

        statement = statement.order_by(AgentPolicy.effective_from.desc())

        return list(self.session.scalars(statement))

    def list_effective(
        self,
        agent_id: str,
        at: datetime | None = None,
    ) -> list[AgentPolicy]:
        """Return bindings in effect for an agent at a point in time."""
        moment = at or utc_now()

        statement = (
            select(AgentPolicy)
            .where(
                AgentPolicy.agent_id == agent_id,
                AgentPolicy.enabled.is_(True),
                AgentPolicy.effective_from <= moment,
                (AgentPolicy.effective_to.is_(None))
                | (AgentPolicy.effective_to > moment),
            )
            .order_by(AgentPolicy.effective_from.desc())
        )

        return list(self.session.scalars(statement))


# ============================================================================
# Event sub-entities (behavior / context / execution details)
# ============================================================================


class AgentBehaviorEventRepository:
    """Persistence for agent-behavior details (1:1 with an event)."""

    def __init__(self, session: Session):
        self.session = session

    def create(self, detail: AgentBehaviorEvent) -> AgentBehaviorEvent:
        self.session.add(detail)
        self.session.flush()
        return detail

    def get(self, event_id: str) -> AgentBehaviorEvent | None:
        return self.session.get(AgentBehaviorEvent, event_id)

    def list_for_run(
        self,
        run_id: str,
        limit: int = 100,
    ) -> list[AgentBehaviorEvent]:
        statement = (
            select(AgentBehaviorEvent)
            .join(Event, AgentBehaviorEvent.event_id == Event.event_id)
            .where(Event.run_id == run_id)
            .order_by(Event.sequence_number.asc())
            .limit(limit)
        )

        return list(self.session.scalars(statement))

    def list_by_type(
        self,
        behavior_type: str,
        limit: int = 100,
    ) -> list[AgentBehaviorEvent]:
        statement = (
            select(AgentBehaviorEvent)
            .where(AgentBehaviorEvent.behavior_type == behavior_type)
            .limit(limit)
        )

        return list(self.session.scalars(statement))


class ContextEventRepository:
    """Persistence for context details and the resources considered for them."""

    def __init__(self, session: Session):
        self.session = session

    def create(self, detail: ContextEvent) -> ContextEvent:
        self.session.add(detail)
        self.session.flush()
        return detail

    def get(self, event_id: str) -> ContextEvent | None:
        return self.session.get(ContextEvent, event_id)

    def get_required(self, event_id: str) -> ContextEvent:
        detail = self.get(event_id)

        if detail is None:
            raise EntityNotFoundError(
                f"Context event '{event_id}' does not exist."
            )

        return detail

    def list_for_run(
        self,
        run_id: str,
        limit: int = 100,
    ) -> list[ContextEvent]:
        statement = (
            select(ContextEvent)
            .join(Event, ContextEvent.event_id == Event.event_id)
            .where(Event.run_id == run_id)
            .order_by(Event.sequence_number.asc())
            .limit(limit)
        )

        return list(self.session.scalars(statement))

    def list_by_type(
        self,
        context_type: str,
        limit: int = 100,
    ) -> list[ContextEvent]:
        statement = (
            select(ContextEvent)
            .where(ContextEvent.context_type == context_type)
            .order_by(ContextEvent.event_id)
            .limit(limit)
        )

        return list(self.session.scalars(statement))

    def list_resources(self, event_id: str) -> list[ContextResource]:
        """
        Resources considered for a context, ranked first (unranked rows are
        ordered deterministically before ranked ones on every dialect).
        """
        statement = (
            select(ContextResource)
            .where(ContextResource.context_event_id == event_id)
            .order_by(
                ContextResource.rank.is_(None),
                ContextResource.rank.asc(),
            )
        )

        return list(self.session.scalars(statement))


class ExecutionEventRepository:
    """Persistence for execution details (model / tool / retrieval operations)."""

    def __init__(self, session: Session):
        self.session = session

    def create(self, detail: ExecutionEvent) -> ExecutionEvent:
        self.session.add(detail)
        self.session.flush()
        return detail

    def get(self, event_id: str) -> ExecutionEvent | None:
        return self.session.get(ExecutionEvent, event_id)

    def get_required(self, event_id: str) -> ExecutionEvent:
        detail = self.get(event_id)

        if detail is None:
            raise EntityNotFoundError(
                f"Execution event '{event_id}' does not exist."
            )

        return detail

    def list_for_run(
        self,
        run_id: str,
        limit: int = 100,
    ) -> list[ExecutionEvent]:
        statement = (
            select(ExecutionEvent)
            .join(Event, ExecutionEvent.event_id == Event.event_id)
            .where(Event.run_id == run_id)
            .order_by(Event.sequence_number.asc())
            .limit(limit)
        )

        return list(self.session.scalars(statement))

    def list_by_tool(
        self,
        tool_name: str,
        limit: int = 100,
    ) -> list[ExecutionEvent]:
        statement = (
            select(ExecutionEvent)
            .where(ExecutionEvent.tool_name == tool_name)
            .order_by(
                ExecutionEvent.started_at.is_(None),
                ExecutionEvent.started_at.desc(),
            )
            .limit(limit)
        )

        return list(self.session.scalars(statement))

    def list_by_status(
        self,
        status: str,
        limit: int = 100,
    ) -> list[ExecutionEvent]:
        statement = (
            select(ExecutionEvent)
            .where(ExecutionEvent.status == status)
            .order_by(
                ExecutionEvent.started_at.is_(None),
                ExecutionEvent.started_at.desc(),
            )
            .limit(limit)
        )

        return list(self.session.scalars(statement))