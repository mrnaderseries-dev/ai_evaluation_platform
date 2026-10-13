# Statistical Analysis & ML Analysis Layer — Evidence-Based Audit Report

**Repo:** `ai-evaluation-platform` (`c:\Users\user\ai-evaluation-platform`, branch `main`, commit `04d7525`)
**Date (UTC):** 2026-10-13
**Mode:** Audit + reporting only. No source modified, no files deleted, no worktree reset, no user changes overwritten. New file `statistic.md` only (did not exist; verified `Test-Path statistic.md == False`).
**Basis:** Current worktree **including** pre-existing modified + untracked files. Findings distinguish `HEAD` (committed) vs worktree (modified) vs untracked (uncommitted new files). Nothing inferred from filenames/docs alone — every claim below cites a file read, `git` output, or code search executed during this audit.
**Prior context:** `docs/audit_report.md` (2026-10-10) recorded 8 collection errors `ModuleNotFoundError: No module named 'ai_eval_platform.analysis.engine'` because `analysis/__init__.py` imported `SignalSummaryEngine` from missing `engine.py`. `statistical_analysis_layer.md` (untracked, pre-existing) describes similar bugs; this report re-verifies from scratch and does not trust it.

## 0. Repository state (must read first)

- `git --no-pager status --short --branch` → `## main` + **26 modified tracked files** + large untracked set (probes `_probe_*.py`, `_r3_*.py`, `analysis_3.md`, `statistical_analysis_layer.md`, `migrations/0001_*`, `src/.../analysis/engine.py|evidence.py|models.py|relationship_analyzer.py|service.py|signal_normalizer.py|statistical_analyzer.py`, `tests/analysis/*`, `tests/storage/test_persisted_signals.py`, etc.).
- `git --no-pager diff --stat HEAD` → 1444 insertions, 99 deletions across 26 files (largest: `storage/repositories.py` +391, `monitoring_integration.py` +238).
- `git --no-pager ls-files` (tracked, committed) contains: `src/ai_eval_platform/analysis/__init__.py|analyzer.py|context.py|findings.py` (placeholders in HEAD), `src/ai_eval_platform/statistics/__init__.py|bootstrap.py|confidence.py|effect_size.py|hypothesis.py`, `src/ai_eval_platform/storage/models.py|repositories.py`, `src/ai_eval_platform/evaluation/*`, `src/ai_eval_platform/monitoring/*`, `src/ai_eval_platform/policy/*` (empty), `src/ai_eval_platform/deployment/*` (empty), `.github/workflows/ci.yml|deploy.yml|evaluation.yml`.
- `git --no-pager ls-files --others --exclude-standard` confirms the entire statistical helper suite is **untracked**: `src/ai_eval_platform/analysis/engine.py`, `evidence.py`, `models.py`, `relationship_analyzer.py`, `service.py`, `signal_normalizer.py`, `statistical_analyzer.py`, plus `tests/analysis/test_*.py` (7 files) and `tests/storage/test_analysis_service.py`.
- `git --no-pager show HEAD:pyproject.toml` (committed) dependencies = `pydantic`, `SQLAlchemy`, `psycopg[binary]` only. Worktree `pyproject.toml:19,24-25` adds `fastapi>=0.110`, `numpy>=1.26`, `scipy>=1.11` (read verified). That addition is uncommitted.
- `git --no-pager show HEAD:src/ai_eval_platform/analysis/__init__.py|analyzer.py|context.py|findings.py` → empty output (empty placeholders in HEAD). Worktree `analysis/__init__.py:1-9` now exports `SignalSummaryEngine, AnalysisResult, AnalysisService`. `analyzer.py:9-82`, `context.py:1-131`, `findings.py:1-72` now contain real code (read verified) but are **uncommitted modifications**.
- `git --no-pager show HEAD:src/ai_eval_platform/statistics/bootstrap.py | Measure-Object` → 0 lines / 0 chars; `confidence.py`, `effect_size.py`, `hypothesis.py` identical (read returns `""`). `__init__.py` is only `"""Statistics: hypothesis tests, bootstrap, confidence, effect size."""`. Verified both via `read_files` and `HEAD` show.
- `Test-Path statistic.md == False`; `statistical_analysis_layer.md == True` (pre-existing untracked note, not overwritten).

**Interpretation:** There is **no committed, importable, tested Statistical/ML layer**. What looks like one is uncommitted work-in-progress plus empty placeholders. Any plan must not assume untracked files are merged, and must preserve committed contracts.
## 1. Verified inventory — what exists vs what does not (all paths absolute from repo root)

### 1.1 `src/ai_eval_platform/statistics/` — placeholder package (HEAD committed, empty)
- `src/ai_eval_platform/statistics/__init__.py:1` — `"""Statistics: hypothesis tests, bootstrap, confidence, effect size."""` only. Verified via `read_files` + `git show HEAD:...`.
- `src/ai_eval_platform/statistics/bootstrap.py`, `confidence.py`, `effect_size.py`, `hypothesis.py` — 0 bytes each in HEAD and worktree. Verified `read_files==""`, `(Get-Item).Length==0`, `HEAD Measure-Object 0 lines`. No implementation, no tests import them (`search_codebase sklearn|statsmodels` → none; no `from ai_eval_platform.statistics` hits).
- Verdict: **Missing**. Filename suggests a layer; implementation absent. Do not cite as capability.

### 1.2 `src/ai_eval_platform/analysis/statistical_analyzer.py` — real helper, untracked, no production wiring
- Path untracked (`git ls-files --others`). 244 lines, read fully in two ranges.
- `StatisticalResult(frozen,extra=forbid)` base; `DescriptiveStatistics(sample_size>=1,mean,median,std|None,min,max,p95)`; `CorrelationResult(method pearson|spearman,n>=3,coeff in [-1,1],p in [0,1],limitations)`; `GroupComparisonResult(mann_whitney_u,n1>=1,n2>=1,medians,median_difference,U,p,alpha in (0,1),significant==p<alpha,rank_biserial in [-1,1],limitations)` with `validate_significance_decision`.
- `StatisticalAnalyzer.describe()` (`:117-151`): `np.mean|median|min|max|percentile(95,linear)|std(ddof=1)`; n==1 → std None; `_validate_values(:76-106)` rejects bool/non-Real/non-finite, enforces min size; `_finite_result(:108-115)` raises `ArithmeticError` on overflow.
- `correlate(x,y,method=spearman)` (`:153-189`): `scipy.stats.spearmanr|pearsonr`, equal-length, 2-distinct-values, `np.errstate(over,invalid ignore)` + finite gate.
- `compare_independent_groups(first,second,alpha=0.05)` (`:191-244`): `mannwhitneyu(two-sided,auto)`, medians via `np.median`, `p,U` finite-gated, effect `2.0*statistic/denominator-1.0` (`:223-227`, Kerby first-positive, comment `:52` documents). `median_difference=second-first` — opposite sign to effect by design.
- Deps `numpy/scipy (:5-7)` — present only in uncommitted `pyproject.toml:24-25`; HEAD has neither. Clean HEAD install cannot import this file.
- Callers: tests + `_r3_probe_*.py` + `_probe_*.py` only. `search_codebase StatisticalAnalyzer` → no `src/` production caller, no API/monitoring/evaluation/policy/deployment import.
- Verdict: **Exists as uncommitted pure helper, incomplete as layer** (no CI, power, multi-test, pairing, persistence, or gate wiring).
### 1.3 `src/ai_eval_platform/analysis/engine.py` + `service.py` + `models.py` — untracked, counts only
- `engine.py` untracked (~271 lines, read `:1-150` + `:76-150` overlap): `SignalSummaryEngine.analyze(signals,agent_id)` filters `agent_id==`, requires non-empty str `signal_id`, groups `signal_type|detector→unknown`, window min/max aware `detected_at` (naive→UTC), `metrics={signal_count,counts_by_type,counts_by_detector,excluded_signals}`, `evidence_signal_ids` input order, limitations for empty/agent_mismatch/missing_id/missing_ts. No stats, no EvaluationResult. `AnalysisEngine(session)` builders (`:99-271`) page `PersistedSignalRepository` with SQL `WHERE agent_id` + `ORDER BY detected_at,signal_id` + `LIMIT|OFFSET`, validate into `AnalysisContext` with pagination limitations. `__init__.py:1-9` (modified-tracked) exports `SignalSummaryEngine,AnalysisResult,AnalysisService` — resolves `docs/audit_report.md:10` ModuleNotFoundError in worktree only.
- `service.py` untracked (56 lines, read fully): `AnalysisService(session,engine?).summarize_agent(agent_id,limit=1000,offset=0)` validates 1<=limit<=10000, offset>=0, non-blank agent, `session.flush()`, `list_for_agent(limit+1)` + truncation limit, delegates to engine. Returns mutable `AnalysisResult.limitations:list`.
- `models.py` untracked (22 lines): `AnalysisResult(extra=forbid)` with `analysis_id|agent_id`, defaults `signal_summary/1.0.0`, `evidence_signal_ids:list`, `metrics:dict`, `sample_size>=0`, `window_start|end`, `limitations:list`.
- Verdict: **Untracked summary path, not statistical inference.** No EvaluationResult input, no CI/p-value, no gate verdict.

### 1.4 `analyzer.py` + `context.py` + `findings.py` — tracked-modified, incompatible contract
- `analyzer.py:9-82` (modified): `SignalSummaryAnalyzer.analyze(context:AnalysisContext)->list[AnalysisFinding]` via Counter, dedupes IDs, `metrics={total,unique,duplicate,type_counts,detector_counts,with|without_ts,scope}`, `signal_summary/info/confidence=None`. Returns list[Finding], NOT AnalysisResult — incompatible with `SignalSummaryEngine` (AnalysisResult) and `AnalysisEngine` (AnalysisContext). No production caller.
- `context.py:1-131` (modified): `AnalysisContext(agent_id,scope run|evaluation|window,signals:tuple[PersistedSignal],run|evaluation_run|window bounds,limitations)` validators pin scope exclusivity + per-signal agent/scope membership (window `start<=detected_at<end`).
- `findings.py:1-72` (modified): `AnalysisFinding(frozen,extra=forbid)` types `{signal_summary,trend,regression,anomaly,correlation,root_cause_hypothesis}`, severities `{info,low,medium,high,critical}`, non-summary requires non-empty unique evidence, `confidence in [0,1]|None`.
- Verdict: **Two competing summary contracts exist** (engine→AnalysisResult vs analyzer→list[Finding]). Must freeze one; see §4.

### 1.5 `evidence.py` + `relationship_analyzer.py` + `signal_normalizer.py` — untracked primitives, no producer
- `evidence.py` (107 lines): `EvidenceRecord(frozen,extra=forbid,non-blank IDs,tz-aware observed_at,trace chain,attributes copy)` + `EvidenceKind` 7 values (comma present — prior bug fixed in worktree) + `EvidenceCollection(unique IDs)`. No `from Signal|Event` builder.
- `relationship_analyzer.py` (349 lines, read `:1-122` + `:242-349`): `RelationshipAnalyzer(max_gap=300s).analyze()->tuple[EvidenceRelationship]` deterministic sort + `type:sorted-ids`. `temporal` same-agent+run 0<gap<=max all-pairs O(n²); `execution_parent` trace/span; `repeated_pattern` needs `attributes[pattern_signature]`+2 runs; `cumulative` needs `aggregation==time_series`+strictly increasing ≥3 grouped by (agent,component,metric). No producer sets trace/pattern/aggregation today → often empty. No stats (no r/CI/p).
- `signal_normalizer.py` (113 lines): `SignalNormalizer.normalize(PersistedSignal)->NormalizedSignal` stringifies IDs, shallow dict copy (nested shared), `attributes=payload-reserved`, preserves `raw_payload`. Pure adapter.
- Verdict: **Candidate-generation helpers, not analysis results.** Usable for RCA stage 3 only after producers exist.


### 1.6 Storage — committed contracts to preserve
- `storage/models.py:299-333 PersistedSignal` (worktree-added): signal PK, origin, source FKs, scope CHECKs, dedupe uniques, payload JSON. No version/trace cols.
- `models.py:425-476 EvaluationResult` (HEAD): PK + run/eval-run/case FKs, metric/passed/score/latency/token/feedback/metrics; 3 partial uniques + CHECK. No direction table.
- `models.py:405-423 EvaluationRun` (HEAD): dataset/agent-version FKs, status, counts, metrics JSON.
- `models.py:574-620 Analysis family` (HEAD): analyses + 3 subtypes, JSON sinks, no writers.
- `repositories.py:827-948 PersistedSignalRepository` (worktree, agent-filter in SQL + ORDER BY detected_at,signal_id): insert-ignore + list_for_run/eval/agent/window. `EvaluationResultRepository:1170-1310`: upsert_many on natural key + list_for_run. `EvaluationRunRepository:1070-1168`: start/finalize.
### 1.7 Evaluation flow — committed, real, no stats consumer
- `evaluation/service.py:43-242 EvaluationService.evaluate_run` builds target (Run + Events + TargetBuilder), runs evaluators, maps via `measurement_to_result`, persists via `upsert_many`, finalizes counts + `metric_summaries{count,passed,failed,mean_score}`. Mean only.
- `domain.py:94-106 EvaluationMeasurement`, `result_mapper.py:37-58 measurement_to_result`, `runner.py:22-85 ERROR mapping`, `engines/embedding_similarity.py cosine`, `engines/groundedness_judge.py clipped [0,1]` — all tracked, none compute CI/test.
- No EvaluationMetric catalog, no gate class. Search `Gate|EvaluationMetric` hits docs only.

### 1.8 Monitoring detectors — threshold rules, not inference
- `detectors/strategies/drift.py:20-31`: abs delta > threshold. `anomaly.py:20-22`: abs dev > threshold. Confidence directional drop. `isolation/analyzer.py:63` hardcodes confidence=0.5.
- `signals/models.py:30-134 Signal` + `signals/types.py:1-106` (7 types, 3 origins incl. reserved ANALYSIS) + `persistence/__init__.py:12-110` semantic dedupe hash — all modified-tracked, real.
- `monitoring_integration.py:217-397` completed-run only; anomaly/drift deferred (no baseline). No eval-run signals.
- Verdict: threshold rules, not ML. Do not relabel.
- Preserve models, CHECKs, uniques, flush-only exactly.
## 2. Data flow — verified connections and gaps
- Signals: Events → run monitoring (completed only) → detectors (pattern+confidence; anomaly/drift deferred) → PersistedSignal → summarize/build_context → counts. No eval-scoped producer; EvaluationCompletedEvent unmapped.
- Evaluation: Run+Events → Target → Evaluators → upsert EvaluationResult → finalize means. No stats reader; no Differential/Drift/RootCause writer.
- Gates/RCA/Policy: tables empty sinks; policy/deployment/API-eval/workflows empty (0B); FaultIsolation is infra-health with hardcoded 0.5. No gate query, no threshold table, no CI gate.

## 3. Tests — observed coverage and gaps
- Untracked analysis tests (7 files): stats 6 (n=1 None, ddof 1.2909, bool/nan/inf reject, constant reject, MWU U 9 + rb 1 + diff -9, Kerby 3-case); engine 2; regressions 6; context builders (verbatim forward + filter-before-pagination); evidence (tz/IDs/trace); relationship (4 rules); normalizer 6; storage service 5 (truncation, empty, evidence==sample).
- Missing: stats on real EvaluationResult rows; CI/power/multi-test; bootstrap/Wilson (files empty); gate/RCA/ML tests; failure paths (n=1+1 trivial p, ties, n=3 nil power, ArithmeticError, bool, paired misuse, multi-test inflation, truncation bias, cross-agent, O(n2), shallow copy, naive ts).
- Suite not re-run here (dirty tree + prior 8 collection errors in docs/audit_report.md:10). Clean-install numpy/scipy remains unverified (HEAD lacks them).

## 4. Proposed architecture (additive only)
- Freeze `SignalSummaryEngine→AnalysisResult` as public; keep Analyzer list[Finding] legacy or behind converter. Keep AnalysisEngine builders.
- Add `analysis/comparison.py`: compare_versions(baseline,candidate,metric) reads EvaluationResult scores, calls StatisticalAnalyzer + CI helpers, returns frozen VersionComparison. Persist optionally into DifferentialAnalysis.statistical_tests JSON — no schema change.
- Add `analysis/calibration.py`: Wilson, Newcombe, bootstrap p95 (seeded), Holm/BH. Fills statistics/ placeholders by delegation.
- Add `analysis/gate.py`: evaluate_gate(comparison, thresholds)→allow|block|review. Thresholds as args, no new table. Never auto-act.
- RCA ladder: Fact→Association (r+CI)→Candidate→Supported→Verified (likely out of scope). Reuse Signal/Normalizer/Relationship + new evidence builder + counter-evidence query → RootCauseAnalysis sink.
## 5. Appropriate methods (assumptions, limits, data)
- Descriptives (exists): comparable units; pair mean with median/p95. Data >=1 finite.
- MWU + rb (exists): independent non-normal A-vs-B scores. Not paired. Needs version lineage. Add HL CI + power warn.
- Spearman/Pearson (exists): paired only — key absent, must join. n=3 power ~nil. Non-causal.
- Needed: Wilson, Newcombe, bootstrap p95 seeded, Holm/BH, Cliff optional.
- ML: none justified yet. Keep tables as registry.
- Gates: block only on pre-reg + n + sig + effect + direction; else review with IDs + limits.
## 6. Plan (additive, preserves contracts)
1. Review untracked helpers before staging; keep HEAD contracts.
2. Commit numpy/scipy + fallback; freeze one summary contract.
3. Add comparison.py, calibration.py, gate.py; no schema change.
4. Evidence builder + lineage; read-only gate wiring; docs.

## 7. Risks
Dual contracts; untracked-not-merged; mutable vs frozen; sign mismatch; shallow copy; O(n2); truncation bias; no signal version; HEAD deps; JSON duplication; 0B policy/deploy/workflows.

## 8. Test strategy
Correctness (scipy oracle, Wilson, seed, Holm); edges (n=1, ties, bool/inf, truncation, cross-agent); integration on sqlite (results→comparison→gate; signals→summary; upsert); missing (CI/power/adjust/gate/RCA-on-rows, paired warn, min_n review, deepcopy).

## 9. Acceptance
statistic.md cites HEAD + paths; status unchanged except new file; verdict explicit; additive plan + methods + gate + tests included.

## 10. Unverified
Full pytest; clean install; live PG/Kafka; migration bootstrap; owner acceptance.
