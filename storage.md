# Storage Layer Analysis - AI Evaluation Platform
_Date: 2026-10-01 | Scope: read-only audit + read-only probing, no source changed_

## 1. Inventory

`src/ai_eval_platform/storage/models.py` (1387 lines, 22 entities): Agent, AgentVersion, Run, Event, Dataset, DatasetVersion, EvaluationCase, EvaluationRun, EvaluationResult, MLModel, ModelVersion, PipelineVersion, PipelineComponent, Experiment, TrainingRun, Analysis, DifferentialAnalysis, DriftAnalysis, RootCauseAnalysis, Artifact, Lineage, PolicyDecision. `repositories.py` (~990 lines): one repository per aggregate with create/get/get_required/list + Event.create_many/list_for_run/list_by_type + Run.update_status. `postgres.py`: engine + SessionLocal + session_scope + create/drop/check/dispose.

Core idea (correct): Run = lifecycle row (run_id PK, agent_version FK CASCADE, started_at tz, finished_at nullable, status str, metadata JSON). Event = fact row (event_id PK, run_id FK CASCADE, event_type str, schema_version str, event_time tz, ingestion_time default, processing_time nullable, payload JSON). Envelope maps: event_id->event_id, run_id->run_id, event_type->event_type (.value), schema_version->schema_version, timestamp->event_time, payload->payload (needs model_dump not raw object). system_id/system_version live on Agent/AgentVersion, not per event row - normalized correctly.

## 2. Verdict: idea 8/10, as-built 3/10 BLOCKED


## 3. Entity/tuple notes + read-only layer testing

Entities: 22 tables listed above; focus tuples: Run(run_id, agent_version_id, started_at, finished_at?, status, metadata) - status is free str, no CHECK; consider Literal later but not now. Event(event_id, run_id, event_type str(100) - no FK to enum, correct for extensibility, event_time, ingestion_time, processing_time?, payload JSON NOT NULL - strict payloads serialize to full objects with nulls, fine). EvaluationRun(evaluation_run_id, agent_version FK RESTRICT, dataset_version FK RESTRICT, baseline_run_id self-FK SET NULL, started/finished, status, config) + EvaluationResult(result_id, run FK CASCADE, case FK RESTRICT, metric_name/value, passed?, evaluator_type/version) - mirrors EvaluationCompleted event fields; dataset_id/version from event payload has matching Dataset/DatasetVersion tables.

Read-only tests performed (no writes to repo): (a) contract suite: `pytest tests/contract -q` = 63 passed, still green; (b) event->row mapping probe (4 events: model_called, model_output, error, evaluation_completed) = mapping logic OK, one gotcha: `event.payload` is a BaseModel so row insert must use `event.payload.model_dump()` / `event.model_dump()` not the raw object; (c) storage import probe = FAILED (metadata collision above); (d) sqlite create_all = not reached due to (c). No code changed. tests/e2e, tests/integration, tests/unit, tests/evaluation dirs exist but are empty - no storage+event integration tests exist yet.

Risks: P0 metadata name (fix: rename column attr e.g. extra_data with `metadata` DB name, or DeclarativeBase with custom registry - decision for later, not done here). P1 postgres.py imports DATABASE_URL at module import (raises RuntimeError without env) - untestable without DB env; use sqlite override in tests later. P1 no storage tests at all. P2 status free-strings, no event payload JSON schema check at DB level (fine - keep validation in Pydantic). Verdict: storage idea is right, code needs one rename fix before any storage+event integration test can run.

Good: 3-timestamp event design (event_time vs ingestion_time vs processing_time enables lag metrics), Run/Event split matches contract, indexes on (run_id,event_time) + (event_type,event_time) + (ingestion_time) cover replay and failure-scan queries, CASCADE run->events prevents orphans, EvaluationRun self-FK baseline_run enables A/B diff, RESTRICT on eval FKs protects datasets under test.

Broken: importing models.py crashes 100%: `Attribute name 'metadata' is reserved` - every entity declares `metadata: Mapped[dict] = mapped_column(JSON...)` which collides with DeclarativeBase.metadata. Reproduced: `from src.ai_eval_platform.storage.models import Base` -> InvalidRequestError; `create_engine sqlite + create_all` never reached. So zero tables creatable, zero repositories instantiable, ingestion/storage path fully blocked. Rating as-built 3/10 only because design is sound; runtime is 0.

---

# DEEP ARCHITECTURE REVIEW (pass 2 - self-contained)

_Date: 2026-10-01 | DB: PostgreSQL | ORM: SQLAlchemy 2.0.51 | Python 3.12 | Pydantic 2.13.4_

_Evidence gathered this pass:_
- `import ai_eval_platform.storage.models` -> **`sqlalchemy.exc.InvalidRequestError: Attribute name 'metadata' is reserved when using the Declarative API.`** raised at `models.py:27 class Agent(Base)`.
- 15 occurrences of `metadata: Mapped[...] = mapped_column(...)` (lines 38, 79, 142, 268, 322, 389, 464, 549, 605, 664, 815, 977, 1254, 1307, 1371).
- **Probe (temp copy, repo untouched):** renaming `metadata:` -> `extra_data:` in a throwaway copy makes the module import, `configure_mappers()` pass, and `Base.metadata.create_all(sqlite)` succeed with **all 22 tables**. => the reserved name is the **only** import/DDL-blocking defect; every other item below is design/correctness/scalability, not a crash.
- `pytest tests/contract -q` = **63 passed** (event layer is green).
- Repo reality: only `storage/*` + event schemas + contract tests contain code; **`api/`, `ingestion/`, `monitoring/`, `deployment/`, `features/`, `policy/`, `security/`, `statistics/`, `batch/`, `ml/`, `sdk/`, `producers/`, `configs/`, `docker-compose.yml`, `Makefile`, `.env.example` are 0-byte placeholders.** Storage is the first real component, so it defines the contract everything else must satisfy.

## 1. ALL runtime-breaking issues in models.py

| # | Issue | Severity | Effect |
|---|-------|----------|--------|
| RB-1 | `metadata` attribute on 15 entities collides with `DeclarativeBase.metadata` | **P0 blocking** | Import of `models.py` raises `InvalidRequestError`; no table, no repo, no DDL. Whole storage + ingestion path dead. |
| RB-2 | `postgres.py` runs `DATABASE_URL = _get_database_url()` **at module import** and raises `RuntimeError` if env unset | P1 (blocking in test/dev) | Any `import storage.postgres` (and anything importing the engine/session factory) explodes without live `DATABASE_URL`. Unit tests/tooling are un-importable. |
| RB-3 | Repository `create()` = naive `add()+flush()` with **no idempotency** | P0 for correctness (not import) | Duplicate `event_id` / replayed `EvaluationCompleted` raises `IntegrityError` -> aborts enclosing txn; at-least-once Kafka is not survivable. |

RB-1 is the only *import-time* crash in `models.py`. RB-2 is import-time in `postgres.py`. RB-3 appears at first duplicate delivery. There is **no** second class-body/mapper error hidden behind RB-1 (proven by the probe).

## 2. Why each issue breaks runtime / creates incorrect behavior

- **RB-1**: `DeclarativeBase` reserves `metadata` because every mapped class stores its `Table`/schema collection on `cls.metadata`. Declaring `metadata: Mapped[dict]` shadows it; SQLAlchemy raises during class mapping (`_extract_mappable_attributes`) before any engine/table exists. Nothing downstream can run.
- **RB-2**: config is read and validated eagerly at import. A module-level `raise` in a leaf import makes the package un-importable in CI/`pytest`/workers lacking the DB env. Tests cannot even import the session factory to patch it.
- **RB-3**: `add()+flush()` always attempts INSERT. With `event_id` PK, a redelivered message = second INSERT = `IntegrityError`. Without `ON CONFLICT DO NOTHING` or an inbox table the consumer either poison-loops or (if it swallows the error without rollback) leaves the session failed so every later statement in the txn dies. Worse: `EvaluationResult` and `Lineage` have **no natural unique key**, so replays there don't even error - they silently **duplicate rows** (metric inflation).

## 3. Fixing the SQLAlchemy `metadata` naming conflict (conceptual)

`metadata` is reserved on the *Python attribute* side only, so there are exactly two clean idioms:

```python
# Option A - rename attribute AND column (recommended default)
meta: Mapped[dict[str, Any]] = mapped_column("metadata_json", JSONB, default=dict, nullable=False)

# Option B - rename attribute, keep DB column name "metadata"
meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict, nullable=False)
```

**Recommendation for THIS project: Option A, with a self-describing name (`metadata_json`), NOT `mapped_column("metadata", ...)`.** Reasons:

1. **Parity & greppability.** Attribute name == column name. `grep metadata_json` finds both ORM and raw-SQL usages; `mapped_column("metadata", ...)` creates a hidden rename that bites anyone writing SQL, Alembic migrations, BI/dashboards, or `text()` queries.
2. **No reserved-word ambiguity for readers.** Keeping a DB column literally named `metadata` invites the exact confusion that caused this bug.
3. **Type upgrade is the real fix.** The column should become **`JSONB`**, not `JSON`. Postgres `JSON` stores raw text (no dedup, no index, re-parsed on every read); `JSONB` is binary with GIN indexes and `@>` containment - which event/analysis queries need. The rename is the natural moment to also switch type.
4. **Mutation tracking.** With `JSONB`, wrap with `Mutable.as_mutable(JSONB)` so in-place dict edits flush; otherwise `obj.meta["k"]="v"` is silently dropped.

If an external contract *already* requires the physical name `metadata` (BI tool, published data contract, frozen SQL), use Option B (`mapped_column("metadata", JSONB, ...)`) and document the one mapping. There is **no** way to keep the bare attribute named `metadata`; do **not** "fix" it with `__mapper_args__` or a custom registry - that only hides the collision and breaks tooling.

## 4. Entity-by-entity review (all 22)

Format per entity: **D**=responsibility, **Design**=1-10, then missing/weak, unnecessary, relationship/normalization/consistency/scalability problems, first-class verdict.

### 4.1 Agent (table `agents`)
- **D:** Registry of logical agents/systems (agent-agnostic root). **Design: 8/10.**
- Missing/weak: `name` not unique; no `description`, no `status`/`lifecycle` (active/retired), no `owner`/`team`, no `tenant_id`, no `updated_at`. `agent_type` free string (no enum/CHECK).
- Unnecessary: none.
- Relationship: `versions` cascade `all, delete-orphan`; consistent. But hard-delete of an Agent cascades to versions which CASCADEs to runs while `evaluation_runs` FK is RESTRICT -> delete Agent raises. **Delete semantics inconsistent.**
- Normalization: fine (system_id/system_version live here + AgentVersion).
- Consistency: `metadata` JSON (RB-1).
- Scalability: trivial cardinality; fine.
- First-class? **Yes.**

### 4.2 AgentVersion
- **D:** Immutable version of an agent (git_commit, version). **Design: 8/10.**
- Missing: `artifact_uri`/container image ref, `release_notes`, `is_current`/`released_at`, `updated_at`. No `system_version` semantic clarity (is `version` the semver?). `created_at` only.
- Unnecessary: none.
- Relationship: `runs` cascade delete-orphan (aggressive for a versioned artifact); `evaluation_runs` has no cascade (good). `uq_agent_version(agent_id, version)` good.
- Normalization: good.
- Consistency: metadata JSON (RB-1).
- Scalability: fine.
- First-class? **Yes.**

### 4.3 Run
- **D:** Lifecycle row for one execution of an agent version. **Design: 8/10.**
- Missing: `run_type`/trigger (interactive/batch/eval), `parent_run_id` (for nested/sub-agent runs), `tags`, `error_summary`, `duration` (derivable), `updated_at`, `tenant_id`, optimistic-lock `version`.
- Unnecessary: none.
- Relationship: `events` cascade `all, delete-orphan` -> ORM will load+DELETE every event on parent delete (catastrophic at million-event scale). DB `ondelete=CASCADE` already covers it; the ORM cascade is redundant and harmful. `agent_version` fine.
- Normalization: good (system_id/version not duplicated).
- Consistency: free-string `status` (no CHECK); `started_at` nullable=False but no default; RB-1 metadata.
- Scalability: `ix_runs_agent_version_started_at` good. Cascade issue above. Runs are high-cardinality -> candidates for partitioning/archival.
- First-class? **Yes.**

### 4.4 Event
- **D:** Immutable fact during a run; 3 timestamps. **Design: 9/10** (best-designed table here).
- Missing: no `producer`/`kafka_partition`/`kafka_offset`, no `idempotency`-supporting metadata, no `source_topic`. `schema_version String(20)` fine. Payload JSON -> JSONB recommended.
- Unnecessary: none.
- Relationship: `run` back-ref; DB CASCADE ok. The ORM `Run.events` delete-orphan is the problem (see 4.3), not here.
- Normalization: good - envelope `system_id`/`system_version` correctly NOT duplicated (reached via Run->AgentVersion).
- Consistency: `ingestion_time default=utcnow` is **app-side**; prefer `server_default=func.now()` to avoid client clock skew. No `payload` schema version linkage to a registry.
- Scalability: **highest-volume table.** Indexes good, but no partitioning/retention plan; `ix_events_ingestion_time` alone is low-selectivity. JSONB + GIN needed for payload queries.
- First-class? **Yes.**

### 4.5 Dataset
- **D:** Logical evaluation/training dataset registry. **Design: 7/10.**
- Missing: `name` not unique; no `description`, `owner`, `tenant_id`, `updated_at`, `label_schema`/`task_type`. `dataset_type` free string.
- Unnecessary: none.
- Relationship: `versions` cascade delete-orphan (ok). No link to the runs that use it except through versions (correct).
- Normalization: fine.
- Consistency: metadata JSON (RB-1). `dataset_type` unconstrained.
- Scalability: fine.
- First-class? **Yes.**

### 4.6 DatasetVersion
- **D:** Immutable, checksummed snapshot of a dataset. **Design: 8/10.**
- Missing: **split** (train/val/test) - see Section 6; no `split`/`parent_version_id`, no `created_by`, no `schema_hash`/`label_distribution`, no `is_frozen`. `checksum` NOT NULL good. `row_count` present.
- Unnecessary: none.
- Relationship: `evaluation_cases` cascade; `evaluation_runs`/`training_runs` no cascade (good, RESTRICT). **DriftAnalysis FKs to `dataset_versions` with RESTRICT** (reference/current) - means you can never delete a dataset version used in drift. Intentional but locks deletion.
- Normalization: good.
- Consistency: metadata JSON (RB-1); `dataset_type` on Dataset only.
- Scalability: cases can be large but bounded; fine.
- First-class? **Yes.** NOTE: the stated domain diagram has `DatasetVersion -> DataSnapshot`, but **no `DataSnapshot` entity exists** (Section 6).

### 4.7 EvaluationCase
- **D:** One test case (input + expected output) in a dataset version. **Design: 7/10.**
- Missing: `case_index`/ordering, `split`, `tags`, `weight`, `expected_output` schema/type hint, `created_at`. `external_case_id` not unique within version.
- Unnecessary: none.
- Relationship: `results` back-ref (no cascade -> good; RESTRICT protects cases under test).
- Normalization: fine; `input`/`expected_output` as JSON is right for agent-agnostic.
- Consistency: metadata JSON (RB-1); no ordering column (relying on case_id ordering is arbitrary).
- Scalability: fine (large datasets => many rows, but simple).
- First-class? **Yes.**

### 4.8 EvaluationRun
- **D:** One offline evaluation execution of an agent version over a dataset version. **Design: 8/10.**
- Missing: `evaluator_suite`/`metric_set` reference (metrics are only on results), `triggered_by`/`ci_pipeline_id` (CI/CD gate provenance), `commit_sha` (git_commit like TrainingRun), `updated_at`, `objective`/`pass_threshold`, `tenant_id`. `config` JSON present (good).
- Unnecessary: none.
- Relationship: `baseline_run_id` self-FK `SET NULL` (good for A/B); `baseline_run` relationship has `remote_side` but **no back_populates/reverse** -> cannot query "which runs baseline this one". `results` cascade ok. FKs to agent_version & dataset_version are RESTRICT (safe).
- Normalization: good.
- Consistency: free-string `status`; metadata JSON (RB-1); `config` vs `metadata` overlap (two JSON blobs, unclear boundary).
- Scalability: `ix_evaluation_runs_(agent_version,dataset_version)` good; but no index on `baseline_run_id`.
- First-class? **Yes.**

### 4.9 EvaluationResult
- **D:** One metric value for one case in one evaluation run. **Design: 6/10.**
- Missing: **natural unique key `(evaluation_run_id, case_id, metric_name)`** - without it, event replay duplicates rows (RB-3). Missing `threshold` (event payload carries it, model doesn't), `evaluator_name` (event carries it; model only has type/version), `metric_type`, `duration_ms`, `failure_reason`, `created_at`.
- Unnecessary: none.
- Relationship: FK case RESTRICT, run CASCADE (good).
- Normalization: `metric_name` is a **free string**, not FK to a metric definition -> typos fragment analytics (needs EvaluationMetric, Section 6).
- Consistency: metadata JSON (RB-1); metric_value Float loses precision for ints/exact decimals.
- Scalability: rows = runs x cases x metrics -> large; needs the composite unique + composite index (index exists on (run,metric) but not unique, and no (case) covering).
- First-class? **Yes**, but needs constraints.

### 4.10 MLModel
- **D:** Registry of logical ML models. **Design: 7/10.**
- Missing: `name` not unique; no `description`, `owner`, `task_type`, `tenant_id`, `updated_at`.
- Unnecessary: none.
- Relationship: `versions` cascade delete-orphan (ok).
- Normalization: fine.
- Consistency: metadata JSON (RB-1); `model_type` free string.
- Scalability: fine.
- First-class? **Yes.**

### 4.11 ModelVersion
- **D:** Immutable model artifact version. **Design: 8/10.**
- Missing: `training_run_id` back-link (only the reverse exists on TrainingRun), `stage` (dev/staging/prod), `is_current`, `created_by`, `input_schema`/`output_schema`. `artifact_uri`/`checksum`/`framework` present (good).
- Unnecessary: none.
- Relationship: `training_runs` 1:N (a model version can come from many training runs - usually 1:1/1:N from training, acceptable). `uq_model_version(model_id, version)` good.
- Normalization: `artifact_uri`+`checksum` here **duplicate the Artifact concept** (Section 7) -> two sources of truth.
- Consistency: metadata JSON (RB-1).
- Scalability: fine.
- First-class? **Yes.**

### 4.12 PipelineVersion
- **D:** Versioned definition of an ML/data pipeline. **Design: 7/10.**
- Missing: no owning `Pipeline` (logical) parent -> `name`+`version` unique globally (see 4.13); no `git_commit`, `description`, `owner`, `updated_at`, `schedule`/`orchestrator`.
- Unnecessary: none.
- Relationship: `components` cascade ordered (good); `training_runs` no cascade (good).
- Normalization: **merge/split concern** - a version without a definition entity means "pipeline identity" is a `name` string. Acceptable short-term.
- Consistency: `config` JSON present but **no metadata column** (inconsistent with siblings).
- Scalability: fine.
- First-class? **Yes.**

### 4.13 PipelineComponent
- **D:** Ordered step within a pipeline version. **Design: 6/10.**
- Missing: `input_artifacts`/`output_artifacts` (lineage endpoints), `image`/`entrypoint`/`command`, `depends_on` (DAG edges - only linear `order_index` now), `status`. `parameters` JSON present.
- Unnecessary: `version` on a component inside an already-immutable PipelineVersion is redundant.
- Relationship: `uq(pipeline_version_id, order_index)` enforces **linear order -> cannot model fan-out/DAG** (only a chain).
- Normalization: params JSON fine.
- Consistency: no `created_at`/metadata (inconsistent with other tables).
- Scalability: fine.
- First-class? **Yes**, but the linear-only constraint is a **model limitation** for real DAGs.

### 4.14 Experiment
- **D:** Grouping of training runs under a hypothesis. **Design: 7/10.**
- Missing: `name` not unique; no `owner`, `objective`/metrics to optimize, `status`, `started_at`/`ended_at`, `tenant_id`, `updated_at`.
- Unnecessary: none.
- Relationship: `training_runs` no cascade (good); Experiment->TrainingRun 1:N correct.
- Normalization: fine.
- Consistency: metadata JSON (RB-1).
- Scalability: fine.
- First-class? **Yes.**

### 4.15 TrainingRun
- **D:** One training execution producing/updating a model version. **Design: 7/10.**
- Missing: `git_commit` present (good) but no `hyperparameters` (vs generic `parameters`), no `best_metric`/`objective`, `updated_at`, `triggered_by`, `compute`, **no FK to a train split**. `metrics`/`parameters`/`environment` JSON present (good).
- Unnecessary: none.
- Relationship: `model_version_id` RESTRICT, `dataset_version_id` RESTRICT, `pipeline_version_id` RESTRICT, `experiment_id` SET NULL (good optionality).
- **Problem:** `metrics` is JSON -> training metrics cannot be queried/indexed/compared in SQL; no ModelPerformanceMetric table (Section 6). Experiment comparison is a stated goal -> real gap.
- Normalization: `environment` JSON overlaps an `Environment` entity (Section 6).
- Consistency: free-string `status`; **no metadata** (only parameters/metrics/environment) - inconsistent with the rest.
- Scalability: fine volume; metric queries don't scale in JSON.
- First-class? **Yes.**

### 4.16 Analysis (base)
- **D:** Polymorphic umbrella for an analysis over a scope. **Design: 6/10.**
- Missing: `analysis_type` free string (should be enum + `__mapper_args__.polymorphic_identity`); no `started_at`/`finished_at` (only `created_at`), no `produced_by_run_id`, `severity`, `is_significant`. Has `result` JSON + `metadata` JSON + `status`.
- Unnecessary: `result` JSON **duplicates** the typed subtype columns (Differential/Drift/RootCause) -> two sources of truth.
- Relationship: no relationships declared to subtypes (they point up via `analysis`); no polymorphic mapper configured. Scope is polymorphic (`scope_type`/`scope_id`) with **no FK** -> dangling refs possible.
- Normalization: `scope_type/scope_id` unenforced polymorphic FK (tradeoff; no CHECK on allowed types).
- Consistency: free-string `status`/`analysis_type`; RB-1 metadata.
- Scalability: fine; scope scans use composite index (present).
- First-class? **Yes as a base** - see Section 7 for split/merge.

### 4.17 DifferentialAnalysis
- **D:** Baseline-vs-candidate metric delta (regression/improvement). **Design: 8/10.**
- Missing: no `verdict` (regression/improvement/neutral), no `sample_size`, no `is_regression` bool. (Has p_value/effect_size/CI - good.)
- Unnecessary: none.
- Relationship: `analysis_id` = FK+PK (1:1 to Analysis, correct); `baseline_run`/`candidate_run` FKs to evaluation_runs with explicit `foreign_keys` (good). Analysis relationship `uselist=False` correct.
- Normalization: `metric_name` free string (same issue as EvaluationResult).
- Consistency: no `created_at`/metadata (relies on parent) - reasonable for a subtype.
- Scalability: fine.
- First-class? **Yes (subtype).**

### 4.18 DriftAnalysis
- **D:** Per-feature drift test between two dataset versions. **Design: 7/10.**
- Missing: `analysis` relationship **not declared** (inconsistent with the other two subtypes); no `threshold` used, `drift_score`/`psi`, `sample_size`.
- Unnecessary: none.
- Relationship: `analysis_id` is FK **not PK** + separate `drift_id` PK -> **1:N** (many features per analysis), reasonable but **inconsistent** with Differential/RootCause (1:1). Two dataset-version FKs RESTRICT.
- Normalization: `feature_name`/`method` free strings (no Feature entity, Section 6).
- Consistency: no `analysis` back-ref; no metadata (ok for subtype).
- Scalability: 1:N fine; index exists on feature_name but **not on `analysis_id`**.
- First-class? **Yes (subtype)**, but reconcile the PK shape.

### 4.19 RootCauseAnalysis
- **D:** Candidate root cause with contribution/confidence. **Design: 7/10.**
- Missing: explicit FK to the **DifferentialAnalysis** it explains (only polymorphic `target_type/target_id` + `candidate_component` string). No `rank`, no `method`.
- Unnecessary: none.
- Relationship: `analysis_id` FK+PK (1:1, correct); `analysis` relationship declared (good).
- Normalization: `target_type/target_id` polymorphic + `candidate_component` string -> weak linkage to PipelineComponent/Feature.
- Consistency: `evidence` JSON (good).
- Scalability: fine.
- First-class? **Yes (subtype).**

### 4.20 Artifact
- **D:** A stored file/blob reference (uri, checksum, size). **Design: 6/10.**
- Missing: **no owner link** (Artifact is an island - not connected to ModelVersion/DatasetVersion/Run/TrainingRun except via generic Lineage); no `content_type`, `storage_backend`/`bucket`, `created_by`, `updated_at`. `checksum` not unique.
- Unnecessary: none.
- Relationship: none declared at all -> cannot navigate owner<->artifact in ORM.
- Normalization: `artifact_uri` is **duplicated** in ModelVersion, DatasetVersion, AnswerGenerated payload, Artifact -> multiple representations of one concept.
- Consistency: `size_bytes Integer` (32-bit) will **overflow >2GB** -> should be `BigInteger`.
- Scalability: size overflow; no dedup index on checksum.
- First-class? **Yes**, but must be re-anchored (Section 7).

### 4.21 Lineage
- **D:** Generic directed edge between any two entities. **Design: 6/10.**
- Missing: **no unique constraint** on `(source_type, source_id, target_type, target_id, relationship)` -> duplicate edges on replay (RB-3). No `run_id`/`created_by` that produced the edge.
- Unnecessary: none.
- Relationship: fully polymorphic `source/target type+id`; **no FKs** (by design) -> dangling edges possible.
- Normalization: acceptable graph model, but unconstrained.
- Consistency: metadata JSON (RB-1).
- Scalability: fine volume; needs composite unique + direction indexes (present); ideally include `relationship` in the index.
- First-class? **Yes.**

### 4.22 PolicyDecision
- **D:** Recorded policy decision for a scope. **Design: 6/10.**
- Missing: `policy_id` (only `policy_version` string), link to the `Analysis`/`RootCause` that caused the decision, `triggered_rule_ids`/`reason_codes` (event payload has them; model only has free-text `reason`), `decided_by`, `updated_at`.
- Unnecessary: none.
- Relationship: `scope_type/scope_id` polymorphic (no FK); **no link to Analysis/RootCauseAnalysis** though the domain diagram chains Root Cause -> Policy Decision.
- Normalization: `reason` Text vs structured `reason_codes` (payload list) -> lossy.
- Consistency: `decision` free string (payload uses Literal allow/block/review -> should mirror with CHECK/enum).
- Scalability: fine; scope + created_at indexes present.
- First-class? **Yes**, but must be wired to the analysis chain.

## 5. Relationship review (do NOT assume current links are correct)

- **Agent -> AgentVersion -> Run -> Event:** Correct spine, correctly normalized (system_id/version live on Agent/AgentVersion, not re-stored per Event). Issues: (a) `Run.events` ORM cascade `all, delete-orphan` is dangerous at scale (loads+deletes every event in Python); rely on DB `ON DELETE CASCADE` and drop the ORM delete-orphan. (b) `AgentVersion.runs` cascade delete-orphan too aggressive for versioned artifacts. (c) No `parent_run_id` for nested/sub-agent runs. (d) Delete semantics conflict: Agent CASCADE -> AgentVersion CASCADE -> Run vs EvaluationRun RESTRICT => deleting an Agent with eval runs fails at the DB.
- **Dataset -> DatasetVersion -> EvaluationCase:** Correct. Issues: (a) missing `split` (train/val/test) and case ordering; (b) `EvaluationCase.external_case_id` not unique per version; (c) `DatasetVersion` has no `parent_version_id`.
- **EvaluationRun -> EvaluationResult:** Conceptually correct, but (a) **no uniqueness** on `(evaluation_run_id, case_id, metric_name)` -> duplicate results on replay; (b) `EvaluationRun.baseline_run` is a one-way self-FK with no reverse relationship; (c) `EvaluationResult.metric_name` is a free string not a FK -> needs a metric definition.
- **MLModel -> ModelVersion -> TrainingRun:** Correct 1:N. Issues: (a) TrainingRun also FKs DatasetVersion/PipelineVersion (all RESTRICT) - fine, but the training **dataset snapshot** should reference a split; (b) ModelVersion has no direct `training_run_id` (only reverse) so "which run produced this version" is a join; (c) metrics in JSON -> can't compare experiments in SQL.
- **Experiment -> TrainingRun:** Correct 1:N, `experiment_id` SET NULL optional (good). Missing experiment-level aggregate metrics/results and uniqueness on name.
- **PipelineVersion -> PipelineComponent:** Correct 1:N ordered, BUT `uq(pipeline_version_id, order_index)` enforces a **linear chain**, not a DAG; no component I/O artifact links, so pipeline lineage is not representable.
- **Analysis and its analysis types:** Base+subtype shape is right in spirit, but wired as **association tables** (subtypes FK+PK back to `analyses`) rather than SQLAlchemy polymorphic inheritance. Two real bugs: (a) `Analysis.result` JSON **duplicates** subtype columns -> ambiguous source of truth; (b) **DriftAnalysis uses a different PK shape** (`drift_id` PK, `analysis_id` FK non-PK => 1:N) while Differential/RootCause use `analysis_id` as PK (1:1) -> inconsistent contract for consumers.
- **DifferentialAnalysis / Regression / RootCause:** DifferentialAnalysis exists and is well-formed. **A `Regression` entity is missing entirely** despite appearing in the stated diagram and in `evaluation/regression.py`. RootCauseAnalysis exists but does **not** FK to the DifferentialAnalysis it explains (only polymorphic `target_type/target_id` + a `candidate_component` string). The intended chain `Differential -> Regression -> RootCause -> PolicyDecision` is therefore **not enforced by the schema**.
- **Artifact / Lineage:** Both exist but only loosely coupled. `Artifact` has **no owning relationship at all**, and `Lineage` is fully polymorphic with no uniqueness. ModelVersion/DatasetVersion `artifact_uri` duplicate Artifact. These should be the single source of truth for produced outputs and their edges.
- **PolicyDecision:** Isolated. References a scope polymorphically and stores free-text `reason`; **not linked to the Analysis/RootCause** that produced it, and `decision` is an unconstrained string although the event layer defines `Literal["allow","block","review"]`.

## 6. Missing entities for a production AI-eval/MLOps platform

For each: problem solved / why existing can't / relationship / NOW vs LATER.

1. **OutboxEvent (transactional outbox)** - Solves the dual-write problem (DB commit vs Kafka publish). `Event` is the *ingested* fact, not the *to-be-published* record; you cannot atomically "insert domain row + emit Kafka" without it. Relationship: side table written in the same txn as Run/EvaluationRun; a relay publishes then marks dispatched. **NOW (P1)** before any Kafka producer/consumer is wired.
2. **ConsumerState / ProcessedEvent (inbox)** - Records per-consumer processed `event_id`/offset so redelivery is a no-op and offsets commit safely. `Event` PK dedups the *store* but not side effects (creating EvaluationResults); an inbox keyed `(consumer, event_id)` gives idempotent side effects. Relationship: references Event by `event_id`. **NOW (P1)**.
3. **EvaluationMetric (definition)** - Catalog of metric name + direction (higher/lower is better) + default threshold. `EvaluationResult.metric_name` is a free string today, so typos silently fragment analytics and gates can't know direction. Relationship: `EvaluationResult.metric_id -> EvaluationMetric`; `DifferentialAnalysis.metric_id`. **NOW (P1)** - gates depend on it.
4. **EvaluationSuite** - Named, versioned set of (dataset version + metric set + thresholds) for a repeatable gate. `EvaluationRun.config` JSON can't be referenced/versioned/diffed. Relationship: DatasetVersion + EvaluationMetrics; EvaluationRun FKs a suite version. **LATER (P2)** but design now.
5. **EvaluationConfig (versioned)** - Only justified once suites/baselines are versioned; `config` JSON is fine now. **LATER (P2)**.
6. **DatasetSplit** - train/val/test membership + roles. `DatasetVersion`/`EvaluationCase` have no split concept, so eval vs training data can't be distinguished; drift reference/current need it too. Relationship: `DatasetVersion 1:N DatasetSplit N:M EvaluationCase`. **LATER (P2)** (add a `split` column to EvaluationCase now as a cheap first step).
7. **Baseline** - First-class approved reference (points at an EvaluationRun/ModelVersion with approval metadata). Today `baseline_run_id` is an ad-hoc self-FK with no approval/state. Relationship: -> EvaluationRun/ModelVersion; referenced by DifferentialAnalysis. **LATER (P2)**.
8. **Deployment / ModelServing / Environment** - Which ModelVersion (and AgentVersion) is deployed where (env, endpoint, rollout state). Nothing today records serving; `deployment/` is an empty skeleton. Relationship: Deployment -> ModelVersion + Environment; Lineage edges from EvaluationRun gates. **LATER (P2)**.
9. **Alert** - Durable alert state (fired/ack/resolved) rather than a transient event. `monitoring/alerts.py` empty; alerts need state machines + dedup keys. Relationship: -> scope (Analysis/Drift) + rule. **LATER (P2)**.
10. **HumanReview** - `HUMAN_REVIEW_*` events exist but reviews are stateful (pending/assigned/decided); events can't be queried as "open reviews". Relationship: -> scope + reviewer; links to PolicyDecision. **LATER (P2)**.
11. **AuditRecord** - Immutable who-did-what-when for policy/deployment/dataset changes (compliance). Events are machine facts; audit is about actors and mutations. Relationship: polymorphic target + actor. **LATER (P2)**.
12. **DataQualityResult** - Per dataset-version quality checks (nulls, schema, duplicates). `batch/data_quality.py` planned; drift is different. Relationship: -> DatasetVersion (+ split). **LATER (P3)**.
13. **EventSchema / SchemaRegistry** - Registers `event_type` + `schema_version` + JSON schema + compatibility so ingestion can validate/reject incompatible payloads. `Event.schema_version` is a bare string today. Relationship: referenced by Event. **LATER (P2)** (ingestion validation depends on it).
14. **FeatureDefinition / FeatureSet** - Named features/types/sources so drift/anomaly reference features instead of free strings. `DriftAnalysis.feature_name` is a string today. Relationship: FeatureSet -> FeatureDefinition; DriftAnalysis FK. **LATER (P3)**.
15. **ModelPerformanceMetric / ExperimentResult** - Queryable numeric metrics for TrainingRun/ModelVersion/Experiment (instead of JSON) so gates/comparisons work in SQL. Relationship: -> TrainingRun/ModelVersion. **LATER (P2)** (JSON covers proof-of-concept only).
16. **RunTag** - Key/value tags for filtering runs; now only JSON metadata. **LATER (P3)**.
17. **Tenant / Organization / Project** - Multi-tenancy boundary on every root entity. None today; retrofitting later is expensive. Relationship: `tenant_id` FK on Agent/Dataset/MLModel/etc. **NOW if multi-tenant is real; else reserve the column (P2)**.
18. **Artifact owner link / ArtifactLink** - Artifact is an island; add an owner relation (or rely on Lineage). **NOW (P1)** as part of the Artifact fix.

Entities deliberately **NOT** recommended now (avoid schema for schema's sake): `DataSnapshot` (DatasetVersion already *is* the snapshot - make that explicit rather than add a table), `PlanStep` tables (keep in events), `ExperimentResult` as its own table before metric tables exist.

## 7. Merge / split / remove review

- **Analysis (base):** **KEEP as base, CHANGE its mechanics.** Adopt real SQLAlchemy polymorphic inheritance (`__mapper_args__ = {"polymorphic_on": analysis_type, "polymorphic_identity": "analysis"}`) so `session.get(Analysis, id)` returns the right subtype and queries are typed. **Remove `Analysis.result` JSON** (or keep as a read cache explicitly documented) so the subtype columns are the single source of truth.
- **DifferentialAnalysis:** **KEEP.** Add `verdict`/`is_regression`/`sample_size`; FK `metric_id` once EvaluationMetric exists.
- **DriftAnalysis:** **KEEP but CHANGE its PK shape.** Either make `analysis_id` the PK (1:1, consistent) and move multi-feature drift to a child `DriftFeatureResult` table, **or** accept 1:N and add the missing `analysis` relationship + `analysis_id` index. Pick one and document it.
- **RootCauseAnalysis:** **KEEP**, and ADD a real FK to the `DifferentialAnalysis`/`Regression` it explains instead of only polymorphic `target_type/target_id`.
- **Regression:** **ADD (missing).** It is in the stated model and `evaluation/regression.py`; without it the differential->rootcause chain has a hole.
- **PolicyDecision:** **KEEP**, wiring it to the Analysis/RootCause chain, mirroring the event `Literal` for `decision`, and storing structured `reason_codes`/`triggered_rule_ids`.
- **PipelineComponent:** **KEEP** (needed) but relax the linear-only unique constraint and add component I/O artifact links; do not split yet.
- **Artifact:** **KEEP, RE-ANCHOR.** Give it an owner (FK/Lineage), `BigInteger` size, `content_type`, and make it (not per-entity `artifact_uri`) the source of truth for blobs.
- **Lineage:** **KEEP, CONSTRAIN.** Add composite unique on the edge; consider typed edge tables only if edge volume/typed queries demand it (LATER).
- **Remove/Merge candidates:** No entity should be deleted outright. `Dataset`/`MLModel`/`Agent`/`Experiment` name strings are the only reason to consider a shared "logical entity/NamedThing" abstraction - **do not** merge; keep them explicit. The only true "merge" is folding duplicated `artifact_uri`/JSON blobs into Artifact and into typed subtype columns.

## 8. PostgreSQL design concerns

- **Primary keys:** All `String(64)` surrogate IDs. Works, but text PKs inflate index size and fragment inserts. Prefer native `UUID` (or ULID stored as UUID) with a consistent generator; keep `event_id` exactly as the producer sends it (never regenerate - idempotency depends on it).
- **Foreign keys:** Present and mostly sane, but (a) polymorphic `scope_type/scope_id`, `source/target_type/id`, `target_type/target_id` have **no FK** (add CHECK on allowed type values); (b) `ondelete` is **inconsistent** across the graph (CASCADE vs RESTRICT vs SET NULL in one path).
- **Unique constraints:** Missing on: `agents.name`, `datasets.name`, `ml_models.name`, `experiments.name`; `evaluation_results(evaluation_run_id, case_id, metric_name)`; `lineage(source_type, source_id, target_type, target_id, relationship)`; `artifacts.checksum` (dedup). Add them.
- **Indexes / composite indexes:** Good coverage on `events`. Missing: `evaluation_runs(baseline_run_id)`, `training_runs(experiment_id)`, `drift_analyses(analysis_id)`, `evaluation_results(case_id)` exists (good). Add GIN on `events.payload` (JSONB) for payload search.
- **Idempotency:** see Section 9.
- **JSON vs JSONB:** All JSON -> **JSONB** (indexing, containment, dedup, faster reads). Use `Mutable.as_mutable(JSONB)` for mutated blobs.
- **Nullable vs NOT NULL:** JSON blobs are `NOT NULL default {}` (good). But `status` is a free string with no CHECK; `started_at NOT NULL` has no default; several important fields (`checksum`, `row_count`) are nullable without a reason.
- **Timestamps:** All are **Python-side** `default=utcnow` (no `server_default`). Raw SQL/bulk loads/other services won't populate them, and `ingestion_time` inherits client clock skew -> use `server_default=func.now()` for ingestion/created and keep `event_time` from the envelope. **No `updated_at` anywhere**; **no timezone mismatch guard** despite `DateTime(timezone=True)`.
- **Optimistic concurrency:** None. `Run`/`EvaluationRun`/`TrainingRun`/`Analysis` status transitions are read-modify-write -> lost updates under concurrent consumers. Add a `version_id`/`lock_version` (SQLAlchemy `version_id_col`) or `UPDATE ... WHERE status = <expected>`.
- **Large artifacts:** `Artifact.size_bytes Integer` (32-bit) overflows >2GB -> `BigInteger`. Keep blobs **out** of Postgres (uri only); `Event.payload` can still be large but JSONB TOASTs it - watch for very large payloads. Add checksum-based dedup.
- **Polymorphic references:** Deliberate flexibility, but unenforced -> dangling references. Mitigate with CHECK constraints on the type columns and an integrity job.
- **Retention/partitioning:** `events` (append-only, time-series) should be **range-partitioned by `ingestion_time`** (e.g. monthly) with drop/archive; `evaluation_results`, `lineage`, `analyses`, `runs` are also growth tables. No partitioning/retention exists today.

## 9. Idempotency review

- **Which DB constraints provide the guarantee:** Today, only the **`events.event_id` primary key**. That guarantees the *event row* is inserted once, and nothing else. `evaluation_results`, `lineage`, `analyses`, `policy_decisions` have **no natural unique key**, so they have **no idempotency guarantee** - replays duplicate them.
- **Where idempotent consumer behavior belongs:** In the **ingestion/consumer layer** (currently empty `ingestion/kafka_consumer.py`, `deduplicator.py`), expressed as **SQL upsert semantics**, not in the ORM `create()`. The DB enforces it; the consumer orchestrates the txn.
- **Should `event_id` be the primary key?** **Yes.** It is the producer-supplied, stable idempotency key and already PK. Keep it. Do **not** add a surrogate autoincrement PK - that would destroy the natural key. Optionally add a unique `event_id` per topic-partition awareness via a `(topic, event_id)` inbox key, but the PK is the anchor.
- **Is check-then-insert unsafe?** **Yes.** A `SELECT`-then-`INSERT` is a classic TOCTOU race: two consumers both see "absent", both `INSERT`, one gets `IntegrityError` (or, without a unique key, you get silent duplicates). Never implement idempotency with an application-level existence check.
- **Should PostgreSQL `ON CONFLICT` be used?** **Yes.** Use the Postgres dialect upsert: `insert(Event).values(...).on_conflict_do_nothing(index_elements=["event_id"])`, and for side-effect tables `on_conflict_do_update(...)` on their natural keys. This makes duplicate delivery a *no-op at the DB level* with no exception and no aborted txn.
- **How duplicate Kafka delivery should behave:** Kafka is at-least-once -> duplicates are **expected**. A duplicate `event_id` must: (1) be treated as **success** (not error), (2) NOT be dead-lettered, (3) NOT abort the batch, (4) optionally increment a `duplicates_total` metric, (5) commit the offset only after the enclosing transaction that stored the event (and any derived rows) commits. Offsets must be committed **after** DB commit (or use the outbox) to avoid losing events on crash.

## 10. Transaction review

- **What belongs in `postgres.py`:** engine creation, `SessionLocal` factory, `session_scope` (unit-of-work context manager), pool config, connection health check, and migrations hook. It should **not** contain domain logic. **Change:** make `DATABASE_URL` resolution **lazy** (factory function), so importing the module never raises (RB-2).
- **What belongs in `repositories.py`:** only persistence/query logic bound to an injected `Session`. **No commits.**
- **Where transaction boundaries should live:** in the **service/use-case layer**, one `session_scope()` per business operation (e.g. "ingest a batch of events", "create evaluation run + results"). The repository methods just `flush()` inside that scope.
- **Should repositories commit or only flush?** **Only `flush()`** - and that is exactly what the current code does (good). Committing inside repositories would prevent composing multiple repositories in one atomic unit.
- **How multiple repositories participate in one transaction:** construct them with the **same `Session`** from a single `session_scope()`; all their `flush()`es share one DB transaction that commits once at the scope exit. (This is the current model and it is correct.)
- **How Transactional Outbox fits:** In the same txn that writes the domain rows, also `INSERT` an `OutboxEvent` row (aggregate type/id, payload, topic, status=NEW). A **separate relay** (poller or CDC/Debezium) reads NEW rows, publishes to Kafka, and marks them SENT - all with at-least-once + idempotent consumers. This gives atomic "DB change AND event emitted" without 2PC. It requires: `OutboxEvent` table (Section 6 #1), a relay process, and a `status`/`attempts`/`next_attempt_at` state machine.

## 11. Repository layer review

- **Only persistence/query logic?** Mostly **yes**. `repositories.py` contains `create/get/list/get_by_*/update_status/add_component` - persistence-shaped. Guard clauses (`get_required` raising `EntityNotFoundError`) are acceptable boundary helpers.
- **Business logic present?** Minimal but present: `update_status` encodes a state transition and `PipelineRepository.add_component` encodes aggregate composition. **State-transition rules** (which status may follow which, and who may set it) belong in a domain/service layer, not the repo. Move validation out later.
- **Do they commit?** **No*** - all methods end in `flush()`. This is **correct**; keep it. (Verify in review that no future `commit()` sneaks in.)
- **Duplicate/idempotent inserts handled?** **No.** Every `create()` is a blind `add()+flush()`. This is the **main repository defect** (RB-3). Add explicit idempotent methods: `EventRepository.insert_ignore_duplicates(events)` using `ON CONFLICT DO NOTHING`; `EvaluationResultRepository.upsert_many(...)` using `ON CONFLICT (evaluation_run_id, case_id, metric_name) DO UPDATE`.
- **Useful query methods?** Partial. Present: `get_by_version`, `list_for_*`, `list_by_type`, `get_latest_for_scope`, `get_by_checksum`. **Missing:** `get_many(ids)`, cursor/keyset pagination (only `limit/offset`), exists checks, time-range queries on events (`between`), `count`/aggregate helpers for gating, `EvaluationRunRepository.get_latest_for_agent`, `DatasetVersionRepository.get_by_checksum`, `LineageRepository.exists(edge)`, `AgentRepository.get_or_create`.
- **Missing methods required by the domain:** idempotent ingest (above); `get_latest`/`leading` helpers for baselines; a gate query that joins `EvaluationResult` + `EvaluationMetric` (does not exist yet because EvaluationMetric doesn't); outbox enqueue/claim/mark-sent (n/a until OutboxEvent exists); bulk `insert` for high-volume events (current `create_many` is per-row `add_all`, not a single `INSERT ... VALUES (...),(...)`).
- **Cross-cutting:** repositories take a `Session` in `__init__` (good DI, enables shared transactions). No repository returns ORM entities for lazy-loaded relationships beyond the session lifetime risk if a caller keeps them after close - acceptable with `expire_on_commit=False`.

## 12. Architecture maturity assessment

| Dimension | Rating | Justification |
|---|---|---|
| Domain model | **7/10** | Correct core (Agent/Run/Event/Dataset/Model/Training/Analysis). Docked for missing Regression, missing metric/feature definitions, free-string enums, duplicated JSON-vs-column sources of truth, and no tenancy. |
| Database design | **6/10** | Sensible tables/indexes; but `metadata` crash, JSON-not-JSONB, no server-side defaults, no `updated_at`, `Integer` size overflow, missing unique keys, no partitioning. |
| Relationship design | **6/10** | Spine is right; a subtype (Drift) has a different PK shape, `artifact_uri` duplication, Artifact/PolicyDecision islands, missing reverse relationships, inconsistent `ondelete`. |
| Idempotency design | **3/10** | Only `events.event_id` PK; side-effect tables have no natural keys; no `ON CONFLICT`; no inbox; deduplicator/consumer empty. |
| Transaction design | **7/10** | `session_scope` + flush-only repos is the right pattern; docked for import-time `DATABASE_URL`, no outbox, no optimistic locking. |
| Repository design | **6/10** | Clean flush-only DI structure; but no idempotent writes, no bulk insert, limited query surface, a little business logic. |
| Scalability | **4/10** | No partitioning/retention on `events`; dangerous ORM delete-orphan cascade; text PKs; per-row inserts; no optimistic concurrency. |
| MLOps suitability | **5/10** | Has models/training/experiments/pipelines, but metrics are JSON (unqueryable), no deployment/serving, no model registry stage/approval, no feature store linkage. |
| AI evaluation suitability | **6/10** | Has datasets/cases/results/runs/baselines/drift/differential; missing metric definitions, suites, splits, regression entity, verdicts. |
| Production readiness | **3/10** | Import crashes; no tests for storage; no migrations (Alembic) wired; no idempotency/outbox; no tenancy; no observability wiring. |

**Overall:** design instinct **7/10**, as-built **~4/10**, and **0/10 until RB-1 is fixed** (it will not import).

## 13. Prioritized action plan

### P0 - runtime-breaking / correctness
1. **Rename the reserved `metadata` attribute** (15 entities) -> `custom_metadata` (attribute + column). *Why:* module won't import; whole platform dead. *Fix:* rename + update repositories/references. *File:* `storage/models.py`. *NOW.*
2. **Make `DATABASE_URL` lazy** in `postgres.py` (factory, not module-level). *Why:* import raises without env; tests/CI un-importable. *Fix:* `get_engine()`/`get_sessionmaker()` with memoization. *File:* `storage/postgres.py`. *NOW.*
3. **Add natural unique keys + idempotent inserts.** `uq(evaluation_run_id, case_id, metric_name)` on EvaluationResult; `uq` on Lineage edge; `ON CONFLICT DO NOTHING`/`DO UPDATE` repository methods. *Why:* Kafka replay silently duplicates results/edges. *File:* `models.py` + `repositories.py`. *NOW.*

### P1 - fix before Kafka/integration
4. **Transactional Outbox (`OutboxEvent`)** + relay. *Why:* atomic DB+emit. *File:* new table in `models.py` + relay in `ingestion/`. *NOW.*
5. **Idempotent consumer inbox/ConsumerState** keyed `(consumer, event_id)`; commit offsets after DB commit. *Why:* safe at-least-once. *File:* `ingestion/kafka_consumer.py`, `ingestion/deduplicator.py`. *NOW.*
6. **`EvaluationMetric` definition entity** + FK from `EvaluationResult.metric_name`/`DifferentialAnalysis`. *Why:* gates need metric direction/thresholds; free strings fragment analytics. *File:* `models.py`. *NOW.*
7. **Remove dangerous ORM cascades** (`Run.events`, `AgentVersion.runs` delete-orphan); rely on DB `ON DELETE CASCADE`. *Why:* Python-side delete of millions of events. *File:* `models.py`. *NOW.*
8. **JSON -> JSONB everywhere** + `Mutable` wrapper + GIN on `events.payload`. *Why:* query/index/dedup. *File:* `models.py`. *NOW.*
9. **Add storage test suite** (import, `create_all`, repo smoke, idempotency/replay, txn rollback) using SQLite/`DATABASE_URL` override. *Why:* zero coverage today. *File:* `tests/`. *NOW.*

### P2 - important production improvements
10. **`updated_at` + `server_default=func.now()`** on mutable tables; `ingestion_time` server-side. *File:* `models.py`.
11. **Optimistic concurrency** `version_id` on Run/EvaluationRun/TrainingRun/Analysis. *File:* `models.py`.
12. **Status enums/CHECK** mirroring event `Literal`s (allow/block/review; run statuses). *File:* `models.py`.
13. **`Regression` entity** + FK from RootCauseAnalysis to Differential/Regression; wire PolicyDecision to the analysis chain. *File:* `models.py`.
14. **Reconcile Analysis subtype mechanics** (polymorphic inheritance; Drift PK shape; drop `result` duplication). *File:* `models.py`.
15. **Artifact re-anchoring** (owner link, `BigInteger` size, `content_type`, checksum unique) + de-dup `artifact_uri`. *File:* `models.py`.
16. **Multi-tenancy** `tenant_id` (or reserve column). *File:* `models.py`.
17. **EvaluationSuite / Baseline / DatasetSplit (`split` col) / HumanReview / ModelPerformanceMetric / EventSchema** as justified. *File:* `models.py`.
18. **Alembic migrations** replacing `create_all` for prod. *File:* new `migrations/`.
19. **Missing indexes/unique names** (`agents.name`, `datasets.name`, `ml_models.name`, `experiments.name`; `evaluation_runs(baseline_run_id)`, `training_runs(experiment_id)`, `drift_analyses(analysis_id)`).

### P3 - future
20. **Partitioning/retention** for `events` (monthly range) + archival.
21. **FeatureDefinition/FeatureSet**, **DataQualityResult**, **RunTag**, **Deployment/Environment/ModelServing**, **Alert**, **AuditRecord**.
22. **Bulk `INSERT ... VALUES` ingest**, keyset pagination, `get_many`, aggregate/gate query helpers.

## 14. No files modified

Per instruction, **no source file was changed this pass**. The only write was appending this review to `storage.md`. The `metadata` rename, JSONB switch, and constraint additions are **recommendations**, not yet applied. (The temp probe renamed a throwaway copy in `%TEMP%` only; the repo copy is untouched.)

## A. KEEP
- The **Run/Event split** (lifecycle row vs immutable fact) and the **3-timestamp** event design.
- **Envelope normalization**: `system_id`/`system_version` on Agent/AgentVersion, referenced by Run - do not denormalize onto Event.
- **Agent -> AgentVersion -> Run -> Event** spine; **Dataset -> DatasetVersion -> EvaluationCase**; **MLModel -> ModelVersion -> TrainingRun**; **Experiment -> TrainingRun**; **Analysis base + Differential/Drift/RootCause subtypes** (concept); **Artifact** and **Lineage** as cross-cutting concepts.
- **Repositories flush-only + injected Session**, and **`session_scope`** transaction pattern.
- **`events.event_id` as PK** (the idempotency anchor).
- Composite indexes already on `events`/`analyses`; `uq_*_version` constraints on AgentVersion/DatasetVersion/ModelVersion/PipelineVersion.

## B. CHANGE
- `metadata` -> `custom_metadata` (attribute + column) on all 15 entities - **the P0.**
- `JSON` -> `JSONB` (+ `Mutable`); add GIN on `events.payload`.
- Lazy `DATABASE_URL`; add Alembic; server-side `created_at`/`ingestion_time`; add `updated_at`.
- Add optimistic-lock `version_id`; status CHECK/enums; missing unique keys/indexes.
- Drop ORM `delete-orphan` cascades on `Run.events` / `AgentVersion.runs`.
- Reconcile Analysis subtype PK mechanics; remove `Analysis.result` duplication.
- Re-anchor Artifact; `size_bytes` -> `BigInteger`; wire PolicyDecision to the analysis chain.
- Repository `create()` -> add idempotent/upsert variants and bulk insert.

## C. ADD
- **Now (P1):** OutboxEvent, ConsumerState/ProcessedEvent (inbox), EvaluationMetric, Artifact owner link.
- **Later (P2/P3):** Regression, EvaluationSuite, Baseline, DatasetSplit, HumanReview, AuditRecord, EventSchema/SchemaRegistry, ModelPerformanceMetric, Tenant, Deployment/Environment, Alert, DataQualityResult, FeatureDefinition/FeatureSet, RunTag.

## D. REMOVE/MERGE
- **No entity deletion.** Merge duplicated blob references into **Artifact**; collapse duplicated `Analysis.result` JSON (typed subtype columns authoritative); treat **DatasetVersion as the DataSnapshot** (do not add a separate snapshot table).

## E. FINAL RECOMMENDED DOMAIN MODEL (ASCII)

```
[Tenant]--< Agent >--< AgentVersion >--< Run >--< Event
                                             |          (event_id PK = idempotency anchor)
                                             +--< EvaluationRun >--< EvaluationResult >-- EvaluationMetric
                                             |        |  (baseline_run_id self-FK)
                                             |        +--< EvaluationCase >-- DatasetVersion >-- Dataset
                                             |                                   |        (split: train/val/test)
                                             |                                   +--< DataQualityResult (later)
                                             |
                                             +--< TrainingRun >--< ModelVersion >-- MLModel
                                                       |  (metrics -> ModelPerformanceMetric later)
                                                       +-- Experiment
                                                       +-- PipelineVersion >-- PipelineComponent
                                                       +-- DatasetVersion

Analysis (base, polymorphic)
   |-- DifferentialAnalysis (baseline vs candidate)  --> EvaluationRun x2
   |-- Regression            (later; = materialized differential verdict)
   |-- DriftAnalysis         (reference vs current DatasetVersion; per feature)
   |-- RootCauseAnalysis     (-FK-> DifferentialAnalysis/Regression; evidence)
            |
            +--> PolicyDecision  (decision: allow|block|review; reason_codes)

Artifact  (owned by ModelVersion/DatasetVersion/Run/TrainingRun; checksum unique)
Lineage   (source_type/id -> target_type/id, unique edge)

OutboxEvent   (written in same txn as domain rows -> relay -> Kafka)
ConsumerState (consumer, event_id)  <- idempotent ingestion inbox
```

## F. IMPLEMENTATION ORDER
1. **P0.1** rename `metadata` -> `custom_metadata` everywhere (`models.py`), fix repo references; confirm `import` + `create_all`.
2. **P0.2** lazy `DATABASE_URL` in `postgres.py`.
3. **P1.9** storage tests (import, create_all, repo smoke) - lock in the fix.
4. **P0.3** natural unique keys (EvaluationResult, Lineage) + idempotent repo methods (`ON CONFLICT`).
5. **P1.7** remove risky ORM cascades; **P1.8** JSON->JSONB (+Mutable, GIN).
6. **P1.4** OutboxEvent + relay; **P1.5** ConsumerState/inbox + offset-after-commit.
7. **P1.6** EvaluationMetric + FKs.
8. **P2** timestamps/updated_at, optimistic lock, status CHECKs, missing unique names/indexes.
9. **P2** Regression entity + PolicyDecision wiring; Analysis polymorphic reconciliation.
10. **P2** Artifact re-anchor; tenancy; Alembic migrations.
11. **P2/P3** EvaluationSuite/Baseline/DatasetSplit/HumanReview/ModelPerformanceMetric/EventSchema, then partitioning/retention.

_End of review pass 2. Keep every later update in this file._

