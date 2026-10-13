# Fix Validation Review

Scope: review ONLY. No source, test, config, model, or architecture file was
modified in this pass. Evidence gathered read-only from fixed files, callers,
and tests.

## Executive Verdict

Fix 1 (alert severity) and Fix 4 (percentile iterator) are correct,
contract-supported, minimal repairs. KEEP both. Fix 2 (cosine similarity)
is mathematically sound but completes an underspecified contract with a
conventional default the repo never pins down. KEEP WITH NOTE. Fix 3
(groundedness prompt/parse) runs but invents a wire format no contract
defines. UNCERTAIN trending REVERT-if-wired: harmless while unreachable,
unsafe the moment any production caller trusts its scores. The suite (399
passed, 15 pre-existing env-gated skips) proves none of the fixes crash; it
proves real-world correctness only for Fixes 1 and 4.

## Fix 1 - MetricThresholdRule Severity

Decision: KEEP
Confidence: HIGH

Evidence:
- alerts/models.py:9-12 defines AlertSeverity (INFO/WARNING/CRITICAL);
  Alert.severity requires it (models.py:27). The enum is the canonical type.
- rules.py:19 now types severity as AlertSeverity; evaluator.py:56 passes it
  through. The health path already used the enum directly
  (evaluator.py:95,110), so the metric path now matches existing convention.
- No caller constructs MetricThresholdRule anywhere in src or tests (search:
  zero hits). Nothing depended on passing a raw string.
- analysis_system.md C6 independently prescribed exactly this change.

What is correct:
- Fail-fast moved from fire time to construction time; the demonstrated
  crash (ValueError: 'bogus' is not a valid AlertSeverity) is now
  impossible to defer. Strict simplification with same output type.


## Fix 2 - Embedding Similarity

Decision: KEEP (functionally; epistemically PARTIALLY SUPPORTED)
Confidence: MEDIUM

Evidence:
- engines/embedding_similarity.py:15-32 wraps injected embedder.embed(text)
  and returns cosine_similarity(a, b). SemanticSimilarityEvaluator
  (evaluators/semantic_similarity.py) forwards (output, expected), stores
  the float as value/score with no range assertion and no clamp.
- No test exercises the real engine: test_evaluation_layer.py uses
  FakeSimilarityEngine (canned score) and FailingSimilarityEngine (raises).
  The 1.0 probe in analysis_3.md is a smoke check, not a contract proof.
- No canonical similarity exists elsewhere; no doc, config, or test pins
  metric, range, embedder output type, or edge semantics.
- The helper is mathematically correct cosine similarity (dot over product
  of norms), with explicit zero-vector ValueError.

What is correct:
- Resolves a genuine NameError on a non-empty module imported by a real
  evaluator. The call shape constrains the missing piece to a vector
  similarity; cosine is the conventional completion. Errors still map to
  evaluator ERROR via existing try/except.

Doubts:
- Range UNKNOWN: evaluator never clamps, so negative scores flow into
  value/score. Nothing ratifies [-1,1] vs [0,1].
- Edges chosen, not derived: zero raises; empty/mismatched dims handled
  only incidentally (zip truncation, no length check). Unspecified.
- Embedder output assumed list-like numerics; numpy/torch/generator inputs
  may behave unexpectedly. Pre-existing untyped port, but helper bakes it in.

Unsupported assumptions: PARTIALLY SUPPORTED. Similarity-over-vectors is
supported; cosine-unclamped-zip-truncated-zero-raises is invented detail.


## Fix 3 - Groundedness Judge

Decision: UNCERTAIN (do not promote to trusted; revert-if-wired)
Confidence: LOW on invented behavior correctness

Evidence:
- engines/groundedness_judge.py:6-28 builds prompt, calls
  model.generate(prompt), parses score, clamps [0,1]. GroundednessEvaluator
  (evaluators/groundedness.py:40-55) calls evaluate(output, items), floats
  result, clamps again, maps exceptions to ERROR.
- No contract defines prompt format, response format, score range pre-clamp,
  item representation, ordering, or empty-context behavior. No test asserts
  any prompt string or parses real output through _parse_score.
- Tests use FakeGroundednessJudge (canned float) and FailingGroundednessJudge
  (raises); neither touches the real class.
- evaluation.md:378-382 records prior drift at this seam but documents no
  intended schema either.

What is correct:
- Resolves a genuine AttributeError; helper signatures match pre-existing
  call sites exactly. Double clamp consistent with evaluator.

Doubts:
- Prompt Answer-plus-newline-context format: zero evidence. A real judge
  model is prompt-sensitive; this implies unvalidated prompt engineering.
- _parse_score float(stripped text): assumes bare numeric response. JSON,
  explanations, or Score-prefixed replies raise ValueError, yielding
  systematic ERROR disguised as per-call fallibility.
- Empty context yields a prompt inviting hallucinated scoring rather than
  UNAVAILABLE. Item str() conversion and ordering unspecified.

Unsupported assumptions: UNSUPPORTED ASSUMPTION for prompt format and
response schema. Plausible implementation, not derived behavior. The 0.9
stub probe proves plumbing runs, not model agreement.

Regression risk: LOW while unreachable (no production instantiation; fakes
in tests), HIGH if wired without review (systematic mis-scoring or total
ERROR regime misread as model failure).

Conclusion: UNCERTAIN. Leave inert but untrusted. Any real wiring must

## Fix 4 - Percentile Iterator

Decision: KEEP
Confidence: HIGH

Evidence:
- calculator.py:71-77,112-115 post-fix: items = list(telemetry) once; both
  values pass and max(timestamp) read items. counter_rate already did this
  (:22), so methods are now consistent.
- Bug mechanically certain: pre-fix double consumption left max() over an
  empty sequence for generator input. Signature invites Iterable, so
  materializing honors the contract. List/tuple behavior bit-identical.

What is correct: minimal, complete, no change for re-iterable inputs; crash
gone for one-shot inputs. O(n) memory matches counter_rate and is inherent
to percentile over a stream at this layer.

Doubts: none on the fix. Quantile/index crudeness untouched and out of scope.

Unsupported assumptions: none. No algorithmic choice made.

Regression risk: NEGLIGIBLE. Previously-working inputs identical;
previously-crashing inputs now return. No caller depended on the ValueError.

Conclusion: textbook single-pass fix. KEEP.

## Test Validation

Suite (399 passed / 15 env-gated skips) is a regression net, not proof:
- Fix 1: no test constructs the rule; suite proves no breakage. The ad-hoc
  WARNING in/out probe is sufficient for this mechanical change. SUPPORTED.
- Fix 2: fakes bypass the real engine (test_evaluation_layer.py:48-60,
  317-371). Tests assert plumbing, never cosine math/range/edges. WEAK.
- Fix 3: fakes bypass the real judge (:63-76, 476-525). Zero assertions on
  prompts or parsing. WEAKEST for the highest-risk fix.
- Fix 4: no calculator tests pass a generator; the iter() probe is the only
  evidence, sufficient for this mechanical bug. SUPPORTED.
- Pattern: injected fakes structurally hide broken reals; suite stayed green
  before (paths never invoked) and after. Green does not mean proven.

## Cross-Fix Concerns

No fix interaction: disjoint files and call graphs. Shared theme: Fixes 2
and 3 land without test coverage of new logic due to fakes. Fix 2 choice is
low-stakes math; Fix 3 is high-stakes wire format. Do not treat as equally
validated.

## Final Decision

- Fix 1 -> KEEP
- Fix 2 -> KEEP (range, dims, embedder protocol need ratification)
- Fix 3 -> UNCERTAIN (specify judge contract before wiring; else revert to
  loud stubs)
- Fix 4 -> KEEP

specify prompt/response/version first, else revert helpers to loud stubs.

Regression risk: LOW today (no production instantiation; tests use fakes),
MEDIUM if adopted unwittingly.

Conclusion: keep (reverting restores guaranteed NameError), but metric
choice, range, dimension checks, and embedder protocol need owner sign-off.

Doubts: none material. comparison stays a match-time-validated string,
correctly out of scope.

Unsupported assumptions: none. The fix reuses the existing enum.

Regression risk: LOW. Only hypothetical external string-passing code could
break; no such loader exists. AlertSeverity is a str enum, so comparisons
and JSON round-trips keep working; only invalid strings now fail, as
intended.

Conclusion: the correct fix for the documented contradiction. KEEP.
