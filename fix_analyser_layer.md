# Fix Analyser Layer — Engineering Audit and Repair Report

**Audit rounds:** Round 1 (initial defects DEF-001..005) and Round 2 (deep
re-verification: sign-convention experiments, execution-flow reconstruction,
file-by-file map, architecture/contract audit, per-finding status labels).

## Section 1 — Audit Scope

Initial document inspected: `statistical_analysis_layer.md` (full read, 146 lines).

Source files inspected (Round 1) and re-read in full (Round 2):
`src/ai_eval_platform/analysis/__init__.py`, `analyzer.py`, `context.py`,
`findings.py`, `engine.py`, `evidence.py`, `models.py`, `service.py`,
`signal_normalizer.py`, `statistical_analyzer.py` (all lines, including
L100-160 describe, L191-244 compare_independent_groups, L225 effect size),
`relationship_analyzer.py` (all lines), `storage/repositories.py`
L745-866 (PersistedSignalRepository), `storage/models.py` L271-299
(PersistedSignal), `pyproject.toml`.

Tests inspected: `tests/analysis/test_engine.py`,
`tests/analysis/test_analysis_regressions.py`,
`tests/analysis/test_statistical_analyzer.py`,
`tests/storage/test_analysis_service.py`,
`tests/storage/test_persisted_signals.py` L585-701,
`tests/storage/conftest.py`.

Integration points inspected: `AnalysisService.summarize_agent` callers
(none outside tests), `StatisticalAnalyzer` consumers (tests only),
`AnalysisEngine` / `SignalSummaryAnalyzer` / `SignalNormalizer` /
`RelationshipAnalyzer` callers (no production callers found), repository
`list_for_*` callers (`engine.py`, `service.py`).

External references consulted (Round 2): SciPy 1.18 `mannwhitneyu`
documentation (statistic orientation), R `effectsize::rank_biserial`
documentation (Kerby/Cureton sign convention).

Environment: Python 3.12, numpy 2.5.1, scipy 1.18.0. Git state re-checked
Round 2: HEAD = `04d7525` (initial snapshot, single commit); all Round-1
repairs are present as uncommitted working-tree changes (5 modified tracked
files under `analysis/` + `pyproject.toml`; `engine.py`, `evidence.py`,
`models.py`, `relationship_analyzer.py`, `service.py`,
`signal_normalizer.py`, `statistical_analyzer.py`, the analysis tests and
the storage signal tests are untracked additions). Round 1's empty
`git status`/`git diff` output was a transient tooling failure; re-run in
Round 2 produced full output and is the basis for this statement.

Unavailable: none. No production consumer exists for the statistical layer,
so all behavior claims are verified by controlled experiments and tests.


## Section 2 — Investigation Log

### Round 1

| Seq | Finding | Trigger | Inspected / Tested | Learned | Decision |
|-----|---------|---------|-------------------|---------|----------|
| 1 | DEF-001 | Prior report BUG2 + import of statistical_analyzer | Read L223-227; py_compile | `1.0 2.0 * statistic / denominator` was a live SyntaxError blocking every test that imports the package | Fix formula, add regression test |
| 2 | DEF-002 | Prior report BUG1 | Read `__init__.py:1`, `engine.py:9`, `service.py:3`; ran pytest | `SignalSummaryEngine` did not exist; package import failed; 3 test modules errored at collection | Provide the expected engine contract |
| 3 | DEF-003 | Prior report BUG4 | Read `repositories.py` L772-814; tests L585-701 | Repo already supports `agent_id` kwarg + ordering; `engine.py` builders ignored it and filtered post-pagination | Pass agent_id into repository |
| 4 | SUS-001 | MWU direction audit mandate | Read L40-69, L207-244; probe U=9.0 | Round 1 read only the comment; concluded "documented contrast" | Closed NOT_A_DEFECT — **overturned in Round 2** |
| 5 | SUS-002 | n=1 group support | Read L42-43 ge=1; scipy behavior | scipy accepts n=1 (p=1.0); model allows ge=1 | NOT_A_DEFECT, document low power |
| 6 | CON-001 | Service vs engine | Read `service.py`, `models.py`, tests | Service needs `AnalysisResult` with mutable `.limitations`; neither engine satisfied it | Engine must return AnalysisResult |
| 7 | SUS-003 | numpy/scipy missing deps | Read pyproject; `python -c "import numpy, scipy"` | Importable in env, absent from install metadata | Add to dependencies |
| 8 | DEF-005 | Signal test failures | Read tests L585-701; CHECK constraints | Test-data bugs, not repo bugs | Correct the tests |

### Round 2 (deep re-verification)

| Seq | Finding | Trigger | Inspected / Tested | Learned | Decision |
|-----|---------|---------|-------------------|---------|----------|
| 9 | repo state | Empty git output in Round 1 | `git status --porcelain`, `git log`, `git diff --stat` | HEAD is the initial snapshot only; every Round-1 repair lives in the working tree; empty output was a tooling glitch | Document true state in Section 1 |
| 10 | SUS-001 reopen | Mandate: verify sign with controlled samples | 5 controlled experiments vs SciPy + `mannwhitneyu` docs + R `effectsize` docs | SciPy returns U for the **first** sample ("mannwhitneyu always reports the statistic associated with the first sample"); Kerby/effectsize convention: +1 = first sample dominant; implemented `1-2U/(n1n2)` gave **-1.0 when the first group was entirely larger** — inverted vs the L52 comment and Kerby | **CONFIRMED defect; formula changed to `2U/(n1n2)-1`** |
| 11 | sign tests | Mandate: first>second, first<second, ties | `test_rank_biserial_sign_follows_kerby_convention` + scipy cross-check in existing test | first>second → r=+1.0/diff=-9.0; first<second → r=-1.0/diff=+9.0; all-ties → r=0.0/diff=0.0; U equals scipy's U exactly | Test pins orientation for all three cases |
| 12 | edge inputs | Statistical behavior audit | n=1 both sides; constant distinct groups; fully tied groups | n=1 works (U=1.0, p=1.0); constant distinct works (p≈0.047); fully tied → scipy p=NaN → `_finite_result` raises `ArithmeticError` (gate fires as designed) | Documented; gate keeps NaN out of models |
| 13 | flow trace | Mandate: trace end-to-end flow | `service.py`, `engine.py`, builders, repositories, models, tests | Flow reconstructed (FTE §C); AnalysisResult is returned, never persisted; no HTTP entry point exists | Document as-is; no persistence invented |
| 14 | file map | Mandate: file-by-file map | All 11 analysis modules + storage + tests + docs | Responsibilities and unwired status recorded (FTE §B) | Document |
| 15 | architecture | Q-001 resolution mandate | Compared three components' signatures, callers, tests | Roles are separable and non-conflicting; see FTE §D | Documented resolution, no code change |
| 16 | contracts | Mandate: contract audit | Pydantic↔SQLAlchemy↔repo↔service boundaries | Verdicts in FTE §E; one latent risk (window-scope signals) recorded | Documented |

## Section 3 — Confirmed Defects

Finding ID: DEF-001
Title: Rank-biserial line was a SyntaxError blocking statistical import
Severity: Critical
Status: FIXED AND VERIFIED (Round 1); formula superseded by DEF-006 (Round 2)
File and symbol: `statistical_analyzer.py`, `StatisticalAnalyzer.compare_independent_groups`, L225
Evidence: `py_compile` failed before fix with `1.0 2.0 * statistic / denominator`; after fix `compile ok`
Actual behavior (before): module unimportable; every analysis test errored
Expected behavior: module imports; effect size computes a finite r in [-1, 1]
Why it is a defect: violates Python grammar
Potential consequences: no statistical operation executable
Root cause: missing operator (transcription error)
Chosen correction: restored an operator → `1.0 - 2.0 * statistic / denominator`
(algebraically later corrected to `2.0 * statistic / denominator - 1.0` by DEF-006
after Round-2 sign experiments showed the orientation was inverted)
Alternatives considered: deleting the line — rejected, effect size is part of the model contract
Files modified: `statistical_analyzer.py` L225; `pyproject.toml`;
`tests/analysis/test_statistical_analyzer.py` (new)
Tests: `test_mann_whitney_effect_size_direction_matches_u` (U matches scipy,
r=+1.0 after DEF-006, median_difference=-9.0, significance consistency)
Verification: tests/analysis 15 passed; full suite 476 passed, 11 skipped
Remaining limitations: n=1 allowed with trivial power; no multi-test correction (Q-002)

Finding ID: DEF-002
Title: Package exported non-existent SignalSummaryEngine
Severity: Critical
Status: FIXED AND VERIFIED
File and symbol: `analysis/__init__.py:1`, `service.py:3,14,17`, `engine.py:11`
Evidence: pytest collection ImportError before fix; analysis dir green after
Actual behavior (before): `import ai_eval_platform.analysis` raised ImportError
Expected behavior: package imports; engine satisfies service + tests
Why it is a defect: public contract named a missing symbol
Potential consequences: analysis + service layers unusable
Root cause: refactor renamed class without updating exports
Chosen correction: added `SignalSummaryEngine.analyze(signals, agent_id)` returning `AnalysisResult` per tested contract; kept `AnalysisEngine`
Alternatives considered: renaming tests/service — rejected, tests define the intended contract
Files modified: `engine.py`
Tests: existing analysis (8) + service (5) now execute
Verification: tests/analysis 15 passed; service tests green
Remaining limitations: two engines coexist; canonical choice documented in FTE §D (Q-001 resolution)


Finding ID: DEF-003
Title: Context builders filtered by agent after pagination
Severity: High
Status: FIXED AND VERIFIED
File and symbol: `engine.py` `AnalysisEngine.build_run_context`, `build_evaluation_context`
Evidence: prior code called `list_for_run` without `agent_id` then Python-filtered; repo already supported the kwarg (`repositories.py` L782-783, L805-806)
Actual behavior (before): foreign-agent rows consumed the page → empty/wrong results + misleading truncation flags
Expected behavior: agent predicate in SQL before LIMIT/OFFSET
Why it is a defect: breaks pagination correctness and agent isolation
Potential consequences: missing own signals, wrong truncation; cross-agent rows would fail `AnalysisContext` validation and turn a page into an error
Root cause: repository kwarg ignored by caller
Chosen correction: pass `agent_id=agent_id` into both builder calls (now `engine.py` L116, L148)
Alternatives considered: none
Files modified: `engine.py` builders
Tests: `test_list_for_run_applies_agent_filter_before_limit`, `test_list_for_evaluation_run_applies_agent_filter`
Verification: storage signal tests green; full suite 476 passed, 11 skipped
Remaining limitations: truncation stays heuristic (no total count; PROP-003)

Finding ID: DEF-004
Title: numpy/scipy imported but absent from install metadata
Severity: High
Status: FIXED AND VERIFIED
File and symbol: `pyproject.toml` vs `statistical_analyzer.py` L5, L7
Evidence: env has numpy 2.5.1 / scipy 1.18.0; metadata listed only pydantic/SQLAlchemy/psycopg
Actual behavior (before): fresh install breaks on statistical import
Expected behavior: declared deps match imports
Why it is a defect: install contract contradicts code
Potential consequences: production import failure
Root cause: code deps added without metadata update
Chosen correction: added `numpy>=1.26`, `scipy>=1.11` (visible in `git diff pyproject.toml`)
Alternatives considered: pure-python fallback — rejected, scipy exact-U and tie handling are load-bearing
Files modified: `pyproject.toml`
Verification: suite imports cleanly; green suite
Remaining limitations: none known

Finding ID: DEF-005
Title: Signal/analysis test-data bugs contradicted storage invariants
Severity: Medium
Status: FIXED AND VERIFIED
File and symbol: `test_persisted_signals.py:626,689`, `test_analysis_service.py` setup blocks; `models.py:274-280` CHECK constraints
Evidence: test seeded 3 agent-2 rows then asserted agent-2 query == []; eval test used scope `evaluation_run` rejected by CHECK `scope IN ('run','evaluation','window')`; service test built rows without `add_all`
Actual behavior (before): 3 failures from test-data bugs, not repo bugs
Expected behavior: tests honor CHECK + repo semantics + row visibility
Why it is a defect: tests violated the contracts they verify
Potential consequences: false failure signals masking real regressions
Root cause: test setup errors
Chosen correction: assert absent agent-3 == []; scope `evaluation`; `add_all` + flush
Alternatives considered: changing repo to hide present agents — rejected
Files modified: two test files (3 edits)
Verification: storage signals+service tests green (29 passed)


Finding ID: DEF-006 (new in Round 2; supersedes the Round-1 SUS-001 closure)
Title: Rank-biserial sign convention inverted relative to comment, Kerby convention, and median_difference documentation
Severity: High
Status: FIXED AND VERIFIED
File and symbol: `statistical_analyzer.py` L225 (`compare_independent_groups`), comment L52
Evidence (controlled samples, scipy 1.18.0):
- first=[10,11,12], second=[1,2,3] (first entirely larger): scipy U(first)=9.0=n1·n2.
  Before: r = 1 − 2·9/9 = **−1.0**. After: r = 2·9/9 − 1 = **+1.0**.
- first=[1,2,3], second=[10,11,12] (first entirely smaller): U=0.0 → before r=+1.0, after r=−1.0.
- all ties [1,2,3] vs [1,2,3]: U=4.5 → r=0.0 in both formulas.
- SciPy docs: "mannwhitneyu always reports the statistic associated with the first sample."
- R `effectsize::rank_biserial` (Kerby 2014): "Values range from −1 complete dominance of the second sample … to +1 complete dominance of the first sample."
Actual behavior (before): r = −1.0 exactly when the first group was stochastically maximal — contradicting the source comment "Positive means the first group tends to have larger values" (L52). With `median_difference = second − first` (−9.0 when first larger), both fields then carried the same sign, while documentation claimed deliberately opposed conventions.
Expected behavior: positive r ⇔ first group larger (Kerby/effectsize), median_difference stays second−first so the two documented conventions are genuinely opposite.
Why it is a defect: comment, documentation, and formula disagreed; a consumer trusting the comment would read dominance backwards.
Potential consequences: inverted regression/dominance conclusions at any confidence threshold; misleading dashboards once a production caller exists.
Root cause: the Round-1 repair restored the missing operator as `1 − 2U/(n1n2)` — the standard form only when U is the *second* group's statistic; scipy supplies the *first* group's U. The Round-1 SUS-001 check compared the two documented sentences against each other and never ran the orientation experiment against SciPy's U.
Chosen correction: `2.0 * statistic / denominator - 1.0`
Alternatives considered: flipping arguments to `mannwhitneyu(second, first)` while keeping `1 − 2U/(n1n2)` — numerically equivalent but would break the invariant that `statistic` equals scipy's U for the first argument (now asserted in tests); rejected.
Files modified: `statistical_analyzer.py` L225; `tests/analysis/test_statistical_analyzer.py`
Tests: direction test updated (r=+1.0 plus scipy cross-check `result.statistic == mannwhitneyu(first, second).statistic == 9.0`); new `test_rank_biserial_sign_follows_kerby_convention` pins first>second → +1.0, first<second → −1.0, ties → 0.0 and the opposed median_difference signs.
Verification: tests/analysis 15 passed; full suite 476 passed, 11 skipped
Remaining limitations: two-sided p-value unaffected; no confidence interval on r (deferred with PROP-001).


## Section 4 — Suspicion Register

Finding ID: SUS-001
Suspicion: rank_biserial vs median_difference sign conventions conflict
Round-1 status: NOT_A_DEFECT (closed on documentation reading only)
Round-2 status: **CONFIRMED** — reopened, escalated, and fixed as DEF-006
Reason for reopening: the Round-1 closure asserted "each field correct" without testing the formula against SciPy's first-sample U orientation. Controlled samples (first>second, first<second, ties) showed r = −1.0 for a maximally-larger first group — the exact opposite of the L52 comment and the Kerby/effectsize convention.
Evidence: experiment table in DEF-006; SciPy and R effectsize documentation quotes
Final status: CONFIRMED (fixed as DEF-006)
Follow-up: regression tests pin all three orientations.

Finding ID: SUS-002
Suspicion: n=1 groups should be rejected (trivial p=1.0, no power)
Status: NOT A DEFECT
Inspected: L42-43 ge=1, L197-198 min 1; scipy accepts n=1
Supporting: power concern is real
Against: model, code, and scipy agree; degenerate comparison is a legitimate caller choice
Experiments: n=1 first-larger → U=1.0, r=+1.0, p=1.0, significant=False; n=1 first-smaller → U=0.0, r=−1.0
Reason: deliberate contract; statistical power is the caller's duty
Follow-up: documented limitation (FTE §H)

Finding ID: SUS-003
Suspicion: evidence.py missing-comma bug still present (`"errorquantitative"`)
Status: NOT A DEFECT
Inspected: live evidence.py L15-23 — comma present between "error" and "quantitative"
Experiments: direct read; `EvidenceKind` literals valid
Reason: fixed before this task
Follow-up: none

Finding ID: SUS-004
Suspicion: np.errstate misses some SciPy failures → NaN could escape into models
Status: NOT A DEFECT
Inspected: L109-115 finite gate, L124/L173 errstate blocks, L220-227 output gates
Supporting: errstate only suppresses numpy warnings, not scipy NaN returns
Against: every outbound float passes `_finite_result`, which raises `ArithmeticError` on non-finite values
Experiments: fully-tied groups ([5,5,5] vs [5,5,5]) → scipy p=NaN → `ArithmeticError("p_value is not finite")` raised; no warning leakage; NaN never reaches a model
Reason: output gate is independent of warnings; failure is loud, not silent
Follow-up: none


## Section 5 — Contract Inconsistencies

Finding ID: CON-001
Conflicts: service wants `SignalSummaryEngine → AnalysisResult` (mutable limitations); tracked `AnalysisEngine` produced `AnalysisContext`; `SignalSummaryAnalyzer` produces `list[AnalysisFinding]`
Source: `service.py` L46-54, `models.py` L20, tests
Interpretation: tests + service govern the persistence-facing path; the context analyzer is the descriptive path
Resolution: added `SignalSummaryEngine` (DEF-002), additive only; both paths now coexist with documented roles (FTE §D)
Status: RESOLVED (verified green)
Verification: tests/analysis 15 + service tests pass. Link: DEF-002

Finding ID: CON-002
Conflicts: pyproject (no numpy/scipy) vs analyzer imports
Source: `pyproject.toml` dependencies; analyzer L5/L7
Interpretation: code right, metadata stale
Resolution: deps added (DEF-004); no API change
Status: RESOLVED. Link: DEF-004

Finding ID: CON-003
Conflicts: agent-filter tests vs CHECK constraints + repository semantics
Source: DEF-005
Interpretation: repository + CHECK constraints govern; tests were wrong
Resolution: tests corrected; no production code changed
Status: RESOLVED. Link: DEF-005

Finding ID: CON-004 (new in Round 2)
Conflicts: L52 comment says "positive = first group larger"; formula computed the opposite; median_difference is documented as deliberately opposed to r
Source: `statistical_analyzer.py` L52 vs L225 vs L235-238
Interpretation: the "opposed conventions" claim was only true after the formula fix; before DEF-006 both fields agreed in sign while docs claimed opposition
Resolution: formula changed (DEF-006) so comment, docs, and tests are mutually consistent
Status: RESOLVED. Link: DEF-006

## Section 6 — Improvement Proposals

Finding ID: PROP-001
Problem: no multiple-testing correction across families of tests. Change: none now.
Benefit: controls FPR when scanning signal types/versions. Cost: needs a defined test family + schema owner.
Compatibility: an added field would affect future consumers only (none exist yet), but family definition is undecided.
Decision: DEFERRED. Reason: no batched testing orchestration exists; owned by Q-002.

Finding ID: PROP-002
Problem: three summary components overlap (SignalSummaryEngine, AnalysisEngine, SignalSummaryAnalyzer). Change: documentation only.
Benefit: clarity of ownership. Cost: none. Compatibility: none.
Decision: IMPLEMENTED (documented resolution in FTE §D; no code removal — each component has a distinct, tested contract).
Reason: smallest safe clarification; deletion would break tests that define intended contracts.

Finding ID: PROP-003
Problem: truncation flags are heuristic (limit+1 probe, no total count). Change: deferred.
Benefit: exact truncation reporting. Cost: extra COUNT query + API surface.
Decision: DEFERRED. Reason: not needed for any proven defect; limitation recorded in `limitations` text.

## Section 7 — Unresolved Questions

Finding ID: Q-001
Question: which engine is canonical long-term — `SignalSummaryEngine`, `AnalysisEngine`, or `SignalSummaryAnalyzer`?
Status: RESOLVED BY DOCUMENTATION (Round 2); no code change
Answer: they are not competitors. `AnalysisService` + `SignalSummaryEngine` is the production summary path (returns `AnalysisResult`, wired in `__init__`, exercised by 5 service tests + 8 engine tests). `AnalysisEngine` builds validated `AnalysisContext` pages for the future findings path (repo-filtered, untested directly — see FTE §K gap). `SignalSummaryAnalyzer` turns a context into `AnalysisFinding`s (descriptive counting only, no production caller). Keep all three; do not merge — their outputs are different model types consumed by different future layers.
Follow-up: if a product decision later drops the findings path, delete `SignalSummaryAnalyzer` + `AnalysisEngine` together in one commit.

Finding ID: Q-002
Question: who owns multiple-testing correction later?
Status: UNRESOLVED
Why: family-wise error grows with the number of tests; no batched orchestration exists yet.
Follow-up: assign an owner when version-comparison orchestration is designed (linked to PROP-001).


## Section 8 — Repair Ledger

| Repair | Root | Changed | Behavior | Tests | Result |
|--------|------|---------|----------|-------|--------|
| R-001 | DEF-001 | analyzer L225 | SyntaxError → computable r | 5 initial new tests | passed |
| R-002 | DEF-002 | engine (+`SignalSummaryEngine`) | package imports; tested contract satisfied | 8 engine + 5 service | passed |
| R-003 | DEF-003 | builders pass `agent_id` | predicate applied pre-LIMIT | signal suite | passed |
| R-004 | DEF-004 | pyproject | numpy/scipy declared | import + suite | green |
| R-005 | DEF-005 | 2 test files | tests honor CHECK/repo semantics | 29 storage signals+service | passed |
| R-006 | service flush | `service.py` `session.flush()` | caller rows visible to query | 5 service | passed |
| R-007 | DEF-006 | analyzer L225 `2U/(n1n2)-1` | r sign follows Kerby; opposed to median_difference as documented | 6 statistical tests incl. 3-case orientation test | passed |

No code comments were added by any repair; the pre-existing L52 comment was
made truthful by correcting the formula rather than by editing the comment.

## Section 9 — Test and Regression Results

Round 1: tests/analysis 14 passed; signals+service 29; models+repos 45;
full minus integration 475 passed, 11 skipped.

Round 2 (final): tests/analysis **15 passed** (one orientation test added);
signals+service **29 passed**; models+repos **45 passed**;
full suite `pytest tests/ -q --ignore=tests/integration` →
**476 passed, 11 skipped** (19.94s). No failures. Skips are pre-existing
(live-service integration). `tests/integration` excluded by design.

Extra regression evidence (Round 2 probes, outside pytest):
- n=1 groups: U=1.0/0.0, r=+1.0/−1.0, p=1.0 — no crash, as documented.
- Constant distinct groups: U=9.0, r=+1.0, p≈0.0469, significant=True.
- Fully tied groups: scipy p=NaN → ArithmeticError raised at the finite gate.
- Perfect Spearman: coefficient=1.0, p=0.0; describe([1,2,3,4]) std=1.2909944487358056, p95=3.8499999999999996.


## Section 10 — Per-Finding Status Matrix

| Finding | Round-1 status | Final status (Round 2) | Basis |
|---------|----------------|------------------------|-------|
| DEF-001 | Verified | **FIXED AND VERIFIED** (formula later superseded by DEF-006) | compile ok; suite green |
| DEF-002 | Verified | **FIXED AND VERIFIED** | imports; 15 analysis tests pass |
| DEF-003 | Verified | **FIXED AND VERIFIED** | agent predicate pre-LIMIT; signal tests pass |
| DEF-004 | Verified | **FIXED AND VERIFIED** | deps in `git diff pyproject.toml`; suite imports |
| DEF-005 | Verified | **FIXED AND VERIFIED** | tests honor CHECK/repo; 29 storage tests pass |
| DEF-006 | — (new) | **FIXED AND VERIFIED** | 3-case orientation test + scipy cross-check; 476+11 |
| SUS-001 | NOT_A_DEFECT | **CONFIRMED → FIXED (as DEF-006)** | controlled samples inverted the Round-1 conclusion |
| SUS-002 | NOT_A_DEFECT | **NOT A DEFECT** (re-verified) | n=1 probes behave as documented |
| SUS-003 | NOT_A_DEFECT | **NOT A DEFECT** (re-verified) | comma present in live evidence.py |
| SUS-004 | NOT_A_DEFECT | **NOT A DEFECT** (re-verified) | tied-group probe: ArithmeticError at finite gate |
| CON-001 | changed | **RESOLVED** | SignalSummaryEngine contract green |
| CON-002 | changed | **RESOLVED** | deps declared |
| CON-003 | changed | **RESOLVED** | tests corrected |
| CON-004 | — (new) | **RESOLVED** | comment/docs/formula/tests consistent after DEF-006 |
| PROP-001 | Defer | **DEFERRED** | no batched testing exists |
| PROP-002 | Implement (docs) | **IMPLEMENTED** | documented in FTE §D |
| PROP-003 | Defer | **DEFERRED** | heuristic truncation acceptable; recorded |
| Q-001 | Unresolved | **RESOLVED BY DOCUMENTATION** | roles separated in FTE §D |
| Q-002 | Unresolved | **UNRESOLVED** | owner assigned only at orchestration design |

Evidence totals: 476 passed, 11 skipped; 0 failures.


## Final Technical Explanation — How the Analysis Layer Actually Works

This section is the final section of the report by mandate.

### A. Purpose and boundaries
The analysis layer produces two kinds of output: (1) **signal summaries**
— validated counts over persisted monitoring signals, returned as
`AnalysisResult` or `AnalysisFinding`; and (2) **statistics** — validated
descriptive, correlational, and two-group nonparametric computations over
caller-supplied float samples (`StatisticalAnalyzer`). The layer does not
detect incidents, declare regressions, or infer causes. There is no
production caller for any analysis component today: `summarize_agent`,
`StatisticalAnalyzer`, the builders, `SignalSummaryAnalyzer`,
`SignalNormalizer`, and `RelationshipAnalyzer` are exercised by tests and
probe scripts only, and no HTTP/API entry point references them.

### B. File-by-file responsibility map
- `__init__.py` — public surface: re-exports `SignalSummaryEngine`,
  `AnalysisResult`, `AnalysisService` (and nothing else; consumers import
  statistical/evidence modules directly from their modules).
- `engine.py` — two classes. `SignalSummaryEngine.analyze(signals, agent_id)`
  is a pure function: agent/id/timestamp filtering with exclusion counters,
  UTC-normalizing min/max window, type/detector counts, builds
  `AnalysisResult` (uuid `analysis_id`). `AnalysisEngine` is session-bound:
  `build_run_context` / `build_evaluation_context` / `build_window_context`
  page `PersistedSignalRepository` (agent predicate in SQL since DEF-003)
  and validate into `AnalysisContext`, with pagination limitations.
- `service.py` — `AnalysisService.summarize_agent`: argument validation
  (limit 1..10000, offset ≥ 0, non-empty agent), `session.flush()` so
  caller rows are visible (R-006), fetch `limit+1` rows, truncation
  detection, delegates to the engine, appends the truncation limitation.
- `models.py` — `AnalysisResult`: Pydantic, `extra=forbid`, mutable
  `limitations` list (the service appends to it by design).
- `analyzer.py` — `SignalSummaryAnalyzer.analyze(context)`:
  Counter-based descriptive metrics → one `AnalysisFinding`
  (`signal_summary`, severity `info`, confidence `None`).
- `context.py` — `AnalysisContext`: scope enum `run|evaluation|window`
  with mutual-exclusion validators and per-signal membership checks
  (agent, run/eval id, half-open `[start, end)` window containment).
- `findings.py` — `AnalysisFinding`: frozen; non-summary findings require
  evidence; evidence ids must be unique; confidence 0..1 or null.
- `statistical_analyzer.py` — `StatisticalAnalyzer.describe/correlate/
  compare_independent_groups` plus frozen result models with finite-float
  fields and a `p < alpha` cross-validator (details in §F–§H).
- `signal_normalizer.py` — `SignalNormalizer`: `PersistedSignal` → frozen
  `NormalizedSignal` (string ids, payload copied, attributes = payload
  minus reserved keys). Adapter only; no consumer in the tree.
- `evidence.py` — `EvidenceRecord`/`EvidenceCollection` contracts for
  multi-source evidence (observation/temporal/dependency/execution/pattern/
  error/quantitative kinds; trace-field dependency validation). No consumer.
- `relationship_analyzer.py` — `RelationshipAnalyzer.analyze(collection)`:
  temporal (same agent+run, gap ≤ 300 s), execution_parent (trace/span),
  repeated_pattern, cumulative (strictly increasing time-series metric)
  → deduplicated, sorted `EvidenceRelationship` tuples. No consumer.
- `storage/models.py` `PersistedSignal` (L271-299) — CHECK constraints
  tying `scope` to exactly one of run/evaluation/window identifiers,
  unique `dedupe_key`, agent/type/time indexes.
- `storage/repositories.py` `PersistedSignalRepository` (L745-866) —
  insert-ignore-duplicates; `list_for_run/evaluation_run` (optional
  `agent_id` predicate), `list_for_agent` (type/time filters),
  `list_for_window` (half-open); all ordered by `(detected_at, signal_id)`.
- Tests — `tests/analysis/*` (15: engine behavior, regressions,
  statistical gates + sign orientation), `tests/storage/
  test_analysis_service.py` (5), `tests/storage/test_persisted_signals.py`
  (24, incl. agent-filter-before-limit).
- Docs — `statistical_analysis_layer.md` (Round-1 audit input),
  `fix_analyser_layer.md` (this report).


### C. Actual execution flow (traced end to end)
Production summary path (the only path with a persistence handle):
1. **Entry** — `AnalysisService(session).summarize_agent(agent_id, limit, offset)`.
   No API/router calls it; tests are the entry.
2. **Argument validation** — `ValueError` for limit outside 1..10000,
   negative offset, empty/blank agent_id. Fails before any query.
3. **Visibility flush** — `session.flush()` writes the caller's pending
   rows so the subsequent SELECT sees them (R-006).
4. **Retrieval** — `PersistedSignalRepository.list_for_agent` with
   `LIMIT limit+1`, `OFFSET`, ordered `(detected_at, signal_id)`;
   SQL `WHERE agent_id = ?` (isolation at the database).
5. **Truncation probe** — `len(rows) > limit` ⇒ keep first `limit`,
   `truncated=True`.
6. **Analysis** — `SignalSummaryEngine.analyze(page, agent_id)`: per item,
   agent mismatch → `excluded_signals.agent_mismatch`; missing/blank/
   non-string `signal_id` → `missing_signal_id` (item dropped from
   evidence); type/detector counted with `"unknown"` fallback for blank
   keys; `detected_at` missing → counted in `missing_detected_at` and
   omitted from the window; naive timestamps coerced to UTC, aware kept.
   Window = min/max over surviving aware timestamps (`None` if none).
7. **Aggregation** — metrics `{signal_count, signal_counts_by_type,
   signal_counts_by_detector, excluded_signals}`; limitations text for
   empty input and each exclusion class; `AnalysisResult` built with a
   fresh uuid4 `analysis_id`.
8. **Post-processing** — service appends "Result truncated at N signal(s)…"
   when `truncated` (mutable `limitations` by design).
9. **Response** — `AnalysisResult` returned to the caller. **No
   persistence**: nothing writes `AnalysisResult`/`AnalysisFinding` to a
   table (storage's `analyses` table is a separate, unwired concept).
10. **Failure modes** — `ValueError` (args), SQLAlchemy errors (session),
    pydantic `ValidationError` (any contract violation). Nothing is
    swallowed; no try/except exists in `service.py`/`engine.py`.
11. **Tests covering the path** — 5 service tests (empty agent, truncation
    on/off, empty result, evidence/sample agreement), 8 engine tests
    (grouping, exclusions, tz normalization, null types, other-agent-only,
    empty agent_id), 6 statistical tests.

Parallel (unwired) context path: `AnalysisEngine.build_*_context` →
`AnalysisContext` (validated) → `SignalSummaryAnalyzer.analyze` →
`[AnalysisFinding]`. Documented, importable, tested indirectly through
context validators; no production caller. Statistics path:
caller floats → `StatisticalAnalyzer` → frozen result model; tests only.
Evidence/relationship/normalizer paths: implemented, importable, zero
callers (recorded as intentional per Q-001/PROP-002).


### D. Architecture resolution — the three components (Q-001)
The three components are **layers, not rivals**, and each has its own
tested input/output contract:

| Component | Input | Output | Callers | Role |
|-----------|-------|--------|---------|------|
| `SignalSummaryEngine` | in-memory signals + agent_id | `AnalysisResult` | `AnalysisService`, tests | **canonical summary path** (persistence-facing) |
| `AnalysisEngine` | session + scope ids, limit/offset | `AnalysisContext` | none yet | context builder for the future findings path |
| `SignalSummaryAnalyzer` | `AnalysisContext` | `list[AnalysisFinding]` | none yet | descriptive finding generation |

Resolution: keep all three. `AnalysisService` + `SignalSummaryEngine` is
the exported, tested production path (`__init__.py` exports exactly these
plus `AnalysisResult`). `AnalysisEngine`/`SignalSummaryAnalyzer` form a
cohesive future path whose value is scope validation (run/evaluation/window
mutual exclusion, signal membership, half-open window containment) — that
logic is real and tested via `context.py` validators. Merging them would
either drag SQLAlchemy into the pure engine or push mutation into the
finding model. PROP-002's documentation deliverable is this subsection.
Removal criterion: if the findings path is ever dropped, delete
`AnalysisEngine` + `SignalSummaryAnalyzer` + `context.py` + `findings.py`
together in one commit.

### E. Data contracts audit (Pydantic ↔ SQLAlchemy ↔ repository ↔ service)
1. **SQLAlchemy → repository**: `PersistedSignal` CHECK constraints force
   `scope` to select exactly one identifier set; repositories return
   `list[PersistedSignal]` ordered deterministically. Verdict: sound.
2. **repository → engine/service**: engine consumes duck-typed items
   (attribute *or* dict access), so ORM rows, `SimpleNamespace`, and dicts
   all work — the widest-tolerance boundary. Service enforces row
   visibility via flush. Verdict: sound; agent filter is in SQL (DEF-003).
3. **engine → service → caller**: `AnalysisResult` is pydantic with
   `extra=forbid`, `sample_size >= 0`, `analysis_id/agent_id min_length=1`;
   `limitations` deliberately mutable so the service can append truncation
   text. `evidence_signal_ids` is a `list[str]` without uniqueness
   constraint — engine guarantees uniqueness by construction (one id per
   kept signal, signals are keyed by primary key). Verdict: sound;
   documented asymmetry (mutable list in an otherwise frozen-style result).
4. **session → `AnalysisContext`**: validates agent membership, scope
   identifier agreement, and window containment for every signal — a
   second, stricter line of defense after SQL filtering. Latent risk:
   the `window` scope is representable in `AnalysisContext` but
   `PersistedSignal` rows carry no `window_start/window_end` values in
   builder-populated pages, so window contexts are currently built only
   from `detected_at` containment; no defect (validators don't require
   the columns), recorded for future schema work.
5. **floats → statistical models**: every field is `FiniteFloat` with
   range constraints (`coefficient/p_value in [0..1]/[-1..1]`,
   `statistic >= 0`, `alpha in (0,1)`) and `statistically_significant`
   is cross-validated against `p < alpha`. Verdict: sound; validators
   never coerce invalid input into valid-looking output.


### F. Statistical behavior — exact conventions (verified by experiment)
- **Descriptive** (`describe`): container must be list/tuple with ≥1
  element; bool/NaN/Inf rejected (`TypeError`/`ValueError`); sample
  standard deviation `ddof=1`, `None` when n=1; p95 via numpy
  `percentile(..., method="linear")`. Verified: `[1,2,3,4]` → mean 2.5,
  std 1.2909944487358056, p95 3.8499999999999996.
- **Correlation** (`correlate`, default `spearman`): ≥3 paired
  observations, equal lengths, each variable ≥2 distinct values (constant
  input rejected with `ValueError` before scipy runs); pearsonr/spearmanr
  under `np.errstate`; coefficient and p pass the finite gate.
  Verified: perfect monotone pair → coefficient 1.0, p 0.0.
- **Group comparison** (`compare_independent_groups`):
  - `statistic` = SciPy's `mannwhitneyu(first, second,
    alternative="two-sided", method="auto")` U for the **first** sample
    (SciPy docs: "mannwhitneyu always reports the statistic associated
    with the first sample"); cross-asserted against scipy in tests.
  - **Rank-biserial r = 2U/(n1·n2) − 1** (DEF-006). Orientation table
    from controlled samples:

    | Case | U (first) | r (after fix) | median_difference (2nd−1st) |
    |------|-----------|---------------|------------------------------|
    | first entirely larger [10,11,12] vs [1,2,3] | 9.0 | **+1.0** | −9.0 |
    | first entirely smaller | 0.0 | **−1.0** | +9.0 |
    | all ties [1,2,3] vs [1,2,3] | 4.5 | 0.0 | 0.0 |
    | n=1 first larger [5] vs [1] | 1.0 | +1.0 | −4.0 |
    | constant distinct [5,5,5] vs [3,3,3] | 9.0 | +1.0 | −2.0 |

    Positive r ⇔ first group stochastically larger (Kerby 2014 /
    R `effectsize`: "+1 complete dominance of the first sample");
    `median_difference` deliberately keeps the *opposite* orientation
    (second − first), so the two fields are documented opposites —
    exactly what the L52 comment and §E contract claim. Before DEF-006
    the formula produced r = −1.0 for the first row, making both fields
    agree in sign and contradicting the comment.
  - p-values two-sided, method auto (exact when a sample ≤8 and untied,
    asymptotic with tie correction otherwise); significance `p < alpha`
    strict, cross-validated by the model; alpha must be a finite real in
    (0,1).
  - Fully tied inputs → scipy returns NaN p → `_finite_result` raises
    `ArithmeticError`; NaN never enters a model (SUS-004 evidence).
  - Ties and zero spread otherwise: exact tests return p=1.0 as expected.

### G. Validation and failure behavior
Rejected (raise): non-list/tuple containers, wrong sizes (0 for groups,
<3 for correlation), bool/non-Real elements, NaN/Inf elements, mismatched
correlation lengths, constant correlation variables, invalid method string,
alpha outside (0,1) or non-finite; pydantic contract violations on any
result/context/finding model; empty agent_id, limit outside 1..10000,
negative offset, non-positive pagination in builders, non-timezone-aware
window boundaries, `window_start >= window_end`.
Failed computation (loud): non-finite intermediates or outputs →
`ArithmeticError` naming the field. Nothing coerces invalid input into
valid-looking output; no exception in this layer is swallowed.


### H. Assumptions and limitations
Enforced by code: container types, finiteness, sizes, distinctness, alpha
range, significance/`p < alpha` consistency, scope mutual exclusion,
signal membership, agent isolation in SQL, UTC normalization of naive
timestamps.
Caller-owned (documented, not enforced): pairing validity and independence
within/between groups, outlier handling (no trimming), skew interpretation,
sample power (n=1 allowed → trivial p=1.0), multiple-comparison control
(Q-002/PROP-001), practical-importance thresholds (absent — statistical
significance ≠ importance), causality (never implied), time dependence
(unmodeled). Summary-path limitations appended at runtime: truncation
text, empty-input text, exclusion texts. Truncation is heuristic
(PROP-003): no total-count, so "additional records may exist" is stated,
not a number.

### I. Integration with the wider platform
Storage side: `PersistedSignal` ← monitoring persistence (insert-ignore +
dedupe_key), indexed by `(agent_id, detected_at)` and
`(signal_type, detected_at)` — both align with the repository's ORDER BY.
Consumers: none in production; `analysis/__init__` exports the summary
path only. Non-integration that matters: `AnalysisResult` is never
persisted (storage's `analyses`/`DriftAnalysis` tables belong to the
evaluation/drift domain and are untouched); statistics results are
returned, not stored; `SignalNormalizer`/`RelationshipAnalyzer`/
`EvidenceRecord` form a coherent but currently dormant evidence pipeline
(recorded as intentional per Q-001). Nothing in monitoring, ingestion,
evaluation, policy, or run-lifecycle imports `ai_eval_platform.analysis`.

### J. Executable examples (verified this session)
- `AnalysisService(session).summarize_agent("agent-1", limit=2)` →
  `sample_size=2`, limitation contains "truncated"; `limit=3` on 3 rows →
  no truncation; unknown agent → `sample_size=0` + empty-input limitation.
- `SignalSummaryEngine().analyze([...3 mixed-agent SimpleNamespace...],
  agent_id="agent-a")` → `sample_size=2`, evidence ids of agent-a only.
- `StatisticalAnalyzer().describe([1,2,3,4])` → std
  1.2909944487358056, p95 3.8499999999999996.
- `compare_independent_groups([10,11,12],[1,2,3])` → U 9.0, r **+1.0**,
  median_difference −9.0, p 0.1, significant False; reversed → r −1.0;
  ties → r 0.0.
- Rejections: `describe([True])` TypeError; `describe([1.0, nan])`
  ValueError; `correlate([5,5,5],[1,2,3])` ValueError; fully tied groups
  ArithmeticError; `summarize_agent("", limit=0)` ValueError.

### K. Test coverage and gaps
Covered: engine behavior (grouping, exclusions, tz, null keys, empty
agent), service validation/truncation/evidence agreement, repository
agent-filter-before-limit and window half-open semantics, statistical
gates (sizes, bool/nan/inf, constant correlation), sign orientation
(3 cases + scipy cross-check), CHECK-constraint conformance.
Known gaps (documented, not blocking): no direct unit test of
`AnalysisEngine.build_*_context` (covered indirectly by repository tests
+ context validators); no tests for `SignalNormalizer`,
`RelationshipAnalyzer`, `EvidenceRecord` (zero callers — tests would
freeze contracts nobody consumes); no persistence test for
`AnalysisResult` (no persistence exists); no integration tests (live
services, excluded by design); no CI job asserts the full 476-test count.


### L. Change inventory (what differs from HEAD `04d7525`)
Modified tracked files: `pyproject.toml` (+numpy≥1.26, +scipy≥1.11);
`analysis/__init__.py` (exports); `analysis/analyzer.py`,
`analysis/context.py`, `analysis/findings.py` (content restored from
empty blobs — they are the tracked baseline of the descriptive path).
Untracked analysis files: `engine.py`, `evidence.py`, `models.py`,
`relationship_analyzer.py`, `service.py`, `signal_normalizer.py`,
`statistical_analyzer.py`. Untracked tests: `tests/analysis/` (3 files,
15 tests), `tests/storage/test_analysis_service.py`,
`tests/storage/test_persisted_signals.py`. This report:
`fix_analyser_layer.md`. No production caller, API route, schema,
migration, or config was added or changed. No code comments were added.

### M. Final checklist
1. Every prior finding re-verified against live source in Round 2 — yes
   (Sections 3–4, 10).
2. Effect-size sign convention verified with controlled samples
   (first>second, first<second, ties) against SciPy's U orientation and
   the Kerby/effectsize literature — yes; defect found (DEF-006), fixed,
   and pinned by tests.
3. Full execution flow traced entry→tests — yes (§C).
4. File-by-file responsibility map for analysis/ + related
   storage/tests/docs — yes (§B).
5. Q-001 architecture resolved by documentation without code churn —
   yes (§D); Q-002 honestly left unresolved.
6. Data contracts audited across Pydantic/SQLAlchemy/repository/service —
   yes (§E), including one latent window-scope risk recorded.
7. Statistical behavior documented with verified numbers — yes (§F).
8. Every finding carries a final status label — yes (§10 matrix).
9. Defects repaired where justified; nothing invented beyond evidence —
   yes (R-001..R-007).
10. Full suite re-run: **476 passed, 11 skipped, 0 failed** — yes.
11. "Final Technical Explanation" is the absolute last section — yes.

### N. Summary
Round 1 fixed five defects (broken effect-size line, missing export,
post-pagination agent filter, undeclared dependencies, wrong test data)
and made the layer importable and green (475+11). Round 2 re-verified
everything against the live working tree, corrected the Round-1 git-state
reporting error, and — driven by the mandated controlled samples — found
and fixed a sixth, subtler defect (DEF-006): the rank-biserial formula
was algebraically valid but sign-inverted relative to SciPy's first-sample
U, the source comment, and the documented opposition to
`median_difference`. With `r = 2U/(n1n2) − 1`, the comment "positive =
first group larger", the Kerby/effectsize convention, the test suite,
and this report's examples now all agree. The analysis layer is
honestly characterized as: a fully tested, contract-validated,
persistence-aware **summary path**; a dormant but coherent
context/finding, evidence, and statistics toolkit; zero production
callers; and two open questions (Q-002 owner, PROP-001/003 deferrals)
that belong to future orchestration design, not to this layer.

### O. Round 3 — Context builders, RelationshipAnalyzer and SignalNormalizer contracts

**Goal:** finish the remaining contract/behavioral tests for the context builders
(`AnalysisContext` construction), `RelationshipAnalyzer`, and `SignalNormalizer`,
then re-run the full analysis suite.

**Files completed / created (all untracked):**

| File | Role |
|------|------|
| `tests/analysis/test_context_builders.py` | Contract + sqlite integration tests for the AnalysisContext builders (caller-side pagination/agent forwarding, SQL-layer agent filtering, `result_limit_reached`, exact-page and offset rules, repository error propagation). |
| `tests/analysis/test_relationship_analyzer.py` | Behavioral contract tests for `RelationshipAnalyzer`: temporal gaps, same-agent/same-run, zero-gap, execution parent (trace/span matching), repeated patterns, cumulative, analyze ordering, and `EvidenceRelationship` pydantic rules. |
| `tests/analysis/test_evidence_contracts.py` | Record/collection/relationship pydantic contract tests. |
| `tests/analysis/test_signal_normalizer.py` | Signal normalizer contract tests. |

**Verification:** `python -m pytest tests/analysis/ -x` → **71 passed** (56 added by
this round; 15 pre-existing analysis tests unchanged). No regressions.

**Behavioral pins recorded (RelationshipAnalyzer, confirmed with probes):**

- `temporal`: same agent + same run, `0 < gap <= max_temporal_gap_seconds`
  (boundary inclusive); zero gap and mismatched agent/run skipped.
- `execution_parent`: child `(trace_id, parent_span_id)` must resolve to a span
  record `(trace_id, span_id)` with matching agent; the span record must not
  carry `parent_span_id` (otherwise the analyzer matches the span to itself).
- `repeated_pattern`: identical `pattern_signature` across >= 2 distinct runs of
  the same agent; same-run groups and missing signatures skipped.
- `cumulative`: same agent/component/metric, numeric (non-bool) `metric_value`,
  `aggregation == "time_series"`, >= 3 strictly increasing samples.
  Within-gap temporal pairs remain legitimate side-effects.

**Fixes made in Round 3:** `build_*_context` are `AnalysisEngine` methods (not
module-level functions in `context.py`) with builder-specific argument shapes;
`PersistedSignal` lives in `storage.models`; the sqlite engine fixture mirrors
the repo setup (`sqlite+pysqlite:///:memory:`, `Base.metadata.create_all`);
Pydantic message expectations corrected (`too_short` vs `min_length`,
`span_id` required with `parent_span_id`); `EvidenceRelationship` lives in
`relationship_analyzer` (not `evidence`).

**Tooling note:** `ruff`/`flake8` are not installed in this environment and
`pyproject.toml` defines no lint config; validation was performed with `pytest`
and `ast.parse`. Exploratory probes and this report document the closed findings.

