# Evaluation Layer — Run/Events → EvaluationResult Integration (layer.md)

This document records the architectural reasoning for closing the connection
gap between the existing Evaluation domain components and the existing
storage layer, and for wiring online, offline and benchmark evaluation on top
of that connection.

## 1. Current architecture (what already existed before this change)

Inspected by reading implementations and tests, not filenames.

**Evaluation domain (all present, single definitions under
`src/ai_eval_platform/evaluation/`):**

- `domain.py` — `EvaluationStatus` (SUCCESS/UNAVAILABLE/ERROR),
  `EvaluationContext`, `EvaluationCriteria`, `EvaluationTarget`,
  `EvaluationMeasurement` (frozen: `metric_name`, `status`, `value`, `score`,
  `passed`, `feedback`, `error_type`, `metadata`).
- `target_builder.py` — `EvaluationTargetBuilder.build(run, events, criteria)`
  translates persisted `Run` + `Event` rows into an `EvaluationTarget`;
  `criteria_from_case(EvaluationCase)` derives `EvaluationCriteria` from a
  benchmark case.
- `target_validator.py` — `EvaluationTargetValidator.validate(target)` raises
  `InvalidEvaluationTargetError` for a missing/structurally invalid target.
- `evaluator.py` — `Evaluator` ABC: `evaluate(target) -> EvaluationMeasurement`,
  one metric per evaluator.
- `runner.py` — `EvaluationRunner.run(evaluator, target)`: validates the target
  (re-raises `InvalidEvaluationTargetError`), executes the evaluator, and
  normalizes `EvaluationConfigurationError` / `EvaluationInfrastructureError` /
  any other exception into an ERROR `EvaluationMeasurement`.
- `evaluators/` — 7 evaluators (`latency`, `exact_match`,
  `semantic_similarity`, `groundedness`, `retrieval`, `citation`,
  `tool_usage`), all with `UNAVAILABLE` short-circuits for missing data.
- `errors.py` — the five-category error taxonomy.

**Storage (all present):**

- `EvaluationResult` with three **partial unique indexes**:
  `uq_evaluation_result_online (run_id, metric_name)` where
  `evaluation_run_id IS NULL AND case_id IS NULL`,
  `uq_evaluation_result_offline (evaluation_run_id, run_id, metric_name)` where
  `evaluation_run_id IS NOT NULL AND case_id IS NULL`,
  `uq_evaluation_result_benchmark (evaluation_run_id, case_id, metric_name)`
  where `case_id IS NOT NULL`, plus check constraint
  `ck_evaluation_result_case_requires_run`.
- `EvaluationRun` with lifecycle columns: `status` (default `pending`),
  `evaluator_version`, `started_at`, `completed_at`, `total_cases`,
  `passed_cases`, `failed_cases`, `metrics`.
- `Dataset → DatasetVersion → EvaluationCase`, `EvaluationRun.dataset_version_id`,
  `Run.agent_version_id` lineage foreign keys.
- `EvaluationResultRepository.upsert/upsert_many` — dialect-specific
  `INSERT ... ON CONFLICT ... DO UPDATE` with arbiter predicates matching the
  three partial indexes (database-enforced idempotency, never
  check-then-insert). `EvaluationRunRepository`, `EvaluationCaseRepository`,
  `RunRepository`, `EventRepository.list_for_run`.
- `session_scope()` in `storage/postgres.py` — the only commit point;
  repositories flush only.

**What did not exist (verified by repository-wide search):** no module in
`src/` ever invoked builder → runner → result repository together; no
measurement-to-result conversion; no evaluator registry, factory, or
metric-resolution mechanism; nothing ever wrote `EvaluationRun.started_at`,
status transitions, or the summary counters; `api/` is empty.

## 2. Problem discovered

The Evaluation layer was a disconnected in-memory library. `Run + Events →
EvaluationTarget → Evaluator → EvaluationMeasurement` worked and was tested,
but the chain dead-ended: there was no conversion from
`EvaluationMeasurement` to `EvaluationResult`, no orchestrator that loads a
Run and its Events, builds the target, invokes the runner, and persists the
outcome, no mechanism for selecting which evaluators execute, and no
`EvaluationRun` lifecycle integration. The storage idempotency machinery
(the three partial unique indexes + `upsert_many`) had zero production
callers, and the `EvaluationRun` summary columns could never leave
`0`/`pending`/`NULL`.

## 3. Why the gap matters

- Nothing could actually be evaluated end to end: metrics computed by
  evaluators died in memory, so online, offline, and benchmark evaluation
  were all unexecutable despite the schema being explicitly designed for
  the three modes.
- The three distinct uniqueness identities (online/offline/benchmark) were
  unused, so any ad-hoc writer written later would have had no reason to
  respect them — inviting exactly the overwriting bugs the schema prevents
  (one metric clobbering another; `run_id` used alone as offline identity).
- `EvaluationRun` could not answer "what did this batch evaluate, when did
  it start and finish, how many cases passed or failed", making batch
  evaluation unauditable.
- Without an explicit orchestration point, the error categories
  (invalid target / unavailable metric / evaluator failure / persistence
  failure / failed criterion) had no single place where they are kept
  distinct on the way to storage.

## 4. Decision

Implemented the smallest application-level abstraction that connects the
existing components, plus the missing conversion:

1. `evaluation/result_mapper.py` — pure function
   `measurement_to_result(measurement, target, *, evaluation_run_id, case_id)`
   converting one `EvaluationMeasurement` (plus the `EvaluationTarget` fields
   the schema stores) into one `EvaluationResult` row object.
2. `evaluation/service.py` — `EvaluationService`, the application
   orchestrator: resolves evaluators by metric name from an injected
   collection, loads Run + Events (+ EvaluationRun/EvaluationCase when
   scoped), builds the target, runs `EvaluationRunner`, maps measurements,
   and persists via `EvaluationResultRepository.upsert_many`. It also owns
   `EvaluationRun` lifecycle: `start_evaluation_run`,
   `complete_evaluation_run`, `fail_evaluation_run`.
3. `storage/repositories.py` — two small persistence additions following the
   existing `update_status` precedent: `EvaluationRunRepository.start`
   (status + `started_at`, preserving an already-recorded start time) and
   `EvaluationRunRepository.finalize` (status, `completed_at`, counters,
   `metrics`), plus `EvaluationCaseRepository.get_required` mirroring every
   other repository's `get_required`.

No schema change, no new registry, no new evaluator, no metric-name branches,
no Kafka, no API wiring.

### Measurement → result field mapping

`measurement_to_result` maps every field the current schema supports; no
column was invented:

| Source | `EvaluationResult` column |
|---|---|
| `measurement.metric_name` | `metric_name` |
| `measurement.status` | `metrics["status"]` (the schema has no status column; `status` and `value` are authoritative keys over merged metadata) |
| `measurement.value` | `metrics["value"]` (the schema has no value column; `latency_ms` is additionally set from the target context) |
| `measurement.score` | `score` |
| `measurement.passed` | `passed` |
| `measurement.feedback` | `evaluator_feedback` |
| `measurement.error_type` | `failure_category` |
| `measurement.metadata` | merged into `metrics` |
| `target.run_id` | `run_id` |
| `target.context.output` | `predicted_answer` |
| `target.context.latency_ms` | `latency_ms` |
| `target.context.token_usage` | `token_usage` (non-`NULL` fields only) |
| caller-supplied identity | `evaluation_run_id`, `case_id` |
| generated | `evaluation_result_id` (`new_id()`, because the repository's core `INSERT` reads the attribute before any ORM flush applies the column default) |

Per-result evaluator identity/version has **no column** in the current
schema; that gap is recorded in section 17 rather than silently altering the
schema.

## 5. Why this layer owns the responsibility

- **Not in `EvaluationTargetBuilder`**: the builder's job is translation from
  persistence rows to evaluation domain data; it must not know about
  `EvaluationResult`, evaluators, or transactions.
- **Not in `TargetValidator`**: it validates a single `EvaluationTarget`
  structurally; storage-lineage checks (case ↔ evaluation-run dataset) are
  about the *request*, which the validator's input type does not model.
- **Not in `Evaluator`/individual evaluators**: evaluators measure exactly
  one metric and must stay free of SQL, SQLAlchemy, and persistence.
- **Not in `EvaluationRunner`**: it normalizes evaluator execution failures
  for one invocation; it must not load rows or write results.
- **Not in the repository**: repositories own persistence and idempotency
  mechanics (flush-only, `ON CONFLICT`); they must not decide which
  evaluators run, build targets, or compute lifecycle summaries from
  business rules.
- **Not in the storage model**: models declare columns and constraints only.
- The *coordination* of these components — mode identity (which of the three
  uniqueness identities applies), evaluator selection, transaction scoping,
  lifecycle transitions, and summary computation — is application-layer
  work, so it lives in `EvaluationService` inside the evaluation package,
  matching how `ingestion/` hosts the ingestion application layer
  (`KafkaEventConsumer`, handlers) next to its domain code.

## 6. Data flow

```
Run + Events (PostgreSQL)
    -> RunRepository.get_required / EventRepository.list_for_run
    -> EvaluationTargetBuilder.build(run, events, criteria)
    -> EvaluationTargetValidator (invoked inside EvaluationRunner.run)
    -> EvaluationRunner.run(evaluator, target)
    -> Evaluator.evaluate(target)
    -> EvaluationMeasurement
    -> measurement_to_result(measurement, target, evaluation_run_id, case_id)
    -> EvaluationResultRepository.upsert_many (ON CONFLICT per mode)
    -> PostgreSQL (single commit in session_scope)
```

For benchmark, before the builder: `EvaluationRunRepository.get_required` +
`EvaluationCaseRepository.get_required` verify identity/lineage and supply
`EvaluationTargetBuilder.criteria_from_case(case)` when the caller passes no
explicit criteria. `EvaluationRun` lifecycle is a separate path:
`start_evaluation_run` → (zero or more `evaluate_run` calls, each its own
transaction pair) → `complete_evaluation_run` / `fail_evaluation_run`.

## 7. Online semantics

- Entry point: `evaluate_run(run_id, metrics=...)` with `evaluation_run_id`
  and `case_id` both `NULL`.
- The result row is identified by `(run_id, metric_name)` under the partial
  index `uq_evaluation_result_online`. The same Run can receive any number
  of different metrics; each metric is its own row and cannot overwrite
  another metric.
- Re-evaluating the same metric for the same Run updates that row in place
  (original `evaluation_result_id` preserved); it never duplicates.
- No `EvaluationRun` is created, referenced, or required. Online evaluation
  never flows through benchmark/offline machinery.

## 8. Offline semantics

- Entry point: `evaluate_run(run_id, evaluation_run_id=...)` with `case_id`
  `NULL`. The `EvaluationRun` (created beforehand by the caller through
  `EvaluationRunRepository.create`, typically with
  `dataset_version_id = NULL`) scopes the batch.
- The result row is identified by `(evaluation_run_id, run_id, metric_name)`
  under `uq_evaluation_result_offline` — **not** by `run_id` alone. The same
  Run evaluated in two different EvaluationRuns produces two independent
  rows; unrelated results are never overwritten.
- Criteria (e.g. expected output) are supplied explicitly by the caller per
  call; offline evaluation has no case to derive them from.

## 9. Benchmark semantics

- Entry point: `evaluate_run(run_id, evaluation_run_id=..., case_id=...)`.
  All three identifiers are required together: the check constraint
  `ck_evaluation_result_case_requires_run` is pre-validated by the service
  (case without evaluation run → `InvalidEvaluationTargetError`) and remains
  the database backstop.
- Lineage `DatasetVersion → EvaluationCase → Run`:
  - `EvaluationRun.dataset_version_id` must equal
    `EvaluationCase.dataset_version_id` whenever the EvaluationRun declares a
    dataset version; a case from a different dataset version is rejected
    before any evaluation happens.
  - The `Run ↔ EvaluationCase` association is **not** invented: the schema
    defines no such relationship, so the caller supplies the pair and the
    persisted association is exactly the `(evaluation_run_id, case_id,
    run_id)` triple on the result row.
  - When `EvaluationRun.dataset_version_id IS NULL` the lineage check cannot
    run; this is documented as a gap, not silently "fixed".
- Criteria default to `EvaluationTargetBuilder.criteria_from_case(case)`
  (expected answer from the case); an explicit `criteria` argument overrides
  it.
- The result row is identified by `(evaluation_run_id, case_id, metric_name)`
  under `uq_evaluation_result_benchmark`; `run_id` is an updatable column, so
  re-running a case inside the same EvaluationRun refreshes which Run
  produced the current answer instead of adding a second row.

## 10. Idempotency

Uniqueness identity is decided by the presence of `evaluation_run_id` /
`case_id`, exactly as the schema already encodes, and enforced **in the
database**, never by a SELECT-before-INSERT:

| Mode | Identity | Mechanism |
|---|---|---|
| Online | `(run_id, metric_name)` where both `evaluation_run_id IS NULL AND case_id IS NULL` | `uq_evaluation_result_online` + `ON CONFLICT ... DO UPDATE` |
| Offline | `(evaluation_run_id, run_id, metric_name)` where `case_id IS NULL` | `uq_evaluation_result_offline` + `ON CONFLICT ... DO UPDATE` |
| Benchmark | `(evaluation_run_id, case_id, metric_name)` where `case_id IS NOT NULL` | `uq_evaluation_result_benchmark` + `ON CONFLICT ... DO UPDATE` |

`EvaluationService` always persists through
`EvaluationResultRepository.upsert_many`, which groups rows by the identity
of each mode and builds the matching `ON CONFLICT` arbiter (index elements +
partial-index predicate). On conflict the row is updated in place
(`UPDATABLE_COLUMNS` excludes the primary key, so `evaluation_result_id` is
stable). Duplicate execution of the same evaluation therefore converges to
one row per identity; different metrics, different evaluation runs, and
online-vs-offline-vs-benchmark rows never collide. The existing guarantee is
used as-is; nothing was weakened and no application-level existence check
was introduced.

## 11. Transaction boundary

- `session_scope()` remains the only place a transaction commits. The
  service never calls `session.commit()`; it depends on an injected
  `session_factory` (production wiring: `session_scope`), the same pattern
  `KafkaEventConsumer` uses (`session_factory` + handler receiving the
  session). Repositories continue to flush only.
- `evaluate_run` performs **two scopes**:
  1. read scope — load Run/Events/(EvaluationRun/Case) and build the
     `EvaluationTarget`; exits immediately (read-only commit);
  2. evaluator execution — **outside any transaction**;
  3. write scope — map measurements → results and `upsert_many`; commits
     atomically, so all metrics of one `evaluate_run` land together or not
     at all.
- Rationale for splitting: this project's documented ordering principle is
  that external side effects happen after the transaction exits (`dlq.md`:
  "publish to DLQ after the transaction has exited, so a failed publish
  cannot leave an open transaction holding locks"). Evaluators can call
  external engines (embedding similarity, LLM judges), so holding a DB
  transaction open across evaluator execution would violate that principle.
  Reads are non-mattering; the atomic unit that matters — persisting the
  measurement set — is one transaction.
- `start_evaluation_run`, `complete_evaluation_run`, `fail_evaluation_run`
  each open exactly one scope; the lifecycle transition, counters, and
  metrics summary commit together with the rows they describe.
- A failure in the write scope propagates out of `session_scope`, which
  rolls back — no partial persistence, no hidden commit (behaviorally
  tested with a non-committing factory: the service leaves nothing durable
  if the boundary does not commit).

## 12. Error semantics

The five categories stay distinct end to end; nothing is collapsed into a
single exception:

| Category | Where it surfaces | Persisted effect |
|---|---|---|
| Invalid `EvaluationTarget` (missing/blank run id, missing context; case without evaluation run; case from another dataset version) | `InvalidEvaluationTargetError` raised from the service (pre-checks) or from `EvaluationRunner` (target validation) | Nothing is persisted; no write scope is opened |
| Missing data for a specific metric (no expected output, no latency, no retrieved context) | Evaluator returns `UNAVAILABLE` measurement; the runner does not intervene | Row persisted with `metrics.status = "unavailable"`, `passed = NULL`, `score = NULL`, `failure_category = NULL` |
| Evaluator configuration failure at execution time | Runner converts `EvaluationConfigurationError` to an `ERROR` measurement | Row with `metrics.status = "error"`, `failure_category = "EvaluationConfigurationError"` |
| Evaluator configuration failure at selection time (unknown metric, no evaluators, empty metric list, duplicate metric registration) | `EvaluationConfigurationError` raised by the service **before any scope opens** | Nothing is persisted |
| Evaluator execution failure (exception inside `evaluate`) | Runner converts to an `ERROR` measurement (`error_type` = exception class) | Row with `metrics.status = "error"`, `failure_category` = error type, `passed = NULL` |
| Infrastructure/persistence failure (missing Run/Case/EvaluationRun → `EntityNotFoundError`; SQL/serialization failure → SQLAlchemy `StatementError`) | Propagates untouched from the read or write scope | Write scope rolls back; no rows from the failed call |
| Actual metric criterion failure | Evaluator returns `SUCCESS` with `passed=False` | Row with `metrics.status = "success"`, `passed = FALSE`; counts as a failed *case* in the summary, never as an evaluator or run failure |

`passed=False` is never treated as evaluator failure: batch completion with
failed criteria still yields `status = "completed"`, and `fail_evaluation_run`
is reserved for evaluation-process failure (caller-decided), keeping
case-level failure distinct from process failure. Conversely `UNAVAILABLE`
is never stored as an infrastructure `ERROR` and never counts as a failed
case.

## 13. Reproducibility

What identifies "what produced this metric" with the current schema:

- **Run** (`run_id`) → `agent_id`, `agent_version_id`, input, final answer,
  timestamps: identifies the agent version under test.
- **EvaluationRun** (`evaluation_run_id`) → `agent_version_id`,
  `dataset_version_id` (benchmark), `evaluator_version` (batch-level string,
  set by whoever creates the run), lifecycle timestamps, and the final
  `metrics` summary.
- **EvaluationCase** (`case_id`) → `dataset_version_id`, `external_case_id`,
  question and `expected_answer` (the ground truth the criteria derive from).
- **metric_name** + the persisted `metrics` payload (`status`, `value`,
  measurement metadata) + `score`/`passed`/`evaluator_feedback`.
- **Evaluation criteria**: for benchmark they are reproducible from the
  persisted case (`expected_answer`). For online/offline the caller-supplied
  `EvaluationCriteria` is *not* persisted anywhere (gap, section 17).

`EvaluationRun`, `EvaluationCase`, and `Run` together therefore pin dataset
version, agent version, case truth, and metric identity for benchmark and
offline evaluation; online results pin agent version through the Run.

## 14. Alternatives rejected

- **Putting persistence inside `Evaluator`** — rejected: destroys the
  one-metric in-memory contract, puts SQL in evaluators, and makes every
  evaluator own idempotency.
- **Putting orchestration inside `EvaluationRunner`** — rejected: the runner
  is a per-invocation executor/normalizer; loading Runs/Events, mode
  identity, and lifecycle are not its responsibility.
- **Forcing online evaluation through `EvaluationRun`** — rejected: the
  schema deliberately makes `evaluation_run_id` nullable with a dedicated
  partial index for online rows; online is `Run → Result` only.
- **Check-then-insert idempotency** (`SELECT` existing row, then `INSERT`) —
  rejected: TOCTOU race; the repository's `ON CONFLICT` path already exists
  and is tested on SQLite and PostgreSQL.
- **Metric-specific branches in the orchestrator**
  (`if metric == "latency": ...`) — rejected: no such pattern exists
  anywhere in the codebase; the service works only with the `Evaluator`
  abstraction and an injected metric → evaluator mapping.
- **Creating a second evaluator registry / global default registry** —
  rejected: no registry existed; dependency injection of an evaluator
  collection at construction is the project's existing DI style and avoids
  global mutable state.
- **Holding one transaction across read + evaluate + write** — rejected:
  would keep a transaction open during potentially external evaluator calls,
  contradicting the project's documented ordering of transactions vs external
  side effects (section 11).
- **Moving lifecycle counter updates into the service by mutating ORM
  entities directly** — rejected: all writes in this project go through a
  repository (`update_status` precedent); `start`/`finalize` keep flush
  mechanics in `EvaluationRunRepository` while the summary *rule* stays in
  the service.
- **A `evaluate_batch` workflow engine / auto-creating `EvaluationRun`
  rows inside `evaluate_run`** — rejected: batch composition stays an
  explicit caller loop (`start → evaluate_run* → complete`); creation stays
  with `EvaluationRunRepository.create`, keeping the service minimal.
- **Adding `status`/`value`/evaluator-version columns to
  `evaluation_results`** — rejected: the audit forbids schema changes
  unless proven necessary; `metrics` JSON carries `status`/`value` without
  weakening anything.

### EvaluationRun summary rule

Lifecycle states used: `pending` (model default) → `running`
(`start_evaluation_run`) → `completed` or `failed`
(`complete_evaluation_run` / `fail_evaluation_run`). The service applies
these transitions explicitly because the repository defines no state machine;
it does not invent ordering guards beyond `start` never overwriting an
already-recorded `started_at`.

Counters are computed at finalization from the persisted results of that
`EvaluationRun` only (not incrementally):

- Entities are grouped by `(case_id, run_id)` — in benchmark mode one entity
  per case, in offline mode one entity per Run.
- `total_cases` = number of entities.
- `failed_cases` = entities with at least one result `passed = FALSE`.
- `passed_cases` = entities with at least one `passed = TRUE` and no
  `passed = FALSE`.
- Entities whose results are all `passed IS NULL` (unavailable/error
  metrics) count toward `total_cases` only — they are neither passed nor
  failed, preserving the distinction between criterion failure and
  evaluation failure.
- `metrics` = `{"result_count", "status_counts": {"success",
  "unavailable", "error"}, "metric_summaries": {metric: {"count",
  "passed", "failed", "mean_score"}}}`.

One failed case never fails the batch: `complete_evaluation_run` marks the
run `completed` regardless of `failed_cases`; `fail_evaluation_run` marks
process failure and records the same summary over whatever partial results
were persisted (partial results are kept, never deleted).

## 15. Files changed

| File | Change |
|---|---|
| `src/ai_eval_platform/evaluation/result_mapper.py` | **New** — `measurement_to_result` conversion (the missing measurement → persistence mapping). |
| `src/ai_eval_platform/evaluation/service.py` | **New** — `EvaluationService` orchestration, evaluator resolution, mode identity, lifecycle methods, summary rule. |
| `src/ai_eval_platform/storage/repositories.py` | Added `EvaluationRunRepository.start`, `EvaluationRunRepository.finalize`, `EvaluationCaseRepository.get_required`. No existing method changed. |
| `tests/evaluation/conftest.py` | **New** — SQLite engine, `session_scope`-equivalent factory, seeded online/offline/benchmark graph. |
| `tests/evaluation/test_result_mapper.py` | **New** — 5 mapping tests. |
| `tests/evaluation/test_evaluation_service.py` | **New** — 30 orchestration tests (modes, idempotency, errors, lifecycle, transaction boundary). |
| `layer.md` | **New** — this document. |

No other file was modified. Existing tests, models, indexes, evaluators,
runner, builder, validator, Kafka/ingestion code, and documentation files
(`evaluation.md`, `evaluation_layer.md`, `storage.md`, ...) are untouched.

## 16. Tests

Added (35) and executed:

- `tests/evaluation/test_result_mapper.py` — 5 tests: full field mapping,
  online identity NULLs, `error_type → failure_category`, UNAVAILABLE
  mapping, metadata merge.
- `tests/evaluation/test_evaluation_service.py` — 30 tests covering:
  online evaluation of one Run; multiple metrics on the same Run; duplicate
  online persistence converging in place; missing Run; offline evaluation
  scoped to an EvaluationRun; the same Run in two EvaluationRuns; benchmark
  evaluation with `EvaluationCase`; DatasetVersion ↔ Case ↔ Run lineage;
  case from a foreign dataset rejected; case without evaluation run
  rejected; missing case/evaluation run; unknown metric, no evaluators,
  empty metric selection, duplicate metric registration; missing metric data
  → UNAVAILABLE; `passed=False` ≠ evaluator failure; evaluator exception →
  ERROR persisted; invalid target aborts before persistence; persistence
  failure rolls back; `start`/`completed_at`/counters/summary lifecycle;
  unavailable-only batch; partial case failures still complete; failed run
  keeps partial results; summary scoped to its own evaluation run; missing
  evaluation run on completion; no commit outside the session boundary.
- Existing suites re-run unmodified.

Results:

- `python -m pytest tests/evaluation -q` → **107 passed**.
- `python -m pytest -q` (complete project suite) → **349 passed, 15 skipped,
  0 failed** (baseline before this change: 314 passed, 15 skipped; the skips
  are pre-existing Kafka/PostgreSQL-environment skips).
- `python -m compileall` over the changed packages → clean.
- Comment scan (`#`, `TODO`, `FIXME`) over all new and changed files → zero
  hits; no comments were added anywhere.

## 17. Remaining gaps

Honest list of what is still missing or deliberately unfinished:

1. **No per-result evaluator identity/version.** `evaluation_results` has no
   evaluator columns; only `evaluation_runs.evaluator_version` (batch-level,
   caller-set) exists. "Which evaluator build produced this row" is not
   persisted. Requires a schema change, which this work was not permitted to
   make.
2. **No Run ↔ EvaluationCase mapping mechanism.** The schema defines none,
   so benchmark callers must pass `(run_id, case_id)` pairs; there is no
   stored record of how a Run was produced for a case beyond the result row
   itself.
3. **Online/offline criteria are not persisted.** Caller-supplied
   `EvaluationCriteria` (expected output for offline, thresholds for online)
   exist only during the call, so re-deriving exactly what criteria a
   non-benchmark result used is not possible from the database.
4. **No state machine for `EvaluationRun`.** Transitions are applied
   explicitly but ordering (e.g. completing an already-failed run) is not
   guarded; no repository/domain rule defines legal transitions.
5. **No trigger or entry point.** `api/` is empty and nothing schedules
   evaluation (no job, no Kafka consumer calling `evaluate_run`, no
   `evaluation_completed` event → result ingestion). The service exists but
   must be wired by a future application entry point.
6. **`EvaluationRun` creation is still the caller's job** and
   `evaluator_version` is not populated by any code path.
7. **`EventRepository.list_for_run` default limit is 1000 events** — runs
   with more events are evaluated on a truncated (first-1000, ordered by
   sequence) history.
8. **Lineage check skipped when `EvaluationRun.dataset_version_id IS NULL`
   with a `case_id`** — permitted by the schema, unverifiable by the
   service.
9. **Engines remain stubs** (`embedding_similarity`, `groundedness_judge`
   reference undefined internals) — pre-existing, out of scope; external
   engine behavior is untested against real backends.
10. **No PostgreSQL execution of the new service tests** — the new
    orchestration tests run on SQLite; PostgreSQL coverage comes only from
    the pre-existing repository integration tests (skipped without
    `DATABASE_URL`).





