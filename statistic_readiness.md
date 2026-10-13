# Statistical Analysis — Implementation-Readiness Review

**Baseline:** `statistic.md` (112 lines, audit 2026-10-13, commit `04d7525`, branch `main`)
**Date (UTC):** 2026-10-13
**Mode:** Review + file-by-file change plan only. No source modified, no files reset, cleaned, overwritten, or deleted. New file `statistic_readiness.md` only (`Test-Path` was `False` before creation). `statistic.md` and all pre-existing modified/untracked files preserved.
**Scope:** Resolve competing analysis contracts, verify statistical helpers + tests, smallest safe sequence to a tested statistical foundation on actual `EvaluationResult` rows, `PersistedSignal` kept as separate evidence source. No RCA, no CI/CD gates implemented.

## 1. Worktree + Git status (verified this turn)

- `git status --short --branch` → `## main` + **26 modified tracked files** (1444 insertions, 99 deletions — identical to `statistic.md §0`) + `?? statistic.md` + same untracked set: `src/ai_eval_platform/analysis/engine.py|evidence.py|models.py|relationship_analyzer.py|service.py|signal_normalizer.py|statistical_analyzer.py`, `tests/analysis/` (7 files), `tests/storage/test_analysis_service.py`, probes `_probe_*.py|_r3_*.py`, docs, `migrations/`.
- `git diff --name-only HEAD` → `pyproject.toml`, `analysis/__init__.py|analyzer.py|context.py|findings.py`, `api/*`, `evaluation/engines/*`, `ingestion/retry_consumer.py`, `monitoring/*`, `monitoring_integration.py`, `storage/models.py|repositories.py`, 4 test files. All pre-existing user work — preserved, none touched.
- `git show HEAD:pyproject.toml` → deps `pydantic|SQLAlchemy|psycopg` only. Worktree `pyproject.toml:19,24-25` adds `fastapi|numpy|scipy` (uncommitted).
- Live imports OK: `SignalSummaryEngine,AnalysisResult,AnalysisService`, `StatisticalAnalyzer`, `AnalysisEngine`, `SignalSummaryAnalyzer` all import. `py_compile` on all 10 `analysis/*.py` → exit `0`.
- Live env: `Python 3.12.0`, `numpy 2.5.1`, `scipy 1.18.0` importable in worktree.
- Live collection (read-only, `-p no:cacheprovider`): `tests/analysis` → **71 collected, 0 errors**; `tests/storage/test_analysis_service.py` → **6 collected, 0 errors**. Resolves `docs/audit_report.md:10` (8× `ModuleNotFoundError ...engine`) **in worktree only** — HEAD without untracked `engine.py` would still fail (Blocker B1).

## 2. Competing contracts — resolution (no edits this turn)
- A. Summary→Result: `analysis/engine.py:11-12 SignalSummaryEngine.analyze->AnalysisResult` + `models.py:7-20` + `service.py:19-56` + `__init__.py:1-9`. PUBLIC, tested. Keep.
- B. Context→Findings: `analysis/analyzer.py:9-82 SignalSummaryAnalyzer->list[AnalysisFinding]` + `context.py` + `findings.py`. Competing, no prod caller. Demote to legacy via docstring only; do not delete, do not export.
- C. Session→Context: `analysis/engine.py:99-271 AnalysisEngine.build_*_context->AnalysisContext`. SQL agent filter + ordering + pagination. Keep.
- D. Pure stats: `analysis/statistical_analyzer.py:72-244`. Keep pure, zero prod callers.
Decision: freeze A public, C builder, D stats. B legacy/internal.
## 3. Statistical helpers + tests — verified
- `statistical_analyzer.py` 244 lines: describe (ddof=1, n==1→None, bool/non-finite rejected), correlate (spearman default, n>=3, 2-distinct), MWU two-sided + rb=2U/n1n2-1 Kerby + diff=second-first. Correct; no CI/power/multi-test/pairing.
- Tests (6): n=1 None, ddof 1.2909, bool/nan/inf reject, constant reject, U==9 + rb==1 + diff==-9, Kerby 3-case. All collect.
- Out of scope (untouched): evidence (no builder), relationship (O(n2), no producers), normalizer (shallow copy).

## 4. Storage contracts to preserve (do not touch)
- `PersistedSignal models.py:299-333`: 12 indexes verified. Separate evidence source — no scores, no stats writes.
- `EvaluationResult models.py:425-477`: 9 indexes verified. Primary stats input. 3 partial uniques + CHECK.
- `EvaluationRun`, `Analysis family` (empty sinks). Repos flush-only: signals SQL agent filter; results upsert_many + list_for_run; runs start/finalize. Preserve exactly.
- `evaluation/service.py` means-only + upsert flow. Do not alter. `conftest.py:seeded_evaluation_run` reusable.
## 5. Blockers
- B1 Install/collection split (P0): numpy/scipy importable here, absent from HEAD deps. HEAD install cannot import stats or run its tests; HEAD lacks engine.py (8 errors in docs/audit_report.md:10). Worktree passes only because files are present-but-untracked. `.pytest_cache/lastfailed` stale (lists moved tests) — do not trust cache. Mitigation: commit pyproject deps first + import guard.
- B2 Missing validation (P0): no CI (Wilson/Newcombe/bootstrap/HL), no power/min-n warn (n=1+1→p=1 silent), no Holm/BH, no pairing key, ties p=NaN→ArithmeticError untested, diff-vs-rb sign opposition, p95-linear unpinned, None-score policy missing, truncation bias, zero tests on real EvaluationResult rows. Mitigation: S4+S5 pure helpers + limitations; gates/RCA deferred.
- B3 Contract/data risks (P1): dual shapes A vs B, mutable vs frozen, dict|object + naive→UTC vs strict PersistedSignal, service BOM + flush side-effect, list_for_run no metric filter + case_id ordering + None scores, no agent_version on signals (join via runs/eval-runs), shallow copy, O(n2), free-string metric_name, no EvaluationMetric/gate tables. Mitigation: guards in S4; signals read-only for stats (counts/context only), scores only from EvaluationResult.

## 6. File-by-file change plan — smallest safe sequence (plan only, stop after S5)
- S0 Verify (no edits): status, py_compile, collect-only, imports. Done above; re-run before each step.
- S1 `pyproject.toml` (tracked-modified): keep numpy>=1.26/scipy>=1.11 (:24-25) + comment. Risk: env drift (live 2.5/1.18 vs floor). Test: import + clean-venv install post-merge. Why first: unblocks clean collection (B1).
- S2 `analysis/__init__.py` (9 lines): ADD StatisticalAnalyzer + result types to __all__ (additive, no removal). Do not export legacy Analyzer/AnalysisEngine-as-public. Test: 71+6 still collect + new test_package_exports asserts exact __all__.
- S3 `analysis/analyzer.py` (82 lines): docstring-only legacy note. No logic. Test: existing green.
- S4 NEW `analysis/comparison.py` (~120 lines, no schema): compare_evaluation_runs(session, baseline_run_id, candidate_run_id, metric_name, alpha=0.05, min_n=5) → frozen VersionComparison. Steps: list_for_run ×2 (no repo change) → filter metric + finite scores (bool rejected, None skipped+counted; zero scores → insufficient_scores + significant False, never coerce passed) → case-align for descriptives, MWU on independent vectors (document assumption + version-lineage warn) → describe ×2 + compare + CI (S5 or ci=None+limitation) → truncation guard → return (ids, n1/n2, medians, diff, U, p, alpha, significant, rb, ci, limitations) with significance validator. Must NOT import PersistedSignal/Normalizer/Relationship (enforce via import test). Risks mitigated via limitations (paired_unsupported, same_version, scores_missing, small_n, free_string).
- S5 NEW `analysis/calibration.py` (~80 lines, pure): wilson_interval, hodges_lehmann_median_diff (or seeded bootstrap), holm_adjust. math/numpy only. Test vs hand values + seed repro.
- S6 Tests (additive, no existing edits): test_comparison.py (sqlite seeded_evaluation_run + 2nd run: oracle vs scipy, evidence==Result IDs not signal IDs, None/bool handling, min_n→False+limitation, cross-run isolation, upsert-replay idempotent); test_calibration.py (Wilson/HL/Holm/seed); test_package_contracts.py (__all__ exact, legacy importable, no PersistedSignal import in comparison). Existing 71+6 unmodified must pass.
- Deferred: Differential/Drift/RootCause writers, EvaluationMetric, gate.py, workflows, statistics/* backfill, evidence builder, relationship producers, any models.py/repositories.py schema change, any evaluation/service.py change, any signal-stats input.

## 7. Acceptance for foundation step
- S1+S2+S4+S5+S6 merged, S3 docstring applied; status shows only those files + new tests.
- pytest analysis + analysis_service + repositories green (replaces stale lastfailed).
- comparison on seeded EvaluationResult rows matches scipy oracle, reports limitations, never reads PersistedSignal.
- No storage/evaluation signatures changed; no RCA/gate code; all prior files preserved.
