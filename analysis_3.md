# analysis_3.md — Fix pass: real integration problems found and fixed

Scope: implemented code only. Empty/future modules
(analysis/, root_cause/, policy/, api/, deployment/, security/,
observability/, statistics/, features/, sdk/, producers/, batch/,
ml/, engines stubs excluded below where noted) were left untouched.
No comments were added to source files. Full suite before and after:
399 passed, 15 skipped (skips are pre-existing Kafka/Postgres-gated).

## Fix 1

Problem:
MetricThresholdRule carried severity as a free string while AlertEvaluator
converted it with AlertSeverity(rule.severity) at match time. Any invalid
severity string constructed fine and exploded only when an alert fired,
and the rule type contradicted the Alert model which requires AlertSeverity.

Location:
src/ai_eval_platform/monitoring/system/alerts/rules.py,
src/ai_eval_platform/monitoring/system/alerts/evaluator.py.

Cause:
Rule dataclass was written without reusing the existing AlertSeverity enum,
and the evaluator papered over the gap with a runtime enum cast.

Change:
MetricThresholdRule.severity is now typed AlertSeverity; the evaluator passes
rule.severity through directly with no cast.

Result:
Illegal severities are unrepresentable at construction; verified a WARNING
rule now yields a WARNING alert through evaluate_metrics; full suite green.

## Fix 2

Problem:
EmbeddingSimilarityEngine.compare called a bare global cosine_similarity
that does not exist anywhere in the repo, so any live use raised NameError.
The module is non-empty implemented code and is imported by the implemented
SemanticSimilarityEvaluator, so this was Case A (broken implemented code),
not an empty future stub.

Location:
src/ai_eval_platform/evaluation/engines/embedding_similarity.py.

Cause:
Helper was referenced but never defined; tests inject fake engines so the
NameError survived.

Change:
Added a minimal cosine_similarity over the injected embedder outputs with a
zero-vector guard. No API change; evaluator contract unchanged.

Result:
Verified compare returns 1.0 on identical directions; engine failures still
surface as evaluator ERROR via the existing try/except; full suite green.

## Fix 3

Problem:
GroundednessJudge.evaluate called self._build_prompt and self._parse_score,
neither of which existed, so any live use raised AttributeError. Same Case A
reasoning as Fix 2: non-empty module on a real (if thin) path.

Location:
src/ai_eval_platform/evaluation/engines/groundedness_judge.py.

Cause:
Prompt-building and score-parsing helpers were referenced but never defined;
tests inject fake judges so the gap survived.

Change:
Added minimal _build_prompt (answer plus newline-joined context items) and
_parse_score (float of stripped model text). Existing clamp and evaluator
error mapping unchanged.

Result:
Verified evaluate returns 0.9 through a stub model; judge failures still map
to evaluator ERROR; full suite green.

## Fix 4

Problem:
MetricCalculator.percentile consumed its telemetry Iterable twice: once to
collect values, once for max(timestamp). A one-shot generator/iterator as
input made the second pass raise ValueError from max() on an empty sequence,
a real latent crash on an implemented path.

Location:
src/ai_eval_platform/monitoring/system/metrics/calculator.py.

Cause:
telemetry was iterated directly in both the values comprehension and the
timestamp max() instead of being materialized once.

Change:
Materialize items = list(telemetry) once; both passes read items.

Result:
Verified percentile over iter([...]) returns a value; full suite green.

## Observation

Alert rule comparison operators remain plain strings validated at match time
(gt/gte/lt/lte, ValueError otherwise). Left unchanged: no caller passes an
invalid operator today, and narrowing the type is a design choice for the
owners, not a live contradiction. Same for the percentile index approximation
and equal-weight system health aggregation: known weaknesses from the prior
review, but behavior is self-consistent and tested, so per instructions they
were not redesigned in this pass.

## Observation

Drift detector duplication (strategies/drift.py vs domain/tool_usage.py
emitting the same TOOL_USAGE_DRIFT signal) is real duplication but both
implementations are individually correct and tested. Merging them is a
refactor with behavior choices for the owners; not a broken integration, so
left unchanged.

## Future Work

Intentionally untouched: analysis/, root_cause/, policy/, api/, deployment/,
security/, observability/, statistics/, features/, sdk/, producers/, batch/,
ml/ disconnected scripts, signal persistence wiring, outbox relay, detector
and evaluator versioning, alert lifecycle, baseline backends, multi-tenancy.
No tables, topics, modules, services, or abstractions were added.

## Remaining Issue

None that is both real and safely fixable in this pass. Candidate follow-ups
for the owners (not bugs fixed here): rule comparison typing, percentile
index method, component criticality weighting, isolation confidence honesty,
timeout-vs-cancellation semantics, baseline/reference lineage on signals.
Each is documented in analysis_system.md with evidence and was verified to be
self-consistent as implemented.
