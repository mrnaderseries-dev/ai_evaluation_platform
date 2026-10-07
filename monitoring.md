# Monitoring Architecture Audit
READ-ONLY audit. No source, test, config, model, or doc modified except this report.

Date (UTC): 2026-10-07. Repo: `c:\Users\user\ai-evaluation-platform`.
Scope: `src/ai_eval_platform/monitoring/`, plus tests, storage models, event schemas, ingestion/Kafka, evaluation, policy, analysis/root-cause, ML/statistics.

## 1. Executive Summary

Monitoring is a **skeleton with a sound Signal core but broken packaging and broken detector-to-Signal wiring**. `signals/models.py` + `signals/types.py` correctly enforce RUN/EVALUATION/WINDOW invariants and strict SignalType→Payload mapping — the best part of the subsystem. Everything around it is immature:

- Intended tree `signals/ + detectors/{base,inputs,strategies/{pattern,anomaly,drift},domain/{route,confidence,tool_usage}} + system/{metrics,status}` is **not realised**: `domain/` exists as an **empty directory without `__init__.py`**; the three domain detectors sit one level too high at `detectors/route.py, confidence.py, tool_usage.py`; `strategies/` has **no `__init__.py`**; both package `__init__.py` files import from **non-existent locations** so `import ai_eval_platform.monitoring` and `import ai_eval_platform.monitoring.detectors` **fail on import** (Confirmed Defects D1/D2).
- 6 legacy flat modules + `configs/monitoring.yaml` + `configs/policies.yaml` + all `policy/*.py`, `analysis/*.py`, `root_cause/*.py`, `statistics/*.py`, `ml/*/*.py` probed are **0-byte placeholders**.
- 3/6 detectors **cannot ever emit a valid Signal**: `detectors/route.py` sets `window_start==window_end==now()` violating `window_start<window_end`; `strategies/anomaly.py` and `strategies/pattern.py` emit `scope=WINDOW` **without any window fields** when `run_id is None`; `detectors/confidence.py` sets `run_id=agent_id` (fabricated lineage); `detectors/tool_usage.py` fabricates `window_start/window_end=now()`.
- No detector produces `EVALUATION` scope; no `EVALUATION` input exists; online/offline/window trichotomy is therefore **not implemented**.
- System monitoring (`system/metrics.py,status.py,__init__.py`) is **0 bytes** — separation holds vacuously, not by design enforcement.
- Zero tests reference monitoring/signals/detectors; zero cross-package imports; zero persistence/registry/orchestration.
- Biggest weakness: **detector input + scope/window lineage is fabricated at emit time (`now()`, `agent_id as run_id`) instead of received as validated input**, combined with broken packaging that prevents any integration from even importing the subsystem.

Maturity overall ~1.2/5 (skeleton → weak domain contract). Signal contract alone ~2.5/5. Do not build analysis/policy/ML on top until packaging + scope/window defects are fixed and Signal lineage (AgentVersion, EventIDs, DatasetVersion) is added.

## 2. Repository Structure Audit

Actual `monitoring` tree (verified via `rglob`, `dir`):

```
src/ai_eval_platform/monitoring/
  __init__.py  (BROKEN imports, see D1)
  alerts.py (0B), anomaly.py (0B), cost.py (0B), drift.py (0B), latency.py (0B), route_drift.py (0B)
  detectors/
    __init__.py (BROKEN imports, see D2)
    base.py (OK)
    inputs.py (OK, strategy inputs only)
    route.py, confidence.py, tool_usage.py (MISPLACED — belong in domain/)
    domain/  (EMPTY DIRECTORY, no __init__.py, no .py files)
    strategies/
      anomaly.py, drift.py, pattern.py (NO __init__.py)
  signals/
    __init__.py, models.py, types.py (correct set)
  system/
    __init__.py, metrics.py, status.py (all 0B)
```

Intended tree requires: `signals/{__init__,models,types}`, `detectors/{__init__,base,inputs,strategies/{__init__,pattern,anomaly,drift},domain/{__init__,route,confidence,tool_usage}}`, `system/{__init__,metrics,status}`.

A–F answers:

A. Filesystem structure is **incorrect** (details + tree above; narrative in §4).

## 3. Intended Architecture

RAW AGENT DATA → DETECTORS → SIGNALS → ANALYSIS → ROOT CAUSE → POLICY → PASS/REVIEW/BLOCK; SYSTEM METRICS → SYSTEM MONITORING → SYSTEM STATUS kept separate. Strategies (pattern/anomaly/drift) are reusable mechanics; domain (route/confidence/tool_usage) are behaviour areas composing strategies. Detector: typed input + policy → list[Signal]; no RCA/policy-decision/persistence/Kafka. Scopes RUN/EVALUATION/WINDOW with strict lineage; SignalType→typed Payload.

## 4. Actual Architecture

What exists: `signals/models.py:Signal` + `signals/types.py:SignalScope/SignalType/6 payloads` (sound); `detectors/base.py:Detector/InputT/PolicyT/DetectorPolicy` (sound generic ABC); `detectors/inputs.py:DetectorInput/Pattern/Anomaly/DriftDetectionInput` (strategy-only); `strategies/{pattern,anomaly,drift}.py:Pattern/Anomaly/DriftDetector` (extend Detector, policy-gated); `detectors/{route,confidence,tool_usage}.py:RouteChange/ConfidenceDrop/ToolUsageDriftDetector` (plain classes, NO base/policy); `system/*` empty; 6 flat root modules empty. No registry, persistence, orchestration, Kafka consumer, analysis/root-cause/policy wiring. Imports: `monitoring/__init__.py:1-12` does `from .confidence import ... / .route / .tool_usage` — paths do not exist (files are under `detectors/`); `detectors/__init__.py:1-3` does `from .anomaly/.drift/.pattern import ...` — files are under `strategies/`. Both packages unimportable. Strategies import `..base/..inputs/...signals` correctly; domain files import `...signals` directly, bypassing base/inputs. `domain/` empty so intended `strategies vs domain` boundary unenforced.

A. Filesystem structure **incorrect**. B. Correctly placed: `signals/*` (3 files), `detectors/base.py`, `detectors/inputs.py`, `strategies/{pattern,anomaly,drift}.py` files (content-wise), `system/*` paths. C. Misplaced: `detectors/route.py,confidence.py,tool_usage.py` → should be `detectors/domain/`. D. Missing: `detectors/strategies/__init__.py`, `detectors/domain/__init__.py`, `detectors/domain/{route,confidence,tool_usage}.py` (as placed), `system` implementations. E. Should-not-exist (or remove/redirect): 6 flat 0B modules `alerts,anomaly,cost,drift,latency,route_drift.py` at `monitoring/` root. F. Not scalable as-is — broken imports block all reuse; domain/strategy split unenforceable while domain dir is empty.

## 5. Signal Contract Audit

File: `src/ai_eval_platform/monitoring/signals/models.py:27-111`, `types.py:6-18`.
Intended fields present: signal_id/agent_id/signal_type/scope/run_id/evaluation_run_id/window_start/window_end/detected_at/payload — YES.
RUN invariant (`models.py:46-58`): requires run_id, forbids evaluation_run_id + windows — CORRECT. EVALUATION (`60-74`): requires evaluation_run_id, forbids run_id + windows — CORRECT. WINDOW (`76-91`): requires both windows, window_start<window_end, forbids run_id, ALLOWS evaluation_run_id as lineage — CORRECT per spec allowance.
Violations/gaps: (1) `agent_id: str` not cross-validated against `Run.agent_id` — no FK/lookup, expected at this layer but lineage gap (§16). (2) `signal_id: str min_length=1` — no UUID format enforcement; collisions rely on `uuid4()` at emit sites. Uniqueness assumption undocumented. (3) `detected_at: datetime` REQUIRED but NO tz-aware validator — unlike `schemas/events/common.py:67-74` which rejects naive timestamps. All detectors use `datetime.now(timezone.utc)` so current emitters are safe, but contract allows naive `detected_at`/windows. MEDIUM. (4) No `agent_version_id`, `event_ids`, `correlation_id`, `dataset_version_id` fields — lineage insufficient (§16). (5) `extra=forbid + validate_assignment=True` GOOD. (6) `payload: PayloadType` union + `validate_payload_matches_type` GOOD — wrong-type payload rejected. (7) No online/offline discriminator beyond scope; no schema-version field for Signal itself — persistence risk (§20/25).

## 6. SignalType and Payload Audit

File: `signals/types.py:12-68`, `signals/__init__.py:1-23`.
Types: LATENCY_ANOMALY/ROUTE_CHANGE/CONFIDENCE_DROP/REPEATED_TOOL_USAGE/TOOL_USAGE_DRIFT/ERROR_PATTERN — matches intended examples. Concrete, coherent, scalable enum (str-enum). Mapping enforced 1:1 in `models.py:96-109` — cannot mix payload types. Strongly typed payloads, all `extra=forbid` via `SignalPayload`. No `dict[str,Any]` garbage container — GOOD.
Issues: (a) ERROR_PATTERN defined in types+models but NO detector emits it — dead type, no coverage. LOW. (b) `LatencyAnomalyPayload.baseline_window: str` is free-text; detectors set `"detector_input"` placeholder (`strategies/anomaly.py:57`) — redundant/meaningless, should be window ref or enum. MEDIUM. (c) `RepeatedToolUsagePayload.severity: 0..1` always `0.0` in `strategies/pattern.py:50` — dead field, misleading. MEDIUM. (d) `ConfidenceDropPayload.delta/baseline` vs `LatencyAnomalyPayload.deviation/threshold` naming inconsistent (delta vs deviation) — minor. LOW. (e) `ToolUsageDriftPayload.drift_score==delta` always (`strategies/drift.py:71`, `detectors/tool_usage.py:46`) — redundant field, collapses future metric choice (e.g., PSI/KS/JSD). MEDIUM — keep field but document it must diverge. (f) No analysis/root-cause/system-status leakage in payloads — GOOD. (g) Payloads lack `agent_version_id/event_ids` except `RepeatedToolUsagePayload.related_event_ids` + `ErrorPatternPayload.related_event_ids` — inconsistent lineage. MEDIUM. Verdict: keep concrete types; do NOT genericise.

## 7. Detector Contract Audit

Intended: input+policy → list[Signal]; must NOT RCA/policy-decide/persist/Kafka/infra/PASS-REVIEW-BLOCK/be Analysis.
`detectors/base.py:13-28`: `DetectorPolicy(enabled,minimum_samples)` + `Detector[InputT:BaseModel,PolicyT:DetectorPolicy].detect→list[Signal]` — CORRECT abstraction, testable, no infra imports. GOOD.
Per-detector: `strategies/pattern|anomaly|drift` EXTEND base, honour `enabled`, return `[]` on no-detection, no DB/Kafka/policy-decision/RCA — PASS. Domain `route|confidence|tool_usage` do NOT extend base, have NO policy, embed thresholds in input — FAIL (see §8/10). None persist or touch Kafka — PASS. None decide PASS/REVIEW/BLOCK — PASS. None implement Analysis — PASS (they under-provide lineage rather than over-interpret). Aggregate/compare/classify: pattern loops sequences, anomaly compares deviation, drift loops feature deltas — minimal but within remit.
Gaps: `minimum_samples` in base policy is NEVER checked by any strategy detector — dead knob. `detect` has no batch/aggregate signature (single input object) — WINDOW aggregation must be pre-assembled by caller; no orchestration exists.

## 8. Strategy vs Domain Audit

Intended: `strategies/{pattern,anomaly,drift}` mechanics; `domain/{route,confidence,tool_usage}` behaviour areas composing strategies.
Reality: split NOT respected. `strategies/*` each hardcode ONE SignalType (pattern→REPEATED_TOOL_USAGE, anomaly→LATENCY_ANOMALY, drift→TOOL_USAGE_DRIFT) instead of being generic mechanics — they are de-facto domain detectors with strategy names. Domain files duplicate strategy logic inline: `detectors/tool_usage.py:33-46` re-implements drift delta/threshold/payload identical to `strategies/drift.py:61-72` (copy-paste duplication); `detectors/confidence.py:34-45` re-implements anomaly threshold compare; `detectors/route.py:29-56` hand-rolls equality check. Domain detectors do NOT reuse strategy detectors, do NOT extend `Detector`, have NO policy. Future `RouteAnomaly/RoutePattern/ToolUsageDrift/ConfidenceAnomaly` would require N×M class explosion — current design does NOT support cleanly. Extensibility verdict: NOT extensible; needs composition (domain detector OWNS strategy instance(s)) or strategy generics parametrised by SignalType/payload-builder. No circular deps; dependency direction strategies→signals, domain→signals (correct direction, wrong reuse).

## 9. Detector Input Audit

`detectors/inputs.py:6-35`: `DetectorInput(agent_id)` base GOOD (`extra=forbid`, no `Any`). `PatternDetectionInput(run_id?,event_sequences,event_ids)`; `AnomalyDetectionInput(run_id?,metric/observed/expected/baseline/threshold)`; `DriftDetectionInput(reference/current windows+distributions+drift_metric/threshold)` — strongly typed, explicit, no `Any` — GOOD, avoids intended anti-pattern.

## 10. Detector Policy Audit

`base.py:13-17` DetectorPolicy(enabled,minimum_samples) GOOD; `strategies/pattern.py:16-17` adds minimum_occurrences≥2; `anomaly.py:16-17` adds absolute_threshold; `drift.py:16-17` adds drift_threshold≥0 — meaningful, not hardcoded. `enabled` honoured by all 3 strategy detectors. `minimum_samples`honoured by NONE — dead. Domain detectors have NO policy class at all; thresholds ride inside input (`confidence.threshold`, `tool_usage.threshold`) — conflates per-call data with configuration; cannot disable, version, or own policy. `configs/monitoring.yaml` + `configs/policies.yaml` are 0B — no externalised config; everything is constructor args. No confusion with Platform Policy (`storage/models.py:104-153 Policy/PolicyVersion/AgentPolicy` + empty `policy/*.py`) — detector policies are local pydantic models, never import platform policy — GOOD separation, but must stay that way: detector policy = how to detect; platform policy = what to do after RCA. Risk: `AnomalyDetectionInput.threshold` vs `AnomalyDetectorPolicy.absolute_threshold` dual-source — caller vs policy precedence implicit (`anomaly.py:42-46` policy wins if set). Document or remove input threshold.

## 11. Analysis Boundary Audit

Intended: Signals → Analysis ("performance degraded after route change"). `src/ai_eval_platform/analysis/{analyzer,context,findings}.py` all 0B; `storage/models.py:507-517 Analysis(analysis_id,analysis_type,status,source_evaluation_run_id,summary,findings,metrics)` + `repositories.py:1178+ AnalysisRepository` exist but unwired. Detectors do NOT interpret/correlate (each emits single-type signals, no cross-signal narrative) — GOOD, no boundary violation. Risk: Analysis has NO `source_signal_ids`/`signal_refs` column — only `source_evaluation_run_id` — so future Analysis cannot trace which Signals it consumed without schema change (§27). Do NOT create duplicate Analysis entity — reuse `storage.models.Analysis` with `analysis_type='monitoring_correlation'` convention later.

## 12. Root Cause Boundary Audit

Intended: Signals+Analysis+Evidence+Versions → Root Cause (why). `root_cause/*.py` 0B; `storage/models.py:543-552 RootCauseAnalysis(suspected_causes,evidence_event_ids,affected_slices,confidence,conclusion)` keyed to `analysis_id` — adequate sink. Signals preserve: agent_id, run_id OR evaluation_run_id OR window — but NEVER `agent_version_id`, only one payload type carries `related_event_ids`, none carry `correlation_id/trace_id/dataset_version_id` — RCA would have to reconstruct version + evidence joins from `runs/events` via run_id alone; WINDOW signals lack run list entirely. Lineage insufficient — see §16. Detectors do NOT duplicate RCA logic — GOOD.

## 13. System Monitoring Separation

Intended: Kafka lag/DB pool/CPU/mem/throughput/consumer health separate from Agent Signals. `system/__init__.py,metrics.py,status.py` ALL 0B — no implementation, no mixing — separation holds VACUOUSLY. No SignalType carries infra semantics; no detector reads infra metrics; no `observability/metrics.py` import. GOOD but unproven. Risk: flat 0B `monitoring/{latency,cost,anomaly,drift,route_drift,alerts}.py` names invite future infra/agent conflation (e.g., `cost.py` could become billing-infra vs agent-tool-cost). Recommend deleting or documenting them as removed legacy (§23-D12).

## 14. Online / Offline / Window Analysis

ONLINE (Run→Detector→Signal): PARTIAL. `strategies/anomaly|pattern` + `confidence` emit RUN when run_id present — but confidence fabricates run_id (D6). OFFLINE (EvaluationRun/Dataset→Runs→Detector→Signal): ABSENT. No EVALUATION emitter, no evaluation_run_id input, no dataset linkage. WINDOW (Agent→runs-in-window→Signal): BROKEN. `strategies/drift` + `route + tool_usage` emit WINDOW but route/tool_usage fabricate `now()/now()` (D3/D7, equal bounds → invalid), anomaly/pattern emit WINDOW with NO bounds (D4/D5). Implementation ASSUMES single-Run-or-adhoc-window, never a real run-set aggregation. MAJOR architectural issue — confirmed.

## 15. Agent Boundary Analysis

Inputs scope to single `agent_id`; `Signal.agent_id` copied verbatim; drift loops per-feature within one agent's distributions — no cross-agent aggregation in code. GOOD. Risk: `DriftDetectionInput` has NO agent check between reference/current distributions — caller could mix Agent A reference with Agent B current and detector would not reject. `PatternDetectionInput.event_sequences/event_ids` have no agent tag per sequence — caller could interleave agents. Mitigation belongs in orchestration/validation layer (not yet existent) + documented precondition. No explicit cross-agent detector exists — correct to keep forbidden by default.

## 16. Data Lineage Audit

Preserved: signal_id (uuid), agent_id, one of run_id/evaluation_run_id/window, detected_at, signal_type→payload scalars, `related_event_ids` ONLY in 2/6 payloads.
Missing: (1) `agent_version_id` — CRITICAL: `Run.agent_version_id` + `AgentVersion(version/code_hash/config/model)` exist; without it cannot compare behaviour between versions, defeating DifferentialAnalysis/offline eval. (2) `event_ids` generally — only repeated/error payloads carry them; latency/route/confidence/drift leave RCA to re-derive evidence. (3) `correlation_id/trace_id` — `Event.correlation_id` + `Run.trace_id` exist for cross-event joins; signals drop them. (4) `dataset_version_id/evaluation linkage` — `EvaluationRun.dataset_version_id/agent_version_id` exist; WINDOW-with-eval-lineage allowance unused. (5) Reference window for drift (only current emitted). (6) Detector identity/version (`detector_name/version/policy_version`) — no reproducibility. (7) `signal_schema_version` — blocks future payload evolution. Each missing field forces RCA/analysis to re-query or guess; window signals with zero run refs are un-auditable.

Domain inputs (`detectors/route.py:14-21`, `confidence.py:14-22`, `tool_usage.py:14-21`) are SEPARATE `BaseModel`s NOT extending `DetectorInput`, each with own `agent_id` — fragmentation; no shared window/eval lineage.
RUN: strategies accept `run_id?` optionally — allows RUN (run_id set) or WINDOW (run_id None) but WINDOW path emits invalid Signals (D4/D5). `confidence.py` has NO run_id field yet emits RUN with `run_id=agent_id` — FABRICATED (D6). `route.py/tool_usage.py` have NO window fields yet emit WINDOW with `now()` — FABRICATED (D3/D7). EVALUATION: NO input carries `evaluation_run_id`; NO detector emits EVALUATION — offline path unimplemented. `DriftDetectionInput` HAS reference+current windows but `strategies/drift.py` DISCARDS reference window, emits only current window — half-lineage (D8). `AnomalyDetectionInput` carries BOTH `threshold` and `expected_value` while policy may override via `absolute_threshold` — threshold ownership ambiguous (§10). No detector invents evaluation context silently (they omit it), but all WINDOW/RUN emitters invent time/identity lineage — architectural defects confirmed.

## 17. Cross-Project Compatibility

Ingestion/Events→Kafka→persistence: `schemas/events/*` (AIEvent tz-aware, 30+ EventTypes, per-family payloads) + `ingestion/{event_validator,event_router,handler,kafka_consumer}.py` + `storage/models.py:Event(+AgentBehavior/Context/Execution details)` form a REAL pipeline. Monitoring consumes NONE of it — no importer, no event→input adapter, no `run_id→agent_id` resolver. Compatible in principle (run_id/event_ids/correlation_id present in both worlds) but unwired. Run: `storage/models.py:77-102 Run(run_id,agent_id,agent_version_id,status,started/completed/duration_ms,error_*,trace_id)` — RUN signals CAN join; but detectors never read Run. Evaluation: `EvaluationRun(342-355)+EvaluationResult(358-409, online/offline/benchmark uniqueness)` — EVALUATION signals have nowhere to attach (no producer). AgentVersion: present, unused. Kafka: `infrastructure/kafka/config.py` + `configs/kafka.yaml` real; detectors correctly Kafka-free — GOOD. Storage: NO signal table/repo; Analysis-family tables exist. ML: `ml/*` + `statistics/*` all 0B — no conflict, no reuse. Overall: fits conceptually, connects to nothing.

## 18. Evaluation Integration

`evaluation/{domain,evaluator,runner}.py` real (EvaluationTarget/Context/Criteria, Evaluator ABC, EvaluationRunner with ERROR mapping); `EvaluationResult` uniqueness covers online/offline/benchmark. EVALUATION-scoped Signals COULD be produced from `EvaluationResult(metric_name,score,passed,latency_ms)` aggregates per `evaluation_run_id` — but no detector accepts `evaluation_run_id`, none reads results, `EvaluationCompletedEvent(evaluation_run_id,metric,value,passed)` never mapped to input. Verdict: architecture ALLOWS it; implementation ABSENT. Offline detector family is greenfield.

## 19. Ingestion / Kafka Integration

`ingestion/kafka_consumer.py:61+ validate→route→handler→classify→commit`, `event_router.py:50-81 EXPECTED_ROUTES`, `handler.py:28-137` five persisting handlers, `infrastructure/kafka/config.py` explicit-commit semantics — mature. Monitoring has NO consumer, NO handler, NO topic, NO adapter; correctly avoids Kafka imports inside detectors (verified — detectors import only pydantic/signals/uuid/datetime). Future wiring must live OUTSIDE detectors (adapter: events→DetectorInput; orchestrator: batch/online scheduling). No premature-commit or ordering risk introduced by monitoring today. `Event.sequence_number` + `uq_event_run_sequence` give detectors a safe per-run order source when adapters arrive.

## 20. Storage Integration

No `signals` table, no repository, no migration. Future mapping options: (a) own `signals` table (signal_id PK, agent_id/run_id/evaluation_run_id/window cols, signal_type/scope indexes, payload JSONB, detector/version cols) — RECOMMENDED for queryability + retention; (b) JSON-only in Analysis.findings — REJECT (unqueryable); (c) normalised subtype tables per SignalType — premature. Hybrid (core cols + JSONB payload + GIN) fits existing `payload: JSON` precedent (`Event.payload`, `EvaluationResult.metrics`). Must add `agent_version_id`, `event_ids`, `detector_name/version`, `schema_version`, dedup key (agent+type+scope+window/run hash). Do NOT implement now.

## 21. ML / Statistical Readiness

Rule→statistical→ML progression SUPPORTED by contracts: `Signal` unchanged while detector internals evolve (threshold→baseline→model); `DetectorPolicy` can carry model URIs/thresholds later; payloads carry observed/expected/drift_score placeholders. BUT: no baseline/reference store, no feature builder link (`features/*` 0B), no `statistics/*` impl, `drift_score==delta` collapses metric choice, `baseline_window` free-text, no detector/model versioning, no statistical validation harness. `ml/{anomaly,drift,route_prediction}/*` 0B. Verdict: contract-compatible, capability-absent. Statistical drift (PSI/KS/JSD) and IsolationForest can slot into `DriftDetector/AnomalyDetector` WITHOUT Signal change — IF window/reference lineage fixed first.

## 22. Software Engineering Quality

Coupling: low (detectors→signals only); cohesion: signals high, detectors medium (domain dup); abstraction: base generic GOOD, domain bypass BAD; direction: correct (no cycles; verified no `monitoring` imports elsewhere, no detector↔strategy cycle). Type safety: strong (pydantic, no `Any` in monitoring; `extra=forbid` everywhere except Signal allows union only). Validation: Signal scope/payload validators GOOD; tz-naive allowed BAD; domain inputs lack window/eval validation. Naming: coherent except `baseline_window`/`severity`/`drift_score` placeholders. Extensibility: poor (§8). Testability: pure functions, deterministic EXCEPT `datetime.now(timezone.utc)` ×3 per emit + `uuid4` — inject clock/id for tests. Error handling: `[]` on disabled/no-data GOOD; no exceptions for invalid windows — Signal validator raises instead (fail-loud at wrong layer). Serialization: pydantic-native GOOD. Immutability: inputs mutable (no frozen) — minor. Duplication: tool_usage⇄drift, confidence⇄anomaly — HIGH. Hidden assumptions: run_id presence⇒RUN validity; `now()`⇒window; agent_id⇒run_id. Infra leakage: none. Style not the problem — lineage + packaging are.

## 23. Confirmed Defects

D1 CRITICAL `monitoring/__init__.py:1-12` imports `.confidence/.route/.tool_usage` — files live at `detectors/`. Package unimportable. Impact: nothing can import root package. Direction: re-export from `detectors.domain.*` once moved.
D2 CRITICAL `detectors/__init__.py:1-3` imports `.anomaly/.drift/.pattern` — files live at `strategies/`. Same impact. Direction: re-export from `.strategies.*` + `.domain.*`.
D3 CRITICAL `detectors/route.py:52-53` window_start=now, window_end=now violates `models.py:82-85` (< required). Always invalid. Direction: receive bounds in input.
D4 HIGH `strategies/anomaly.py:60-75` WINDOW scope with NO window fields violates `models.py:77-80`. Direction: require window input or RUN-only.
D5 HIGH `strategies/pattern.py:54-69` same WINDOW-without-bounds defect. Same direction.
D6 HIGH `detectors/confidence.py:53` run_id=agent_id fabricates lineage. Direction: add run_id to input.
D7 HIGH `detectors/tool_usage.py:55-56` fabricates windows with now()/now() (also violates <). Direction: receive windows.
D8 MEDIUM `strategies/drift.py:80-81` drops reference window, emits current only. Direction: carry both.
D9 MEDIUM `Signal` datetimes accept naive (models.py:39-41) vs event tz enforcement. Direction: mirror `schemas/events/common.py:67-74`.
D10 MEDIUM domain detectors bypass `Detector` base, no policy. Direction: extend `Detector`.
D11 MEDIUM `minimum_samples` never enforced. Direction: enforce or remove.
D12 LOW 6 flat 0B modules + empty domain/ + missing inits. Direction: delete/move/add inits.
D13 LOW severity=0.0 always, drift_score==delta, baseline_window placeholder, ERROR_PATTERN unemitted.

## 24. Architectural Risks

R1 Strategies hardcoded to one SignalType → N×M explosion for RouteAnomaly etc.
R2 WINDOW aggregation owner unclear (detector vs orchestrator); single-shot `detect` pushes it to unknown caller.
R3 No detector/version identity on Signal → irreproducible ML evolution.
R4 No baseline/reference store — drift story has no home.
R5 No dedup/correlation — noisy detectors flood downstream.
R6 No `schema_version` — payload evolution breaks readers.
R7 Adapter coupling: confidence/latency fields assumed but not guaranteed by `schemas/events/*`.
R8 Threshold-in-input vs policy-owned inconsistency will worsen.
R9 Empty system/* + 0B flats invite infra/agent conflation.
R10 Zero tests — every future fix unverified.

## 25. Missing / Incomplete Pieces

Required: packaging fix; RUN/WINDOW lineage inputs; EVALUATION input+emitter; tz validators; minimum_samples; Signal lineage (agent_version_id, event_ids, detector version, schema_version); table design; registry/lifecycle; contract tests; baseline store; dedup key.
Recommended: online/batch orchestration; batch aggregation contract; related_event_ids everywhere; trace passthrough; detector metrics; retention; alerting seam (outside detectors).

Future/Optional: subtype tables; cross-agent detectors; ML registry; backpressure/DLQ; policy hookup; review loop.

## 26. Strengths

Scope validator + 1:1 type→payload + extra=forbid + typed payloads are genuinely good. `Detector` generic ABC + policy separation is the right abstraction. No Any/Kafka/DB leakage; agent-scoped inputs; correct import direction. Gaps are honest (empty, not fake). MLOps-compatible shape. Do NOT praise wiring/persistence/tests — broken/absent.

## 27. Cross-Project Inconsistencies

(1) Both `__init__.py` contradict filesystem; contained (zero external importers) but total. (2) AIEvent tz-enforced vs Signal tz-optional. (3) Run/Eval carry agent_version_id/dataset_version_id; Signal carries neither. (4) EvaluationResult knows online/offline/benchmark; monitoring emits no EVALUATION. (5) Events carry correlation/trace/sequence; signals drop them. (6) Analysis keys only on source_evaluation_run_id — no signal refs. (7) monitoring/policies/evaluation yamls empty while thresholds live in code. (8) Events use Literal locking + reuse; strategies hardcode one type each.

## 28. Maturity Assessment

0=conceptual 1=skeleton 2=contract 3=working 4=prod-oriented 5=ready.
Architecture 1 — tree intended but packaging broken, domain empty. Domain model 2 — Signal solid, detector inputs half. Type safety 2 — forbid+validators good, naive dt + fabricated lineage drag. Detector impl 1 — 3/6 never valid, 0/6 EVALUATION, duplication. Integration 0 — nothing wired. Persistence 1 — direction clear, nothing designed. Testing 0. ML readiness 1 — contract allows, capability absent. Observability 0. Production 0 — unimportable. Overall ~1.

## 29. Recommended Next Steps

1. Packaging (D1/D2/D12) — unblocks imports. 2. Scope/lineage (D3-D8) — valid emission. 3. Signal hardening (tz+lineage) — lock contract. 4. Unify policy/base (D10/D11). 5. Contract tests (scope×payload, invalid windows). 6. Event→input adapters (outside detectors). 7. EVALUATION/offline + baseline store. 8. Persistence design, then impl. Order matters: import → emit valid → lock contract → test → connect → persist. No code written per mandate.

## 30. Final Architectural Verdict

Sound? Signal/ABC YES; wiring/packaging/lineage NO — fixable, no redesign. Filesystem? NO — intended tree right, actual wrong. Signal? MOSTLY — add tz+lineage. Type→Payload? YES — keep concrete; fix placeholder fields; cover ERROR_PATTERN. Detector? BASE yes; domain non-conforming; strategies over-specialised — use composition. Split justified? Conceptually yes, implemented no. Fits platform? Conceptually, connected to nothing. Biggest weakness: fabricated lineage + unimportable packaging. NOT to change: scope invariants, 1:1 mapping, Detector generic, Kafka-free detectors, agent scoping, forbid. MUST fix: D1-D8 + tests. Can wait: persistence impl, orchestration, ML, alerting, system impl.

## Repair Log

### R1 — Package structure (D1/D2/D12)

Status: FIXED. Verified: pre-repair `import ai_eval_platform.monitoring` failed with `No module named 'ai_eval_platform.monitoring.confidence'`; post-repair all five imports pass (root, signals, detectors, strategies, domain). Change: moved `detectors/{route,confidence,tool_usage}.py` into `detectors/domain/`; added `strategies/__init__.py` and `domain/__init__.py` with policy exports; fixed both `__init__.py` re-exports. Tests: `test_imports_resolve` + full monitoring suite (30 passed). Remaining: 6 flat 0B legacy modules (`alerts/anomaly/cost/drift/latency/route_drift.py`) intentionally left untouched — out of repair scope, no import effect.

### R2 — Signal timezone contract (D9)

Status: FIXED. Evidence: `signals/models.py` gained `require_timezone_aware` on `window_start/window_end/detected_at`, mirroring `schemas/events/common.py`. Verification: naive `detected_at`/windows rejected (tests), tz-aware accepted. Remaining: none in monitoring; event contract untouched.

### R3 — Drift reference lineage (D8)

Status: FIXED. `ToolUsageDriftPayload` gained `drift_metric` + `reference_window_start/end` (tz-validated); `DriftDetector` emits reference bounds in payload, current bounds on signal. Verified by `test_drift_preserves_reference_window`. Remaining: metric is still absolute-delta; PSI/KS/JSD deferred (needs baseline store).

### R4 — Fabricated lineage (D3–D7)

Status: FIXED. `ScopedDetectorInput.exactly_one_scope` + window validators force callers to supply lineage; route/confidence/tool_usage/anomaly/pattern now copy supplied values only. Verified: route equal-bounds impossible (input rejects), confidence `run-77` preserved verbatim, tool usage windows from input, anomaly/pattern EVALUATION paths tested. Remaining: none.

### R5 — Strategy/domain split + minimum_samples (D10/D11/D13)

Status: FIXED. Domain detectors extend `Detector`, thresholds moved to policies, `ToolUsageDriftDetector` delegates to `drifted_features`. `severity`/`baseline_window` are now inputs, not placeholders. Minimum samples enforced per detector (anomaly `sample_count`, pattern sequence count, drift feature count, route `affected_runs`). Follow-up R9: route input gained tz+ordering validators during final review. `ERROR_PATTERN` documented as intentionally unimplemented (no source inside scope). Tests: disabled/min-samples/domain-contract/interaction tests pass.

## Open Questions

O1 `minimum_samples` semantics differ per detector (count of what?) — documented per class, no single global meaning. Acceptable or needs a shared `sample_count` field on all inputs?
O2 `drift_score == delta` still identical — kept until a real metric (PSI/KS) is chosen with the baseline-store phase. Fine to leave?
O3 Signal still lacks `agent_version_id`/`dataset_version_id`/`correlation_id`/detector version — needs cross-project agreement before adding. Deferred, not guessed.

## Deferred Cross-Project Changes

None required for this repair. Adapters, Kafka, storage, evaluation, policy, analysis/RCA all correctly left untouched — monitoring is importable and fully testable without them.

## Final Repair Status

Status: contract repair complete; implementation valid and tested; not production-deployable (no persistence/orchestration by phase design).

Files Changed: `monitoring/{__init__,signals/models,signals/types,detectors/__init__,detectors/inputs,detectors/strategies/*,detectors/domain/*}` (moves + contract fixes, no comments/docstrings added); `tests/monitoring/` (4 new files, 30 tests); `monitoring.md` (this log); `analysis_layer_monototring.md` (fixes/analysis/ratings).

Tests: `tests/monitoring` 30/30; `tests/contract` 66/66; `tests/evaluation+ingestion` 238/238. Broader suite not run (postgres/kafka-gated); no failures observed in runnable scope.

Architecture: scope invariants + strict type→payload preserved; strategies reusable via pure helpers; domain composes (tool usage) or stays bespoke where semantics differ (route, confidence); no Kafka/DB/policy/RCA inside detectors.

Remaining Issues: O1–O3 above; ERROR_PATTERN unimplemented; 0B legacy/system modules left as-is.

Deferred Changes: persistence, orchestration, adapters, baselines, alerting — separate phases.

Cross-Project Changes: none.

Model Review: self-review performed (final checklist §30 verified except persistence/orchestration which are out of phase); no model switch available in this environment — recorded instead of skipped silently.
