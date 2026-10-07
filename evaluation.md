# Evaluation Layer Architectural Audit

## 1. Executive Summary

FACT: `tests/evaluation/test_evaluation_layer.py` cannot be collected. It
imports `src.ai_eval_platform.evaluation.domain` and sibling modules that do
not exist; every file under `src/ai_eval_platform/evaluation/` except a
docstring `__init__.py` is 0 bytes. An evaluation implementation does exist
elsewhere, under the top-level `datasets/evaluation/` package
(`domain.py`, `evaluator.py`, `runner.py`, `target_validator.py`,
`errors.py`, `evaluators/`, `engines/`). The test file therefore audits a
planned package location, not the implemented one.

OBSERVATION: judged purely on what is implemented in
`datasets/evaluation/`, the domain is a small, clean, well-separated
in-memory judging library: frozen dataclass target/context/criteria,
`Evaluator.evaluate(target) -> EvaluationMeasurement` with one metric per
evaluator, explicit SUCCESS/UNAVAILABLE/ERROR semantics, constructor
injection of engines/judges, and 7 deterministic evaluators. Judged as a
platform Evaluation Layer (storage, modes, lineage, orchestration, policy
integration), it is a disconnected fragment: no target builder from
`Run`/`Event` rows, no `EvaluationResult` writer, no runner-to-storage path,
no mode handling beyond a nullable criteria object, no evaluator versioning,
and no wiring into ingestion, analysis, monitoring, policy, deployment, or
CI.

RISK: the strongest danger is location confusion becoming permanent: two
candidate homes (`src/ai_eval_platform/evaluation/` empty,
`datasets/evaluation/` implemented) with the test pointing at the empty one.
Any future work that creates the `src` modules from scratch instead of
moving/aligning the `datasets` implementation forks the layer.

OPINION: the in-memory judging design is sound and worth keeping; the
platform integration around it is missing. Verdict: PARTIALLY fits, and the
test as written cannot pass without either moving the implementation or
rewriting the imports.

## 2. Test Execution

Command executed:

`python -m pytest tests/evaluation/test_evaluation_layer.py -q`

Result: collection error, exit code 1.

- Passed: 0
- Failed: 0 (no test ran)
- Skipped: 0
- Errors: 1 collection error
- Execution time: ~0.3s
- Error: `ModuleNotFoundError: No module named
  'src.ai_eval_platform.evaluation.domain'` from line 5 of the test module.

FACT: full-suite collection shows 256 tests collectable plus this 1 error;
nothing in the repository makes `src.ai_eval_platform.evaluation.domain`
importable (all candidate files are 0 bytes). No import/path fix was applied
per audit rules. The ~40 test functions inside the file (target validation,
7 evaluator behaviours, runner, contract checks) were read as a
specification, not executed.

## 3. Project Context

FACT: the implemented pipeline is AI System (absent producers) → Kafka
(`ai.events`, key `run_id`) → ingestion (validator → router → handlers →
`EventRepository`, explicit offsets, retry/DLQ) → PostgreSQL (`runs` 1:N
`events`, plus `datasets`/`evaluation_*` DDL) → ??? (no evaluation reader).
`src/ai_eval_platform/evaluation/`, `api/`, `monitoring/`, `policy/`,
`statistics/`, `ml/` are placeholders; `configs/evaluation.yaml` is empty.

OBSERVATION: Evaluation sits in the intended gap between Storage and
Metrics/Analysis/Monitoring/Policy, but nothing connects it: no target
builder queries `runs`/`events`, no measurement writer calls
`EvaluationResultRepository.upsert`, no runner is invoked by any job or
consumer, and `EvaluationCompletedEvent` ingestion persists an event row
only. The `datasets/evaluation/` package imports nothing from
`ai_eval_platform` or `schemas`, so it is dependency-isolated but also
integration-free.

## 4. Evaluation Architecture

FACT: `datasets/evaluation/` contains `domain.py` (8 frozen dataclasses +
status enum), `evaluator.py` (ABC, one abstract method), `runner.py`
(validate → evaluate → convert exceptions to ERROR measurements),
`target_validator.py` (None/blank run_id/missing context checks),
`errors.py` (5 exceptions), `evaluators/` (7 concrete evaluators),
`engines/` (2 thin wrappers, both referencing undefined helpers).
`src/ai_eval_platform/evaluation/` contains no code.

OPINION: as a judging kernel the layering is correct (domain → evaluator →
engine/judge → runner). As a platform layer it lacks every outward edge:
no inbound adapter (storage → target), no outbound adapter (measurement →
result row), no orchestration (modes, batching, retries, idempotency), no
configuration surface. Planned architecture (test imports) and implemented
architecture (datasets package) disagree on package home.
## 5. Evaluation Domain Audit

FACT: `EvaluationTarget(run_id, context, criteria=None)`; `EvaluationContext`
carries input/output, model, token_usage, latency_ms + latency_phases,
citations, single context_source, tool_calls, error, metadata;
`EvaluationCriteria` carries expected_output, required/forbidden tools,
max_latency_ms, expected_context_sources, metadata.

OBSERVATION: target shape is a sensible evaluation-ready view of a Run:
identity + observation + optional ground truth, frozen and mode-neutral.
Context coverage is good for judging but single-source retrieval
under-models multi-hop runs, and there is no agent/prompt/run-status/trace
linkage. Criteria is cohesive today (5 ground-truth fields) but is the
God-Object risk: every new metric wants one more optional field.
RECOMMENDATION: keep target/context; split criteria into per-metric
criterion objects rather than extending the flat dataclass.

## 6. Evaluation Modes

Online: target criteria=None path tested; storage online partial index
exists; no writer. Offline: dataset-less eval runs supported in storage;
domain has no eval-run field. Batch: no batch runner; caller loops.
Streaming: no trigger/ordering guard; redelivery would re-execute judges.
Benchmark: case join must be hand-built; criteria duplicates EvaluationCase
columns under different names. The evaluator contract works in every mode;
orchestration + persistence wiring is missing in every mode.

## 7. Evaluator-by-Evaluator Audit

LatencyEvaluator — Arch 8/10, Impl 7/10, Tests 8/10. Measures total,
echoes phases + percentages, applies budget threshold. `score` never set;
phase names free strings; no sum-vs-total check. Severity P2.
ExactMatchEvaluator — 8/9/8. Strict strip-compare, clean UNAVAILABLE paths.
Whitespace-only normalization. Severity P3.
SemanticSimilarityEvaluator — 8/8/8. Engine injection, no call on missing
data, exceptions to ERROR. Untyped engine param; no 0..1 clamp. P2.
ToolUsageEvaluator — 7/7/8. Required+forbidden in one check. Value=count
vs score=pass mixes units; name-only matching. P2.
TokenUsageEvaluator — absent from code and tests; token data in context is
unevaluated. Gap, P1.
RetrievalEvaluator — 7/6/7. Recall correct, missing sources listed.
Exact-string set match, no rank awareness; value flips count/recall. P1.
CitationEvaluator — 6/5/7. Counts valid/invalid. Validity is non-empty
source only, so fabricated citations pass; value=count vs score=ratio. P1.
GroundednessEvaluator — 8/7/8. Judge injection, short-circuit, ERROR path,
clamped. Untyped callable, no judge version captured. P2.

## 8. Latency Architecture

FACT: phases are free-string `LatencyPhase(name, duration_ms, metadata)`;
test uses planning/retrieval/llm/tool_call/healing. Evaluator measures
total, echoes phases + percentages, applies optional budget.

OPINION: phases belong in context (observation) — correct. Extensible
strings beat a fixed enum for heterogeneous agents; document conventions
instead of enforcing. Percentages aid debugging, weak regression signal.
Root-cause analysis belongs in analysis/, not here. Do NOT create one
evaluator per phase.

## 9. Error Semantics

FACT: SUCCESS/UNAVAILABLE/ERROR enforced everywhere; runner maps config /
infra / generic exceptions to ERROR and re-raises only invalid targets.
Tests assert UNAVAILABLE/ERROR never set passed=False.

OPINION: evaluator failure vs AI failure is correctly separated and is the
best-tested property. `MissingEvaluationDataError` and
`EvaluationExecutionError` are defined but never raised (P3 cleanup). No
passed=False misuse found. Evaluator contract rating: 8/10 — stable,
injected, testable, reusable across modes; docked for untyped judge port
and missing batch/persistence story.

## 10. Measurement Model

FACT: `EvaluationMeasurement(metric_name, status, value, score, passed,
feedback, error_type, metadata)`; latency value=ms score=None; match/
similarity/groundedness value==score 0..1; citation value=count
score=ratio; counts value=N score=None or 0/1.

OPINION: value/score/passed split is sound; examples coexist when consumers
read the trio. RISK: convention unenforced — a future evaluator could set
score=ms. RECOMMENDATION: document per-metric unit contract; no runtime
validation yet.

## 11. Engines and Judges

FACT: similarity engine port typed, groundedness callable untyped; both
wrapped in try/except to ERROR. Engine files reference undefined
`cosine_similarity`, `_build_prompt`, `_parse_score` (stubs around injected
backends).

OPINION: evaluator/engine split justified (deterministic logic vs
replaceable backend; fakes prove testability). Pattern 8/10, typing 5/10.
Engines are infrastructure long-term; current placement harmless while
backends stay injected.

## 12. Storage Compatibility

FACT: storage supports online/offline/benchmark via three partial unique
indexes + case-requires-run check; run_id + metric_name required; upsert
groups by identity with matching predicates. Domain has no
evaluation_run_id/case_id/dataset/evaluator-version fields.

Compatibility 7/10. Agreements: one-metric-per-evaluator matches partial
identity; measurement maps onto result columns. Mismatches: caller must
thread eval-run/case identity outside the abstraction; evaluator version
has storage columns but no domain source; criteria names differ from
EvaluationCase (`expected_output` vs `expected_answer`);
`EvaluationCompletedEvent` requires eval_run+case+metric so it cannot
express online rows. Repository NULL handling correct; domain ignores it.

## 13. Reproducibility

Preserved: run_id, metric, value/score/passed, row timestamp. Missing:
agent_version link on result, dataset_version except via eval run,
evaluator id/version (columns exist, never supplied), criteria snapshot,
explicit mode, judge/model version, evaluation config. RECOMMENDATION:
capture evaluator id+version and criteria hash at write time.

## 14. End-to-End Data Flow

AI to Storage Implemented; Storage to Target Missing (no builder);
Target to Measurement Planned (code exists, unreachable via tested
imports); Measurement to Result Missing (no writer); Result to Analysis /
Policy Missing. Runner is synchronous in-process; no async path, no Kafka
trigger. RECOMMENDATION: keep PostgreSQL as source of truth with
evaluation as downstream reader. Storage upsert supports repeats; domain
has no re-run story.

## 15. Software Engineering Assessment

Separation, DI, frozen types, explicit errors, package boundaries all
strong; runner logging good. Weaknesses: untyped judge/engine ports,
dead error variants, engine stubs calling undefined helpers, criteria God
Object trajectory, dual package homes. Rating 7/10.

## 16. AI/ML Engineering Assessment

Real signals present: exact match, embedding similarity, groundedness
judge, recall-style retrieval, citation counting, latency phases, error
taxonomy. Missing: token efficiency evaluator, statistical/CI treatment,
rank-aware retrieval, citation grounding, versioned judges, drift/regression
wiring. Rating 6/10 — genuine judging breadth, not LLM wrapping, but narrow
and uncalibrated.

## 17. MLOps / Platform Assessment

CI gates, regression, model/agent/dataset comparison, monitoring,
benchmarks, drift, policy/deployment hooks: none wired. Storage identity
and upsert are the only MLOps-ready pieces. Rating 4/10.

## 18. Strong Architectural Decisions

Target/context/criteria split 8/10: frozen, mode-neutral, test-proven.
One evaluator to one metric 9/10: matches storage identity, reusable.
SUCCESS/UNAVAILABLE/ERROR 9/10: best-tested property, no passed=False
misuse. Evaluator/engine split 8/10: fakes prove DI and testability.
Latency phases in context 8/10: observation preserved, analysis deferred.
Runner exception-to-ERROR mapping 8/10: infrastructure faults stay
measurements, structural faults raise.

## 19. Problems and Risks

P0-1 Package-home mismatch: test imports `src...evaluation.*`, code lives
in `datasets/evaluation/`, `src` files are 0 bytes. Blocks the suite and
forks future work. Evidence: collection error + file sizes. Urgency now.
P0-2 No storage wiring: no target builder, no result writer, no runner
invocation. Judging cannot affect the platform. Urgency now.
P1-1 Retrieval exact-string recall overstates benchmark readiness.
P1-2 Citation validity is non-empty-string check; fabricated citations
pass. P1-3 TokenUsage unevaluated (no evaluator). P1-4 Engine stubs call
undefined helpers (`cosine_similarity`, `_build_prompt`, `_parse_score`).
P1-5 Criteria/case field-name drift (`expected_output` vs
`expected_answer`). P2 items: untyped judge port, no score clamp on
similarity, value/score unit conventions undocumented, no evaluator
versioning, no batch runner, single context_source. P3: dead error
variants, style drift, percentage-only regression weakness.

## 20. Prioritized Improvements

1. Resolve package home (move or re-export, fix test imports). 2. Add
storage adapters (target builder + measurement writer) without touching
evaluators. 3. Version evaluators/engines and persist identity. 4. Split
criteria per metric. 5. Rank-aware retrieval + grounded citations +
token evaluator before benchmark claims.

## 21. Ratings

Architecture 6/10. Domain 8/10. Evaluator Abstraction 8/10. Modes 5/10.
Implementations 6/10. Error Semantics 9/10. Measurement 7/10. Storage
Compat 7/10. Reproducibility 5/10. Software Eng 7/10. AI/ML Eng 6/10.
MLOps fit 4/10. Testing 5/10 (strong spec, zero executable). Extensible
7/10. Overall 6/10.

## 22. Final Verdict

PARTIALLY. In-memory judging fits and should be kept; platform Evaluation
does not yet exist as a connected layer. Strong: target split, one-metric
evaluators, error semantics, engine DI, storage identity design. Weak:
package location, wiring, versioning, retrieval/citation depth. Missing:
builders, writers, orchestration, lineage, policy hooks. Over-engineered:
nothing. Under-engineered: integration, versioning, benchmark truth.
Do NOT change evaluator logic or storage contracts now. Improve: home,
adapters, versions, criteria split, retrieval/citation/token depth. Next:
fix imports so the spec executes, then thin storage adapters. Do NOT build
Kafka triggers, services, statistics, or monitoring yet.

## 23. Recommended Next Steps

Fix package home and prove the suite green; add read-only target builder
and upsert writer reusing existing repositories; stamp evaluator versions;
narrow criteria per metric; harden retrieval/citation/token coverage; then
consider batch orchestration. No new frameworks or triggers.


---

# Phase 1 Repair Analysis — Evaluation Layer Package Relocation

Note: sections 1–23 above record the pre-repair audit, at a point when the
suite could not be collected. This part records the Phase 1 repair that
followed. Where the two disagree on facts (file locations, frozen
dataclasses, test executability), this part is current.

## P1.1. Scope and Outcome

FACT: Phase 1 relocated the implementation from `datasets/evaluation/` to
the canonical `src/ai_eval_platform/evaluation/`, fixed only the defects the
existing tests revealed, and removed the old location. No adapters,
orchestration, triggers, storage, Kafka, monitoring, policy, or ML work was
performed; no tests, schema, migrations, or storage contracts were touched;
no dependencies were added; no comments were added to source code.

RESULT: `python -m pytest tests/evaluation/test_evaluation_layer.py -q` →
38 passed, 0 failed. Full suite → 280 passed, 15 skipped, 0 failed (the
skips are pre-existing and unrelated to evaluation). P0-1, the package-home
mismatch, is resolved.

## P1.2. Inspection Findings

FACT: before the change, `src/ai_eval_platform/evaluation/` held seven
0-byte placeholders (`engine.py`, `groundedness.py`, `metrics.py`,
`offline.py`, `online.py`, `regression.py`, `tool_use.py`) plus a docstring
`__init__.py`; `datasets/evaluation/` held the real code (domain, errors,
evaluator, runner, target_validator, 7 evaluators, 2 engines).
`evaluators/` and `engines/` shipped no `__init__.py` and worked only as
namespace portions. No module anywhere imported `datasets.evaluation`; the
only references to it were in this audit document. Tests import
`src.ai_eval_platform.evaluation.*`; pytest sets `pythonpath = [".",
"src"]`; setuptools uses `namespaces = false`, so every packaged subpackage
must ship an `__init__.py`.

## P1.3. Migration Decisions

FACT: the implementation was copied verbatim into the canonical package;
relative imports (`from ..domain import ...`) required zero rewrites; empty
`__init__.py` files were added to `evaluators/` and `engines/` to satisfy
the packaging convention; the seven 0-byte placeholders were deleted as
unreferenced remnants of the competing planned layout; the whole
`datasets/evaluation/` tree was deleted; unrelated `datasets/versions/` was
preserved.

OPINION: relocation was the correct choice over re-export — one canonical
implementation, no shim layer, no dual import paths to maintain. The
production package needed no import changes at all; only the defects below
blocked the specification from executing.

## P1.4. Defects Revealed by the Tests

FACT (1 of 3, 19 failures): the tests mutate `target.run_id`,
`target.context.*` and `target.criteria.*` in 18 places, but
`EvaluationTarget`, `EvaluationContext` and `EvaluationCriteria` were
declared `frozen=True`, so every mutation raised `FrozenInstanceError`.
FIX: unfroze exactly those three dataclasses. Leaf observation types
(`LatencyPhase`, `TokenUsage`, `ModelInfo`, `ContextSource`, `ToolCall`,
`ErrorInfo`, `Citation`) and `EvaluationMeasurement` remain frozen.

OPINION: section 18 of the pre-repair audit called frozen "test-proven";
the tests had never been executed against it. The tests are the
specification and they require mutability, so frozen-ness was the defect,
not the tests. Mutability of target/context/criteria is now part of the
domain contract.

FACT (2 of 3, 2 failures): `GroundednessEvaluator` called the injected judge
as a bare function, `judge(output, items)`, while both the production
`GroundednessJudge` API and the test fakes expose
`.evaluate(answer, context)`. The judge-case test saw `ERROR` with
`error_type='TypeError'` instead of `SUCCESS`, and the failure-path test
required `error_type='RuntimeError'` and got `'TypeError'`.
FIX: the evaluator now calls `.evaluate(...)` on the injected object.
Constructor signature, metric name, clamping, short-circuit paths and
public contract are unchanged.

FACT (3 of 3, 1 failure): the fixture pairs output
"The capital of France is Paris." with expected
"Paris is the capital of France."; `test_exact_match_passes` requires 1.0
while `test_exact_match_fails` requires "London" → 0.0. Strict
strip-equality scores the fixture pair 0.0, and a naive sorted-token
comparison also fails because the sentence-final period lands on different
words (`paris.` versus `france.`).
FIX: the comparison is now case-insensitive, punctuation-stripping and
word-order-insensitive over tokens. Both tests and every UNAVAILABLE path
hold; metric name and measurement shape are unchanged. This is the one
evaluator whose semantics were altered, permitted because the test suite
demonstrably requires it.

## P1.5. Deliberately Left Unchanged

FACT: `engines/embedding_similarity.py` still references undefined
`cosine_similarity`; `engines/groundedness_judge.py` still references
undefined `self._build_prompt` / `self._parse_score`.

OPINION: these fail only if the stubs are invoked with real backends; they
do not affect import, execution, or any test, and Phase 1 forbids building
embedding providers or LLM judges, so they remain placeholders for a later
phase. Also untouched by design: untyped engine/judge ports, flat criteria
structure, retrieval/citation/token-usage coverage gaps, evaluator
versioning, and value/score unit conventions — all still valid items from
sections 7, 10, 13 and 19.

## P1.6. Verification

FACT: every core symbol (`EvaluationTarget`, `EvaluationContext`,
`EvaluationCriteria`, `EvaluationMeasurement`, `EvaluationStatus`,
`Evaluator`, `EvaluationRunner`, `EvaluationTargetValidator`, and all 7
evaluators) is defined exactly once, under
`src/ai_eval_platform/evaluation/`. `datasets/evaluation/` no longer exists;
remaining textual mentions of the old path live only in this document. A
comment scan over the package (`#`, `TODO`, `FIXME`) returned zero hits. No
test file was modified. No schema, migration, Kafka, storage-repository,
monitoring, policy, or ML file was modified.

NOTE: the repository has no git commits, so `git diff` cannot show the
change set; verification used directory listings and repository-wide
searches instead.

## P1.7. Ratings After Phase 1

Testing moves 5/10 → executable and green (38 tests). The P0-1 location
risk from section 19 is closed. Overall stays approximately 6/10 because
every other gap identified in section 19 is untouched by design. Domain
holds 8/10 with one caveat: mutability of target/context/criteria is now
required by contract.

## P1.8. Next Phase

Phase 2, not started here: a read-only Run → `EvaluationTarget` builder and
an `EvaluationMeasurement` → `EvaluationResult` upsert writer reusing the
existing repositories, plus evaluator id/version stamping at write time.
No Kafka triggers, no batch or streaming orchestration, no monitoring or
policy integration yet.



