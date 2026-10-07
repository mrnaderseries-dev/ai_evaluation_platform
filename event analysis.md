# Event Layer Analysis — AI Evaluation / MLOps Platform
_Date: 2026-09-30 | Scope: read-only audit, no code changed_

> Intended architecture: `Run` = domain entity/execution. `Event` = something that happens during a Run. Universal envelope `event_id, schema_version, system_id, system_version, run_id, event_type, timestamp, payload`. Payload flexible, agent-agnostic. No `run_started / run_finished` in `EventType`.

## 1. Current Inventory (actual repo state)

`schemas/events/common.py` (2405 bytes):
- `EventType(str, Enum)` = 30 values: model_called, model_output, tool_called, tool_completed, retrieval_started, retrieval_completed, answer_generated, error, recovery_attempted, recovery_completed, context_assembled, context_resource_used, context_updated, plan_created, plan_updated, plan_step_started, plan_step_completed, evaluation_completed, policy_checked, policy_allowed, policy_blocked, behavior_rule_triggered, human_review_requested, human_review_completed, agent_action, verification_attempted, counter_evidence_requested, answer_revised, abstention, behavior_pattern_detected.
- `AIEvent(BaseModel)`: extra=forbid, validate_assignment=True, required event_id / schema_version (regex ^\d+\.\d+$) / system_id / system_version / run_id, event_type: EventType, tz-aware timestamp validator, payload dict[str,Any] = {}.

Modules in `schemas/events/`:
- execution_events.py (5877 B): 10 events (ModelCalled, ModelOutput, ToolCalled, ToolCompleted, RetrievalStarted, RetrievalCompleted, AnswerGenerated, Error, RecoveryAttempted, RecoveryCompleted) with strict BaseModel payloads.
- evaluation_events.py (910 B): 1 event, strict payload with Literal[evaluator_type].
- context_events.py (1710 B): 3 events, strict payloads.
- policy_events.py (2735 B): 6 events, strict payloads with Literal[decision].
- agent_behavior_events.py (2926 B): 6 events, strict payloads.
- model_events.py: MISSING (ENOENT). tool_events.py / retrieval_events.py: gone. plan_*: NO FILE (4 enum values orphaned).
- Total: 26 concrete event classes, all using `event_type: EventType = Field(default=..., frozen=True)`.

Tests `tests/contract/test_event_schema.py` (11 tests): still imports `from schemas.events.model_events import ModelCalledEvent, ModelOutputEvent` -> ModuleNotFoundError on current tree. Expects flexible dict payloads, extra-top-level rejected, naive timestamp rejected, wrong event_type rejected, no RUN_STARTED/RUN_FINISHED, envelope serialization.

pyproject.toml: valid, pythonpath=["."], testpaths=["tests"].

## 2. What Is Good (keep it)

1. Envelope correct: Run != Event respected. No run_started/run_finished (grep: only negative asserts). run_id required.
2. Hygiene: extra=forbid + tz-aware timestamp + schema_version regex + min_length=1. Right for v1.
3. EventType(str, Enum): JSON-serializable, Kafka/Postgres-friendly.
4. Split by file (execution/evaluation/context/policy/agent_behavior) scales well.
5. Strict payloads well-written in isolation: ge/le, Optional vs required, nested models.


## 3. Failures / Risks (ordered by severity)

### P0 - Test suite red by construction (import break)
Test imports model_events which does not exist (moved to execution_events, no shim). Collection error, 0 tests run, CI red. Risk HIGH.

### P0 - event_type not locked (26/26 classes)
Pattern `event_type: EventType = Field(default=..., frozen=True)` does NOT block construction with wrong value in Pydantic v2; frozen only blocks re-assignment. Needs Literal[EventType.X]. Zero files use Literal for event_type. test_wrong_event_type_is_rejected fails everywhere. Silent misrouting downstream. Risk HIGH.

### P1 - Strict payloads vs flexible contract
Base AIEvent.payload is dict[str,Any] but children override with strict models (ModelOutputPayload requires model, latency_ms, success; ToolCalledPayload requires tool_name, call_id; RetrievalStarted requires retrieval_id, strategy, query, top_k). Test expects payload={} and varying shapes valid. Base says flexible, children say strict, test says flexible. Must decide explicitly. Simple agents (Ollama-style {"model","output","latency_ms"} with no success flag) get ValidationError or must fabricate fields. Risk HIGH.

### P1 - Orphan enum PLAN_*
PLAN_CREATED, PLAN_UPDATED, PLAN_STEP_STARTED, PLAN_STEP_COMPLETED have no file/class. Dead taxonomy or missing impl. Risk MEDIUM.

### P2 - Drift hazards
AnswerGeneratedPayload has content + candidate (leftover? typo, inconsistent spacing `candidate:str|None=None`). ModelOutputPayload.success required bool (no default) while output optional - odd. ToolCompletedPayload.status free str vs PolicyDecisionPayload.decision Literal - inconsistent strictness. No __init__ re-exports so refactor broke consumers silently. Risk MEDIUM.

### P3 - Thin coverage
11 tests cover only ModelCalled/ModelOutput flexible case. 0 tests for other 24 events, strict required-fields, PLAN_* orphans. Coverage ratio ~2/30. Risk MEDIUM.

## 4. Ratings

- Domain logic / modeling: 7.5/10. Envelope + Run!=Event right; taxonomy thoughtful; payload fields sensible. Deduct for strict-vs-flexible contradiction + orphan PLAN_*.
- Correctness (as-built): 3.5/10. P0 import break + P0 Literal bug + P1 strict-vs-test mismatch = suite red.
- Maturity / level: L2 prototype-to-early-contract (v0.5 -> v1). Good Pydantic hygiene, but no versioning policy, compat shim, docs, or per-event tests. Not yet L3 production contract.
- Agent-agnosticism: 5/10. Base agnostic; children opinionated (require success, latency_ms, call_id, retrieval_id...).
- Extensibility: 6.5/10. New event = enum + payload + class (easy). But 30-value enum + no registry/discriminated-union helper will get messy.
- Test alignment: 2.5/10. Test encodes original flexible minimal contract; code moved to strict expanded contract; neither updated the other.
- Overall health: 5.0/10 - stabilize before Kafka/DB/API.

Fail probability today: pytest tests/contract ~100% fail (import error, then Literal + strict-payload failures). Silent wrong-type acceptance 100% reproducible for all 26 classes. Producer rejection HIGH if strict enforced. Drift MEDIUM-HIGH (enum 30 outgrew tests covering ~2).

## 5. Why This Happened (inference)

1. Started from correct minimal contract (common + model_events, flexible dict) matching 11-test suite.
2. Valuable domain expansion (execution/context/policy/agent_behavior/evaluation with typed payloads) changed semantics flexible->strict without updating tests or migration note; moved modules without re-export shim.
3. frozen=True mistaken for constant. Understandable; needs Literal pattern everywhere.
4. EventType became catch-all (30 values) before consumers existed. Freeze enum additions until registry + test-per-event rule adopted.

## 6. Options (decision only, no code changed)

- A (minimal, test-aligned): flexible dict payloads + Literal fix + import fix. Fastest green, most agnostic. Loses early validation.
- B (strict, code-aligned): keep strict payloads, rewrite tests with all required fields + per-event tests. Best quality, highest producer burden. Must relax fields agents lack (make success/latency_ms optional) or producers will lie.
- C (hybrid, recommended): envelope strict, payload StrictPayload | dict union or BaseModel extra=allow + optional fields + from_loose(). Typed helpers + dict escape hatch. Most work, only option both safe and agnostic.

Also: Literal on all 26 event_types, resolve model_events import, decide PLAN_* (implement or remove), one positive + one negative test per event, freeze EventType additions behind review.

## 7. Verdict

Logic: sound foundation, thoughtful extension, currently self-contradictory. Level: prototype-plus, not stable v1 to build infra on. Risk: HIGH if built upon today, LOW fix cost (hours: Literal + import/payload decision + tests). Do not weaken tests to pass; do not add run_started/run_finished; do not add infra yet. Decide flexible vs strict explicitly, then make code and tests agree.

*End of analysis - no source files modified.*

## 8. Update 2026-09-30 - frozen removal + Literal migration (read-only)

Scope of user change since last audit: execution_events.py converted to Literal (10/10, frozen gone, `from typing import Any, Literal` present). context_events.py converted but BROKEN import. policy_events.py converted OK. evaluation_events.py + agent_behavior_events.py NOT converted (still frozen).


### 8.1 Syntax / import rating (per file)

- execution_events.py: 9/10. Correct `Literal[X] = X` on all 10 classes, compiles, imports OK. Minor: drops Field() so loses explicit description/metadata, but Literal default is valid Pydantic. Style inconsistent vs other files (spaces around = here, none elsewhere) - cosmetic only.
- context_events.py: 2/10. Uses Literal on 3/3 classes BUT `from typing import Any` missing Literal -> NameError at import time, whole module unusable. P0 syntax failure. Fix is one word, but as-is score is failing.
- policy_events.py: 9/10. 6/6 Literal, import present, compiles + imports. Same cosmetic spacing inconsistency.
- evaluation_events.py: 4/10. Still `EventType + Field(frozen=True)` on 1/1 class. Compiles and runs, but wrong-type construction still accepted (the exact P0 hole from section 3). Import already has Literal (used for evaluator_type) so fix is trivial, but not done.
- agent_behavior_events.py: 3/10. Still frozen on 6/6 classes, and `from typing import Any` lacks Literal so even copy-paste of Literal pattern will crash until import fixed. Compiles today only because old pattern needs no Literal.
- common.py: unchanged, 8/10. Envelope + EventType (26 values now, PLAN_* gone - good, orphan resolved) solid. Base still `event_type: EventType` which is correct for base; locking belongs in subclasses.


### 8.2 Logic rating (does Literal-without-Field work?)

Yes, logically correct where it imports. `event_type: Literal[EventType.X] = EventType.X` constrains validation to one value at construction AND, with AIEvent `validate_assignment=True`, also on assignment. So Literal alone (no Field, no frozen) fixes the P0 hole. Rating logic-intent 8/10.

Caveat: removing Field() also removes frozen, so post-creation reassignment to the SAME literal value is now allowed (harmless) and reassignment to a different value is still rejected by Literal + validate_assignment. Net: strictly better than frozen-without-Literal. No logic regression in execution/policy files.

Unmigrated files (evaluation 1 class, agent 6 classes = 7/26) keep old hole: wrong-type construction accepted. Logic coverage 19/26 fixed (73%), 7/26 still vulnerable.

### 8.3 Risk / fail assessment

- context_events import crash: P0, probability 100% on `import schemas.events.context_events`. Any consumer (API, worker, test) touching context events dies with NameError. Blast radius limited to context flows, but it is a hard crash not a soft validation error.
- evaluation + agent_behavior wrong-type hole: P0 residual, 7 classes. Same silent-misrouting risk as before, now concentrated in policy-adjacent + eval flows (arguably higher-value flows: human review, abstention, eval scores).
- Style drift (spaces vs no spaces, Field vs bare default): P3, no runtime effect, but review friction + future merge conflicts.
- Contract test file still imports deleted model_events (unchanged): suite still red regardless of this migration. So functional risk did not decrease yet from CI perspective.
- Overall fail risk from THIS change set: MEDIUM-HIGH. Fixed 19 classes, broke 1 module entirely, left 7 vulnerable. Net forward but not shippable until context import fixed + eval/agent migrated + test import resolved.

### 8.4 Scoreboard (1-10, this update only)

- execution_events logic 9, syntax 9, risk LOW.
- policy_events logic 9, syntax 9, risk LOW.
- context_events logic 7 (intent right), syntax 2 (NameError), risk P0-HIGH.
- evaluation_events logic 4 (hole remains), syntax 6 (runs but stale), risk MEDIUM.
- agent_behavior_events logic 3, syntax 5 (runs but stale + missing import), risk MEDIUM-HIGH (6 classes).
- Migration consistency: 4/10. Three styles now coexist: Literal-bare (exec/policy/context), Field+frozen (eval/agent), base EventType (common, correct).
- To reach 9+: add Literal to eval (1 line) + agent (6 lines), fix `from typing import Any, Literal` in context + agent, normalize spacing, re-run imports + contract tests. Estimated minutes, no redesign.

Overall migration: 5.5/10 - direction right, half-done, one module hard-broken. No source files modified in this audit.

## 9. Update 2026-09-30 late - Literal migration finished + contract suite restored

Changes: context_events import fixed + 3 classes normalized to Literal; evaluation_events 1 class to Literal; agent_behavior_events import fixed + 6 classes to Literal; policy_events spacing normalized; zero frozen=True remains (grep empty); no RUN_STARTED/RUN_FINISHED/ExecutionResultEvent added (grep empty); common.py untouched.

Tests: test_event_schema.py rewritten to import ModelCalled/ModelOutput from execution_events with minimum valid strict payloads (model required; output requires model+latency_ms+success); kept wrong-type rejection, extra-field rejection, naive-timestamp rejection, lifecycle-absence, envelope, serialization, payload-divergence checks against strict models. New test_event_type_locking.py covers all 26 classes with accept-correct + reject-wrong parametrization (52 cases).

Ratings: logic 9/10, syntax 9/10, consistency 9/10 (one style: Literal[X] = X), test alignment 9/10, overall 9/10. Remaining: coverage of payload edge cases per event is thin beyond locking; no further blockers. Results: pytest tests/contract -q = 63 passed; pytest -q = 63 passed.

## 10. Rating of your idea (remove frozen, use Literal everywhere)

Your idea: drop every frozen=True and lock with event_type: Literal[EventType.X] = EventType.X. Rating: 9.5/10. Correct diagnosis (frozen only blocks reassignment, Literal blocks construction), minimal diff, no redesign, uniform style, fixed 26/26 hole with zero regressions. Half point off only because bare Literal drops Field() metadata slot (harmless now, useful later for docs). Decision quality 10/10, execution after final pass 9/10. This is the canonical Pydantic v2 constant pattern.

## 11. Opinion on the event-layer idea itself (execution / context / policy / agent-behavior / evaluation)

Short version: strong idea, 8.5/10 as a concept. You are modelling the right seams of an agentic system: what the machine did (execution), what it knew (context), what was allowed (policy), how it behaved (agent-behavior), and how good it was (evaluation). Most tracing systems stop at execution; you kept the four layers that actually explain failures. That is the correct instinct for an evaluation/MLOps platform.

Per-domain verdict:
- Execution (10 events): 9/10. ModelCalled/Output, ToolCalled/Completed, RetrievalStarted/Completed, AnswerGenerated, Error + Recovery pair - this is the spine. RecoveryAttempted/Completed is the standout; few teams model recovery as first-class events, and it is exactly what lets you measure self-healing instead of just errors.
- Context (3 events): 8.5/10. Assembled / ResourceUsed / Updated captures the RAG lifecycle without drowning in per-chunk noise. Right granularity. Missing piece is a context-quality signal (e.g. relevance/truncation), but that belongs in evaluation, not here.
- Policy (6 events): 9/10. Checked / Allowed / Blocked + RuleTriggered + HumanReviewRequested/Completed is a real governance trail, not theater. The Literal decisions (allow/block/review, approved/rejected/modified) force downstream honesty. Best-designed payloads in the tree.
- Agent-behavior (6 events): 8/10. This is your differentiator and your risk. AgentAction, VerificationAttempted, CounterEvidenceRequested, AnswerRevised, Abstention, BehaviorPatternDetected - nobody gets this from spans alone; you have to emit it. Abstention + AnswerRevised are gold for trust metrics. Risk: these are interpretive (who decides something was a verification?), so producers can be inconsistent. Needs producer guidelines, not schema changes.
- Evaluation (1 event): 7/10. Correct minimal shape (metric + value + evaluator_type + passed), and evaluator_type Literal is right. But a single completed event cannot carry the eval lifecycle later (started/failed/skipped, dataset version, baseline diff). Fine for now; expect this file to grow.

Honest criticisms:
1. 26 types is a lot before a single consumer exists - you built taxonomy ahead of queries. It will pay off only if dashboards/alerts actually slice by these types; otherwise it is overhead per emit.
2. Strict payloads fight agent-agnosticism. The idea is right for a mature fleet, heavy for a new/foreign agent that must fabricate success/latency_ms/call_id. The escape hatch question is still open.
3. No plan_* anymore (good, orphan removed), but long-horizon agents will eventually need plan/step events back - keep the door open, do not re-add prematurely.

Overall: keep it. The five-domain split is the idea worth defending. Execution tells you what happened, context tells you why it knew it, policy tells you whether it should have, behavior tells you whether it acted like a good agent, evaluation tells you whether it was any good. That is a complete story, and almost no open-source eval harness tells all five. 8.5/10 - ambitious but justified, provided you now freeze the taxonomy and build one real consumer (a failure-analysis query) before adding event 27.
