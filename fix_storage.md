# Storage Fixes - AI Evaluation Platform

_Date: 2026-10-01 | Scope: implement the P0/P1 storage fixes from `storage.md` (review pass 2) | No architecture redesign, no unrelated features_

## Storage Fix Summary

| Fix | Problem | Before | After | Status |
|-----|---------|--------|-------|--------|
| FIX-1 | Reserved `metadata` attribute crashed import | 15 entities had `metadata: Mapped[dict] = mapped_column(JSON...)`; `import models` raised `InvalidRequestError: Attribute name 'metadata' is reserved` | All 15 renamed to `custom_metadata`; module imports, mappers configure, 23 tables create | DONE |
| FIX-2 | `DATABASE_URL` read at import time | `postgres.py` had module-level `DATABASE_URL = _get_database_url()` + `engine = create_engine(...)`; importing raised `RuntimeError` when env unset | Lazy `get_database_url()` / `get_engine()` / `get_sessionmaker()`; import never touches env | DONE |
| FIX-3 | `Event` had to keep `event_id` PK (no surrogate) | `event_id` was already the sole PK | Unchanged by design; explicitly asserted in tests (`primary_key == ["event_id"]`) | DONE (verified) |
| FIX-4 | No natural unique keys -> replay duplicates | `evaluation_results` and `lineage` had no uniqueness | `uq_evaluation_result_natural_key(evaluation_run_id, case_id, metric_name)`, `uq_lineage_edge(source_type, source_id, target_type, target_id, relationship)` | DONE |
| FIX-5 | Event insert was blind `add()+flush()` -> `IntegrityError` on replay | `EventRepository.create/create_many` only INSERT | `EventRepository.insert_ignore_duplicates()` -> `INSERT ... ON CONFLICT (event_id) DO NOTHING`; duplicates are no-ops | DONE |
| FIX-6 | Replayed evaluation results duplicated rows | `EvaluationResultRepository.create()` only INSERT | `upsert()` / `upsert_many()` -> `INSERT ... ON CONFLICT (natural key) DO UPDATE` | DONE |
| FIX-7 | Transaction boundaries must live in `session_scope()` | repos already flushed only; `session_scope` committed | Confirmed + documented: repos never commit, only flush; `session_scope` is the single commit point | DONE (verified) |
| FIX-8 | No transactional outbox | No outbox concept at all | Added minimal `OutboxEvent` model + `OutboxEventRepository` (no Kafka relay) | DONE |
| FIX-9 | Concrete relationship/PK/FK gaps | `DriftAnalysis` had no `analysis` relationship and no index on `analysis_id` | Added `DriftAnalysis.analysis` relationship + `ix_drift_analysis_analysis_id` | DONE |
| FIX-10 | No storage test coverage | Only `tests/contract` existed | Added `tests/storage/` (16 tests); `pythonpath` now includes `src` | DONE |

---

### FIX-1

Problem:
`models.py` could not be imported. Every one of 15 entities declared a mapped attribute named `metadata`, which collides with `DeclarativeBase.metadata`. Import raised `sqlalchemy.exc.InvalidRequestError: Attribute name 'metadata' is reserved when using the Declarative API.` at `class Agent(Base)`.

Before:
```python
class Agent(Base):
    ...
    metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
```
(identical `metadata: Mapped[...] = mapped_column(JSON...)` on Agent, AgentVersion, Run, Dataset, DatasetVersion, EvaluationCase, EvaluationRun, EvaluationResult, MLModel, ModelVersion, Experiment, Analysis, Artifact, Lineage, PolicyDecision - 15 total)

After:
```python
class Agent(Base):
    ...
    custom_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
```
Rename `metadata` -> `custom_metadata` on all 15 entities. No other references existed in the repo (checked: the only other `metadata` hits are Pydantic payload fields in `schemas/events/*` and `Base.metadata` in `postgres.py`, both unrelated and left untouched).

Why:
`DeclarativeBase` reserves `metadata` for the class/Table schema collection. Renaming the attribute (and column) removes the collision while keeping a JSON metadata blob per entity. The name `custom_metadata` is explicit and greppable.

Files:
- `src/ai_eval_platform/storage/models.py`

Validation:
- `python -c "import ai_eval_platform.storage.models; from sqlalchemy.orm import configure_mappers; configure_mappers()"` -> `IMPORT OK`, `MAPPERS OK`, `tables: 23`.
### FIX-2

Problem:
`postgres.py` resolved the database URL at import time and built the engine eagerly. Importing the module raised `RuntimeError: DATABASE_URL environment variable is not configured.` whenever the env var was absent, which broke tests, CI, and any tooling that never touches a database.

Before:
```python
DATABASE_URL = _get_database_url()   # raises at import if env missing

engine: Engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    ...
)

SessionLocal = sessionmaker(bind=engine, ...)

def get_session() -> Session:
    return SessionLocal()

@contextmanager
def session_scope() -> Generator[Session, None, None]:
    session = SessionLocal()
    ...
```

After:
```python
def get_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL environment variable is not configured.")
    return url

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None

def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(get_database_url(), pool_pre_ping=True, ...)
    return _engine

def get_sessionmaker() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), class_=Session,
                                        autoflush=False, expire_on_commit=False)
    return _session_factory

def get_session() -> Session:
    return get_sessionmaker()()

@contextmanager
def session_scope() -> Generator[Session, None, None]:
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
```
`create_tables` / `drop_tables` / `check_connection` now use `get_engine()`. `dispose_engine()` also resets the lazy globals.

Why:
Importing a module must not perform side-effecting validation of configuration. Deferring both the env read and the engine creation to first use keeps the module importable for tests and tooling, while still failing fast (RuntimeError) the moment a real connection is actually requested.

Files:
- `src/ai_eval_platform/storage/postgres.py`

Validation:
- `python -c "import ai_eval_platform.storage.postgres"` with `DATABASE_URL` unset -> imports OK.
- `get_database_url()` -> raises `RuntimeError` (expected).
- `get_engine()` -> raises `RuntimeError` (expected).
- `tests/storage/test_models_and_tables.py::test_postgres_module_imports_without_database_url` -> pass.

---

### FIX-3

Problem:
None (regression guard). The review required that `Event.event_id` remain the primary key so Kafka idempotency has a stable natural key. During the idempotency work it was important not to introduce a surrogate ID.

Before:
```python
class Event(Base):
    __tablename__ = "events"
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
```

After:
```python
class Event(Base):
    __tablename__ = "events"
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
```
Unchanged - but now explicitly covered by a test so it cannot silently regress.

Why:
`event_id` is the producer-supplied idempotency key. A surrogate autoincrement PK would destroy the ability to `ON CONFLICT (event_id)` and would allow duplicate events.

Files:
- `src/ai_eval_platform/storage/models.py` (no change)
- `tests/storage/test_models_and_tables.py` (guard test)

Validation:
### FIX-4

Problem:
`evaluation_results` and `lineage` had no natural unique keys, so replayed events silently inserted duplicate rows (metric inflation, duplicated graph edges). This is the correctness half of the idempotency defect.

Before:
```python
class EvaluationResult(Base):
    __table_args__ = (
        Index("ix_evaluation_results_run_metric", "evaluation_run_id", "metric_name"),
        Index("ix_evaluation_results_case", "case_id"),
    )

class Lineage(Base):
    __table_args__ = (
        Index("ix_lineage_source", "source_type", "source_id"),
        Index("ix_lineage_target", "target_type", "target_id"),
    )
```

After:
```python
class EvaluationResult(Base):
    __table_args__ = (
        UniqueConstraint(
            "evaluation_run_id", "case_id", "metric_name",
            name="uq_evaluation_result_natural_key",
        ),
        Index("ix_evaluation_results_run_metric", "evaluation_run_id", "metric_name"),
        Index("ix_evaluation_results_case", "case_id"),
    )

class Lineage(Base):
    __table_args__ = (
        UniqueConstraint(
            "source_type", "source_id", "target_type", "target_id", "relationship",
            name="uq_lineage_edge",
        ),
        Index("ix_lineage_source", "source_type", "source_id"),
        Index("ix_lineage_target", "target_type", "target_id"),
    )
```

Why:
Both are natural keys. They are exactly what `ON CONFLICT` uses for upsert (FIX-6) and what makes a duplicate edge/result a database-enforced impossibility. Both are `NOT NULL` columns, so the constraint has no NULL caveat.

Files:
- `src/ai_eval_platform/storage/models.py`

Validation:
- `test_evaluation_result_natural_unique_constraint_exists`, `test_lineage_unique_constraint_exists` -> pass.
- `test_evaluation_result_unique_constraint`, `test_lineage_unique_constraint` ->
  inserting the duplicate raises `IntegrityError` -> pass.

---

### FIX-5

Problem:
`EventRepository.create()` / `create_many()` were blind `session.add()` + `flush()`. A redelivered Kafka message (at-least-once) tried a second `INSERT` with the same `event_id` and raised `IntegrityError`, aborting the enclosing transaction. There was also no idempotent path.

Before:
```python
class EventRepository:
    def create(self, event: Event) -> Event:
        self.session.add(event)
        self.session.flush()
        return event

    def create_many(self, events: list[Event]) -> list[Event]:
        self.session.add_all(events)
        self.session.flush()
        return events
```

After:
```python
def _dialect_insert(session: Session):
    """PostgreSQL in prod, SQLite for tests - both provide ON CONFLICT."""
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as dialect_insert
        return dialect_insert
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as dialect_insert
        return dialect_insert
    raise RepositoryError(f"Idempotent insert/upsert is not supported for dialect '{dialect}'.")


class EventRepository:
    def insert_ignore_duplicates(self, events: list[Event]) -> int:
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
        rowcount = result.rowcount
        return int(rowcount) if rowcount is not None and rowcount >= 0 else 0
```
(`create` / `create_many` are kept for explicit non-idempotent uses; the ingestion path uses `insert_ignore_duplicates`.)

Why:
`INSERT ... ON CONFLICT (event_id) DO NOTHING` is the correct, race-free idempotency mechanism. Unlike check-then-insert (a TOCTOU race between SELECT and INSERT), the conflict is resolved atomically by the database. Duplicate deliveries become successful no-ops that return `0` inserted rows and never raise or abort the transaction. The `ON CONFLICT` behavior is identical on PostgreSQL (production) and SQLite (tests), so tests exercise the real semantics without a live Postgres server.

Files:
- `src/ai_eval_platform/storage/repositories.py`

Validation:
- `test_duplicate_event_ingestion_is_successful_noop` -> 1 then 0 inserted, row count == 1 -> pass.
- `test_duplicate_event_within_single_batch` -> `[e, e]` inserts 1 -> pass.
- Manual probe on SQLite 3.42: first=1, dup=0, batch-with-internal-dup=1.

---

### FIX-6

Problem:
`EvaluationResultRepository.create()` / `create_many()` inserted unconditionally. Re-consuming an `evaluation_completed` event (which carries `evaluation_run_id`, `evaluation_case_id`, `metric`, `value`) created a duplicate result row because there was no natural key to conflict on.

Before:
```python
class EvaluationResultRepository:
    def create(self, result: EvaluationResult) -> EvaluationResult:
        self.session.add(result)
        self.session.flush()
        return result

    def create_many(self, results: list[EvaluationResult]) -> list[EvaluationResult]:
        self.session.add_all(results)
        self.session.flush()
        return results
```

After:
```python
class EvaluationResultRepository:
    NATURAL_KEY = ("evaluation_run_id", "case_id", "metric_name")
    UPDATABLE_COLUMNS = (
        "metric_value", "passed", "evaluator_type",
        "evaluator_version", "custom_metadata",
    )

    def upsert(self, result: EvaluationResult) -> int:
        return self.upsert_many([result])

    def upsert_many(self, results: list[EvaluationResult]) -> int:
        if not results:
            return 0
        rows = [self._to_row(result) for result in results]
        base = _dialect_insert(self.session)(EvaluationResult)
        statement = base.values(rows).on_conflict_do_update(
            index_elements=list(self.NATURAL_KEY),
            set_={col: getattr(base.excluded, col) for col in self.UPDATABLE_COLUMNS},
        )
        result = self.session.execute(statement)
        self.session.flush()
        rowcount = result.rowcount
        return int(rowcount) if rowcount is not None and rowcount >= 0 else 0
```
(`create` / `create_many` are kept for explicit non-idempotent uses.)

Why:
`INSERT ... ON CONFLICT (evaluation_run_id, case_id, metric_name) DO UPDATE` (FIX-4 constraint) makes re-ingestion an in-place update: the run's metric is refreshed, the row is not duplicated, and the original `result_id` (PK) is preserved because `result_id` is not in the update set. Same operation on PostgreSQL and SQLite.

Files:
- `src/ai_eval_platform/storage/repositories.py`

Validation:
- `test_evaluation_result_upsert_updates_in_place` -> after two upserts of the same natural key, `list_for_run` returns 1 row, value == 0.9, `result_id` unchanged -> pass.

---

### FIX-7

Problem:
Transaction boundaries had to remain in `session_scope()`, and repositories must never commit. The repositories already only flushed, but the invariant had to be preserved while adding the new methods and proven by a test.

Before (behaviour unchanged, shown for clarity):
```python
# repositories.py - every method ends in flush(), never commit()
def create(self, agent: Agent) -> Agent:
    self.session.add(agent)
    self.session.flush()
    return agent

# postgres.py - the single commit point
@contextmanager
def session_scope() -> Generator[Session, None, None]:
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
```

After:
Same design: one commit point in `session_scope`; repositories flush only. The new `insert_ignore_duplicates` / `upsert` / `upsert_many` / outbox methods also end in `flush()` and never commit, so several repositories can still participate in one transaction by sharing the session yielded by `session_scope()`.

Why:
A repository that commits destroys composability: you could not atomically write a domain row and (FIX-8) its outbox entry, nor combine multiple repositories. Flush-only repositories plus a single `session_scope` commit point is the correct unit-of-work boundary.

Files:
- `src/ai_eval_platform/storage/postgres.py`
- `src/ai_eval_platform/storage/repositories.py`

Validation:
- `test_repositories_do_not_commit` -> `AgentRepository.create()` flushes (row visible), then `session.rollback()` discards it -> pass.

---

### FIX-8

Problem:
There was no transactional outbox, so a domain change could not be committed atomically together with the event to publish to Kafka. (The full Kafka relay was explicitly out of scope.)

Before:
No outbox concept existed in the schema.

After:
```python
class OutboxEvent(Base):
    __tablename__ = "outbox_events"

    outbox_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    aggregate_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    topic: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_outbox_events_status_created_at", "status", "created_at"),
        Index("ix_outbox_events_aggregate", "aggregate_type", "aggregate_id"),
    )
```
Plus a minimal `OutboxEventRepository` (`create`, `get`, `list_pending`, `mark_published`). No relay is implemented.

Why:
The outbox pattern requires the publishable event to be written in the SAME transaction as the domain change, so it is committed iff the change is committed. `status` + `attempts` + `published_at` and a `(status, created_at)` index are the minimum a future relay needs to poll and mark records published with retry accounting. No relay, Kafka, or poison handling added.

Files:
- `src/ai_eval_platform/storage/models.py`
- `src/ai_eval_platform/storage/repositories.py`

Validation:
- `test_outbox_table_columns` -> expected columns present -> pass.
- `test_outbox_enqueue_and_publish` -> enqueue, list pending, mark published, list pending empty -> pass.

---

### FIX-9

Problem:
While implementing the rename/constraints, a concrete relationship gap was confirmed: `DriftAnalysis` had `analysis_id` as a foreign key but no `analysis` relationship (unlike `DifferentialAnalysis` and `RootCauseAnalysis`), and no index on `analysis_id`. Drift results were inconsistent with the other analysis subtypes and the FK was unindexed.

Before:
```python
class DriftAnalysis(Base):
    __tablename__ = "drift_analyses"
    ...
    analysis_id: Mapped[str] = mapped_column(
        ForeignKey("analyses.analysis_id", ondelete="CASCADE"),
        nullable=False,
    )
    ...
    __table_args__ = (
        Index("ix_drift_analysis_feature", "feature_name"),
    )
```

After:
```python
class DriftAnalysis(Base):
    __tablename__ = "drift_analyses"
    ...
    analysis_id: Mapped[str] = mapped_column(
        ForeignKey("analyses.analysis_id", ondelete="CASCADE"),
        nullable=False,
    )
    ...
    analysis: Mapped["Analysis"] = relationship()

    __table_args__ = (
        Index("ix_drift_analysis_feature", "feature_name"),
        Index("ix_drift_analysis_analysis_id", "analysis_id"),
    )
```

Why:
Adding the relationship makes `DriftAnalysis.analysis` consistent with the other subtypes and lets callers navigate drift -> analysis in the ORM. The index supports the FK (PostgreSQL does not index foreign keys automatically) and the `analysis_id` lookups drift consumers need. No entity was redesigned; only the missing, consistent piece was added.

Files:
- `src/ai_eval_platform/storage/models.py`

Validation:
- `configure_mappers()` succeeds; `python -m pytest tests -q` -> `79 passed`.

---

### FIX-10

Problem:
No storage tests existed, so the crash fix, DB-level idempotency, and new constraints were unverified. `tests/` also could not import `ai_eval_platform` because `src` was not on the pytest path.

Before:
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
```
Only `tests/contract/` existed (63 tests).

After:
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = [".", "src"]
```
New `tests/storage/` suite (16 tests): model import + table registration, `custom_metadata` rename, `event_id` PK guard, unique-constraint existence, postgres import without `DATABASE_URL`, CRUD, repository rollback, duplicate event ingestion (repeat + same batch), EvaluationResult upsert, and outbox enqueue/publish.

Why:
The review's P1 required storage tests, and idempotency/constraint behaviour must be regression-protected. SQLite in-memory keeps tests fast and dependency-free while exercising the real `ON CONFLICT` semantics. No existing test was weakened or deleted.

Files:
- `pyproject.toml`
- `tests/storage/conftest.py`
- `tests/storage/test_models_and_tables.py`
- `tests/storage/test_repositories.py`

Validation:
- `python -m pytest tests -q` -> `79 passed` (63 contract + 16 storage).

---

## Remaining Issues

Only genuinely-remaining items (out of scope for this fix set; still open from the review):

1. **JSON, not JSONB.** JSON columns remain `JSON`; the review recommended `JSONB` (+ `Mutable`, GIN on `events.payload`).
2. **Outbox relay not implemented.** Only the model + minimal repository exist; the publisher/relay, retry/backoff, and `attempts`/`status` transitions are future work.
3. **No ingestion inbox / ConsumerState.** `ON CONFLICT` protects the `events` store, but a consumer-side processed-offset/inbox table (idempotent side effects across consumers) is not added.
4. **Dangerous ORM cascades remain.** `Run.events` / `AgentVersion.runs` still use `cascade="all, delete-orphan"` (behaviour change out of scope).
5. **No server-side timestamps / `updated_at`.** `created_at`/`ingestion_time` are still Python-side `default=utcnow`.
6. **No optimistic concurrency.** No `version_id` on `Run`/`EvaluationRun`/`TrainingRun`/`Analysis`.
7. **Free-string `status` / `decision` / `analysis_type`.** No CHECK/enum constraints yet.
8. **`Artifact.size_bytes` is `Integer` (32-bit).** Can overflow >2GB; should be `BigInteger`.
9. **Missing name uniqueness** on `agents`/`datasets`/`ml_models`/`experiments` and some FK indexes (`evaluation_runs.baseline_run_id`, `training_runs.experiment_id`).
10. **No Alembic migrations.** Schema is created via `Base.metadata.create_all`.
11. **No partitioning/retention** for the high-volume `events` table.
12. **`Analysis.result` JSON still duplicates** the typed subtype columns.
13. **`Regression` entity still missing**; the `Differential -> Regression -> RootCause -> PolicyDecision` chain is not fully wired.

## Final Storage Status

- **Import:** `models.py` and `postgres.py` import cleanly with no `DATABASE_URL`; the reserved `metadata` crash is gone.
- **Schema:** 23 tables create successfully (22 original + `outbox_events`); mappers configure.
- **Idempotency:** `events.event_id` is still the sole PK; duplicates are handled by `ON CONFLICT DO NOTHING` (never check-then-insert). `evaluation_results` has a natural unique key and an `ON CONFLICT DO UPDATE` upsert.
- **Transactions:** repositories flush only; `session_scope()` is the only commit point.
- **Tests:** `python -m pytest tests -q` -> **79 passed**.
- **Status:** the P0 runtime blocker plus the P1 idempotency / transaction / outbox-model items from the review are implemented; everything in **Remaining Issues** is a tracked follow-up, not a regression.

## Axiom (what to remember)

> The **database** is the source of truth for coexistence and identity: read configuration lazily, name reserved attributes explicitly (`custom_metadata`), and make duplicate ingestion a *database-level* no-op (`ON CONFLICT ... DO NOTHING`), never an application-level check-then-insert.

---

# Storage Alignment Pass 3 - Updated `models.py` (Event Layer + PostgreSQL + repositories)

_Date: 2026-10-02 | Scope: align the existing Event Layer, PostgreSQL storage, and repositories with the updated SQLAlchemy `models.py`. No architecture redesign, no competing migration mechanism, no unrelated file changes._

## A. Original problem & evidence

`models.py` was updated (new policy/behavior/context/execution tables, correlation and
ordering columns on `events`, `metadata_json` attribute naming, artifact-based lineage,
renamed outbox columns) but the tests, parts of the repositories, and the Event Layer
envelope still described the **previous** schema.

Evidence - baseline before any change (`python -m pytest tests -q`):

```
7 failed, 67 passed, 5 errors in 2.39s
```

```
FAILED tests/storage/test_models_and_tables.py::test_metadata_attribute_renamed_to_custom_metadata
FAILED tests/storage/test_models_and_tables.py::test_evaluation_result_natural_unique_constraint_exists
FAILED tests/storage/test_models_and_tables.py::test_outbox_table_columns
FAILED tests/storage/test_repositories.py::test_repositories_do_not_commit
FAILED tests/storage/test_repositories.py::test_lineage_unique_constraint
FAILED tests/storage/test_repositories.py::test_agent_version_unique_constraint
FAILED tests/storage/test_repositories.py::test_outbox_enqueue_and_publish
ERROR  tests/storage/test_repositories.py::test_crud_event_roundtrip              (TypeError: 'agent_type')
ERROR  tests/storage/test_repositories.py::test_duplicate_event_ingestion_...      (TypeError: 'agent_type')
ERROR  tests/storage/test_repositories.py::test_duplicate_event_within_single_batch (TypeError: 'agent_type')
ERROR  tests/storage/test_repositories.py::test_evaluation_result_upsert_...      (TypeError: 'agent_type')
ERROR  tests/storage/test_repositories.py::test_evaluation_result_unique_...      (TypeError: 'agent_type')
```

### Audit findings (schema vs code, before edits)

| # | Finding | Evidence |
|---|---------|----------|
| 1 | Tests seeded `Agent(agent_type=...)`, `Dataset(dataset_type=...)`, `EvaluationCase(input=...)`, `DatasetVersion(checksum=...)` - none exist any more; `Run` fixture omitted NOT NULL `agent_id` | TypeError / IntegrityError above |
| 2 | Tests asserted `custom_metadata`, `uq_evaluation_result_natural_key`, `outbox_id`/`event_type` columns, `Lineage(source_*/target_*/relationship)`, `EvaluationResult(result_id, metric_name, metric_value, evaluator_type)` - all previous schema | FAILED list above |
| 3 | `Event.sequence_number` is NOT NULL + `uq(run_id, sequence_number)` but tests never set it; ordering/parent/correlation paths were untested | `_event()` builder had no sequence_number |
| 4 | Event Layer envelope (`AIEvent`) lacked `sequence_number`, `parent_event_id`, `correlation_id`, `causation_id`, `step_id` although `models.Event` persists them - those columns were unreachable from producers | `schemas/events/common.py` vs `models.py` |
| 5 | No adapter mapping the Event Layer envelope -> storage `Event` | grep: no producer/consumer of `models.Event` outside tests |
| 6 | No repositories for `ContextEvent`/`ContextResource`/`ExecutionEvent` retrieval; `PolicyDecisionRepository` had no `list_for_event`/`get_required` | repositories.py class list (26 classes) |
| 7 | Typed redelivery (event + 1:1 detail) would raise `IntegrityError` on the detail PK - no idempotent path | `create_with_*_detail` = plain `add()+flush()` |
| 8 | Stale docstring: `upsert_many` claimed natural key `(evaluation_run_id, case_id, metric_name)`; model constraint is `uq_evaluation_result_case (evaluation_run_id, case_id)` | repositories.py:818 |
| 9 | `EventRepository._to_row` passed explicit `None` for `system_id`, bypassing the model default `system_id="gaia-agent"` (Core INSERT applies Python defaults only for *omitted* columns) | found by first green-run attempt |
| 10 | `postgres.py` / Alembic: project has **no** Alembic (`migrations/` absent); `create_all` is the schema mechanism | grep "alembic" -> docs only |
| 11 | Event producers are **not connected**: `producers/*`, `src/ai_eval_platform/ingestion/*` (4 files), `sdk/ai_eval_sdk/{events,emitter}.py`, `docker/*`, `.github/workflows/*` are all **0-byte placeholders** | `Get-ChildItem ... Length = 0` |

## B. Root cause

Schema evolution of `models.py` was not propagated to its dependents: the storage tests
still encoded the previous schema, the repositories docstring drifted from the
constraint rename, and the Event Layer envelope was never extended with the ordering /
correlation columns the new `events` table expects. Nothing in CI caught this (CI
workflow file is empty; the storage suite itself was failing).

## C. Changes (files & what changed)

### C.1 Event Layer - `schemas/events/common.py`

Added five **optional, backward-compatible** envelope fields to `AIEvent`, each mapping
1:1 to an `events` column (validated: `sequence_number >= 0`, strings `min_length=1`,
all default `None`):

`sequence_number`, `parent_event_id`, `correlation_id`, `causation_id`, `step_id`.

Legacy producers/envelopes are unaffected (fields default to `None`; `extra="forbid"`
still rejects undeclared fields). No existing contract test was modified - only three
new ones added.

### C.2 Repositories - `src/ai_eval_platform/storage/repositories.py`

1. **`event_from_envelope(envelope, *, sequence_number)` (module-level, pure)** -
   the Event Layer -> `models.Event` adapter: maps all envelope fields to columns
   (`timestamp -> occurred_at`, typed `payload` -> dict via
   `model_dump(mode="json", exclude_none=True)`, `event_type` enum -> its value
   string), reads the five new optional fields with `getattr` defaults so
   **legacy envelopes still map**. No database I/O.
2. **`EventRepository.insert_from_envelope(envelope, *, sequence_number=None) -> int`** -
   idempotent ordered ingest: sequence precedence = envelope field, explicit
   argument, then `next_sequence_number(run_id)`; delegates to
   `insert_ignore_duplicates` (stable `event_id` -> `ON CONFLICT DO NOTHING`).
3. **`EventRepository.insert_with_detail_ignore_duplicates(event, detail, resources=None) -> int`** -
   idempotent atomic ingestion of envelope + 1:1 detail (+ context resources).
   Detail rows use `ON CONFLICT (event_id) DO NOTHING` on their own PK; resources
   are inserted **only when the detail row was actually inserted**, so a replay can
   never duplicate them (`context_resources` has no unique constraint of its own).
   Core INSERTs are deliberate - relationship cascades on transient parents would
   re-INSERT the envelope. Validates detail/event `event_id` consistency
   (`RepositoryError` on mismatch). Flushes only; `session_scope()` remains the
   single commit point.
4. **`EventRepository._to_row` now delegates to the shared `_core_row` helper**:
   `None`-valued columns are omitted so Python/server-side model defaults apply
   (fixes audit finding #9: the `system_id` default was bypassed). Nullable columns
   left unset still store NULL - behaviour otherwise unchanged.
5. **New `ContextEventRepository`** - `create/get/get_required/list_for_run`
   (ordered by `Event.sequence_number`)/`list_by_type/list_resources` (rank ASC
   with unranked-first - deterministic on PostgreSQL *and* SQLite, which order
   NULLs differently).
6. **New `ExecutionEventRepository`** - `create/get/get_required/list_for_run`
   (sequence order)/`list_by_tool/list_by_status` (started-at DESC, NULLs last on
   both dialects).
7. **`PolicyDecisionRepository`** - added `get_required` (`EntityNotFoundError`) and
   `list_for_event(event_id)`. Existing methods untouched.
8. **Docstring fix** - `EvaluationResultRepository.upsert_many` natural key is now
   documented as `(evaluation_run_id, case_id)` (`uq_evaluation_result_case`),
   matching the code and the model. Code was already correct.
9. **`insert_ignore_duplicates` docstring** - records the verified conflict-target
   semantics (duplicate `event_id` skipped even when `uq_event_run_sequence` also
   conflicts; a *different* `event_id` reusing the sequence raises).

No existing repository method signature changed; no callers outside `tests/` exist
(verified by grep), so no call sites needed updating.

### C.3 PostgreSQL storage - `src/ai_eval_platform/storage/postgres.py`

**No code change required** (verified, not assumed):

- `create_tables()` = `Base.metadata.create_all` -> automatically emits the updated
  30-table schema, including the new policy/behavior/context/execution tables,
  `uq_event_run_sequence`, `ix_events_correlation`, self-FK `parent_event_id`,
  CHECK `ck_context_resource_rank_nonnegative`, and the `metadata` DB column -
  confirmed against a real PostgreSQL 17 server (see D).
- `session_scope()` remains the only commit point; repositories only flush.
- Lazy `DATABASE_URL` handling unchanged (pass-1 FIX-2 intact).
- **Alembic was NOT introduced**: the project does not use Alembic (no
  `migrations/`, no config; already tracked as pass-1 remaining issue #10) and the
  task forbids introducing a competing migration mechanism. `create_all` remains
  the schema mechanism. Caveat: `create_all` does not ALTER existing tables - see
  limitations.

### C.4 Tests

- `tests/storage/conftest.py` - fixtures aligned (`agent_type` removed, `Run.agent_id`
  set, `EvaluationCase.question` replaces `input`, dataset fields fixed).
- `tests/storage/test_models_and_tables.py` - stale assertions updated to the new
  schema; `EXPECTED_TABLES` now asserts **exact equality** with all 30 tables; new
  tests for `uq_event_run_sequence`, parent/correlation columns + `SET NULL` FK,
  event indexes, 1:1 detail tables (PK = `event_id`, FK `CASCADE`), context-resource
  FK + CHECK, policy constraints/FKs (`CASCADE`/`RESTRICT`).
- `tests/storage/test_repositories.py` - builders updated (`sequence_number`,
  `evaluation_result_id`/`score`, artifact-based `Lineage`, new outbox columns);
  **all original tests kept** (assertions re-expressed against the new schema, not
  weakened). New coverage: sequence-order retrieval, sequence-collision rejection,
  `next_sequence_number`, parent/child + correlation retrieval, ORM detail cascade,
  atomic rollback of event+detail+resources, parametrized idempotent typed ingestion
  (behavior/context/execution fresh + replay; resources never duplicated),
  mismatched-detail rejection, context/execution/behavior repository retrieval,
  policy version lifecycle, policy decision retrieval, envelope ingestion roundtrip
  (auto + explicit sequence, redelivery no-op), legacy envelope mapping.
- `tests/contract/test_event_schema.py` - 3 additive tests for the new envelope
  fields (accepted / default-None / rejects `sequence_number=-1` and
  `correlation_id=""`).
- **New `tests/storage/test_postgres_integration.py`** - gated by `psycopg2` +
  `DATABASE_URL` (auto-skips otherwise): full 30-table `create_all` on PostgreSQL,
  idempotent event ingestion incl. duplicate/batch/sequence-collision semantics,
  evaluation-result upsert (PK preserved), typed event+detail+resources idempotency
  and rollback, context-resource ordering. **Drops/creates schema in the target
  database - scratch DB only.**

## D. PostgreSQL dialect verification (real server, not assumed)

Environment: local `postgresql-x64-17` (17.10), `psycopg2-binary 2.9.13`, scratch DB
`ai_eval_storage_test`. `ON CONFLICT` semantics were probed with raw `psql` against
the exact constraint shape of `events` (`PRIMARY KEY (event_id)` +
`UNIQUE (run_id, sequence_number)`), then re-verified through the actual repository
code in `test_postgres_integration.py`:

| Scenario | `ON CONFLICT (event_id) DO NOTHING` result | PostgreSQL 17 | SQLite |
|---|---|---|---|
| Exact duplicate (same `event_id`, same run/sequence; conflicts on **both** unique indexes) | row skipped, no error | `INSERT 0 0` | rowcount 0 |
| Duplicate inside a single multi-row statement | 1 row inserted total | verified | rowcount 1 |
| Batch of one new + one exact duplicate | new row inserted, dup skipped | verified | rowcount 1 |
| **Different** `event_id` reusing `(run_id, sequence_number)` | `UniqueViolation` raised (ordering guard) | error on `uq_event_run_sequence` | `IntegrityError` |
| `evaluation_results` `ON CONFLICT (uq_evaluation_result_case) DO UPDATE` | row updated, original PK preserved | test passes | test passes |

Conclusion: the existing conflict target (`event_id`) behaves identically on both
dialects for every scenario - **no change to the conflict target was needed**; the
semantics are now locked in by tests on both engines.

## E. Test results (exact)

```
# baseline before this pass
7 failed, 67 passed, 5 errors in 2.39s

# after this pass (default - SQLite only, PG tests skip)
python -m pytest tests -q   ->   106 passed, 5 skipped in 2.42s
    tests/contract -> 66 passed
    tests/storage  -> 40 passed + 5 skipped (PG-gated)

# after this pass (DATABASE_URL -> PostgreSQL 17 scratch DB)
python -m pytest tests -q   ->   111 passed in 3.28s
    tests/storage/test_postgres_integration.py -> 5 passed in 1.38s
```

No test was deleted or weakened; every pre-existing test either still runs unchanged
or asserts the same property against the updated schema names.

## F. Verified fixes vs remaining limitations

### Verified (with tests, on SQLite and/or real PostgreSQL)

1. Full 30-table schema creates on PostgreSQL 17 and SQLite; all FK/unique/CHECK/index
   definitions asserted.
2. Idempotent event ingestion on stable `event_id` (repeat, batch, PG + SQLite).
3. Event ordering: `uq(run_id, sequence_number)` enforced; distinct-event sequence
   collisions raise; retrieval is sequence-ordered (run, children, correlation).
4. `parent_event_id` / `correlation_id` / `causation_id` / `step_id` round-trip from
   envelope -> storage -> retrieval; FK `SET NULL` verified.
5. Atomicity: event + 1:1 detail + context resources flush together and roll back
   together; typed redelivery is a no-op for all three detail kinds; context
   resources are never duplicated on replay.
6. Policy: versioned policies (unique `(policy_id, version)`, activate/deactivate
   cycle) and decisions (create/get/list by run/version/event) work.
7. Evaluation-result upsert on natural key `(evaluation_run_id, case_id)` preserves
   the original PK (PG + SQLite).
8. Event Layer backward compatibility: legacy envelopes (without the new fields)
   validate and map; all 66 contract tests green.

### Remaining limitations (not regressions; out of scope here)

1. **End-to-end event capture is NOT wired.** `producers/*`,
   `src/ai_eval_platform/ingestion/*` (kafka_consumer, deduplicator, dlq,
   event_validator), `sdk/ai_eval_sdk/{events,emitter}.py`, Dockerfiles and CI
   workflows are all **0-byte placeholders**. The envelope adapter
   (`event_from_envelope` / `insert_from_envelope`) is tested, but **no real
   producer or consumer is connected** - storage tests are not proof of
   end-to-end capture.
2. **No Alembic / no migration path for existing databases.** `create_all` creates
   missing tables but does not ALTER existing ones. A database created against the
   pre-update schema needs manual DDL or a rebuild before this schema is usable
   there (fresh deployments are unaffected).
3. `next_sequence_number()` is `max+1` - safe only with a single writer per run.
   Concurrent ingest must assign sequences upstream; the unique constraint is the
   final arbiter (documented on the method).
4. `PolicyDecision` has no natural unique key in `models.py`, so decisions cannot be
   idempotently upserted; consumers re-deriving decisions must guard themselves
   (the linked `event_id` row *is* idempotent).
5. `context_resources` has no unique constraint on `(context_event_id, resource_id)`
   in `models.py`; idempotency is achieved by only inserting resources when the
   parent detail row was inserted.
6. Pass-1 remaining issues still stand: JSON not JSONB (+ no GIN), outbox relay not
   implemented, no consumer inbox, `delete-orphan` cascades on `Run.events`, free
   string statuses, no partitioning/retention for `events`, no optimistic locking.
7. `pip install psycopg2-binary` is required to run the PG integration tests; they
   skip silently otherwise (not declared in `pyproject.toml` because it has no
   dependency section - pre-existing state).

## Pass-3 status

- **Schema/code mismatches: all identified and resolved** (Event Layer, repositories,
  tests; `postgres.py` verified unchanged-and-correct).
- **Tests:** `106 passed, 5 skipped` (default) / `111 passed` (with PostgreSQL).
- **Not claimed:** end-to-end event capture - producers and ingestion are empty
  placeholders.



