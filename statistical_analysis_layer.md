# Statistical Analysis Layer — Architecture, Mathematical and Statistical Audit

**Date:** 2026-10-10 | **Scope:** `src/ai_eval_platform/analysis/*`, `src/ai_eval_platform/storage/models.py` + `repositories.py` (signal paths), `src/ai_eval_platform/monitoring/{signals,detectors,adapters,orchestration,persistence}`, `src/ai_eval_platform/monitoring_integration.py`, `src/ai_eval_platform/run_lifecycle.py`, `schemas/events/*`, `src/ai_eval_platform/ingestion/{event_router,event_validator,handler}.py`, `src/ai_eval_platform/statistics/*`, `src/ai_eval_platform/root_cause/*` | **Mode:** audit-only, no source modified

## Executive Summary

The "analysis layer" is currently **two incompatible layers sharing one package name**:

- **(A) Tracked baseline (HEAD, `04d7525`):** 4 files — `analysis/__init__.py`, `analyzer.py` (`SignalSummaryAnalyzer`), `context.py` (`AnalysisContext`), `findings.py` (`AnalysisFinding`). Small, coherent, importable in isolation. It does **descriptive counting only** — no statistics, no relationships, no RCA.
- **(B) Untracked working-tree extension (not in git):** 7 files — `engine.py` (`AnalysisEngine`), `evidence.py`, `models.py` (`AnalysisResult`), `relationship_analyzer.py`, `service.py` (`AnalysisService`), `signal_normalizer.py`, `statistical_analyzer.py`. Ambitious (evidence normalization + correlations + MWU + temporal/execution/pattern/cumulative relationships). **It does not import, does not compile, and contradicts (A).**

**Confidence: Confirmed by code + reproducible test** — `python -m pytest tests/analysis -q` fails at collection with `ImportError: cannot import name 'SignalSummaryEngine'` (see §10); `py_compile` fails on `statistical_analyzer.py:225`; `evidence.py:15-23` has a missing-comma literal bug.

The storage → detector → signal path that (A) depends on **is real and partially working**, but the analysis entry-points that would paginate/filter it are **broken or inconsistent**, and the mathematical layer that would justify "degradation / regression / root-cause" claims is **either absent (A) or unexecutable (B)**.

**Readiness: NOT READY for RCA; CONDITIONALLY USABLE for descriptive signal summaries only after P0 fixes.** See §12-13.

**Most serious issues (P0):**

1. `analysis/__init__.py:1` imports `SignalSummaryEngine` from `.engine`, but `engine.py:9` defines `AnalysisEngine` — 100% import failure of the package. **Confirmed.**
2. `statistical_analyzer.py:225` — `1.0 2.0 * statistic / denominator` — `SyntaxError`, plus `numpy`/`scipy` not in `pyproject.toml:dependencies`. **Confirmed.**
3. `evidence.py:21-22` — `"error"` `"quantitative"` missing comma → literal `"errorquantitative"`; `EvidenceKind` rejects both intended values. **Confirmed.**
4. `PersistedSignalRepository.list_for_run / list_for_evaluation_run` agent-filter regression — `tests/storage/test_persisted_signals.py::test_list_for_run_applies_agent_filter_before_limit` and `::test_list_for_evaluation_run_applies_agent_filter` fail. Working-tree `engine.py:24-34,59-69` filters in Python after paginating, which hides the same bug. **Confirmed by failing test.**
5. `service.py:3,17,44` vs `engine.py` contract split — `AnalysisService` expects `SignalSummaryEngine.analyze(page, agent_id=)` returning `AnalysisResult` with mutable `.limitations: list`, while tracked `analyzer.py:10` returns `list[AnalysisFinding]` and untracked `engine.py` returns `AnalysisContext`. Neither satisfies the other. **Confirmed.**

Smallest useful next step toward RCA: **freeze one contract** — keep (A) `SignalSummaryAnalyzer + AnalysisContext + AnalysisFinding` as the only public API, delete or gate (B) until it compiles and has repository support, fix the agent-filter-before-limit bug, then add **one** version-to-version MWU + rank-biserial + CI comparison backed by `EvaluationResult.score` (not raw signal counts).

<!--PART2-->
## Scope and Methodology
Scope and Methodology
Tracked files (`git ls-files | Select-String analysis`) are only `analysis/__init__.py, analyzer.py, context.py, findings.py` in HEAD `04d7525`. The other 7 `analysis/*.py` plus `tests/analysis/*`, `run_lifecycle.py`, `migrations/*` are untracked (`??`) — evaluated as proposals, not committed behavior.
Read fully: all 11 `analysis/*.py`, storage signal paths (`models.py` PersistedSignal L271-314, Run L77-134, Event L135-208; `repositories.py` PersistedSignalRepository L745-890, EventRepository L475-610), monitoring signals/detectors/adapters/orchestration/persistence, `monitoring_integration.py`, `run_lifecycle.py`, `schemas/events/*`, ingestion router/validator/handler, `pyproject.toml`, empty `statistics/*` and `root_cause/*`.
Ran: `pytest tests/analysis` (collection ImportError), `test_persisted_signals.py` (2 agent-filter FAILs), `test_run_completion_f.py` (12 passed), `test_validator.py + test_event_schema.py` (25 passed), `test_models_and_tables.py + test_repositories.py` (45 passed); `py_compile` per analysis file; numpy/scipy presence check. No source modified.

## Current Architecture and Data Flow
Current Architecture and Data Flow
```mermaid
flowchart TB
  EVT[Agent events - 26 types] --> VAL[event_validator - envelope+typed payload]
  VAL --> ROUTE[event_router - EXPECTED_ROUTES]
  ROUTE --> HAND[handler.py - 5 handlers persist]
  HAND --> EVTAB[(events table)]
  EVTAB --> MON[monitoring_integration - load_run_context]
  MON --> DET[detectors - pattern/confidence]
  DET --> SIG[Signal pydantic - 6 types]
  SIG --> PERS[persistence signal_to_record + dedupe]
  PERS --> PSTAB[(persisted_signals)]
  PSTAB --> CTXA[AnalysisContext run/eval/window]
  CTXA --> SUMA[SignalSummaryAnalyzer - Counter]
  SUMA --> FINDA[AnalysisFinding signal_summary]
## Module-by-Module Review
Module-by-Module Review
### 3.1 analysis/__init__.py (L1): `from .engine import SignalSummaryEngine` — BROKEN, `engine.py:9` defines `AnalysisEngine`. Package unimportable. Confirmed.
### 3.2 analyzer.py `SignalSummaryAnalyzer.analyze(context)` (82 lines): Counter by type + detector, min/max detected_at, dedupe via dict.fromkeys, limitations for empty/dupes/missing-ts, returns `AnalysisFinding(signal_summary/info/confidence=None/uuid/now-utc)`. No mutation. Correct counting; `findings.py` enum variants trend/regression/anomaly/correlation/root_cause_hypothesis have no producer (dead, misleading).
### 3.3 context.py `AnalysisContext` (131 lines): extra=forbid, run/eval/window mutual exclusion, agent+scope match, window `[start,end)` containment. Good isolation. Gaps: no tz-aware check on signal detected_at (mixed naive/aware raises TypeError in min/max); no system/agent-version field — cross-version impossible here.
### 3.4 findings.py `AnalysisFinding` (72 lines): frozen, non-summary requires evidence + unique IDs, confidence 0..1. Gap: observed_at has no tz validator (unlike Signal/AIEvent).
### 3.5 engine.py `AnalysisEngine` (187 lines): build_run/eval call `list_for_run(run_id,limit,offset)` WITHOUT agent_id then filter in Python post-pagination (L30-34,65-69) — wrong page + spurious limit flags; window path passes agent_id correctly. `count>=limit` off-by-one noisy. No total count, no txn boundary.
### 3.6 service.py `AnalysisService` (54 lines): expects `SignalSummaryEngine.analyze(page,agent_id)->AnalysisResult` mutable limitations (L44, L49-52); neither engine satisfies (list[Finding] vs Context). limit 1..10000 contradicts engine unbounded. Unusable.
### 3.7 models.py `AnalysisResult` (22 lines): evidence list mutable non-unique, no version/method/confidence — insufficient for RCA.
### 3.8 signal_normalizer.py (101 lines): stringifies IDs, shallow dict copy (nested shared — use deepcopy/doc), attributes = payload minus reserved keys. Fine as adapter.
### 3.9 evidence.py (107 lines): L21-22 missing comma `"error"` `"quantitative"` -> literal `"errorquantitative"` — both kinds rejected. Otherwise frozen/extra-forbid/tz-enforced/trace-invariant/unique-IDs good. No builder from Signal/Event exists; `metric_name/metric_value/aggregation==time_series/pattern_signature` conventions assumed downstream have no producer.
### 3.10 statistical_analyzer.py (244 lines): imports numpy+scipy NOT in pyproject dependencies (prod break); L225 `1.0 2.0 * statistic/denominator` SyntaxError (intended rank-biserial `1-2U/n1n2`). Otherwise careful: rejects bool/non-Real/non-finite, min sizes 1/3/1+1, spearman default, 2-distinct-values, MWU two-sided auto, alpha in (0,1), significance==p<alpha validator, honest limitations. No multi-test/CI/power.
### 3.11 relationship_analyzer.py (349 lines): deterministic sort + `type:sorted-ids`. temporal same-agent+run 0<gap<=300s all-pairs O(n2) unbounded; execution_parent (trace,span) lookup directional but no producer sets trace fields (always empty); repeated_pattern needs attributes[pattern_signature]+2 runs but no producer (always empty); cumulative needs aggregation==time_series + strict increase, no units/direction (flags good increases as cumulative), limitations honest.

## Confirmed Bugs and Compatibility Risks
- BUG1 Critical `__init__.py:1` vs `engine.py:9`: ImportError, whole package unimportable. Fix: export one contract.
- BUG2 Critical `statistical_analyzer.py:225` SyntaxError + numpy/scipy missing from pyproject deps. Fix formula to `1.0 - (2.0*statistic)/denominator`, add deps or pure-python fallback.
- BUG3 High `evidence.py:21-22` missing comma -> `errorquantitative`. Fix comma + enum test.
- BUG4 High `PersistedSignalRepository.list_for_run/list_for_evaluation_run` agent filter applied post-limit (failing tests `test_list_for_run_applies_agent_filter_before_limit`, `test_list_for_evaluation_run_applies_agent_filter`); `engine.py` repeats pattern. Fix: SQL WHERE agent_id before LIMIT + ORDER BY detected_at,signal_id + total count.
- BUG5 High `service.py:3,17,44` contract split (expects SignalSummaryEngine->AnalysisResult mutable; gets list[Finding]/Context). Freeze one engine.
- BUG6 Medium `context.py` no tz check -> min/max TypeError on mixed naive/aware. Require tz-aware.
- BUG7 Medium `findings.py:51` observed_at naive allowed. Add tz validator.
- BUG8 Medium `relationship_analyzer.py:105` O(n2) unbounded + cumulative no direction. Cap input, require metric_direction.
- BUG9 Medium `PersistedSignal` lacks agent_version/system_version/trace_id — version isolation requires unjoined runs table. Add cols or documented join + list_for_agent_version.
- BUG10 Low normalizer shallow copy shares nested payload. Deepcopy/doc.
- BUG11 Low `count>=limit` flags exact-limit. Use limit+1 over-fetch.
- BUG12 Info dead finding-type enum variants imply RCA. Remove/gate.
Verified correct (non-bugs): tz on Signal/Event/AIEvent; scope validators; dedupe excludes signal_id/detected_at; ON CONFLICT inserts; run-claim lease; execute_detector agent check; drift empty-guard.
## Mathematical and Statistical Validity
### 5.1 Descriptive (n/mean/median/sd-or-None/min/max/p95): answers center/spread; needs comparable units (not threaded); mean on skewed latency needs median/p95 context; O(n log n). Sound.
### 5.2 Correlation (spearman default, n>=3, 2 distinct): answers monotonic/linear association; needs paired+independent obs (no pairing key exists); pearson linearity/normality unchecked; time autocorrelation inflates sig; n=3 power ~nil (allowed, no warning). Implementation defensible, application indefensible without pairing. Non-causation label correct.
### 5.3 MWU two-sided + medians + rank-biserial: right test for non-normal scores (version A vs B EvaluationResult.score if independent); wrong for repeated/paired data. Effect `1-2U/n1n2` standard but broken syntax; median_difference=second-first vs effect first-positive sign mismatch (document). Ties via auto ok. n=1+1 trivial p=1 no warning. No multi-test correction. Right test, blocked + needs plumbing.
### 5.4 Missing/outlier/skew: rejects non-finite/bool (bool exclusion correct as bool is Real); no silent trim/impute (good); no skew check.
### 5.5 Time-series/baselines: none. Cumulative treats rows as independent trend without lag/seasonality/baseline; anomaly single-threshold no variance. Do not call cumulative degradation without direction+CI.
### 5.6 Practical vs statistical: limitations state significance!=importance (good) but no CI for median diff, no min-meaningful-effect — cannot separate trivial from meaningful.

## Event and Run Utilization Matrix
Envelope `schemas/events/common.py:8-42` 26 EventTypes; `AIEvent:45-75` requires event_id/schema_version/system_id/system_version/run_id/tz-timestamp. `event_validator.py:66-93` + `event_router.py:50-81` + `handler.py:28-137` cover all 26; 25 contract tests passed. No run_started/finished — Run.status covers it; do not add. Producers `producers/*/producer.py` empty (0 bytes) — emitted set Unverified.
| EventType | Persisted? | Consumed? | Verdict |
|---|---|---|---|
| tool_called/tool_completed | Yes | Yes via build_run_records:103-140 -> pattern input | Properly consumed, useful. Confirmed |
| model_called/model_output/retrieval_*/answer_generated/error/recovery_* | Yes | No (eval target_builder only, not monitoring/analysis) | Persisted but unutilized by analysis. Confirmed |
| context_assembled/resource_used/updated | Yes | No | Never consumed. Confirmed |
| evaluation_completed | Yes | Eval service only | Partially utilized. Strongly supported |
| policy_checked/allowed/blocked/behavior_rule_triggered/human_review_* | Yes | No | Never consumed. Confirmed |
| agent_action/verification_attempted/counter_evidence_requested/answer_revised/abstention/behavior_pattern_detected | Yes | No (pattern_signature/verification conventions have no producer) | Declared but not consumed; missing link. Confirmed |
run_id: AIEvent.run_id required; Event.run_id FK nullable SET NULL (orphans possible); build_run_records:96-99 raises on cross-run mix (good); context.py:101-105 enforces match. sequence_number/parent/causation/correlation/step_id persisted-or-enveloped but never used for ordering/dedupe. Missing events -> DeferredDetector (good, monitoring_integration.py:240-267).

## Relationship Analysis Review
| Type | Min evidence | Rule | Concludes | NOT | FP/FN |
|---|---|---|---|---|---|
| temporal | 2 same-agent same-run 0<gap<=300s | L99-136 all pairs | B after A in 5min | causation | FP chatty-run coincidence; FN cross-run missed |
| execution_parent | child(trace,parent_span)+parent(trace,span) same agent | L138-184 | child under parent (directional) | cause | FN always-empty today (no trace fields set). Unverified |
| dependency | — | NONE | — | — | Missing; needs Lineage/ContextResource join |
| repeated_pattern | 2 distinct run_ids same (agent,pattern_signature) | L186-236 | recurred | shared cause | FN always-empty (no signature producer) |
| cumulative | 3 ordered numeric metric_value aggregation==time_series strictly increasing | L238-318 | monotone increase | degradation/significance | FP success-rate flagged; FN noisy trends missed |
IDs deterministic type:sorted-ids; honest non-causation limitations; O(n2) temporal needs cap; component_id None groups cross-component (FP risk).

## Future RCA Architecture
Stages fit existing modules; no graph DB/causal ML until justified:
1 Signal collection (exists; gate: fix BUG4 + version/trace cols). 2 Evidence normalization (fix comma, write build_evidence with metric/pattern/version conventions; merge normalizer). 3 Candidate generation (keep 4 relationships as candidates; add dependency via lineage). 4 Hypothesis ranking (MWU+rank-biserial+CI on EvaluationResult.score, coverage, temporal consistency, alt-penalty). 5 Evidence gathering (get_evidence(run_id): events+signals+eval; counter-evidence query). 6 Validation (survive counter-evidence + alt hypothesis -> supported/refuted/unknown). 7 RCA result (RootCauseAnalysis stub exists-empty; finding + confidence + refs + missing-info). 8 Policy integration (policy/* exists; alert/investigate/human-review; never auto-act on correlation).
Ladder to enforce in types: ObservedFact -> StatisticalAssociation (r+CI, no causal verb) -> CandidateExplanation (relationship+precedence) -> SupportedHypothesis (survives counter-evidence) -> VerifiedCause (interventional/replication; likely out of scope, label honestly).


## Additional Mathematical and Analytical Opportunities
- M1 P0 version MWU+rank-biserial+Hodges-Lehmann CI on EvaluationResult.score (analysis). First defensible regression claim. Needs independent runs.
- M2 P1 Holm/BH across types/versions (analysis). Controls FPR when scanning 6 signal types.
- M3 P1 Wilson/Newcombe pass-rate CI + bootstrap p95 CI (analysis; bootstrap stub empty). Honest dashboards.
- M4 P2 CUSUM/Bayesian changepoint on windowed means (detector streams, analysis confirms). Earlier escalation than strict-monotone.
- M5 P0 metric_direction registry higher_is_better (config/storage). Kills cumulative FP class. Cheap.
- M6 P1 coverage/quality score per finding (analysis). Lets policy threshold on evidence strength.
- M7 P2 counter-evidence + alt-hypothesis schema slots (future RCA). Forces falsification.
- M8 P2 stable pattern_signature = type+payload-hash producer (detector). Makes repeated-pattern non-vacuous.
Ownership: detectors=streaming thresholds; analysis=batch inference; RCA=ranking; policy=action; storage=version joins. No overlaps.

## Test Results and Coverage Gaps
- tests/analysis: ERROR collection ImportError SignalSummaryEngine (contract nonexistent; also expects tz-normalization + excluded_signals no engine implements). Confirmed.
- test_persisted_signals.py: 2 FAILED agent-filter-before-limit + eval-filter; FK tests fail per lastfailed cache. Confirmed regression.
- test_run_completion_f.py: 12 passed (run->detector->persist + deferred correct).
- test_validator.py + test_event_schema.py: 25 passed (26-type contracts hold).
- test_models_and_tables.py + test_repositories.py: 45 passed (core stable pre-filter).
Gaps (untested): analysis<->storage pagination/filter; empty/malformed/dupe/stale inputs; Kafka dupe->dedupe E2E; cross-agent/version isolation at analysis; relationship veracity; MWU/correlation edges (n=1/ties/constant/NaN/bool); RCA traceability (correctly absent).

## Prioritized Remediation Roadmap
P0-1: one export contract; gate/delete untracked until compilable (BUG1/5). P0-2: fix L225 + numpy/scipy deps + U unit test (BUG2). P0-3: agent_id into SQL before LIMIT + ORDER BY + total count (BUG4). P0-4: metric_direction + tz-everywhere (BUG6/7, M5). P1-1: evidence comma + build_evidence + one signature producer (BUG3, M8). P1-2: M1+M2+M3 single version-comparison API. P1-3: pagination/isolation/empty/dupe/tz integration tests. P2: M4, M6, M7, lineage dependency. P3: sequential monitoring only after M1-M3 stable.

## Final Assessment and Readiness Rating
No numeric score — justification: tracked (A) is sound counting with good scope hygiene but no math beyond Counter; cannot support degradation/regression/cause language. Staged (B) has right vocabulary but is unexecutable, contract-split, with repo/evidence gaps that would contaminate agents and emit vacuous relationships if enabled.
- Coherent with platform? (A) yes for per-agent counting; (B) no. Partially.
- Uses event/run info effectively? No — only PersistedSignal aggregates; 24/26 types + sequence/parent/trace + version joins unused. Confirmed gap.
- Math defensible? (A) no inferential claims (vacuously safe). (B) textbook-correct isolated but blocked + inapplicable without pairing/independence/version plumbing. Not end-to-end.
- Fix first? P0-1..P0-4 (§11).
- Smallest RCA step? Freeze (A), fix filter + direction, ship one version MWU+effect+CI on eval scores with counter-evidence field. No graph DB / causal ML / new event types.
---
*Audit-only; no source/config/schema/test/dependency modified. statistics/* and root_cause/* are 0-byte stubs — reported absent, not designed.*

