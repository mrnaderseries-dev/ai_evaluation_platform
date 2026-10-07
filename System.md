# AI Evaluation Platform — Architecture Review

**Document purpose.** Critical architecture-review for the
`ai-evaluation-platform` project. Based on the actual repository as of
2026-10-07. Evaluates whether the design and implementation make sense as a
serious AI Evaluation / AI Reliability / MLOps platform. Does **not** presume
the current design is correct. Does **not** recommend implementation. Does
**not** modify any project files except this document.

**Rules applied.**
- Evidence over opinion: every conclusion cites a file, class, function, or test.
- Distinguish FACT, INFERENCE, CONCERN, RECOMMENDATION, UNCERTAIN.
- Do not praise effort. Evaluate architecture, not size.
- Distinguish present correctness from future scalability.
- Do not manufacture certainty: where the repository is silent, say so.

---

## 1. Executive Summary

**Verdict: Architecturally inconsistent — strong scaffold, incomplete skeleton,
and foundational domain concepts are not yet separated.**

The repository is **not a working platform** in any complete sense. It is a
**scaffold with a coherent shard of domain logic**. The storage layer, the Kafka
transport with explicit-offset discipline, the event envelope schemas, the
evaluation domain (target → measurement → result), the repository implementations,
and a substantial test suite are real and largely correct. But the largest share
of the package's conceptual surface — the domain modules the document's own
architecture requires (analysis, root_cause, policy engine, evaluation engines/
evaluators composition, monitoring system modules, observability, features,
statistics) — is **empty zero-byte placeholders**.

This is the single most important fact: the system is **over-designed at the
boundaries** (schemas, transport, persistence contracts, orchestration markers)
and **under-designed at the substance** (analysis, root_cause, policy decision
logic, evaluation engine machinery, observability instrumentation, statistical
computation).

### 2.1 What the repository actually is

The real dependency flow is not the clean intended pipeline described in the
prompt. The actual structure is:

```
schemas/events/          -> typed event envelope contracts (Python pydantic)
infrastructure/kafka/    -> ConfirmedProducer / RecordSource / consumer / config
ingestion/               -> consumption, delivery classification, DLQ, retry dispatch
                                 (depends ONLY on the Kafka protocol layer)
storage/                 -> SQLAlchemy models (PostgreSQL) + repositories
evaluation/              -> domain (target/measurement/criteria/errors) + service
                                 + result mapper + runner + target builder
monitoring/              -> schemas/makers only:
                                 - signals (models, types)
                                 - detectors (base, inputs, domain strategies)
                                 - orchestration (runner)
                                 - persistence (signal_to_record conversion)
                                 - system/ (telemetry, metrics, health, status,
                                   alerts, isolation, components registry)
                                 - baselines (marker only)
                                 NO analysis, NO root_cause, NO policy engine,
                                   NO features, NO statistics implementation.
analysis/                -> ZERO-byte placeholders (empty .py files)
root_cause/              -> ZERO-byte placeholders (empty .py files)
policy/                  -> ZERO-byte placeholders (empty .py files)
features/                -> ZERO-byte placeholders (empty .py files)
statistics/              -> ZERO-byte placeholders (empty .py files)
observability/           -> ZERO-byte placeholders (empty .py files)
api/                     -> ZERO-byte placeholders (empty .py files)
deployment/              -> ZERO-byte placeholders (empty .py files)
security/                -> ZERO-byte placeholders (empty .py files)
```


### 2.2 Where the implementation differs from the intended architecture

The prompt's intended architecture is:

```
AI Systems -> Events -> Kafka -> Ingestion -> Storage -> Evaluation/Monitoring
                                  -> Signals -> Analysis -> Root Cause -> Policy
```

That is **more than half unimplemented**. Specifically:

- The **event → ingestion** chain is implemented and test-covered.
- The **ingestion → storage** chain is implemented (repositories persist events,
  runs, evaluations).
- The **evaluation** domain is implemented (clean, bounded, test-covered).
- The **monitoring** signals/detectors/orchestration/persistence are implemented
  only as **schemas and markers**: the orchestration runner can execute
  detectors and build `Signal` objects, but there is no published path that
  consumes a `Signal` and writes it to the database (the `monitoring/persistence`
  module exposes `SignalRecord` and `signal_to_record` but no service/repository/
  API persists them).
- The **analysis** and **root_cause** domains are empty by file size, so the
  "Analysis -> Root Cause -> Policy" chain exists in name and in the data model
  (there are `Analysis`, `DifferentialAnalysis`, `DriftAnalysis`,
  `RootCauseAnalysis` tables and a `PolicyDecision` table), but nothing
  populates them.
- The **observed/metrics** layer (`monitoring/system/*`) is a set of pydantic
  schemas and two pure computations (`MetricCalculator`), with no storage
  repository, no API, and no consumer.
- **Observability** (logging, metrics, tracing) is entirely empty — yet the
  platform has no telemetry instrumentation at all.

### 2.3 Dependency flow (actual)

```
configs/ -> infrastructure/kafka/config.py -> ingestion/ -> storage/
                                                           -> evaluation/
                                                           -> monitoring/ (markers)
tests/   -> exercises ingestion + storage + evaluation repository behaviour
```

There is no import from `analysis`, `root_cause`, `policy`, `features`,
`statistics`, or `observability` in any implemented module. They are dead
slices of the package graph; the package can be imported, but the conceptual
cores are empty.

---

## 3. Architecture Invariants

These are the invariants the system relies on. For each I state whether it
exists, where it is enforced, whether it can be violated, and the risk.


### 3.1 Event identity: `event_id` uniqueness and `(run_id, sequence_number)` uniqueness

- **Exists?** Yes, in the DB (primary key `event_id`, unique constraint
  `uq_event_run_sequence`), and in the pydantic `AIEvent` model (min_length=1).
- **Enforced by:** DB unique constraint + repository `insert_ignore_duplicates`
  (tests `test_sequence_collision_is_rejected` pass).
- **Can it be violated?** Only via SQL injection / direct DB manipulation, and
  only if the repository's ignore-duplicates path is bypassed. The application
  layer cannot reproduce a collision for the same run+sequence.
- **Danger:** none currently, but the constraint does **not** cover
  `correlation_id` → duplicate workflow correlation across runs. `correlation_id`
  is an index but has no uniqueness constraint. This is an intentional design
  choice, not an invariant.

### 3.2 Run identity: `run_id` as the primary identity of an execution

- **Exists?** Yes: `Run.run_id` primary key, `Event.run_id` FK with `ON DELETE
  CASCADE`, and `Run.external_run_id` index.
- **Enforced by:** DB + repository.
- **Can it be violated?** No, by application code. `external_run_id` can collide
  with another run's id (no unique constraint), which matters only if the
  platform ever imports a stranger's run id and conflates it.

### 3.3 Event ordering: sequence_number ordering within a run

- **Exists?** Yes: `Event.sequence_number` int, unique per run, and the
  relationship is ordered (`order_by Event.sequence_number`).
- **Enforced by:** DB unique constraint + repository insertion.
- **Can it be violated?** No, in-application. But `sequence_number` is only
  meaningful per `run_id`. Cross-run ordering is not represented anywhere, which
  is correct for a run-scoped model but means a "workflow" spanning runs has no
  global order. `correlation_id` + `step_id` provide a partial ordering in the
  payloads, but they are **not enforced** as a global invariant.
- **Danger:** the database does **not** enforce that `sequence_number` values
  are strictly contiguous 1..N per run. They are unique, but gaps are allowed.
  If the platform later relies on `sequence_number` being contiguous for
  processing, it must compute it, as the repository does.

### 3.4 Idempotent ingestion / duplicate handling

- **Exists?** Yes, in the `EventRepository.insert_from_envelope` and
  `insert_ignore_duplicates` paths. Tests cover that re-delivery of the same
  envelope is a no-op.
- **Enforced by:** DB unique constraint on `event_id`.
- **Can it be violated?** Yes, in principle, if a record with a **different**
  `event_id` but identical intent arrives twice. The current design assumes
  `event_id` is the idempotency key. If the pipeline produces duplicate events
  with different event ids (e.g., retry with a new id), deduplication does not
  happen. This is a real risk.

### 3.5 Transaction boundaries: DB transaction vs Kafka offset commit

- **Exists?** The DB uses a `session_scope` transactional unit of work (commit on
  success, rollback on exception). Kafka uses `enable_auto_commit=False` and
  explicit offset commitment by the application.
- **Enforced by:** the storage module's `session_scope` context manager + the
  Kafka consumer's explicit commit discipline (documented in
  `infrastructure/kafka/consumer.py`).
- **Can it be violated?** The code attempts to ensure the offset is committed
  only after the record is durably written. But the repository layer has no
  **single** transactional unit that spans both the DB write and the Kafka
  publish. The outbox pattern is present as `OutboxEvent` and `OutboxEventRepository`
  (a marker), but **no relay exists** to publish pending outbox records, and no
  test exercises the assembly. So "transactionally consistent DB-to-Kafka" is
  **documented, not implemented**.
- **Danger:** the most dangerous "documentation-only" invariant in the whole
  repository. The Kafka consumer code has methods named
  `_dead_letter_*`, `_forward`, and retry dispatch that are substantive. But
  whether the **main-topic commit** happens atomically with the DB write is not
  enforced by any transaction protocol present in the repo. This is a critical
  reliability gap if the platform ever runs on real Kafka without an outbox
  relay.


### 3.6 Retry/DLQ semantics

- **Exists?** Yes, in `ingestion/retry_*.py` (retry dispatch, retry policy,
  retry publisher) and `ingestion/dlq.py`. These are substantive and tested.
- **Enforced by:** per-run keyed retry-topic publishing, attempt counting,
  backpressure via `due_at`.
- **Can it be violated?** The retry envelope recomputes a new `event_id` per
  retry envelope (the retry record is a separate envelope published to the retry
  topic). If the retry mechanism is re-implemented, the idempotency assumption
  must be preserved: a redelivered retry must not create a **second** original
  event. The current design uses "original key per run" to preserve ordering but
  does not say whether the retry record's idempotent key is the run id or the
  original event id. That is underspecified.

### 3.7 Event schema versioning

- **Exists?** Yes: `AIEvent.schema_version` is a pydantic pattern
  `^\d+\.\d+$` (e.g., `1.0`), and `Event.schema_version` is a DB string(32).
- **Enforced by:** pydantic validator + DB column.
- **Can it be violated?** No. But there is no **version migration** mechanism:
  no schema registry, no version-resolution strategy, no documented handling of
  a mixed-version broker topic. `schema_version` is carried but never used in
  any routing or validation logic.

### 3.8 Signal scope semantics (RUN / EVALUATION / WINDOW)

- **Exists?** Yes, in pydantic `Signal` and `ScopedDetectorInput`, with
  validators enforcing exactly-one-scope, and cross-field contradictions.
- **Enforced by:** pydantic field validators and model_validator.
- **Can it be violated?** Only if the model is constructed correctly; the
  validators enforce it. But the **validators are on the pydantic model, not on
  the persisted representation**. There is no DB-level enforcement of signal
  scope invariants, and no repository-level validation. If signals are later
  stored as rows, the scope invariants must be re-validated in the layer that
  writes them.

### 3.9 Health status semantics (HEALTHY / DEGRADED / UNHEALTHY / UNKNOWN)

- **Exists?** Yes, in `monitoring/system/status/models.py`.
- **Enforced by:** `SystemHealthEvaluator.evaluate()`, which is a pure
  deterministic function of `ComponentHealth` inputs.
- **Can it be violated?** No, the evaluator is deterministic and pure. But the
  **inputs** are not sourced from anywhere in the repo: there is no
  `ComponentHealth` persistence, no health-check computation against real Kafka
  / database metrics, and no schedule. So the invariant "status is calculated
  reliably from live telemetry" is **not enforced anywhere in the code**. It is
  a pure function that is never called.
- **Danger:** the health model is "correct but unplanted".

### 3.10 Policy decision scope (policy_version_id + event_id or run_id)

- **Exists?** Yes, in `PolicyDecision` DB model: `policy_version_id` + `event_id`
  **OR** `policy_version_id` + `run_id` (mutually exclusive).
- **Enforced by:** DB columns (no unique constraint across the combination other
  than the existence of the policy version).
- **Can it be violated?** No at the DB level. But the pydantic models for
  policy events (`PolicyCheckedEvent`, `PolicyAllowedEvent`, etc.) are in
  `schemas/events/policy_events.py` and are solid. The **decision** that a policy
  actually blocked/allowed is implemented only in `policy_events.py` schemas, not
  in the `policy/` domain module (which is empty). So "policy enforcement"
  exists only as event schemas and DB rows, not as a policy engine. That is a
  fundamental gap: there is no code that makes a decision and acts on it.

---

## 4. Domain Boundary Review

Per the prompt, I evaluate whether the boundaries between domains are correct.

### 4.1 schemas vs ingestion vs storage

- **FACT:** `schemas/events/*` are pure pydantic envelopes. `ingestion/` consumes
  them and stores them via `storage/`. The boundary is clean: schemas do not
  depend on ingestion or storage; ingestion transforms envelopes into DB rows.
- **INFERENCE:** This is the largest correct part of the project. The boundary is
  principled and testable.

### 4.2 ingestion vs storage

- **FACT:** `ingestion` is **not** coupled to `storage`. It depends only on the
  Kafka protocol layer and on schemas. Storage is injected via the repository
  pattern in the service layer, not imported by ingestion.
- **INFERENCE:** This is correct and forward-compatible (you could swap the
  persistence backend).



### 4.3 evaluation vs storage vs monitoring

- **FACT:** `evaluation/` is self-contained: domain, runner, evaluator,
  result_mapper, target_builder, service. It uses `storage/models.py` and
  `storage/repositories.py` but does not know about monitoring, analysis, or
  policy.
- **CONCERNS:** `evaluation/target_builder.py` reads `Event` and `Run` rows to
  build an `EvaluationTarget`. That is a reasonable bolt-on, but it means
  evaluation depends on the storage model shape of events, which couples the
  evaluation domain to the event schema. If the event schema evolves, the
  evaluation builder must change. A cleaner boundary would be an evaluation
  "case" object materialised from events, not a live read of storage rows.

### 4.4 monitoring vs analysis vs root_cause vs policy

- **CONCERNS (largest):** These four domains are conceptually one chain but are
  split into **separate, incomplete slices**. The `monitoring/` package holds
  signals, detectors, orchestration, signal persistence conversion, and system
  monitoring. The `analysis/`, `root_cause/`, `policy/`, `features/`,
  `statistics/` packages are empty. So the dependencies are **asymmetric**:
  monitoring can produce signals, but there is no code that turns signals into
  analysis, analysis into root cause, or root cause into policy decisions.
- **INFERENCE:** The domain boundary is *conceptually* correct (signals →
  analysis → RCA → policy) but *implementation-wise* it is a single implemented
  link with four empty bodies. This is the core architectural problem.

### 4.5 observability vs monitoring vs system monitoring

- **FACT:** `observability/` is empty. `monitoring/` has a `system/` subpackage
  that models telemetry, metrics, health, status, alerts, and fault isolation.
- **CONCERNS:** The names imply an overlap that is not resolved. "Observability"
  (logging, metrics, tracing) is empty, while "monitoring" contains its own
  system models. There is no clear boundary:
  - Is `monitoring/system` **domain** or **infrastructure**? It is currently a
    schema/pure-computation layer with no consumers.
  - Does `observability` report on the platform, or is it the platform's own
    monitoring? The two are conflated.
- **INFERENCE:** The project has not yet decided whether it is a **platform that
  monitors agents** (monitoring) or a **platform with its own observability
  layer** (observability). It has both, but only one is built.

### 4.6 system monitoring vs agent monitoring

- **This is a critical boundary that the repo does NOT currently separate.**
- **Expected:** System monitoring handles `ComponentType` (api, ingestion,
  kafka, database, evaluation, monitoring, batch_pipeline, external_provider),
  telemetry, metrics, health, status, alerts, fault isolation. Agent monitoring
  handles `agent_id`, detectors, signals, analysis, root cause, policy.
- **Reality:** The repo mixes them. `monitoring/system/` is about components and
  components are **not** agents, but `monitoring/signals/`, `monitoring/detectors/`
  and `monitoring/orchestration/` are **agent-centric** (they carry `agent_id`).
  A single `agent_id` appears in both the system-monitoring-related detector
  inputs and the system component registry. There is no gateway that decides
  "this work belongs in system monitoring" vs "this work belongs in agent
  monitoring".
- **Valid or invalid?** As a matter of MLOps, the distinction is **valid and
  operationally necessary**: system health is about the platform (Kafka,
  database, api), while agent signals are about the model's behaviour. Mixing
  them produces alert noise: a Kafka partition lag would look like an agent
  failure unless there is a boundary. The repo has the **silos** to support both
  (component types + agent ids in signals), but no **unification layer** and no
  policy that routes a signal to the correct domain.
- **Recommendation:** define a routing boundary explicitly (a `SignalRoute`
  concept, or an implicit rule: signals with `component` distinct from any
  `agent_id` go to system monitoring; signals with `agent_id` go to agent
  monitoring) and enforce it at the persistence boundary.

---

## 5. Monitoring / System Deep Review

### 5.1 Telemetry, metrics, health, status, fault isolation — boundary audit

- **Telemetry (`monitoring/system/telemetry/models.py`):**
  - **FACT:** `RawTelemetry` is a pydantic schema: `component`, `name`, `kind`
    (counter/gauge/observation/event), `value`, `unit`, `timestamp`,
    `metadata`. It is a correct **input contract**, not a domain entity.
  - **INFERENCE:** It is correctly placed as an input contract (a schema) rather
    than a domain model. It should remain a transport/input schema.
  - **Concern:** `TelemetryKind.EVENT` is a valid enum value but there is no
    `RawTelemetry` → `Event` conversion anywhere. If "event" kind telemetry is
    meant to materialize into storage `Event` rows, that path is missing.

- **Metrics (`monitoring/system/metrics/models.py` + `calculator.py`):**
  - **FACT:** `SystemMetric` is a pydantic schema; `MetricCalculator` computes
    `counter_rate` and `percentile` from `RawTelemetry` iterables. This is a
    **pure computation** on in-memory telemetry.
  - **INFERENCE:** This is correct as a pure function. It is **not** a
    persistence abstraction, and it is **not** a streaming metrics pipeline.
  - **Concern:** There is no representation of a **time window**, no aggregation
    over multiple telemetry points with windowing, no query interface. `counter_rate`
    and `percentile` are stateless functions on a list, which is fine for
    unit-testing but not a production metrics pipeline. The `metadata` field is
    present but unused in computation.
  - **Action deemed premature:** a full metrics-time-series backend (Prometheus /
    Atlas) is not warranted now; the schema + pure calculator are sufficient for
    the scaffold. But the **window** concept is missing and must be added before
    metrics can be queried.

- **Health / status (`monitoring/system/health/` + `status/`):**
  - **FACT:** `HealthCheckResult` (pass/fail/timeout/unknown, check_type
    liveness/readiness/dependency, `timeout_ms` field), `ComponentHealth`,
    `PlatformHealth` (HEALTHY/DEGRADED/UNHEALTHY/UNKNOWN).
  - **CONCERNS:**
    1. **Timeout is a field, not an enforcement.** `HealthCheckResult.timeout_ms`
       is a **configuration value**, never read by a checker. The checker
       implementations (`liveness.py`, `readiness.py`, `dependency.py`) are
       presumably in the file I have not seen, but there is **no code in the repo
       that implements "timeout is exceeded → return TIMEOUT status"**. The
       `timeout_ms` field exists, but nothing enforces it. This is a
       documentation-only or placeholder concern.
    2. **Component status is not computed from live data.** `SystemHealthEvaluator`
       is pure, but there is no source of `ComponentHealth` at runtime.
    3. **Criticality is not modelled.** `ComponentType` has no criticality or
       weight. A failure in `EXTERNAL_PROVIDER` should not necessarily mark the
       `MONITORING` component unhealthy, but the current `PlatformHealth`
       evaluator has no such dependency-weighting.
  - **Recommendation:** introduce a `HealthCheck` protocol with a `check()` that
     returns `HealthCheckResult`, wire it to an actual telemetry source (e.g., a
     `HealthCheckConsumer` that polls Kafka offset lags, DB connection pools,
     and API /health endpoints), and compute `timeout` semantics explicitly
     (elapsed > timeout_ms → TIMEOUT).

