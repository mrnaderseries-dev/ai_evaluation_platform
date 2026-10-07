# Monitoring Next Step — Engineering Log (monitoring_2)

## 0. Starting state
READING COMPLETED (source-verified 2026-10-07): monitoring.md §§1–30 + Repair Log R1–R5/R9 + O1–O3; analysis_layer_monototring.md §§1–5 (R1–R8, A1–A6, ratings, overall 2.5/5).
Source authority check: `monitoring/{__init__,signals/*,detectors/base,inputs,strategies/*,domain/*}` match the reports (scoped inputs, tz validators, 6 detectors on Detector contract, pure helpers). `tests/monitoring` 4 files/30 tests + `tests/contract` 66 tests: 96 passed just now. No drift between reports and code except the known R9 follow-up, already applied.
Scope of this task: adapters (platform data → monitoring inputs), orchestration decision, failure semantics, lineage review, EVALUATION honesty, baseline boundary, persistence-readiness assessment. No Analysis/RCA/Policy build. No cross-project redesign.

## D1. Adapter source inventory

Discovery: platform data needed by adapters exists in three shapes with different lineage completeness.
Evidence: `schemas/events/common.py:45-75 AIEvent` (event_id/run_id/event_type/timestamp tz-aware, correlation/causation/step optional); `execution_events.py` (ToolCalled/Completed with call_id/tool_name/latency_ms; ModelOutput with latency_ms; Error with error_type/message); `evaluation_events.py:8-29` (evaluation_run_id/case/metric/value/passed/threshold/duration_ms/dataset_version); `agent_behavior_events.py` (Abstention.confidence optional; BehaviorPatternDetected.confidence/evidence_event_ids); `storage/models.py:77-137 Run` (run_id/agent_id/agent_version_id/trace_id/started/completed/duration_ms/metadata) and `338-410 EvaluationRun/Result` (evaluation_run_id/agent_id/dataset_version_id/metric/score/passed/latency_ms/run_id nullable).
Impact: RUN adapters can join event.run_id → Run.agent_id truthfully; EVALUATION adapters can use EvaluationCompletedEvent/Result rows; confidence has NO first-class event source (only optional abstention/behavior confidence) — confidence adapter must refuse to invent values.
Decision: build adapters over explicit caller-supplied records (events + run/agent lookup results passed in), never DB/Kafka inside monitoring. Adapter outputs: Pattern→event_sequences from tool-call ordering; Anomaly→latency from ModelOutput/ToolCompleted/duration; Confidence→only from supplied measurements, else explicit unavailable.



## D2. Orchestration decision (no registry)

Discovery: six detectors share `Detector.detect` but take six incompatible inputs; no caller existed.
Evidence: `detectors/base.py:20-28`, `orchestration/runner.py`.
Impact: every future caller would re-implement enabled-checks, failure mapping, agent checks.
Decision: `execute_detector`/`run_monitoring` coordinate caller-supplied `(detector, input)` pairs only. No registry: selection needs run/event context that does not exist yet. Rejected: name-keyed registry (would become a giant switch with unknown criteria).
Implementation: `DetectorOutcome` 8-way vocabulary; `DetectorRun`/`MonitoringRun.signals`. Current detectors surface empty as `NO_FINDING`, pydantic problems as `INVALID_INPUT`, unexpected exceptions as `DETECTOR_FAILED` (type name only). Agent violation forced to `DETECTOR_FAILED`. `NO_DATA`/`INSUFFICIENT_SAMPLES`/`CONFIGURATION_ERROR` reserved for detectors that can distinguish them.
Verification: multi-detector + disabled + broken-detector tests pass.

## D3. Lineage review (add nothing)

Discovery: candidates exist elsewhere (`Run.agent_version_id/trace_id`, `Event.correlation_id`, `EvaluationRun.dataset_version_id`) but no truthful in-monitoring source.
Evidence: `storage/models.py:86`, `128-137`, `338-410`; `signals/models.py` unchanged.
Decision: add NO Signal fields. `related_event_ids` already carries evidence where adapters supply it. Revisit when adapters resolve Run/Version rows.

## D4. EVALUATION honesty

Discovery: EVALUATION is contract-supported (inputs/detectors/signals/tests) but no offline pipeline exists.
Decision: `anomaly_input_for_evaluation` is the minimal contract path (EvaluationRecord → input → Signal), tested. Full orchestration deferred — status: integration-ready, not implemented.

## D5. Baselines honesty

Discovery: no baseline store exists (`statistics/*`, `features/*`, `ml/*` empty per audit).
Decision: `baselines/__init__.py` holds only `BaselineReference`, `BaselineLookup`, abstract `BaselineStore`. No backend, no hardcoded values. Rejected: fake seeded store (would lie).

## D6. Persistence readiness (no tables)

Discovery: `Signal` maps cleanly to storage conventions (String PKs, tz DateTime, JSON payload) per `storage/models.py` + `repositories.py:55-79`. But no session/idempotency owner exists in monitoring.
Decision: `persistence/__init__.py` has only `SignalRecord` + pure `signal_to_record`. No ORM/repository/migration. Prerequisites: owner module, dedup key, retention, discriminator column.
Verification: round-trip test asserts type + payload + discriminator.

## Test results

## Final State

Implemented: adapter records + run/evaluation/window adapter functions (never fabricate; `AdapterUnavailable` on missing/mismatch); orchestration runner with 8-way outcome vocabulary and agent-boundary enforcement; baseline interface only; persistence `SignalRecord` + pure converter; 10 new tests.
Not Implemented: registry, baseline backend, ORM/repository/migration, EVALUATION offline pipeline, Analysis/RCA/Policy wiring, System Monitoring.
Deferred: signal lineage fields (no truthful source yet), full EVALUATION orchestration, baseline store backend, persistence tables, alerting.
Remaining Risks: adapters accept caller-built records — a careless caller could still mix agents (mitigated: window adapter checks, runner re-checks signals); `NO_DATA` vs `NO_FINDING` relies on future detectors distinguishing; confidence has no event source so its adapter path stays manual.
Test Results: monitoring 40/40, contract 66/66, evaluation+ingestion 238/238 → 344 passed combined. Postgres/Kafka-gated suites not runnable here — recorded, not hidden.
Architecture Assessment: Detector→Signal core untouched; adapters translate, orchestration coordinates, no layer responsibilities moved; system/agent separation intact.
Recommended Next Step: wire ONE real caller — a Run-completion hook that loads `Run` + run events, builds `ToolCallRecord`/`LatencyRecord` via the adapters, and calls `run_monitoring` — this single path proves the boundary before any EVALUATION/persistence work.
Files Changed: `monitoring/adapters/{__init__,records,run_adapters}.py`, `monitoring/orchestration/{__init__,runner}.py`, `monitoring/baselines/__init__.py`, `monitoring/persistence/__init__.py`, `tests/monitoring/test_integration_e.py`, `tests/monitoring/test_contracts_c.py` (one assertion fix), `monitoring_2.md`.


`tests/monitoring` 40/40 (30 prior + 10 new). `tests/contract` 66/66. Combined 106 passed.


## Verification pass (run-completion integration phase)

READ COMPLETED: monitoring.md (all 30 sections + Repair Log R1–R5/R9 + O1–O3); analysis_layer_monototring.md (§§1–5); monitoring_2.md (whole file). Then re-verified against source before any change. Reports not trusted blindly.

### V1. Inventory verification against source

Discovery: every load-bearing claim in monitoring_2.md D1/D2/D6 matches the code; no behavioral discrepancy found.
Evidence: `monitoring/adapters/records.py` (RunRecord/ToolCallRecord/LatencyRecord/ConfidenceRecord/EvaluationRecord/WindowRecord/AdapterUnavailable); `adapters/run_adapters.py` (`pattern_input_for_run` filters by agent_id+run_id and raises AdapterUnavailable when nothing scoped remains; `anomaly_input_for_run/for_evaluation/for_window`); `orchestration/runner.py` (DetectorOutcome exactly 8 values; execute_detector maps disabled→DISABLED, ValueError/TypeError→INVALID_INPUT, other exceptions→DETECTOR_FAILED with type name only, empty→NO_FINDING, agent-boundary violation→DETECTOR_FAILED at runner.py:65-72); `signals/models.py` (RUN/EVALUATION/WINDOW invariants + tz validators + strict type→payload map); six detectors extend `Detector` — pattern/anomaly/confidence emit RUN scope; route/drift/tool_usage emit WINDOW scope only; `storage/models.py:77-102 Run`, `:163-184 Event`, `:246-265 ExecutionEvent`; `storage/repositories.py:180-234 RunRepository.update_status` (flush-only), `:404-418 EventRepository.list_for_run` (ORDER BY sequence_number ASC, default limit 1000); `schemas/events/common.py:45-75 AIEvent`; `execution_events.py` ToolCalled/ToolCompleted payloads (call_id/tool_name/latency_ms/success); `agent_behavior_events.py:37-51` Abstention/BehaviorPatternDetected `confidence` optional 0..1.
Discrepancy (recorded as instructed): monitoring_2.md D1 cites `storage/models.py:77-137` for `Run` — Run actually spans `:77-102` (`:104+` is Policy). Cosmetic line-range drift only; all field claims are correct. No other difference between the reports and the code.
Decision: monitoring needs no change for this phase; reuse adapters/orchestration/detectors exactly as they are.
Verification: `python -m pytest tests/monitoring tests/contract -q` → 106 passed (40 monitoring + 66 contract) before any change this phase; five-package monitoring import check passes.

### V2. Run-completion boundary discovery

Discovery: there is NO production run-completion call site today. The only completion mechanics that exist are repository-level, and nothing invokes them.
Evidence: repo-wide search for `update_status(` returns only three definitions (`RunRepository` repositories.py:219, `EvaluationRunRepository` :766, `TrainingRunRepository` :1150) — zero callers in `src/` or `tests/`; `api/{main,runs,evaluations,models,health}.py` are all 0 bytes; `EventType` (`schemas/events/common.py:8-42`) has no run-completion event; `ingestion/handler.py` five handlers only `insert_from_envelope` and never touch `Run.status`; `evaluation_layer.md:36` already documents the pre-existing gap "missing run-lifecycle writer". No execution-completion module exists either.
Decision (Run Boundary Selected): the smallest existing legitimate boundary is the point immediately after `RunRepository.update_status(run_id, "completed", ...)` commits. Because no lifecycle writer is wired yet, this phase adds the caller side of that boundary as an application entry modeled 1:1 on `EvaluationService` (`evaluation/service.py:111-186`): `monitor_completed_run(run_id, session_factory=...)` in a new module OUTSIDE `monitoring/`. The entry loads `Run` via `RunRepository.get_required` (missing run → existing `EntityNotFoundError` propagates) and events via `EventRepository.list_for_run` inside ONE read scope, REQUIRES `Run.status == "completed"` (otherwise `RunNotCompletedError`), builds records, closes the scope, then executes detection as pure computation. The lifecycle owner keeps ownership: whoever marks the run completed calls this entry afterwards; Monitoring never writes run state.
Rejected Approach: infer completion inside ingestion handlers — event arrival is not run completion; detecting it there would require a new event type (forbidden) or a fabricated lifecycle inference.
Rejected Approach: put the caller inside `monitoring/` (e.g. `monitoring/run_completion.py` importing storage) — would make the monitoring package depend on SQLAlchemy, breaking prior-phase decision D1 ("never DB/Kafka inside monitoring") and this phase's infrastructure-independence requirement.
Rejected Approach: have `monitor_completed_run` call `RunRepository.update_status` itself — the caller would then own run lifecycle, which is forbidden; the entry only reads.
Rejected Approach: inject a monitoring hook into `RunRepository.update_status` — repositories stay flush-only (storage.md:388 precedent); a repository must not learn about monitoring.
Implementation: `src/ai_eval_platform/monitoring_integration.py` (new, outside the monitoring package).

### V3. Transaction-boundary reasoning

Discovery: `session_scope()` in `storage/postgres.py:91-99` is the only commit point in the project; repositories flush only; `EvaluationService` never calls commit — it opens scopes through an injected `session_factory` (production wiring: `session_scope`), documented in layer.md:271-275.
Decision: the monitoring entry opens exactly one read scope through the same injected `session_factory` pattern, writes nothing (so the scope's commit is a no-op), and runs `run_monitoring` AFTER the scope closes — monitoring therefore holds no session, owns no transaction, and performs no commits inside adapters or detectors. Because the entry only observes committed state in its own scope, it structurally runs after the lifecycle transaction that marked the run completed has committed at `session_scope()`. Adapters and detectors remain pure functions with zero I/O.
Verification: the module contains no `commit`, no engine construction, no session creation — only a caller-supplied `session`/`session_factory` (asserted by the new test file's non-committing factory usage).

### V4. Detector truthfulness matrix (what a completed Run can actually feed)

Discovery: only two of the six detectors can be fed truthfully from one completed Run plus its events; the rest lack a real source or are scope-incompatible.
Evidence: tool events carry `tool_name/call_id/latency_ms/success` (`schemas/events/execution_events.py:52-82`); Abstention/BehaviorPatternDetected carry optional `confidence` (`agent_behavior_events.py:39,49`); no baseline store exists (`monitoring/baselines/__init__.py` is interface-only per D5; `statistics/*`, `ml/*` empty per audit); route/drift/tool_usage detectors emit WINDOW scope only (`domain/route.py:72`, `strategies/drift.py:83`, `domain/tool_usage.py:94`) while this integration mandates scope=RUN; `LatencyRecord` requires `expected_value/baseline_value/baseline_window` (`adapters/records.py:29-39`) with no platform source; `Run.duration_ms` is an observed value, not a baseline.
Decision: selection inside the caller is hard-coded — no registry, no plugin system. PATTERN runs when >=1 tool-call events exist (real event sequence + real event_ids). CONFIDENCE_DROP runs only when >=2 real confidence measurements exist in the run's behavior events (previous = first real value, current = last real value, baseline = first real value — copied from measurements, never invented; absent/single values -> deferred). ANOMALY deferred always (no baseline source). DRIFT deferred always (no baseline distribution + WINDOW scope). ROUTE_CHANGE and TOOL_USAGE_DRIFT deferred always (WINDOW scope only; multi-run/reference history out of RUN-scope integration). Every one of the six detector names appears exactly once per result — either executed or deferred with a reason. Latency observed in events is NOT enough for anomaly: observed without expected/baseline stays unmeasured, never zero-filled.
Rejected Approach: derive an "expected latency" baseline from `Run.duration_ms` or from prior runs in this call — fabricated baseline, forbidden.
Rejected Approach: run route/drift/tool_usage under WINDOW scope as a workaround — task mandates scope=RUN with null window fields; no WINDOW workarounds.

### V5. Agent isolation design

Discovery: `Event` rows have no `agent_id` — agent identity reaches monitoring only through `Run.agent_id`; `Event.run_id` FK to `runs.run_id` plus `list_for_run`'s WHERE clause make cross-run contamination impossible at the DB read, so contamination can only enter through caller-supplied sequences.
Decision: three layers, all existing. (1) `build_run_records` raises `AdapterUnavailable` if any supplied event's `run_id` differs from the Run's `run_id` — no silent inclusion; the rejection is loud because an event outside the run cannot be attributed to this agent. Note recorded: the phase-2 adapter `pattern_input_for_run` silently filters non-matching records as defense-in-depth — documented here; the caller validates BEFORE the adapter sees data, so that filter never fires in this path. (2) All detector inputs are built with `agent_id = Run.agent_id`, `run_id = Run.run_id`. (3) `execute_detector` re-checks every emitted Signal's `agent_id` and converts a violation into `DETECTOR_FAILED` with no signals (runner.py:65-72).

### V6. Confidence source and event-lineage review

Discovery: confidence has a real but optional source: Abstention/BehaviorPatternDetected `payload.confidence`. `ToolCallRecord.event_id` + `RepeatedToolUsagePayload.related_event_ids` give real event lineage for pattern signals. RUN signals carry `run_id` (Run row has `agent_version_id`, but `Signal` has no field for it — D3 decision stands, nothing added).
Decision: confidence measurements are taken from `payload.confidence` only when present and numeric (missing key/None -> measurement skipped, never 0.0); confidence signals therefore carry three real values (first, last, first-as-baseline) or the detector does not run. `list_for_run` default limit 1000 (repositories.py:407) applies — recorded as a lineage-completeness uncertainty for very large runs, the same limitation the evaluation layer already documents (layer.md:489).

### V7. BLOCKER / OUT-OF-SCOPE DISCOVERY — no run-lifecycle writer exists

Discovery: nothing in the repository creates or completes `Run` rows in production code; the run-completion hook this phase wires has no existing caller to attach to.
What is missing: an application service that transitions `Run.status` -> `completed` (via the existing `RunRepository.update_status`) when agent execution finishes.
Why it does not block this integration: the boundary contract is fully provable without it — tests seed a genuinely completed Run through the real repository path and invoke `monitor_completed_run`, which refuses any non-completed Run; the entry point is the exact call site the future lifecycle writer must invoke.
Smallest future fix: in the future run-lifecycle service, after `RunRepository.update_status(run_id, "completed", ...)` and its `session_scope()` commit, call `monitor_completed_run(run_id, session_factory=session_scope)` — one call, no monitoring changes required.
Status: production wiring deferred as out of scope; no lifecycle system invented or redesigned here.


### V8. Implementation record (run-completion integration)

Discovery: no existing module could host the caller without either importing storage into `monitoring/` or inventing lifecycle; a single application module was the smallest addition.
Decision: `src/ai_eval_platform/monitoring_integration.py` — one module, outside the monitoring package, mirroring `EvaluationService` (injected `session_factory`, read-only scope, no commits, detection runs after the scope closes).
Implementation (what changed): NEW `src/ai_eval_platform/monitoring_integration.py` containing: `RunNotCompletedError`; `ConfidenceMeasurement`/`RunMonitoringContext`/`DeferredDetector`/`RunMonitoringResult` (pydantic, `extra=forbid`); `run_record_from_run` (Run row -> existing `RunRecord`); `build_run_records` (sequence-ordered events -> existing `ToolCallRecord` list merging `tool_called`/`tool_completed` by `call_id` + `ConfidenceMeasurement` list from `payload.confidence`; raises `AdapterUnavailable` on any `event.run_id != Run.run_id`); `load_run_monitoring_context` (existing `RunRepository.get_required` + `EventRepository.list_for_run`, enforces `status == "completed"`); `select_run_assignments` (hard-coded truthful selection, no registry; every one of the six detectors appears exactly once as executed or deferred); `monitor_completed_run` (ONE call to the existing `run_monitoring`, plus caller-supplied `extra_assignments` seam). Reused unchanged: all adapters, all six detectors, `run_monitoring`/`DetectorOutcome`, `Signal` models, repositories, session ownership. NOTHING inside `src/ai_eval_platform/monitoring/` was modified. NO comments or docstrings in any new code.
Verification: module import smoke test passes; architecture test `test_monitoring_package_imports_no_storage_or_sqlalchemy` proves the monitoring package still imports no `sqlalchemy`/storage symbols; `monitoring_integration` is the only place that imports both sides.

### V9. Data path actually exercised

Discovery: the full path Run + Events -> adapter records -> typed inputs -> `run_monitoring` -> detectors -> Signals is reachable from real repository data with zero fabricated fields.
Evidence (code path): `monitor_completed_run` -> `load_run_monitoring_context` (Run row, status check, `list_for_run`) -> `build_run_records` (`RunRecord`, `ToolCallRecord` with real `event_id`/`call_id`/`latency_ms`/`success`, `ConfidenceMeasurement` with real `payload.confidence`) -> `select_run_assignments` -> `pattern_input_for_run` / `ConfidenceDetectionInput` (scope=RUN: `run_id` set, `evaluation_run_id`/windows null) -> `run_monitoring` -> `PatternDetector`/`ConfidenceDropDetector` -> `Signal` with `related_event_ids` (pattern) and real previous/current/baseline (confidence).
Decision: latency observed in `tool_completed`/`model_output` events is loaded into `ToolCallRecord.latency_ms` (real data preserved) but deliberately NOT converted into an anomaly input, because expected/baseline have no source — deferred instead.
Verification: tests below, all through the real repository path (SQLite in-memory via the same fixture pattern as `tests/evaluation/conftest.py`; no PostgreSQL/Kafka needed).


### V10. Test results (this phase)

New file: `tests/monitoring/test_run_completion_f.py` — 10 tests, all passing.
1. `test_completed_run_reaches_signal_with_lineage` — completed Run + two real `tool_called` events -> `pattern_input_for_run` -> `PatternDetector` -> REPEATED_TOOL_USAGE Signal; asserts RUN scope, agent/run lineage, `related_event_ids == [evt-t1, evt-t2]`, anomaly deferred.
2. `test_foreign_event_cannot_produce_cross_agent_signal` — a RunRecord for agent-1/run-1 fed agent-2/run-2 events raises `AdapterUnavailable` (no silent inclusion); DB path for run-1 never emits agent-2/run-2 lineage; runner guard converts a deliberately smuggled cross-agent input into `DETECTOR_FAILED` with zero signals.
3. `test_missing_confidence_is_not_fabricated` — one real confidence value + one null -> `confidence_drop` never executed, never receives an input, deferred, no CONFIDENCE_DROP signal.
4. `test_missing_baseline_defers_anomaly` — real `latency_ms=120` present in `ToolCallRecord` (asserted) yet anomaly deferred (no baseline), no LATENCY_ANOMALY signal.
5. `test_no_relevant_events_differs_from_no_finding` — no-relevant-data run yields `runs == []` + deferred entry; non-repeating-tool run yields executed detector with `NO_FINDING`; the two results differ.
6. `test_detector_failure_preserves_outcome` — controlled `_FailingDetector` through `extra_assignments` yields `DETECTOR_FAILED` reason `RuntimeError`, zero signals, and the real pattern detector still runs (`SIGNALS`).
7. `test_run_scope_invariants_for_every_signal` — every produced signal: scope RUN, agent_id = Run.agent_id, run_id = Run.run_id, evaluation_run_id null, both windows null.
8. `test_multiple_applicable_detectors_run_together` — one `monitor_completed_run` call executes pattern + confidence_drop (both `SIGNALS`), executed set union deferred set equals all six detector names with empty intersection; confidence payload values are the three real measurements.
9. `test_boundary_requires_completed_run` — running Run -> `RunNotCompletedError`; missing Run -> existing `EntityNotFoundError`.
10. `test_monitoring_package_imports_no_storage_or_sqlalchemy` — monitoring package modules import no `sqlalchemy`/`Session`/`RunRepository`/`EventRepository` (infrastructure independence enforced by test).

Full-suite results just now: `python -m pytest -q` -> 399 passed, 15 skipped (10 `tests/integration` + 5 `tests/storage` PostgreSQL/Kafka-gated — skipped by their own environment guards, recorded not hidden); zero failures. Prior-phase suites unchanged: monitoring 50/50 (40 + 10 new), contract 66/66, evaluation+ingestion 238/238, storage 45 passed/5 skipped. Baseline before this phase: 106 passed (monitoring+contract) — no existing test was modified or weakened; `git`-visible changes are exactly `monitoring_2.md` plus the two new files.


## Run-Completion Integration

### Implemented

One production integration path, and nothing else: `src/ai_eval_platform/monitoring_integration.py` (new application module, outside the monitoring package) exposing `monitor_completed_run(run_id, *, session_factory, extra_assignments=()) -> RunMonitoringResult`. Supporting pieces in the same module: `RunNotCompletedError`, `ConfidenceMeasurement`, `RunMonitoringContext`, `DeferredDetector`, `RunMonitoringResult`, `run_record_from_run`, `build_run_records`, `load_run_monitoring_context`, `select_run_assignments`. Plus `tests/monitoring/test_run_completion_f.py` (10 tests). NOTHING inside `src/ai_eval_platform/monitoring/` was changed; no Signal/adapter/detector/orchestration type was duplicated or extended; no registry, no persistence, no migrations, no Kafka, no Analysis/RCA/Policy, no EVALUATION orchestration, no baseline backend, no source-code comments or docstrings.

### Actual Data Path

```
completed Run (runs.status == "completed")
    |  RunRepository.get_required  +  EventRepository.list_for_run   (one read scope, injected session_factory, zero writes)
    v
RunRecord  +  ToolCallRecord[]  +  ConfidenceMeasurement[]           (existing adapter records; event_id/call_id/latency/success/confidence copied verbatim)
    |  pattern_input_for_run  /  ConfidenceDetectionInput            (existing adapters; scope = RUN)
    v
run_monitoring(Run.agent_id, [(detector, input), ...])               (existing orchestration, called exactly once)
    |  PatternDetector  /  ConfidenceDropDetector                    (existing detectors)
    v
Signal[]  (scope=RUN, run_id, agent_id, related_event_ids / real confidence values)
    + DeferredDetector[] for the detectors that could not run truthfully
```

### Run Boundary Selected

The repository-level completion point `RunRepository.update_status(run_id, "completed", ...)` (`storage/repositories.py:219-234`, flush-only, committed by `session_scope()` — the project's only commit point) is where a Run becomes completed; verified this phase that NO production code calls it yet (no run-lifecycle writer exists — recorded as V2 and BLOCKER V7). The integration therefore attaches on the caller side of that boundary as an application entry modeled 1:1 on `EvaluationService`: it REQUIRES `status == "completed"` (refuses otherwise), loads Run + Events in one read-only scope, runs detection only after the scope closes, and never writes run state — lifecycle stays entirely with the caller, monitoring only receives the completed Run context. Transaction reasoning recorded in V3: no commits in monitoring/adapters/detectors, no sessions owned by monitoring, post-commit invocation enforced structurally by the separate read scope.

### Detectors That Can Run

- `pattern` (REPEATED_TOOL_USAGE) — when the Run has >=1 real `tool_called`/`tool_completed` events; sequence and `related_event_ids` come from real event order/ids.
- `confidence_drop` (CONFIDENCE_DROP) — only when the Run has >=2 real `payload.confidence` measurements (Abstention/BehaviorPatternDetected): previous = first real value, current = last real value, baseline = first real value; all three copied, never invented.

### Detectors That Cannot Run

- `anomaly` — `LatencyRecord` requires expected/baseline/baseline-window; no baseline store exists (D5); observed latency without expected is not zero-filled. Deferred: "no baseline reference for run latency".
- `drift` — needs reference distribution/windows from a baseline source, and emits WINDOW scope. Deferred: "no baseline distribution for run".
- `route_change` — emits WINDOW scope only and needs multi-run history; RUN-scope mandate forbids WINDOW workarounds. Deferred: "run scope cannot provide window history".
- `tool_usage_drift` — emits WINDOW scope only and needs a reference window. Deferred: "run scope cannot provide reference window".
- `confidence_drop` when <2 measurements; `pattern` when no tool-call events — deferred, not run with fabricated inputs. Every one of the six detector names appears exactly once per result (executed XOR deferred), asserted by test.


### Lineage Verification

- `Signal.agent_id == Run.agent_id`, `Signal.run_id == Run.run_id` — inputs are built only from the loaded Run; asserted for every produced signal (test 7).
- Pattern signals carry `related_event_ids == [real event ids in sequence order]` (test 1).
- Confidence signals carry three real measurement values (test 8).
- `evaluation_run_id`, `window_start`, `window_end` remain null on every signal (scope=RUN; enforced by the Signal model and asserted per signal).
- Event -> Run lineage: `build_run_records` rejects any event whose `run_id` differs from the Run's (see isolation); `list_for_run` additionally filters by `run_id` and orders by `sequence_number`.
- Known incompleteness (unchanged from D3): `Signal` still has no `agent_version_id`/`correlation_id` field, so `Run.agent_version_id`/`Event.correlation_id` are not carried — no Signal field was invented for them.

### Agent Isolation Verification

Three enforced layers, all exercised by test 2: (1) `build_run_records` raises `AdapterUnavailable` on any foreign `run_id` — loud rejection, no silent inclusion; the phase-2 adapter's silent `agent_id/run_id` filter (documented in V5) never fires in this path because the caller validates first; (2) inputs are stamped with `Run.agent_id`/`Run.run_id` only; (3) `execute_detector`'s existing guard converts any cross-agent emitted Signal into `DETECTOR_FAILED` with zero signals ("agent boundary violated"). Cross-agent Signal production is impossible along this path; DB-level `Event.run_id` FK + `list_for_run` WHERE make foreign events unreachable from the loader.

### Failure Semantics

Existing `DetectorOutcome` vocabulary used unchanged: executed detectors surface `SIGNALS` / `NO_FINDING` / `DISABLED` (via policy) / `INVALID_INPUT` / `DETECTOR_FAILED`; the controlled failing detector yields `DETECTOR_FAILED` with `reason == "RuntimeError"` and no signals, while the other detector in the same `run_monitoring` call is unaffected (test 6). Not-executed detectors are NOT folded into any outcome — they appear only as `DeferredDetector(detector_name, reason)` beside `MonitoringRun`, preserving the distinction between "no relevant data" (no `DetectorRun` at all, tests 3/4/5) and "detector executed and found nothing" (`NO_FINDING`, test 5). Run precondition failures use `RunNotCompletedError`/`EntityNotFoundError` before any detector runs; adapter contamination uses `AdapterUnavailable`. No exception is converted into a fake Signal anywhere.

### Tests

`tests/monitoring/test_run_completion_f.py` — 10/10 pass (the 8 required scenarios + completion precondition + infrastructure-independence architecture check; details enumerated in V10). Full suite: `python -m pytest -q` -> 399 passed, 15 skipped (PostgreSQL/Kafka-gated, skipped by their own guards), 0 failures. No existing test was modified or weakened; no mocks hide the adapter boundary (real `Run`/`Event` rows, real repositories, real detectors through the real `run_monitoring`).

### Remaining Gaps

- No production call site completes Runs (BLOCKER V7) — `monitor_completed_run` cannot be wired into a lifecycle writer that does not exist yet; smallest future fix recorded in V7.
- `EventRepository.list_for_run` caps at 1000 events per run (repositories.py:407) — signals from very large runs would be computed over a truncated history (same known limitation as the evaluation layer).
- Confidence semantics: first/last in-run `payload.confidence` used as previous/current/baseline; Abstention vs BehaviorPatternDetected confidences are treated as one metric — semantic interpretation should be revisited when a real confidence store exists.
- Latency/`model_output` data is loaded but unused for detection until a baseline source exists; `severity` on pattern signals stays at the adapter default 0.0 (no event source; pre-existing default, not a measurement).
- `Signal` still lacks `agent_version_id`/`correlation_id`/detector version (D3/O3 unchanged); RUN-scope signals cannot express route/drift without a future multi-run design.

### Risks

- `extra_assignments` lets a caller supply arbitrary `(detector, input)` pairs — mitigated by the runner's agent-boundary re-check, but a careless caller can still obtain `INVALID_INPUT`/`DETECTOR_FAILED` noise by passing junk.
- Deferred reasons are stable strings, not an enum — downstream code must not parse them.
- Once a run-lifecycle writer appears, ordering (commit completion first, then monitor) becomes an operational responsibility; today it is only structurally enforced by the separate read scope.

### Recommended Next Step

ONE step: implement the missing run-lifecycle writer as a thin application service that marks a Run completed via the existing `RunRepository.update_status` in `session_scope()` and then calls `monitor_completed_run(run_id, session_factory=session_scope)` — closing the only gap between this proven boundary and a live end-to-end loop, without touching monitoring internals.

