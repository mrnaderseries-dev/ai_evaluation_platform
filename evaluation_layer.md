# Evaluation Layer Analysis

> Analysis-only deliverable. No production code, schema change, migration,
> refactoring, test, or dependency change was made to produce this document.
> Derived from repository inspection (imports, models, repositories,
> ingestion, schemas, configs, tests, docs) as of 2026-10-05.

## Executive Summary

**FACT:** The repository implements a narrow but solid observability-ingestion
pipeline (`KafkaRecord -> Validator -> Router -> Handler -> EventRepository
-> PostgreSQL`) plus a broad but unwired storage schema (30 tables). Only one
write path exists in production code: validated `AIEvent` envelopes stored as
rows in `events` (full `payload` JSON). All other tables — `runs` parents,
`execution_events` / `context_events` / `agent_behavior_events` /
`policy_decisions` detail side-tables, `datasets` / `dataset_versions` /
`evaluation_cases` / `evaluation_runs` / `evaluation_results`,
`artifacts` / `lineage`, `analyses/*`, `ml_models/*`, `outbox_events` — have
models + repositories + tests but no production writer.
`src/ai_eval_platform/evaluation/`, `api/`, `features/`, `monitoring/`,
`policy/`, `statistics/`, `sdk/`, `producers/`, `batch/`, `ml/` are 0-byte
placeholders.

**INFERENCE:** Correct foundation is storage-driven, not stream-driven:
`Kafka -> Storage -> Evaluation` (option B/C hybrid — read committed `events`
+ `runs` from PostgreSQL; batch/run-complete primary, optional online
pre-aggregation later). Stream `ai.events` is at-least-once, keyed by `run_id`,
single-writer-per-run ordered, idempotent on `event_id` — ideal durable fact
source, hostile as direct evaluation trigger (no run-completion signal, no
transaction spanning evaluation, sequence `MAX()+1` concurrency-unsafe).

**RECOMMENDATION:** Build Evaluation as separate downstream consumer of
PostgreSQL (new service/job, new transaction scope, reuse only
`EventRepository.list_for_run/list_by_type`, `RunRepository`,
`Dataset*`/`Evaluation*` reads, `EvaluationResultRepository.upsert` writes).
Leave `events` immutable, leave `runs` ownership with missing run-lifecycle
writer, close 5 critical gaps before metric work: (1) run input/output +
lifecycle, (2) run-case linkage, (3) ground-truth population, (4) evaluator
identity + run linkage for `evaluation_completed`, (5) completion signal.

Readiness: Data 4/10, Storage 7/10, Event 7/10, Versioning 5/10,
Lineage 3/10, Idempotency 8/10, Architecture 6/10.

## Implementation Status

**FACT.** One production write path exists: all five ingestion handlers call
`EventRepository.insert_from_envelope()` only, so the `events` table is the
sole table written by `src/` code. `ExecutionEventRepository`,
`ContextEventRepository`, `AgentBehaviorEventRepository`,
`PolicyDecisionRepository`, `Dataset*`, `Evaluation*`, `Artifact`/`Lineage`,
`Analysis*`, `ML*`, and `OutboxEventRepository` are reachable only from
tests: no caller in `src/ai_eval_platform` outside `storage/` references
them. `src/ai_eval_platform/evaluation/`, `api/`, `features/`,
`monitoring/`, `observability/`, `policy/`, `security/`, `statistics/`,
`sdk/`, `producers/`, `batch/`, `ml/`, `configs/evaluation.yaml`, and the
`evaluation` CI workflow are 0-byte placeholders. The evaluation DDL is
therefore **schema-present but operationally absent**: upsert-capable and
test-asserted, with zero production rows or writers.

## Current Repository Topology

### Project structure (FACT — sizes + imports)

- `schemas/events/`: `common.py` (2.5KB, AIEvent + 26 EventTypes), execution
  (6.3KB, 10 payloads), context, behavior, evaluation, policy — IMPLEMENTED.
  `schemas/versioning/schema_policy.py` 0B — PLACEHOLDER.
- `src/ai_eval_platform/ingestion/*.py` (~70KB): kafka_record, event_validator,
  event_router, handler, kafka_consumer, retry_policy/publisher/dispatcher/
  consumer, delivery_classifier, dlq, producer protocols — IMPLEMENTED.
- `src/ai_eval_platform/infrastructure/kafka/` config/consumer/producer/errors
  — IMPLEMENTED (optional confluent-kafka via require guard).
- `src/ai_eval_platform/storage/` models (35KB) + repositories (56KB) +
  postgres (3.5KB) — IMPLEMENTED.
- `evaluation/`, `api/`, `features/`, `monitoring/`, `observability/`,
  `policy/`, `security/`, `statistics/`, `deployment/` — 0B + docstring init
  — PLACEHOLDER. Same for `sdk/`, `producers/`, `batch/`, `ml/`.
- `configs/kafka.yaml` IMPLEMENTED; evaluation/platform/policies/monitoring
  0B. `docker/*.Dockerfile`, `docker-compose.yml`, `.env.example` 0B.
- `tests/{contract,ingestion,storage,integration}` IMPLEMENTED (~40 files).
- `pyproject.toml` deps only pydantic/SQLAlchemy/psycopg/pytest (+optional
  confluent-kafka/PyYAML). README forbids adding deps without imports.
### Topology details

Application vs infrastructure (FACT, AST-pinned by test_architecture.py):
ingestion depends only on schemas.events + sqlalchemy.exc + own protocols.
Infrastructure imports ingestion protocols + optional confluent_kafka.
Storage depends on sqlalchemy + schemas.common adapter only.
postgres.session_scope sole committer; repos flush-only.

Who calls whom (FACT): Producers MISSING -> Kafka ai.events key=run_id ->
KafkaRecord (never parses) -> Validator (26 EVENT_MODELS) -> Router
(EXPECTED_ROUTES 26->5 kinds, startup-validated) -> 5 Handlers
(insert_from_envelope only) -> EventRepository ON CONFLICT DO NOTHING ->
session_scope commit -> Classifier (dup ACCEPT; Integrity/routing/domain
DLQ; transient RETRY) -> RetryPublisher / DeadLetterQueue (run-keyed) ->
commit TopicPosition(offset+1) only after durable confirm.

## Current Architecture

AI System ABSENT -> Kafka ai.events/retry/dlq -> APPLICATION (Validator,
Router, Handlers, Consumer validate->scope->route->classify->commit,
Classifier, Retry pause-not-sleep, DLQ versioned) -> session_scope per record
-> STORAGE (PG prod/SQLite tests; 30 tables, 1 written; outbox table NO
relay) -> ??? future Eval.

Event layer: 26 types 5 families; AIEvent envelope with extra=forbid,
Literal-locked types, tz-aware timestamp. Ingestion: transport-agnostic +
thin Kafka adapter; at-least-once explicit commits, auto-commit False,
acks=all. Storage: models/repos/postgres lazy URL pooled engine.
Direction schemas <- ingestion <- infrastructure; storage <- handlers.
No cycles. No service layer.

## Current Data Flow

KafkaRecord -> attempt header -> Validator (failure ALWAYS DLQ) -> typed
AIEvent -> DB scope -> route -> insert_from_envelope (sequence envelope/
arg/MAX+1) -> commit scope -> ACCEPT+offset commit; errors -> rollback ->
DLQ or RetryPolicy(5, backoff)->retry topic; commit only if durable.
Retry loop separate group: future-due -> pause partition no commit; due ->
forward (commit) or DLQ. Ordering via Kafka partition key run_id +
single-thread consumer + arrival sequence. Cross-run concurrency safe;
in-run single-writer by convention.

## Database Inventory

30 tables FACT (EXPECTED_TABLES). PKs String64 UUID except Event natural +
detail shared-PK. TimestampMixin on roots. metadata_json attr. Status free
strings. Per-table FACT from models.py lines cited in recon.
A: agents registry PK id 1:N versions/runs/bindings mutable.
agent_versions FK agent CASCADE uq(agent,version) cols version/hash/config/
prompt/model/status intended immutable.
runs ROOT PK run_id FK agent RESTRICT version SET NULL idx agent+status.
cols external/status/input/output/error/started/finished/duration/tokens/
counts/last_seq. 1:N events CASCADE. Mutable but NO WRITER today.
policies container 1:N versions. policy_versions FK CASCADE
uq(policy,version) rules JSON. agent_policies binding FKs CASCADE/RESTRICT
uq(agent,version,from).

B: events ONLY WRITTEN append-only. PK event_id natural. FK run CASCADE
parent self SET NULL. uq(run,seq) + ix(run,time)+ix(type,time)+ix(corr).
cols schema/system/run/seq/type/occurred/recorded/parent/causation/corr/
step/payload JSON. Idempotency ON CONFLICT DO NOTHING 1/0.
agent_behavior/context/execution 1:1 detail PK+FK CASCADE UNWRITTEN.
context_resources per-resource FK CASCADE UNWRITTEN. policy_decisions FK
version RESTRICT run CASCADE event SET NULL UNWRITTEN.
C: datasets container; dataset_versions FK CASCADE uq(dataset,version)
checksum/case_count UNWRITTEN. evaluation_cases FK version CASCADE
uq(version,external) question/input/expected/context UNWIRED.
evaluation_runs FKs agent_version+dataset RESTRICT counters/config UNWRITTEN.
evaluation_results FKs run CASCADE case RESTRICT uq(run,case) SINGLE-metric
cols metric/score/passed/threshold/evaluator/latency/explanation/details
upsert DO UPDATE replay-safe UNWRITTEN.
D: ml_models + model_versions FK CASCADE uq(model,version) artifact/metrics/
params/training STRING no FK UNWRITTEN. pipeline_versions uq(name,version)
1:N components UNWRITTEN. experiments 1:N training_runs UNWRITTEN.
analyses root FK run SET NULL 1:1 diff/drift/rca unique CASCADE UNWRITTEN.
differential baseline/candidate eval_runs RESTRICT. artifacts pointer FK run
SET NULL hash indexed not unique UNWRITTEN. lineage edge FKs CASCADE
uq(in,out,transformation) plain create no upsert UNWRITTEN. outbox NO FKs
NO RELAY mutable status/attempts.

## Database Relationships

Declared edges only FACT. Agent 1:N AgentVersion 1:N Run 1:N Event 1:1
Behavior/Context/Execution; Context 1:N Resource; Event self-tree; Event
1:N PolicyDecision; Run 1:N PolicyDecision/Artifact/Lineage/Analysis;
AgentPolicy bridge. Dataset 1:N Version 1:N Case 1:N Result; Version 1:N
EvalRun 1:N Result; AgentVersion 1:N EvalRun. Analysis 1:1 Diff/Drift/RCA.
ML/Experiment/Pipeline trees. Artifact N:M via Lineage. Outbox standalone.
Roots: Run observability; Agent identity; Dataset benchmark; EvalRun exec;
Analysis/Artifact downstream. Event fact indexed run-replay/type/corr.
NOT PRESENT: Run<->Case link; run lifecycle types+writer; evalCompleted->
Result writer; labels/MetricDef/Suite/Baseline/Deployment/Split/Evaluator;
Outbox relay; I/O population; detail writers.

## Event Model Analysis

Envelope AIEvent FACT common.py: event_id required identity -> PK;
schema_version regex digit.digit; system_id+version producer; run_id
required identity -> FK + Kafka/DLQ key; event_type 26 enum Literal-locked;
timestamp tz-aware -> occurred_at (recorded_at DB-now lag measurable);
payload dict concrete per type; sequence int>=0 optional ordering override;
parent/correlation/causation/step optional (corr+step indexed).
26 types 5 families FACT:
Exec10: model_called model_id! version/provider/request/input_tokens/temp;
model_output +output_id output_text tokens latency finish;
tool_called id+name+version call+args; tool_completed success result err
latency; retrieval_started id+retriever query top_k; retrieval_completed
count results latency; answer_generated id type content source_ids;
error id type message component recoverable retryable;
recovery_attempted/completed strategy attempt success.
Context3: assembled id count resources[type/id/source/score] tokens;
resource_used id+res+usage; updated prev/curr/reason.
Eval1: evaluation_completed run! case! metric! value finite
evaluator deterministic/llm_judge/human/statistical/custom name/version
dataset strings threshold/passed/fail_reason/duration/metadata strict forbid.
Policy6 strict: checked policy/version/scope/count; allowed/blocked decision
+reasons+rules; behavior_rule_triggered action allow/block/review evidence;
human_requested review/reason/action; human_completed approved/rejected/
modified human/automated reason.
Behavior6: action id/type/target/step/repeat; verification method/result;
counter_evidence query/source/reason; answer_revised id/prev/type/trigger;
abstention reason confidence target; pattern_detected window/count/conf/
evidence.
Evaluator-reliable FACT: run grouping+ordering+causal; output YES
model_text/answer/tool_result/retrieval_results; tools YES by call_id;
retrieval YES by retrieval_id + relevance; errors YES; model YES
id/version/tokens/latency; latency YES per-op + ingest lag + span;
tokens YES; evidence YES source/evidence/trigger ids.
Expected answer NO in stream (only cases.expected_output unwired +
optional completed.metadata). Input PARTIAL (query/args/resources; no
prompt field; runs.input NULL).

## Event → Storage Trace

Traced model_called/output/tool_completed/retrieval_completed/answer/
evaluation_completed FACT via validator/router/handler/repos + roundtrip
test. Kafka bytes -> KafkaRecord -> typed AIEvent (metadata default {}) ->
router EXEC -> handler insert_from_envelope (seq envelope/arg/MAX+1;
payload.model_dump json; timestamp->occurred_at) -> INSERT events ON
CONFLICT DO NOTHING 1/0 -> COMMIT -> ACCEPT+offset.
Preserved: all envelope core + full payload JSON + ids + sequence.
Lost: Kafka coords/key/headers/attempt, retry/due, DLQ ids, ack timing;
runs rollups (cols NULL); side-table rows (never written; JSON only).
Derived: totals/rates/counts/chains/trees/lag/spans from rows.
Missing: runs parent must pre-exist else DLQ; canonical run I/O; prompt
text; doc bodies; tool registry; expected linkage; authoritative results
+counters; multi-metric; evaluator join; completion state.
Side-tables must be treated NOT PRESENT for planning despite DDL.

## Existing Evaluation Concepts

Grep FACT: evaluation/ 7x0B placeholder. evaluation_completed YES prod
persists events row carries run/case/metric/value/evaluator. datasets/
versions schema+repo UNWIRED no rows; cases question/input/expected/context
uq(version,external) test-only row; runs agent+dataset RESTRICT counters
UNWRITTEN no self baseline FK in current DDL; results uq(run,case) single
metric upsert DO UPDATE test-only most ready. metric/score columns only no
MetricDefinition. expected_output exists ground_truth/actual_output nowhere
(actuals in payload). experiment/training provenance string-link broken FK.
ml model_versions string link to agent model_name. evaluator type enum +
name/version strings no registry. benchmark/regression/baseline NOT PRESENT
except 0B filename + stale md; Differential baseline/candidate only A/B.
dataset event linkage free strings unenforced. evaluation.yaml + eval
workflow 0B. Verdict INFERENCE: DDL-complete single-metric V1 but
operationally absent (no population, no run-case join, no multi-metric).

## Evaluation Data Availability

Evidence-only FACT.
Run-level (success/latency/tokens/errors per run_id): inputs events+runs
ordered typed timed READY best-supported (intrinsic no truth needed).
Case-level (score case in eval_run): schema ready zero rows no run-case link
BLOCKED on population+linkage.
Agent-answer (groundedness/correctness): actuals answer content+revision
chain YES; expected NO disconnected PARTIAL.
Retrieval (recall/relevance): retrieved lists+scores YES unindexed; golden
set NO PARTIAL (intrinsic/LLM-judge possible recall impossible).
Tool-use (call/args/success): traces by call_id YES; intent expected only
if encoded case input PARTIAL.
Model (per model quality/cost/latency): tokens/latency across runs YES
aggregates READY; quality needs case link.
Policy/behavior: abstention/verification/revision/allowed/blocked/review
YES descriptive READY; pass/fail vs policy blocked on rules population.
Statistical/drift: tables empty modules 0B; raw stream sufficient offline
SCHEMA-READY PIPELINE-ABSENT.

## Evaluation Boundary

A Kafka->Eval->Storage inline: REJECT breaks scope-per-record txn holds
partitions max.poll risk; failure blocks observability; MAX+1 assumes fast
single writer; redelivery doubles judges; no completion signal.
B Kafka->Storage->Eval from committed rows: ACCEPT reuses ON CONFLICT +
offsets + ordered replay + scope; reruns free; failures isolated; fits
direction. PRIMARY.
C Storage side-reader async: SAME as B explicit decouple own scope/schedule
+ run-complete/batch/hybrid without hot path. ACCEPT B/C same choice.
D independent consumer group: DEFER doubles ops reimplements validation
races parent FK needs PG truth anyway; only later online gating.
Decision INFERENCE->RECOMMENDATION B/C. Existing System->Storage untouched->
NEW Eval reader->NEW results. Evidence txn/event/availability/replay/
idempotency/scale/separation/model.

## Natural Evaluation Unit

First-class FACT: Run (every event FK counters lifecycle), Event (PK order
index), Case/Version/EvalRun (DDL no rows), AgentVersion dual live+bench.
Implicit JSON-only: response/tool/retrieval/model invocation/case-exec.
Answer INFERENCE: natural unit is Run. Runner-up Event step judges. Case
only after linkage closed. Rationale Run sole cross identity key/scope/DLQ/
lineage; ordering/idempotency/retention scope it; all behaviors
reconstructible per run persisted. Run-level zero new data; case/bench needs
gaps.

## Evaluation Input Matrix

Evidence-only FACT dash=absent.
input prompt: runs.input NULL; payload query/args partial; cases no rows;
PARTIAL fragments.
output: runs.output NULL; payload answer/model_text YES via events.
tools: payload call_id YES.
retrieval: payload results/resources YES unindexed.
errors: payload YES.
model/lat/lokens: runs NULL; payload YES strings; version join NO.
expected: no stream field; cases no rows NO.
case-run link: no case on events/runs; no run on cases/results cols YES
no rows NO.
evaluator: payload strings event rows only; result cols no rows PARTIAL.
thresholds: same PARTIAL. policy: IDs only rules unwired PARTIAL.

## Data Gaps

Critical (cannot work without):
1 Run I/O+lifecycle: runs.input/output/error/status/finished/duration/
tokens/counts never written; no started/finished types; no writer; parent
must pre-exist else DLQ. Need question/final/state+WHEN. Source fragments
payload heuristic which answer final. Unobtainable no producer/lifecycle.
2 Run-case link: no case on runs/events no run on cases/results. Need score
vs truth. Source none. Disjoint roots share only agent_version.
3 Ground truth: cases rows + version freeze/checksum. Need correctness.
Source test-only row. No import path evaluation.yaml empty.
4 Result path+identity: completed only events row; no results write no
counters no evaluator registry. Need idempotent scores. Source strings
unenforced. Handler owns Event only no spanning service.
5 Completion/freeze: no finish event/status writer/freeze flag. Need trigger.
Source heuristic MAX time+idle lossy.
Important: 6 multi-metric uq(run,case) overwrites need (run,case,metric);
workaround details JSON. 7 structured detail empty JSON scans no GIN.
8 versioned evaluator/policy/prompt lineage unpopulated no registry.
9 suite thresholds per-row no versioning.
Optional: 10 artifact/lineage writers, outbox relay, baselines/splits,
cost ledgers no pricing.

## Versioning & Lineage

dataset DDL no rows checksum/case_count intended unique; freezable IF
populated. model ML DDL string link only match-only. agent DDL dual
runs+evalruns strongest hook once populated. prompt column string no diff.
evaluator strings conventional no registry. schema format enforced no
policy file 0B tolerate 1.0+. config JSON manual. run singleton new id
good immutable. experiment name only. Lineage artifacts+edge unique zero
writers run SET NULL evidence survives. V1 rely agent/dataset/schema/
evaluator-strings; pinning future.

## Idempotency & Consistency

Event event_id PK DO NOTHING 1/0 no TOCTOU redelivery ACCEPT committed
never DLQ asserted sqlite+PG. Run run_id key PK FK CASCADE dup app-level
missing parent Integrity DLQ never auto-create. Sequence uq(run,seq)
collision distinct ids Integrity DLQ surfaces bug. MAX+1 single-writer AST.
Eval future uq(run,case) DO UPDATE safe asserted. DLQ fresh dlq_id per
delivery distinguishable forensics. Double-execution inline A WOULD double
LLM cost unless keyed upsert; B/C safe rerun. Event result writes safe
natural key; run-complete needs guard else partial; batch needs frozen
version + stable eval id. All consistent IF natural keys never surrogate.

## Event-Driven vs Batch Evaluation

Event-driven per-event judge inline: REJECT no completion partial runs
holds partitions doubles cost no truth join; only stateless checks later.
Run-complete finished->collect ordered->judge->upsert: ACCEPT PRIMARY
matches Run + index + chains; needs completion guard.
Batch Dataset->Job->Runs->Metrics frozen version sweep: ACCEPT PRIMARY
matches DDL+upsert replayable; needs population+linkage.
Hybrid online pre-agg + run judge + offline bench: ACCEPT TARGET phase
run->batch->online uses indexes + analyses + stats placeholders; never
blocks hot path.

## Architectural Constraints

Must-respect FACT pinned:
1 Single-writer-per-run MAX+1; no threads; key run_id. Eval never writes
seq nor parallelizes in-run.
2 Txn scope sole committer flush-only; Eval separate scope never join.
3 Handler Event protocol only; Eval reads list/get + Eval repos never
router/consumer.
4 Events append-only no updated/update; corrections new events scores new
results.
5 At-least-once explicit offsets auto False bare ban acks all retry-topic
DLQ; reruns idempotent natural upserts.
6 PG uniques/RESTRICT benchmark protection/CASCADE history erasure/SET NULL
evidence; no FK on unwritten parent.
7 Schema forbid Literal router validator DLQ fast; new types lockstep;
tolerate minors.
8 App/infra separation AST; judges/queues behind interface own infra module.
9 Size 1MB gate 900KB DLQ/retry; large to artifacts.uri not JSON.
10 No hidden writers all 0B; Eval own entry/schedule/config never outbox as
queue without relay.

## Evaluation Layer Readiness

Data 4/10 actuals YES in payload; expected/run I/O NO; intrinsic ready
correctness blocked.
Storage 7/10 DDL+upsert+FK/index+PG parity YES; 1/30 written detail/result
empty single-metric no GIN no migrations.
Event 7/10 26 typed locked + pipeline + DLQ/retry/order/idempotency YES;
no lifecycle/case/input-field/policy producers absent.
Versioning 5/10 version rows+schema+evaluator strings hooks YES;
population/immutability/registry/config NO.
Lineage 3/10 DDL+evidence ids design YES; writers/store/URI NO.
Idempotency 8/10 DO NOTHING/DO UPDATE/ACCEPT/offsets/scope PG-asserted
strongest; seq DLQ correct; budget fixed.
Architecture 6/10 separation/protocols/pinned YES; no service/entry/
schedule/API/completion/single-writer deploy fence.
Overall storage+events+idempotency carry run/batch V1 intrinsic;
correctness needs 5 critical gaps.

## Recommended Evaluation Architecture

Conceptual no code:
Existing System producers TO BUILD key run_id -> Kafka -> Existing
Ingestion UNTOUCHED -> Existing Storage READ-ONLY events/runs/versions/
datasets/cases WRITE eval_runs/results upsert ONLY never events/runs/
datasets/details -> BOUNDARY new scope/process/job -> Readers RunCollector
guard+ordered CaseJoiner NEW Judges deterministic/llm/human/stat NEW ->
Writers ResultUpserter natural key RunRollup NEW Analysis writers existing
DDL -> Results queryable replayable -> future relay/API NOT V1.
Consume committed rows pins truth rules. Produce runs/results/analyses
future rollup. Live new evaluation/ + runner separate group own scope
schedule sweeper+batch greenfield evaluation.yaml. Depend read repos;
untouched write path/handlers/consumer/DLQ/retry/outbox. Probably new:
linkage, Suite/Metric/threshold versioning, Evaluator registry, Completion,
multi-metric key, Split, evidence writer, GIN, Alembic.

## Confirmed Facts

1 30 DDL 1 writer events. 2 26 types 5 handlers events-only. 3 event PK
DO NOTHING uq seq MAX+1 single. 4 result upsert unwired idempotent. 5
Dataset/Eval/ML/Analysis/Artifact/Outbox/detail models+repos zero callers.
6 no lifecycle types runs NULL parent required else DLQ. 7 completed events
only no result/counter. 8 eval/api/stats/sdk/producers/batch/ml/configs/
compose/docker 0B. 9 explicit offsets auto banned retry-topic DLQ run-keyed.
10 flush-only scope committer app/infra separation.

## Architectural Inferences

Observability-first facts cheap analyze later; Eval deferred DDL sketched.
Run intended analysis key; case/bench disjoint. Details abandoned JSON for
speed. Single-metric assumes one judge. Completed suggests external judge
vs internal batch both halves unconnected. At-least-once+upsert deliberate
no exactly-once claims correct.

## Open Questions

Who creates agents/versions/runs/datasets/cases when; completion definition
+owner rollups; canonical answer among many; truth source+freeze; live-case
mapping; V1 metrics/thresholds/judges; JSON scan scale vs GIN/backfill;
external completed fate vs internal precedence; PII/redaction retention;
consumer/partition fencing + job placement.

## Future Design Decisions

Approve B/C reject A revisit D later. Linkage mechanism. Completion+rollup
owner. Widen uq to run/case/metric + Metric/Suite. Evaluator registry +
completed fate. Detail strategy backfill/JSON/GIN. V1 slate intrinsic first
groundedness second correctness last NO metrics here. Migrations/config/
runner/API greenfield.

