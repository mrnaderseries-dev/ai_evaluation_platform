# analysis_system.md — Second-Order Architecture Review (repository is truth)

Scope: critical verification of the previous review(s) against the actual
repository. Previous reviews are evidence, NOT truth. Every verdict below was
checked against files/classes/tests listed as EVIDENCE.

Test evidence (read-only, nothing modified):
- `python -m pytest tests/monitoring tests/evaluation tests/contract -q` → 223 passed
- `python -m pytest tests/ingestion/test_architecture.py tests/storage/test_models_and_tables.py -q` → 20 passed

Legend: FACT (observed in repo) / INFERENCE (derived) / CONCERN / RECOMMENDATION / UNCERTAINTY.
Per-abstraction verdicts use: KEEP / CHANGE / DEFER / REJECT / UNCERTAIN.

<!-- APPEND -->

## 1. Current Architecture Verdict

DECISION: MOSTLY COHERENT

- FACT: the ingest→persist→evaluate→monitor spine is implemented, typed, and
  tested: `schemas/events/*` (pydantic envelopes, `extra=forbid`), two-stage
  validation (`src/ai_eval_platform/ingestion/event_validator.py`), explicit
  routing (`event_router.py` + `EXPECTED_ROUTES` + startup validator),
  per-route handlers (`handler.py`), explicit offset discipline
  (`infrastructure/kafka/consumer.py`, `ingestion/producer.py`
  `ConfirmedProducer`), evaluation domain+runner+service
  (`evaluation/domain.py`, `runner.py`, `service.py`, `target_builder.py`,
  `result_mapper.py`), agent detectors+signals+orchestration
  (`monitoring/detectors/*`, `monitoring/signals/*`,
  `monitoring/orchestration/runner.py`), system telemetry→metric→health→status
  chain (`monitoring/system/*`).
- INFERENCE: this is **incomplete, not wrong**. The largest "gaps" are empty
  (0-byte) modules, not contradictory designs: `analysis/*`, `root_cause/*`,
  `policy/*`, `observability/*`, `api/*`, `deployment/*`, `security/*`,
  `statistics/*`, `features/*` are all 0 bytes (verified via file-size scan).
  Empty ≠ incoherent.
- What is architecturally strong: event envelope + typed per-event models;
  validate→route→handle pipeline with fail-closed routing; Kafka at-least-once
  discipline (auto-commit refused in `KafkaSettings.__post_init__`, explicit
  `TopicPosition` commits, delivery-confirming producer); repository pattern
  (repos flush, `session_scope` commits); evaluation separation
  (Target/Context/Measurement vs persisted Run/Result); signal scope
  trichotomy with payload/type lock.
- What is architecturally weak: nothing consumes `Signal` durably
  (`monitoring/persistence/__init__.py` has `SignalRecord`/`signal_to_record`
  but no repository/service/API writer — EVIDENCE §6); `OutboxEvent` table
  exists with no writers and no relay (§10); fault isolation fabricates
  `confidence=0.5` (§13); two overlapping drift detectors (§8);
  `SystemHealthEvaluator` weights every component equally (§13).
- What is simply unfinished: Analysis/RCA/Policy execution; signal
  persistence wiring; real system-telemetry sources (collector is in-memory);
  alert lifecycle beyond firing/resolved; baseline/reference infrastructure;
  detector/evaluator per-result versioning.
- What is genuinely wrong (small, fixable, §3): drift-detector duplication;
  fabricated isolation confidence; equal-weight system health; outbox presented
  as a solution with no relay and no DB→Kafka producer needing it yet.

## 2. Architecture We Should Freeze (part A)

### 2.1 Event identity (event_id PK)
- DECISION: KEEP
- Why: replay-safe idempotent ingest depends on it.
- Evidence: FACT — `storage/models.py:154+` `Event.event_id` sole PK;
  `repositories.py:_dialect_insert` + `insert_ignore_duplicates()` use
  `ON CONFLICT (event_id) DO NOTHING` on PG + SQLite.
- Invariant: one event_id ⇒ at most one events row; replays are no-ops.
- Risk if changed: duplicate events, broken DLQ/retry accounting.

### 2.2 Run identity (run_id)
- DECISION: KEEP
- Why: run is the unit of execution AND the evaluation target key.
- Evidence: FACT — `storage/models.py:77+` `Run.run_id` PK;
  `evaluation/domain.py:88-91` `EvaluationTarget.run_id`;
  `models.py:358-391` three partial unique indexes on evaluation_results.
- Invariant: evaluation never invents its own run key; benchmark adds
  (evaluation_run_id, case_id) on top.
- Risk if changed: Run/EvaluationRun confusion; upsert keys break.

### 2.3 Kafka delivery semantics (at-least-once, explicit commits)
- DECISION: KEEP
- Why: strongest, most-documented part of the codebase.
- Evidence: FACT — `KafkaSettings.__post_init__` refuses auto-commit;
  `infrastructure/kafka/consumer.py` requires explicit `TopicPosition`;
  `infrastructure/kafka/producer.py` blocks to delivery report
  (`ConfirmedProducer` in `ingestion/producer.py`); retry attempt in headers
  survives restarts (`kafka_consumer.py:_extract_attempt`).
- Invariant: offsets committed only after durable destination confirmed.
- Risk if changed: silent message loss (premature commit window).

### 2.4 Repository boundaries (repos flush, session_scope commits)
- DECISION: KEEP
- Why: single commit point per transaction; composition works.
- Evidence: FACT — repos take `Session` in ctor, add+flush, never commit;
  `EvaluationService` composes repos via `session_factory`.
- Invariant: no repo commits or opens its own transaction.
- Risk if changed: partial writes, half-finalized runs.

### 2.5 Evaluation domain (Target/Context vs Measurement vs Result)
- DECISION: KEEP
- Why: pure in-memory contract separated from persistence.
- Evidence: FACT — `evaluation/domain.py` frozen dataclasses, no DB imports;
  `evaluator.py` ABC one method; `runner.py` maps errors to ERROR
  Measurements; `result_mapper.py` maps (Measurement, Target) to Result.
- Invariant: evaluators never touch DB; only `EvaluationService` persists.
- Risk if changed: evaluators untestable; online/offline paths diverge.

### 2.6 Signal scope trichotomy (RUN / EVALUATION / WINDOW)
- DECISION: KEEP
- Why: single invariant keeping every detector honest; enforced twice.
- Evidence: FACT — `detectors/inputs.py:32-45` (`exactly_one_scope`) and
  `signals/models.py:51-99` (scope validators) plus type-to-payload lock.
- Invariant: exactly one scope per input and per signal.
- Risk if changed: scope-ambiguous signals; fabricated lineage.


## 3. Architecture That Should Change

### C1. Merge the two drift detectors
- CURRENT DESIGN: `detectors/strategies/drift.py` (multi-feature, WINDOW-only)
  and `detectors/domain/tool_usage.py` (single-tool, WINDOW-only) both emit
  `SignalType.TOOL_USAGE_DRIFT` via shared `drifted_features()`. FACT.
- PROBLEM: two classes, two inputs, one signal type. Callers must guess;
  `monitoring_integration.py:select_run_assignments` defers BOTH for run scope.
- EVIDENCE: `strategies/drift.py:20-31` vs `domain/tool_usage.py:67-71`
  (identical math, different arity); monitoring tests pass either way.
- DECISION: CHANGE
- WHY: one signal type implies one detector; single-tool is the N=1 case.
- PRIORITY: P1. CONFIDENCE: HIGH.

### C2. Fault isolation must stop fabricating confidence
- CURRENT DESIGN: `FaultIsolationAnalyzer.analyze()` assigns confidence=0.5
  to every Fault; cascading/shared_dependency from set overlap. FACT
  (`system/isolation/analyzer.py:59-76`).
- PROBLEM: 0.5 is not a measurement; downstream policy will treat it as
  calibrated. Name overclaims vs behavior (grouping + dependent lookup).
- EVIDENCE: hardcoded literal; `Fault.confidence` bounded [0,1] implying
  calibration (`isolation/models.py:17-20`).
- DECISION: CHANGE (compute real confidence or emit candidates with

### C3. System health aggregation must weight components
- CURRENT DESIGN: `SystemHealthEvaluator.evaluate()` (`status/system.py`):
  any UNHEALTHY implies platform UNHEALTHY; all 8 ComponentTypes equal. FACT.
- PROBLEM: EXTERNAL_PROVIDER degraded pages the same as DATABASE down.
  No criticality concept exists anywhere (searched: absent).
- EVIDENCE: `components/types.py` flat enum; `status/system.py` flat any().
- DECISION: CHANGE (per-component criticality tier; evaluator only).
- WHY: without tiers the platform status is unusable for paging vs ticketing.
- PRIORITY: P1. CONFIDENCE: HIGH.

### C4. Outbox: stop presenting a table as a pattern
- CURRENT DESIGN: `OutboxEvent` + repository exist; zero writers, zero relay,
  zero DB-to-Kafka producer path. Kafka today is ingest-only. FACT.
- PROBLEM: readers conclude DB-to-Kafka is solved. It is not, and no code
  needs it yet. Table without relay is a marker, not a solution.
- EVIDENCE: `storage/models.py:591-609`; only storage+one test reference it.
- DECISION: CHANGE (narrow: document as future scaffold + trigger condition;
  do NOT build relay now — see section 4).
- WHY: prevents false-confidence architecture.
- PRIORITY: P2. CONFIDENCE: HIGH.

### C5. Timeouts: separate deadline-exceeded from cancelled
- CURRENT DESIGN: `HealthStatus.TIMEOUT` exists; component evaluator maps
  TIMEOUT to DEGRADED, FAIL to UNHEALTHY. FACT (`status/component.py`).
- PROBLEM: mapping unjustified; nothing cancels the underlying operation —
  TIMEOUT records an observation, not an interruption. Prior reviews reading
  TIMEOUT as cancellation are wrong against this repo.
- EVIDENCE: `health/liveness.py`, `readiness.py`, `dependency.py` — probes
  return bool; no cancellation tokens or deadline propagation.
- DECISION: CHANGE (semantic: TIMEOUT = probe deadline exceeded, outcome
  unknown; cancellation must be a separate explicit mechanism).
- WHY: conflating detection with interruption causes wrong escalation.
- PRIORITY: P2. CONFIDENCE: HIGH.

### C6. MetricThresholdRule stringly-typed surface
- CURRENT DESIGN: `alerts/rules.py` — severity free string (not AlertSeverity),
  comparison string with runtime ValueError. FACT.
- PROBLEM: invalid rules construct fine, fail at match time; severities
  diverge from Alert model enum.
- EVIDENCE: `alerts/models.py:9-12` AlertSeverity exists but unused by rule.
- DECISION: CHANGE (use enums; no behavior change otherwise).
- WHY: make illegal rules unrepresentable.
- PRIORITY: P2. CONFIDENCE: HIGH.

### C7. Signal persistence write-path gap (minimal binding only)
- CURRENT DESIGN: `monitoring/persistence/__init__.py` defines SignalRecord +
  signal_to_record(); nothing persists it. `monitor_completed_run` returns
  signals in-memory. FACT (search: only that module references them).
- PROBLEM: agent-monitoring story evaporates at process exit; precondition
  for Analysis/RCA/Policy on real data.
- EVIDENCE: no signals table in `storage/models.py`; no repository/service/API
  writer; `System.md:94` corroborates.
- DECISION: CHANGE (minimal: one signals table + repository + single
  persist-after-monitor call site; NO analytics layer).

## 4. Architecture That Should Be Deferred

### D1. Dedicated metrics backend (Prometheus etc.)
- CONCEPT: external TSDB for system metrics.
- WHY IT MAY EVENTUALLY MATTER: retention, dashboards, high-cardinality queries.
- WHY NOT NOW: `MetricCalculator` + in-memory collector satisfy current tests;
  no volume yet; no dashboard consumer exists.
- TRIGGER TO REVISIT: real deployment with retention needs or >1e6 points/day.
- DECISION: DEFER.

### D2. Advanced alerting (lifecycle, dedup, routing, silencing)
- CONCEPT: alert manager with ack/escalation/292 compass.
- WHY IT MAY EVENTUALLY MATTER: alert noise is the predicted top ops pain.
- WHY NOT NOW: `Alert` has only FIRING/RESOLVED; no notification path exists;
  building lifecycle before any alert fires is speculation.
- TRIGGER TO REVISIT: first on-call pain or first notification integration.
- DECISION: DEFER.

### D3. Detector versioning
- CONCEPT: versioned detector builds recorded per signal.
- WHY IT MAY EVENTUALLY MATTER: reproducibility of why a signal fired (§15).
- WHY NOT NOW: detectors are pure threshold functions with policy objects;
  policy params ARE the version today; no signal table to record it in yet.
- TRIGGER TO REVISIT: when C7 signal persistence lands; add detector name +
  policy snapshot then, not a version-registry framework now.
- DECISION: DEFER.

### D4. Evaluator versioning (per-result)
- CONCEPT: which evaluator build produced each result row.
- WHY IT MAY EVENTUALLY MATTER: benchmark comparability over time.
- WHY NOT NOW: FACT — only batch-level `EvaluationRun.evaluator_version`
  (caller-set string) exists; `layer.md:468` confirms no per-result columns.
  Evaluators are code, not registered artifacts, today.
- TRIGGER TO REVISIT: when benchmarks are compared across code changes.
- DECISION: DEFER.

### D5. Outbox relay (DB-to-Kafka publisher)
- CONCEPT: background relay publishing pending outbox rows to Kafka.
- WHY IT MAY EVENTUALLY MATTER: needed the day a DB write must atomically emit.
- WHY NOT NOW: no such write path exists; Kafka is ingest-only; relay with no
  producers is dead code that still needs ops (ordering, partition keys, poison
  handling). Table stays as cheap scaffold per C4.
- TRIGGER TO REVISIT: first real DB→Kafka requirement (specify topic + key +
  ordering then).
- DECISION: DEFER.

### D6. Advanced baseline infrastructure / ML anomaly / sophisticated RCA
- CONCEPT: statistical/ML baselines, learned detectors, causal RCA engine.
- WHY IT MAY EVENTUALLY MATTER: threshold detectors plateau; real RCA needs data.
- WHY NOT NOW: FACT — `ml/*` and `batch/*` are disconnected scripts (no imports
  from src); `analysis/*` and `root_cause/*` are 0-byte; no signal history to
  train on (C7 unbuilt). ML on zero durable data is theater.
- TRIGGER TO REVISIT: durable signals + evaluation history exist; a concrete
  false-positive/negative gap is measured.
- DECISION: DEFER.

## 5. Things We Should Reject

### R1. Generic framework layers (abstract DetectorFactory/Registry/Plugin bus)
- IDEA: plugin registries, detector factories, generic pipeline frameworks.
- WHY IT LOOKS ATTRACTIVE: extensibility theater; "any future detector plugs in".
- WHY IT IS WRONG: FACT — `Detector` ABC (one method) + `run_monitoring` list
  of (detector, input) pairs already composes; `build_route_registry` enforces
  completeness without a framework. No second consumer needs a bus.
- DECISION: REJECT.

### R2. Fake RCA (isolation output treated as root cause)
- IDEA: presenting `FaultImpact` as root-cause findings to Policy/UI.
- WHY IT LOOKS ATTRACTIVE: closes the Signal→RCA→Policy story on paper.
- WHY IT IS WRONG: output carries fabricated 0.5 confidence (C2) and no causal
  evidence; `root_cause/*` is empty, so there is no evidence model to attach.
  Shipping it teaches operators to mistrust the system.
- DECISION: REJECT (until C2 fixed AND evidence model exists).

### R3. Duplicate metric systems (observability/ vs monitoring/system)
- IDEA: building a parallel metrics pipeline under `observability/`.
- WHY IT LOOKS ATTRACTIVE: "observability framework" sounds complete.
- WHY IT IS WRONG: FACT — `observability/*.py` are 0-byte; the real pipeline
  (collector→calculator→health→status) lives in `monitoring/system/*` and is
  tested. Two systems guarantee double-counting and divergent units.
- DECISION: REJECT (observability/ stays app-internal logging/tracing helpers
  IF ever filled; see §14).

### R4. Duplicate analysis systems (Analysis vs RCA as parallel universes)
- IDEA: letting `analysis/*` and `root_cause/*` grow independent signal-reading
  stacks.
- WHY IT LOOKS ATTRACTIVE: team-parallel "progress".
- WHY IT IS WRONG: both are 0-byte; the only specified relationship is
  RCA-as-specialization-of-Analysis on shared evidence. Parallel stacks
  duplicate signal loading, windowing, and lineage.
- DECISION: REJECT (one evidence-loading path; RCA specializes, §12).

### R5. Unnecessary ML (isolation_forest/drift scripts wired into serving path)
- IDEA: promoting `ml/*` scripts to live detectors now.
- WHY IT LOOKS ATTRACTIVE: "intelligent" monitoring.
- WHY IT IS WRONG: FACT — scripts have no imports from src, no persisted
  features, no training/serving split, no evaluation of themselves. Live ML
  with no ground truth and no signal history is untestable.
- DECISION: REJECT (revisit via D6 trigger only).

### R6. Unnecessary event types / premature persistence tables
- IDEA: adding event types or tables "for completeness" (e.g. registry,
  tenant, suite tables with no writers).
- WHY IT LOOKS ATTRACTIVE: schema-first completeness.
- WHY IT IS WRONG: repo already carries unused tables (`OutboxEvent`,

## 6. Monitoring Architecture Decision

- Separation (agent Signals vs platform Health): DECISION KEEP (§2.7).
  FACT — disjoint key spaces (agent_id vs ComponentType), disjoint tests,
  zero cross-imports. INFERENCE: prior reviews proposing a merged
  "unified monitoring event" are rejected against this evidence: the merge
  has no consumer and would erase the scope invariant.
- Can system health become an agent signal? Only via an explicit, reviewed
  correlation record (e.g. WINDOW signal citing degraded-component evidence
  ids), never by direct conversion. No such path exists today — CORRECT.
- Can agent behavior affect system health? Only as load on a component
  (e.g. ingestion lag), observed through that component's telemetry, never
  by writing HealthCheckResults from agent code. No such path exists — CORRECT.
- When is correlation valid? When (a) time windows overlap, (b) both sides
  persisted (needs C7), (c) the correlation rule is versioned and reviewable.
  None hold today ⇒ no correlation layer now (DEFER).
- Where should correlation happen? A future read-side join over persisted
  signals + component health, NOT inside detectors or health checkers.
- Does System Monitoring belong under monitoring/? Yes — KEEP. It shares the
  "observe→decide" shape and ComponentType vocabulary; moving it gains nothing.
- Does observability/ need to own telemetry generation? No — REJECT.
  `observability/*.py` are 0-byte; `monitoring/system/telemetry/collector.py`
  is the working collector. Telemetry generation stays with the component
  being observed; the collector stays a dumb append/snapshot buffer until a
  real backend need (D1) appears.
- Is telemetry a domain concept or an input contract? Input contract: KEEP
  `RawTelemetry` as a forbid-validated boundary DTO (component, name, kind,
  value, timestamp). It must not grow domain methods.

## 7. Signal Architecture Decision

- DECISION on Signal as a whole: KEEP contract; CHANGE persistence (C7).
  Signal is an OBSERVATION (detector output), not an analysis result:
  FACT — detectors are pure threshold/pattern functions emitting Signals;
  no Analysis/RCA/policy content appears in any payload type.
- FIELD agent_id — KEEP. EVIDENCE: every detector input requires it; runner
  enforces agent boundary (`orchestration/runner.py:65-72`). REASON: tenancy
  of observation.
- FIELD signal_type — KEEP. EVIDENCE: closed `SignalType` enum (6 values) +
  payload lock. REASON: prevents stringly-typed consumers.
- FIELD scope — KEEP. EVIDENCE: trichotomy validators (§2.6). REASON: the
  load-bearing invariant of the subsystem.
- FIELD run_id — KEEP. EVIDENCE: RUN-scope requires it, others forbid it.
- FIELD evaluation_run_id — KEEP (narrowed meaning: benchmark-run lineage).
  EVIDENCE: EVALUATION-scope requires it. CONCERN: WINDOW scope currently
  ALLOWS evaluation_run_id as lineage in some paths while Signal validator
  forbids run_id on WINDOW — keep the asymmetry documented, do not widen.
- FIELD window_start/window_end — KEEP. EVIDENCE: WINDOW requires both +
  ordering + tz-awareness (`signals/models.py:44-50` — prior claim of missing
  tz validation is FALSE against current code).
- FIELD detected_at — KEEP. Correction of prior review: tz enforced today.
- FIELD payload — KEEP (typed union + match validator). REASON: per-type
  evidence schema without a generic JSON drawer.
- FIELD payload/type matching — KEEP. EVIDENCE: `validate_payload_matches_type`.

## 8. Detector Architecture Decision

- Detector abstraction (`Detector` ABC, one method) — KEEP. Answers: what
  problem? Uniform (detector, input) composition in `run_monitoring` +
  outcome taxonomy (`DetectorOutcome`) + agent-boundary enforcement.
  EVIDENCE: `detectors/base.py`; `orchestration/runner.py:44-82`.
- Strategy abstraction (anomaly/pattern/drift base split) — CHANGE (fold).
  The strategy/domain split adds no selection logic: domain detectors ARE
  strategies with different inputs. Keep one flat detector package after C1;
  the only justified split is input-type, not "strategy vs domain".
- Adapters (`adapters/records.py`, `run_adapters.py`) — KEEP. Correctly placed
  as the ONLY layer translating storage/event shapes into detector inputs;
  they raise `AdapterUnavailable` instead of fabricating data. EVIDENCE:
  `pattern_input_for_run` filters by (agent_id, run_id); run integration
  defers detectors honestly when data absent (`monitoring_integration.py`).
- Orchestration (`execute_detector`, `run_monitoring`, `MonitoringRun`) —
  KEEP. Correctly minimal: no scheduling, no retries, no persistence — just
  outcomes + boundary checks. Previous "no orchestration" claims are stale.
- Stateless? YES (FACT: detectors hold only policy; no cross-call state).
  KEEP stateless — required for offline replay. Deterministic? YES given
  (input, policy) except `signal_id`/timestamps minted at emit — acceptable;
  versioning the policy snapshot (D3) completes reproducibility later.
- Online and offline? YES by construction: pure functions over records;
  run integration loads context from DB post-completion; same detectors could
  run on live buffers later without code change.
- Anomaly vs pattern vs drift: anomaly (threshold vs expected) vs pattern
  (sequence repetition) are genuinely different — KEEP both. Drift (distribution
  shift) vs anomaly (point deviation): different math, same WINDOW plumbing —
  KEEP the concept, CHANGE the duplication (C1).
- Too much generic machinery? Only the strategy/domain split + duplicate drift
  detector. Rest is concrete. Verdict: CHANGE those two, KEEP the rest,
  REJECT any new generic layer (§5 R1).

## 9. Evaluation Architecture Decision

- Run vs EvaluationRun: genuinely distinct — KEEP both. Invariant: Run =
  one agent execution (input_text, output, latency, error); EvaluationRun =
  one benchmark campaign over (agent_version, dataset_version) with lifecycle
  (pending→completed/failed) and summary counts. EVIDENCE:
  `storage/models.py:77-102` vs `:338-355`; `evaluation/service.py`
  `_finalize_evaluation_run` computes summaries from results.
- Evaluation vs EvaluationResult: distinct — KEEP. Evaluation = act of
  measuring (runner + evaluators); EvaluationResult = persisted row keyed by
  (run/metric[/evaluation_run/case]) under partial unique indexes. The
  online/offline/benchmark trichotomy in `models.py:360-391` is the correct
  encoding — KEEP.
- Evaluator vs engine: KEEP the distinction as-is. `Evaluator` ABC is the
  contract; `engines/` (embedding_similarity, groundedness_judge) are
  0-byte stubs = helpers not yet written, not a rival abstraction. No
  evaluator/engine confusion exists in code; prior-review worry is premature.
- Online vs offline evaluation: SAME evaluators, different triggers —
  KEEP one path. EVIDENCE: `EvaluationService` runs identical
  build→resolve→run→persist for single-run (online, evaluation_run_id=None)
  and benchmark (offline) cases; `result_mapper` handles both.

## 10. Event / Kafka Decision

- event_id — KEEP (sole PK, idempotency key; §2.1).
- run_id — KEEP (partition-adjacent routing key; run-scoped ordering).
- sequence_number — KEEP as producer-supplied ordering hint; storage orders by
  it (`Run.events` order_by). Not a global ordering guarantee — documented.
- correlation_id / causation_id — KEEP optional fields; today's lineage
  carriers. Do not promote to required until a traced flow needs them.
- schema_version — KEEP; validator gates supported versions
  (`event_validator.py`). Pattern `^\d+\.\d+$` enforced.
- Partition key — UNCERTAIN (no explicit key strategy in repo; key passed
  through from record). No change proposed without a throughput case.
- Idempotency — KEEP current: event_id ON CONFLICT DO NOTHING + result upserts
  on natural keys. No inbox table needed while handlers are pure inserts.
- Retries — KEEP: header-carried attempt + exponential backoff + retry topic
  + dispatcher; crash-window tests pass.
- DLQ — KEEP: budget-capped envelopes, confirmed publish before commit,
  uncommitted-on-failure discipline.
- Offset commits — KEEP explicit per-partition `TopicPosition` (§2.3).
- Outbox — the critical correction. Distinguish: (a) Kafka→DB (THIS repo's
  direction: consumer writes envelopes to Postgres; reliability = commit AFTER
  write; NO outbox involved — outbox is irrelevant here and any prior review
  claiming otherwise is wrong); (b) DB→Kafka (no producer path exists; WOULD
  need outbox+relay the day it appears). DECISION: DEFER relay (D5),
  CHANGE presentation (C4). Current table: harmless scaffold, not a solution.

## 11. Storage Architecture Decision

- Models that correspond to real domain concepts — KEEP: Agent, AgentVersion,
  Run, Event (+ Execution/Context/Behavior detail tables), Policy,
  PolicyVersion, AgentPolicy, PolicyDecision, Dataset, DatasetVersion,
  EvaluationCase, EvaluationRun, EvaluationResult, Analysis family,
  Artifact/Lineage. Each has a producer or a committed near-term producer.
- Duplicate sources of truth: NONE found among core tables. Watch item:
  `Run.duration_ms` vs event-derived latency phases (derived, not duplicated —
  builder prefers run value, phases supplementary). `EvaluationResult.metrics`
  JSON mirrors columns (score/passed) for query convenience — acceptable,
  documented in mapper.
- JSON garbage drawers: RISK, not yet violation. `metadata_json`/`findings`/
  `metrics`/`details` are bounded extension points with typed columns for
  queried fields. Rule going forward: any field filtered/sorted/joined on
  becomes a real column + index; JSON stays for opaque payload. No migration
  now.
- Premature tables: `OutboxEvent` (no writers — scaffold per C4/D5), ML
  lifecycle tables (`MLModel/ModelVersion/Pipeline*/Experiment/TrainingRun` —
  no writers outside tests; `ml/*` disconnected). Verdict: KEEP tables
  (cheap, referenced by prior fix docs) but REJECT any new tables without a
  same-change writer+reader (R6). Do NOT drop: concept validity is
  independent of current wiring.
- Missing constraints: mostly fixed by prior work (partial unique indexes on
  results, lineage edge uniqueness, case-requires-run check). Remaining:

## 12. Analysis vs Root Cause vs Policy

- Signal — What does it know? One observed deviation with typed evidence
  (payload), scope, and detection time. Nothing about causes or actions.
- Analysis — What does it know? Aggregates over signals/results windows
  (differentials, drift summaries, slices). Cannot run today (module empty,
  no signal history — needs C7). Analysis CAN exist without RCA: YES
  (summaries without causes are the normal case).
- Root Cause — What additional evidence? Candidate causes + supporting event
  ids + affected slices + calibrated confidence + conclusion. None exists
  today. RCA can exist without Analysis? Only as direct signal→hypothesis
  with evidence attached; preferred shape is RCA-as-specialization-of-Analysis
  sharing one evidence-loading path (REJECT parallel stacks, R4).
- Policy — What does it decide? allow/block/review + rule id + reason, bound
  to a policy version (`PolicyDecision` model). May depend on Signal directly
  (fast path: block on ERROR_PATTERN) AND on RCA (slow path: act on confirmed
  cause) — both edges are legitimate; the model supports either (nullable
  event_id, required policy_version_id). Current DB relationships truthful.
- Redesign? NO. Freeze the chain shape; forbid fabricated RCA (R2); build
  order: C7 signals → Analysis reads → RCA specializes → Policy consumes.

## 13. System Monitoring Decision

- Telemetry (RawTelemetry + collector) — KEEP. Minimal honest buffer
  (append/snapshot); forbid-validated DTO. EVIDENCE: `telemetry/models.py`,
  `collector.py`. No backend claims — correct.
- SystemMetric + MetricDimension + calculator — KEEP. Rate/percentile math
  correct and tested; dimensions (availability/performance/scalability) are
  coarse but sufficient. No new dimensions without a consumer.
- HealthCheck / Liveness / Readiness / Dependency — KEEP. Clean separation;
  `HealthChecker` composes probes. CHANGE semantics per C5 (timeout meaning).
- ComponentHealth + evaluators — KEEP with C3 (criticality weighting).
  Current equal-weight aggregation is the documented flaw, not the model.
- SystemStatus (PlatformHealth) — KEEP shape; values healthy/degraded/
  unhealthy/unknown sufficient. No finer states without a consumer.
- Timeout concept — CHANGE per C5: deadline-exceeded observation only.
- Criticality — MISSING, must be added (C3). Not all components equal.

## 14. Observability Boundary

- Proposed chain Application/Infrastructure → Observability → Telemetry →
  System Monitoring: PARTLY CORRECT, must not become a framework.
- FACT: `observability/*.py` empty; real flow is component code → collector
  (`monitoring/system/telemetry/`) → calculator → health/status. There is no
  observability layer doing anything today.
- DECISION: NOW — components emit `RawTelemetry` via the existing collector
  contract; NOTHING ELSE. LATER — (a) real telemetry SOURCES per component
  (ingestion lag, consumer offsets, DB pool, evaluator durations) when
  operators ask; (b) logging/tracing helpers in observability/ IF two modules
  need them. DEFER — any observability framework, OTel wiring, dedicated
  backend (D1). REJECT — moving the working collector under an empty package
  for "cleanliness" (R3).

## 15. Reproducibility

- Question: can the platform eventually explain why an evaluation, signal,
  analysis, or policy decision happened? TODAY: partially for evaluation,
  no for signals/decisions.
- What exists (FACT): AgentVersion (code_hash, config, prompt_version, model);
  DatasetVersion (content_hash, case_count); EvaluationRun.evaluator_version
  (batch string); Event lineage (event_id, run_id, sequence, correlation/
  causation, trace_id on Run); result_mapper preserves predicted_answer +
  token usage + measurement metadata.
- Genuinely missing: (1) per-result evaluator identity (D4); (2) detector
  name + policy snapshot per signal — record WITH C7, not before (D3);
  (3) signal→analysis→RCA→policy foreign-key chain — build when Analysis
  executes (§12 order); (4) baseline/reference pointers (which window/hash
  was "expected") — needed before drift/anomaly claims are auditable.
- Do NOT add: ModelVersion-per-signal, prompt/config copies per row,
  PolicyVersion-per-signal (PolicyDecision already binds version), Detector
  registry tables. Version fields only where a reader asks "which build" and
  the answer changes outcomes: evaluator-per-result and detector-policy per
  signal qualify; the rest is fashion.

## 16. Scalability Pressure Points (all DEFER, correctly small now)

- PostgreSQL event volume: CURRENT single-row inserts + ON CONFLICT; tests
  green. PRESSURE: sustained >1k eps. WHEN: production traffic. DECISION: DEFER
  (bulk insert + partitioning then).
- High-cardinality metrics: CURRENT in-memory lists. PRESSURE: per-run label
  explosion. WHEN: D1 backend adopted. DECISION: DEFER.
- Retention: CURRENT none defined. PRESSURE: unbounded events/signals growth.
  WHEN: first capacity review. DECISION: DEFER (define tiers then, not now).
- Detector cost: CURRENT O(records) pure functions. PRESSURE: large windows.
  WHEN: measured p99 breach. DECISION: DEFER.
- Large event windows: CURRENT `list_for_run` default limit 1000 (noted in
  layer.md:490 — truncation risk for huge runs). PRESSURE: long agent traces.
  WHEN: first truncated evaluation. DECISION: DEFER (paginate/stream then).
- Alert volume: CURRENT firing-only, no dedup. PRESSURE: threshold flapping.
  WHEN: first noise complaint. DECISION: DEFER (D2).
- JSON growth: CURRENT bounded extension columns. PRESSURE: querying inside

## 17. What We Are Building Too Early

- Outbox relay ambition without producers (§10/C4/D5) — scaffold kept,
  relay correctly absent; prior reviews demanding it now are wrong.
- ML lifecycle tables + scripts wired to nothing (`ml/*`, batch jobs,
  TrainingRun/Experiment tables) — persistence ahead of any training loop.
- Alert lifecycle beyond firing (only FIRING/RESOLVED used; no notifier).
- Strategy-vs-domain detector split + duplicate drift detector (C1) —
  generic machinery with one consumer each.
- Fabricated RCA surface (0.5 confidence) implying production readiness (C2).
- Any new tables/columns without a same-change writer (R6) — the repo
  already carries several; stop adding.

## 18. What We Are Building Too Little

- NOW (blocking continued work): C7 signal persistence (table + repo +
  single call site); C2 isolation honesty (blocks Policy trust); C3 health
  criticality (blocks paging use); baseline/reference pointers (§15.4 —
  drift/anomaly unauditable without them).
- LATER (after NOW): Analysis execution over persisted signals; RCA evidence
  model + specialization; Policy engine reading signals/RCA; real telemetry
  sources per component; per-result evaluator version (D4); detector policy
  snapshots per signal (D3); retention + partitioning when measured.

## 19. Architectural Doubts

- Q: Should WINDOW signals carry evaluation_run_id as lineage?
  WHY UNCERTAIN: Signal validator forbids run_id on WINDOW but lineage
  allowance for evaluation_run_id is inconsistently documented across prior
  reviews. EVIDENCE: `signals/models.py:83-97` (no evaluation_run_id check
  on WINDOW). POSSIBLE: forbid both (pure windows) vs allow eval lineage.
  RESOLVE: first Analysis consumer decides.
- Q: Kafka partition key strategy (run_id vs agent_id vs none)?
  WHY UNCERTAIN: no key policy in repo; ordering needs unknown at current
  scale. RESOLVE: measure throughput + ordering complaints at scale-out.
- Q: Does `Run.duration_ms` or event-derived latency win on conflict?
  WHY UNCERTAIN: builder prefers run value; phases supplementary; no tie-break
  spec. EVIDENCE: `target_builder.py`. RESOLVE: first conflicting case.
- Q: Alert dedup/flap window semantics?
  WHY UNCERTAIN: no lifecycle exists; any choice now is invention.
  RESOLVE: first noise incident (D2 trigger).
- Q: Exact RCA evidence schema?
  WHY UNCERTAIN: `root_cause/*` empty; premature schema risks R4/R2.
  RESOLVE: after C7 + first manual RCA walkthrough.
- Q: Multi-tenancy boundary (agent_id enough?)?
  WHY UNCERTAIN: agent_id scoping works for one operator; no auth/tenant
  model (`security/*` empty). RESOLVE: second tenant request.

## 20. Architectural Predictions

- HIGH CONFIDENCE: (1) Alert noise arrives before any other scaling pain —
  firing-only + equal-weight health + no dedup is the exact recipe; (2) first
  "why did this signal fire" audit will stall on missing baseline pointers +
  policy snapshots (§15); (3) `list_for_run` 1000-limit truncates the first
  very long trace (layer.md:490).

## 21. Decision Matrix

| # | Decision | Verdict | Priority / Trigger |
|---|----------|---------|--------------------|
| §2 | Event/Run identity, Kafka semantics, repo + session boundaries, evaluation domain, signal scope, agent/system split | KEEP (freeze) | — |
| C1 | Merge duplicate drift detectors | CHANGE | P1 |
| C2 | Isolation confidence honesty | CHANGE | P0 |
| C3 | Component criticality weighting | CHANGE | P1 |
| C4 | Outbox presentation fix (no relay) | CHANGE | P2 |
| C5 | Timeout = deadline-exceeded, not cancelled | CHANGE | P2 |
| C6 | Rule typing (enums) | CHANGE | P2 |
| C7 | Signal persistence binding | CHANGE | P0 |
| D1–D7 | Metrics backend, alert lifecycle, versioning, relay, ML/RCA, distribution, tenancy | DEFER | triggers in §4 |
| R1–R6 | Framework layers, fake RCA, duplicate systems, live ML, speculative schema | REJECT | — |
| §15.4 | Baseline/reference pointers | CHANGE (with C7) | P0-adjacent |

## 22. Final Architecture Position

1. What should we freeze? Event/Run identity, at-least-once Kafka discipline,
   repo-flush/session-commit boundaries, evaluation Target-vs-Result split,
   signal scope trichotomy, agent/system monitoring separation (§2).
2. What should we change before continuing? C7 signal persistence + C2
   isolation honesty (P0); C1 drift merge + C3 criticality (P1); C4/C5/C6
   semantic fixes (P2).
3. What should we defer? Metrics backend, alert lifecycle, detector/evaluator
   versioning, outbox relay, ML/RCA sophistication, distribution, tenancy (§4).
4. What should we reject? Generic frameworks, fake RCA, duplicate metric/
   analysis systems, live ML without data, speculative schema (§5).
5. What is the biggest architectural risk? Building Policy/RCA/Analysis on
   undurable, unauditable signals — i.e. proceeding past C7/C2 without fixing
   them, producing confident-looking decisions with fabricated lineage.
6. What is the strongest architectural decision? The Kafka ingest discipline
   (explicit commits + confirmed publish + header-carried retries) composed
   with event_id idempotency — a correct at-least-once spine with tests.
7. What is the next architectural question we should answer? What exact
   lineage (detector policy snapshot + baseline pointer + event ids) must a
   persisted Signal carry so the first Analysis/RCA consumer is auditable —
   because that answer sizes C7 and unblocks §§12/15/19.

- MEDIUM CONFIDENCE: (4) Event table becomes the first DB pressure point
  (insert-per-event, no partitioning); (5) schema evolution pain at
  `payload` unions when the 7th signal type is added (no Signal version
  field); (6) system/agent confusion incidents if correlation is built
  without the §6 rules (someone will chart agent errors against deploy
  health and page the wrong owner).
- SPECULATIVE: (7) Detector execution cost forces batching (only if windows
  grow 100x); (8) lineage-table growth from artifact-heavy flows; (9) Kafka
  rebalance/ordering surprises at multi-partition scale.

  JSON. WHEN: first JSON predicate in a hot query. DECISION: DEFER (promote
  to columns then).
- Kafka partitioning: CURRENT default; explicit offsets safe. PRESSURE:
  ordering + throughput per key. WHEN: multi-partition scale-out. DECISION:
  DEFER (key strategy = run_id then; UNCERTAIN today).
- Historical baseline computation: CURRENT none persisted. PRESSURE:
  recompute cost per detection. WHEN: baselines exist. DECISION: DEFER.

- Fault isolation — CHANGE per C2: today candidate-fault grouping with
  fabricated confidence; must not be called RCA. Honest name + real or null
  confidence.
- Alerts (Alert + evaluator + rules) — KEEP firing logic; CHANGE rules typing
  (C6); DEFER lifecycle (D2). Alert != Status: TRUE and correctly separated
  (Status = current state; Alert = threshold-crossing event with severity).
  Lifecycle beyond firing/resolved NOT justified today.

  status CHECK constraints are application-level strings (status columns lack
  DB CHECKs) — accept; enums live in code, DB stays permissive. No action.
- Missing relationships: `DriftAnalysis.analysis` fixed per fix_storage.md.
  `EvaluationRun` missing relationship to `AgentVersion` object (FK only) —
  cosmetic; DEFER.
- False relationships: NONE found. `PolicyDecision.event_id` nullable FK
  (decisions may precede/escape events) is honest, not false.
- Persistence ahead of domain semantics: Analysis/RCA tables exist while
  `analysis/*` + `root_cause/*` are empty. Verdict: KEEP (concepts valid,
  shapes reasonable) but REJECT filling them with fabricated confidence (R2).

- Benchmark evaluation vs monitoring: distinct — KEEP separated. Benchmark
  asserts quality per release; monitoring watches behavior over time. The
  EVALUATION signal scope references benchmark runs as lineage; it does not
  merge the subsystems. Any proposal to unify them is REJECTED (different
  cadences, owners, math — same argument as §6).
- EvaluationCase/Criteria/Target/Context/Measurement: all distinct, all
  justified — KEEP. Case = dataset row; Criteria = expectations;
  Target = (run, context, criteria) bundle; Context = observed facts;
  Measurement = one metric verdict. Each has exactly one producer and one
  consumer in `service.py`/`target_builder.py`/`result_mapper.py`.
- DatasetVersion: KEEP. Benchmark comparability key; service enforces
  case-version membership (`service.py:267-276`). Missing: per-result
  evaluator version (DEFER D4, not a redesign).

- Missing lineage (detector name/policy snapshot, event ids beyond two
  payloads, correlation_id, dataset/agent/model versions): DEFER as a bundle
  WITH C7 (record them when the table is created), not as speculative fields
  now. UNCERTAINTY: exact lineage set awaits first Analysis consumer (§19).
- NOT ambiguous: Signal = observation. Analysis results go in analyses tables,
  never back into Signal.

  several ML tables) with zero writers; each unowned table is migration debt
  and review noise. Prior-review proposals for suite/tenant/audit tables fail
  the "who writes it this quarter" test.
- DECISION: REJECT (tables only with a committed writer + reader in the
  same change).


### D7. Distributed monitoring orchestration / multi-tenancy / capacity planning
- CONCEPT: sharded detector execution, tenant isolation, SLO platform.
- WHY IT MAY EVENTUALLY MATTER: scale and multi-user operation.
- WHY NOT NOW: single-process `run_monitoring` loop is correct and tested;
  no tenants, no SLO consumers, no load numbers.
- TRIGGER TO REVISIT: measured detector cost or a second tenant.
- DECISION: DEFER.

- WHY: without durable signals there is no lineage (§15) and no Analysis input.
- PRIORITY: P0. CONFIDENCE: HIGH.

  confidence=None + rename).
- WHY: honesty boundary blocking any Policy use of isolation output.
- PRIORITY: P0. CONFIDENCE: HIGH.

### 2.7 Agent/system monitoring separation
- DECISION: KEEP (boundary frozen; wiring rules in section 6)
- Why: different owners, cadences, math; conflating them gives fake causes.
- Evidence: FACT — `monitoring/signals/*` + `detectors/*` keyed by agent_id;
  `monitoring/system/*` keyed by ComponentType; no imports between subtrees.
- Invariant: system health NEVER becomes an agent Signal without an explicit
  reviewed correlation step; agent behavior NEVER sets component health.
- Risk if changed: alert storms blamed on agents and vice versa.


- CONCERN: previous reviews partly describe stale code (e.g. claims that
  `Signal.detected_at`/windows lack tz validation — FALSE today:
  `monitoring/signals/models.py:44-50` enforces tz-awareness; claims of
  "no orchestration" — FALSE: `orchestration/runner.py` exists with
  `DetectorOutcome`, agent-boundary check, `MonitoringRun`).
- RECOMMENDATION: freeze the spine (§2), fix the small-wrong list (§3),
  defer everything else (§4), reject abstraction-for-impressiveness (§5).
