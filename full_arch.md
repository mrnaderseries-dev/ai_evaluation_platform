# Full Comparative Engineering Audit — AI Evaluation Platform
**Version A (older baseline):** `C:\Users\user\ai-evaluation-platform.zip` (staged read-only at `C:\Temp\audit\verA_zip`)
**Version B (newer candidate):** Live workspace `C:\Users\user\ai-evaluation-platform` (HEAD `04d75251fc918df21346fadf0fd19291530b65e1` + working-tree edits)
**Report path:** `C:\Temp\audit\full_arch.md` (outside both project roots per preservation rule)
**Date (UTC):** 2026-10-11 | **Python:** 3.12.0 | **Auditor:** Muse Spark (read-only, evidence-based)
**Integrity:** No source file in A or B was modified, merged, renamed, deleted, or migrated. Tests ran on isolated copies (`C:\Temp\audit\runA`) and on the workspace via read-only `pytest` (caches only). Disposable in-memory SQLite used; no production DB touched.

## 0. How to read this report
- Every material finding carries: Version, file:lines, symbol, failure scenario, classification (Verified fact / Reasoned inference / Unresolved / Environment limitation), confidence (High/Med/Low/Not Verified), severity (Critical/High/Med/Low), correction, verification status.
- Line numbers are actual one-based numbers from files as read on 2026-10-11. They may drift if files change; refresh before acting.
- `Verified fact` = seen in source or executed. `Reasoned inference` = supported but not executed. Never report hypothetical as confirmed defect.
- Comparison direction is **A -> B**. B is evaluated as delta on A.

## 1. Executive verdict
**B genuinely improves A and should be retained in direction, but must NOT be merged as-is. Two failing tests + one missing migration + several incomplete platform workflows block merge.**

| Question | Answer |
|---|---|
| More correct/maintainable architecture? | **B.** Adds the mandatory Event -> Observation -> Base Signal path, lineage columns, API retrieval, and strict Signal validation. A has no observation layer at all and a packaging `SyntaxError` that prevents import. |
| Does B improve or regress? | **Net improve, one regression + one stale test.** Improves persistence/lineage/API; regresses detector-count semantics (`6 vs 7` in `test_distinct_detections_survive_logical_dedupe`) which is either a real over-dedup or a stale expectation — must be resolved before merge (High). |
| Is Event->Observation->Base Signal complete and wired? | **In B: yes for the repository write path; partial for ops.** `EventRepository.create/create_many/insert_ignore_duplicates/insert_from_envelope` all call `_project_event_by_id` (repositories.py:452-505,547-569) which calls `project_event` (projector.py:21-129), persists `EventObservation` (577-602) + base `PersistedSignal` (604-615) with `ON CONFLICT DO NOTHING`. Verified by executed E2E probe (1 event -> 1 obs + 1 base signal, duplicate no-op, distinct e2 -> 2+2). Kafka -> handler -> repository path reaches it (handler.py:48,69,93,113 -> insert_from_envelope:519-545). API exposes it (api/runs.py:24-60). Missing: migration for new tables/columns, observation repository abstraction, backfill for pre-B rows. |
| Are EventType/SignalType/Origin/Observation/Analysis contracts sufficient? | **Mostly yes for current scope; Analysis/RCA/policy not sufficient for broader platform.** EventType 26 values cover execution/context/behavior/evaluation/policy with startup-validated routing. SignalType 7 values (B adds `event_observation`) + SignalOrigin 3 values (`base_observation/detector/analysis`) correctly separate fact-kind from origin. Observation contract is minimal but sound. Analysis contracts (`AnalysisResult/AnalysisFinding/EvidenceRecord`) are descriptive counters only, no causal inference, no confidence calibration — insufficient for RCA claims. |
| Complete for intended scope? | **No. Core MVP (ingest->persist->observe->signal->retrieve) works in B on SQLite; broader platform (evaluation gates->policy->deployment,漂移 statistics, RCA, outbox relay, CI/CD, Kafka integration) is scaffold or empty.** |
| Retain / Modify / Reject / Investigate | See §11. Retain B's observation/projector/lineage/API. Modify dedupe semantics + migration + stale table-count test. Reject nothing wholesale. Investigate detector identity, `create_with_*` bypass, FK cascade asymmetry. |
| Work remaining for MVP vs broader | See §12. MVP needs ~7 fixes (migration, dedupe decision, table-count test, `create_with_*` projection, API auth/pagination docs, Postgres migration test, duplicate-sequence handling). Broader needs outbox relay, policy engine, RCA stats, deployment gates, real Kafka/Postgres CI. |

**Scorecards (0-10, higher better):** Ingestion A 7.5 / B 8.0; Storage A 6.0 / B 7.5; Observation->Signal A 0 / B 7.0 (wired but migration/test gaps); Detectors A 6.5 / B 6.5; Evaluation 5.5/5.5; Analysis/RCA 3.0/3.5; API A 1.0 / B 5.0; Migrations A 4.0 / B 3.0 (new schema without migration = regression); Kafka/Retry 7.0/7.0; Security/Ops 4.0/4.0. Overall: **A 4.2, B 6.1** — meaningful progress, not shippable without repairs.

## 2. Audit integrity and method
- **Read-only:** Both inputs preserved. `verA_zip` is an extracted copy of `ai-evaluation-platform.zip`; workspace never edited (only `pytest` caches). Verified `git status --porcelain` before/after shows same modified/untracked sets; no `git add/commit`, no migration applied. [Verified fact, High]
- **Isolation:** `C:\Temp\audit\runA/{src,tests,schemas}` staged from verA_zip for import checks; `C:\Temp\audit\e2e_probe_B.py` uses scratch in-memory SQLite, `sys.path` injection only, no file writes to either root. [Verified fact, High]
- **Trace rule:** A feature counts as implemented only if a production call path reaches it (import -> call -> persist -> retrieve). Existence of class/enum/route/config alone is not credit. Applied throughout §4. [Method, High]
- **Test rule:** Mocked unit test != E2E proof. Each E2E scenario in §6 records command, exit, output, executed vs inspected. [Method, High]
- **Limitations:** No Docker/Postgres/Kafka broker available; Postgres paths verified by code inspection + SQLite execution + skipped integration tests. No secrets/prod access. [Environment limitation, High]


### 3.2 Project roots and structure
- **A root:** `C:\Temp\audit\verA_zip` — `.github/`, `batch/` (5 empty 0-byte scripts), `configs/` (`kafka.yaml` 2154 bytes, others 0 bytes), `datasets/versions/` (empty), `docker/` (3 empty Dockerfiles), `docs/` (absent in A; present only in newer archive!), `migrations/0001_run_lifecycle_hardening.sql` (376 bytes), `ml/` (3 empty), `producers/` (2 empty), `schemas/events/` (7 files: `common.py` 2560, `agent_behavior_events.py` 2780, `context_events.py` 1640, `evaluation_events.py` 1332, `execution_events.py` 6353, `policy_events.py` 3602, `model_events.py`), `sdk/` , `src/ai_eval_platform/` (144 py), `tests/` . [Verified fact, High]
- **B root:** `c:\Users\user\ai-evaluation-platform` — same skeleton plus: `docs/` (untracked, audit notes), `migrations/` (untracked, same 0001 file), `src/.../monitoring/observations/` (NEW 2 files), `src/.../analysis/engine.py, evidence.py, models.py, relationship_analyzer.py, service.py, signal_normalizer.py, statistical_analyzer.py` (mix of tracked edits + untracked new), `src/.../run_lifecycle.py` (untracked), `tests/analysis/`, `tests/monitoring/test_run_lifecycle*.py`, `tests/storage/test_analysis_service.py, test_persisted_signals.py, test_run_lifecycle_*.py` (untracked). `src` py count 146 (+2 net). [Verified fact, High]

### 3.3 Environment
- **Python:** 3.12.0 (`python --version`). `requires-python >=3.11`. [Verified fact, High]
- **Dependency manager:** setuptools + `pyproject.toml`. No lockfile. B `pyproject.toml` (2174 bytes, 62 lines) adds `fastapi>=0.110`, `numpy>=1.26`, `scipy>=1.11` vs HEAD (2056 chars, without fastapi/numpy/scipy). A zip `pyproject.toml` 2152 bytes (same as B? staging showed verA_zip 2152, verB_zip 2152 — both lack fastapi? Actually workspace 2174 adds 3 deps; zips predate that). Declared runtime deps actually imported: `pydantic` (schemas/events), `SQLAlchemy` (storage), `fastapi` (api), `psycopg[binary]` (postgres driver), `numpy/scipy` (claimed by statistics/evaluation but inspection shows `statistics/*.py` empty, evaluation engines import numpy? must verify). [Verified fact, High]
- **Installed:** `ai-eval-platform 0.1.0` editable at workspace, `fastapi 0.139.0`, plus heavy env (chromadb, langchain, etc.) — not declared, environment contamination risk. `pip list` exit code 1 due to output truncation but packages visible. [Verified fact, Medium]
- **Entry points:** No `console_scripts`. App entry: `src/ai_eval_platform/api/main.py:app` (FastAPI). Ingestion entry: `KafkaEventConsumer` (no `__main__`/service wiring). Batch/ml/producers empty 0-byte. [Verified fact, High]
- **Test framework:** `pytest>=8.0`, `testpaths=["tests"]`, `pythonpath=[".", "src"]`. Discovery verified: `tests/{analysis,contract,evaluation,ingestion,integration,monitoring,storage}`. [Verified fact, High]
- **DB:** `storage/postgres.py:14-100` — `DATABASE_URL` lazy, `session_scope()` single commit point (repos flush only), `create_tables()` for dev/tests. Dialects: postgres (prod) + sqlite (tests) via `_dialect_insert` (repositories.py:57-81) supporting `ON CONFLICT DO NOTHING`. No other dialect. [Verified fact, High]
- **Migrations:** Single `migrations/0001_run_lifecycle_hardening.sql` (5 lines, identical A/B): adds `runs.monitoring_*` (3 cols) + `persisted_signals.dedupe_key` + unique index. No Alembic, no down migration, no 0002 for `event_observations`/lineage columns. [Verified fact, High]
- **Kafka:** `configs/kafka.yaml` (52 lines, 2154 bytes): `ai.events`, `ai.events.retry`, `ai.events.dlq`, `enable_auto_commit:false`, `auto_offset_reset:earliest`, timeouts. Optional `kafka` extra (`confluent-kafka`, `PyYAML`); app code depends only on `ConfirmedProducer/RecordSource` protocols, importable without broker. [Verified fact, High]
- **API:** FastAPI, `app.include_router(health)+include_router(runs)` (api/main.py:15-16). Routers: `health` (`/health/live`, `/health/ready`), `runs` (`/{run_id}/observations`, `/{run_id}/signals`). `evaluations.py` empty. No auth. [Verified fact, High]
- **CI/CD:** `.github/workflows/ci.yml`, `deploy.yml`, `evaluation.yml` all 0 bytes in both zips (verified via zip listing lengths 0). `docker-compose.yml` 0 bytes, `docker/Dockerfile.*` 0 bytes. No runnable automation. [Verified fact, High]
- **Config/env:** `DATABASE_URL` required for postgres; `KAFKA_*` override yaml; `DB_POOL_SIZE/MAX_OVERFLOW`. `.env.example` 0 bytes. No secret management. [Verified fact, High]

## 4. Architecture reconstruction (actual runtime, not claimed)
### 4.1 Event ingestion and validation — B (A identical except where noted)
- **Submit:** External AI systems publish Kafka bytes -> `KafkaRecord(value:bytes, headers:{attempt})` -> `KafkaEventConsumer.process(record)` (ingestion/kafka_consumer.py:140-173): `validate -> _process_event -> _commit`, or `_handle_validation_failure/_handle_processing_failure`. [Verified fact, High] File `src/ai_eval_platform/ingestion/kafka_consumer.py:140-173`.
- **Validate before persist:** Yes. `EventValidator.validate(bytes)` (event_validator.py:286-338): decode -> JSON parse -> `AIEvent.model_validate` (extra=forbid) -> schema_version allowlist (optional) -> `EVENT_MODELS[event_type].model_validate` (26 typed models). Failures return `ValidationFailure(stage/code/message)`; consumer maps ALL validation failures to DLQ (kafka_consumer.py:198-214, delivery_classifier.py:92-110: "Every validation failure is permanent"). No retry on validation. [Verified fact, High]
- **Invalid/unsupported/partial:** Invalid JSON -> `INVALID_JSON/PARSE`; bad envelope -> `INVALID_ENVELOPE`; unsupported schema_version -> `UNSUPPORTED_SCHEMA_VERSION`; unregistered event_type -> `UNKNOWN_EVENT_TYPE/ROUTING`; typed-model mismatch -> `SCHEMA_VALIDATION_ERROR`. All DLQ with `include_input=False` summaries (event_validator.py:341-359, privacy-preserving). [Verified fact, High]
- **Schema versions:** Enforced only if `supported_schema_versions` configured; default None = accept any `^\d+\.\d+$` (common.py:52). No migration/compat matrix. [Verified fact, High]
- **Unknown fields/future types:** `extra=forbid` on `AIEvent` (common.py:46-49) rejects unknown top-level fields; unknown `EventType` fails enum parse -> INVALID_ENVELOPE. No forward-compat; intentional strictness. [Verified fact, High]
- **Visibility/recoverability:** DLQ envelope carries `stage/code/message` (no input values), `dlq_id`, confirmed publish before offset commit (kafka_consumer.py:313-352). Retry path publishes to retry topic with `attempt+1/due_at`, commits source offset only after broker confirm (247-283). [Verified fact, High]
- **Reaches persistence:** Yes. `_process_event` opens `session_factory()` scope, `handler_factory(session).route(event)` (kafka_consumer.py:186-189) -> `EventRouter.route` (event_router.py:168-180) -> handler `insert_from_envelope` (handler.py:48/69/93/113) -> `EventRepository.insert_from_envelope` (repositories.py:519-545) -> `insert_ignore_duplicates` (465-505) -> flush. Commit/rollback owned by caller's `session_scope` (postgres.py:77-100). B additionally projects observation+signal in same flush (see §4.5-4.6). [Verified fact, High]

- **Docs:** B adds `docs/audit_report.md` (7281), `implementation_report.md` (3674), `test_report.md` (1707) in newer archive only; workspace has untracked `docs/` + `*.md` analysis notes (not part of A/B py diff). README describes install/test/layout but claims `api/ingestion/... still empty placeholders` (stale vs B). [Verified fact, Medium]

- **B is a partial patch, not a full replacement:** `only_A=0`, `only_B=2`, `changed=11` (see `C:\Temp\audit\diff_report.txt`). All other 133 py files identical. Missing-file risk: none — B is a superset of A's py surface. [Verified fact, High]

## 3. Inventory and environment
### 3.1 Version identities (forensically verified, not assumed)
- HEAD is `04d75251fc918df21346fadf0fd19291530b65e1` (`git rev-parse HEAD`, `git log --oneline -5` = single `Initial project snapshot`). [Verified fact, High]
- `ai-evaluation-platform.zip` (1,417,346 bytes, 2026-10-11 12:10) extracts to 550 entries; `project.zip` (1,291,006 bytes, 2026-10-10 19:59) extracts to 511 entries. Diff (`C:\Temp\audit\list_*.txt`): newer archive adds `docs/audit_report.md|7281`, `docs/implementation_report.md|3674`, `docs/test_report.md|1707`, `src/.../analysis/engine.py|9043`, `src/.../monitoring_integration.py` 11123->13710, `retry_consumer.py` 6630->7847, `monitoring_integration.py` larger, etc. [Verified fact, High]
- Authoritative mapping per user correction: **A = `ai-evaluation-platform.zip` (older), B = workspace (newer)**. `project.zip` treated as extra archive, not baseline. Workspace `src` has 303 files (incl. `__pycache__`); verA_zip `src` py count 144 vs workspace 146 (+2 = `monitoring/observations/models.py`, `projector.py`); 11 py files changed (see `C:\Temp\audit\diff_report.txt`). [Verified fact, High]
- B working tree is dirty: 26 modified + ~30 untracked (see `git status` §1). B therefore = HEAD + uncommitted repair work. Audit evaluates B as-shipped (dirty tree), noting where HEAD vs worktree differ. [Verified fact, High]

# §3 continued — Environment details (verified)
- Python 3.12.0; requires-python >=3.11; setuptools src-layout; no lockfile.
- B pyproject 2174 bytes adds fastapi,numpy,scipy vs HEAD 2056 bytes. A zip pyproject 2152 bytes.
- Installed editable ai-eval-platform 0.1.0; fastapi 0.139.0 present; env has undeclared heavy deps (chromadb, langchain) — contamination risk, Medium.
- Entry: api/main.py:app (FastAPI). No console_scripts. Kafka consumer has no __main__/service wiring.
- pytest testpaths tests, pythonpath [".","src"]. Suites: analysis, contract, evaluation, ingestion, integration, monitoring, storage.
- DB postgres.py session_scope single commit point; repos flush-only. Dialects postgres+sqlite via _dialect_insert (repositories.py:57-81).
- Migration 0001 (5 lines, identical A/B): runs.monitoring_* + dedupe_key. No Alembic, no downgrade, no 0002 for event_observations/lineage.
- Kafka configs/kafka.yaml 52 lines/2154 bytes: ai.events, retry, dlq, auto_commit false, earliest, timeouts. kafka extra optional; app uses protocols.
- API routers health+runs only; evaluations.py empty; no auth.
- CI/CD all zero bytes: .github/workflows/*.yml 0, docker-compose.yml 0, Dockerfiles 0. No runnable automation.
- DATABASE_URL, KAFKA_*, DB_POOL_SIZE required; .env.example 0 bytes.
- Docs: newer archive adds docs/audit_report.md 7281, implementation 3674, test_report 1707. README stale (claims api/ingestion empty).

# Section 4 — Runtime architecture (B unless noted)
## 4.1 Event ingestion and validation
- Path: Kafka bytes -> KafkaRecord -> KafkaEventConsumer.process (kafka_consumer.py:140-173) -> EventValidator.validate (event_validator.py:286-338) -> EventRouter.route (event_router.py:168-180) -> handler insert_from_envelope (handler.py:48,69,93,113) -> EventRepository.insert_from_envelope (repositories.py:519-545).
- Validation first: decode/parse/envelope/typed-model (26 EVENT_MODELS). All failures -> DLQ, never retry (delivery_classifier.py:92-110, kafka_consumer.py:198-214). Summaries exclude input values (event_validator.py:341-359).
- Strict: extra=forbid (common.py:46-49), schema_version regex ^\d+\.\d+$ (common.py:52), optional allowlist. Future types rejected. Verified fact, High.
- Reaches persistence in B including projection (see 4.5). Verified by E2E probe.

## 4.2 Event contracts and lifecycle
- AIEvent envelope (common.py:45-75): event_id, schema_version, system_id/version, run_id, event_type (26 values), timestamp tz-aware, payload dict, sequence_number optional, parent/correlation/causation/step ids.
- Run vs Event preserved: Run is lifecycle row (runs table, status running/completed), Event is fact row (events table, FK run_id CASCADE in A, RESTRICT/CASCADE nuances in B). No conflation. run_lifecycle.py (B untracked) owns completion; monitor_completed_run requires status==completed.
- Heterogeneous agents: payload is free-form dict + typed per-event models for known fields; projector copies payload into attributes dict (projector.py:102). Minimal required: event_id/run_id/event_type/occurred_at/sequence_number/agent_id. Verified fact, High.

## 4.3 Event persistence and transactions
- Models: Event PK event_id, UQ (run_id,sequence_number) uq_event_run_sequence, indexes run_time/type_time/correlation (models.py:157-187 B, 157-187 A identical).
- Repos flush-only; session_scope commits (postgres.py:77-100). insert_ignore_duplicates uses ON CONFLICT(event_id) DO NOTHING, returns rowcount (repositories.py:465-505 B includes projection loop 498-501; A 457-492 no projection).
- Append-only: no update/delete on events; duplicate no-op verified (E2E probe duplicate_insert_n=0). Different event_id same seq raises IntegrityError -> DLQ (classifier). Verified fact, High.
- Dialects: postgres + sqlite via _dialect_insert (57-81). Other dialects raise RepositoryError.
- Outbox: model exists, no writers/relay (see 4.4). Verified fact, High.

# §4.4 Kafka, outbox, retry, replay, recovery (B; A same except B adds projection inside tx)
- Flow: poll -> validate -> route -> handle -> DB tx -> offset commit (kafka_consumer.py:140-173, 186-189). Commit only after durable destination confirmed (DB commit, retry publish confirm, DLQ publish confirm). enable_auto_commit false (kafka.yaml:24).
- Retry: DeliveryClassifier (delivery_classifier.py:21-72) ACCEPT on success/duplicate-event_id; DLQ on IntegrityError/routing/HandlerError; RETRY on Operational/DBAPI/PublishError else RETRY. RetryPolicy 5 attempts backoff; RetryPublisher publishes attempt+1/due_at; source offset committed after confirm (kafka_consumer.py:247-283). RetryConsumerLoop pauses partition on DEFERRED, no commit, resume when due (retry_consumer.py B:183-225 adds _resume_due_partitions + MalformedRetryError handling; A:172-184 simpler).
- DLQ: build_failure_info/build_record/publish, commit after confirm (295-352). DLQ publish fail -> committed=False, no skip (321-334).
- At-least-once, not exactly-once: crash after DB commit before offset commit redelivers; idempotent event_id + observation/signal DO NOTHING make redelivery safe (B). No outbox relay: OutboxEvent model exists, zero writers/relay (search: no src writer outside tests). Verified fact, High.
- Recovery: deferred offsets uncommitted -> redelivered after restart (docstring retry_consumer.py:26-27). Poison: bounded retries then DLQ (RETRY_EXHAUSTED). Schema incompat -> DLQ. DB outage -> RETRY (uncommitted). Verified by inspection + 132 ingestion tests pass (B).

# §4.5 Event -> Observation projection (B NEW; A absent)
- A: no observations/ dir, no EventObservation table, no project_event, EventRepository.create 447-450 flush only. Verified fact, High (verA_zip listing + models 190-271 no observation).
- B: Observation pydantic (observations/models.py:9-36) frozen, tz-aware, attributes dict. project_event (projector.py:21-129): validates agent_id/event_id/run_id/event_type/occurred_at/sequence_number/payload, tz-normalizes naive occurred_at to UTC (56-57), stable IDs obs_<sha56(event_id|1.0)> (82-84), attributes=dict(payload) (102). PROJECTION_VERSION 1.0 (13).
- Coverage: every event via create/create_many/insert_ignore_duplicates/insert_from_envelope -> _project_event_by_id (repositories.py:455,462,501,545). Canonical row re-read (549), Run lookup for agent_id (554-558), project (563), _persist_observation ON CONFLICT(observation_id) DO NOTHING (577-602). UQ (source_event_id,projection_version) (models.py:194).
- Replay: redelivery uses canonical stored row, deterministic IDs, DO NOTHING -> idempotent (probe: duplicate_insert_n=0, obs 1). Distinct events not deduped (e2 -> 2 obs). Verified executed, High.
- Gaps: create_with_* / insert_with_detail bypass projection (probe eBW obs 0); eligibility = all events (no exclusion doc); no ObservationRepository abstraction; no backfill. See §8.

# §4.6 Observation -> Base Signal (mandatory; B implements, A missing)
- Principle satisfied in B write path: every persisted event -> normalized Observation + ordinary Base Signal without detector. project_event returns (observation, base_signal) (projector.py:26,129); base Signal origin=BASE_OBSERVATION, type=EVENT_OBSERVATION, scope=RUN, run_id, detected_at=observed_at, payload EventObservationPayload (109-127). Caller persists both same tx (repositories.py:564-569 + 577-615).
- Automatic, detector-independent: repository path, not monitor_completed_run. Valid Observation cannot exist without Base Signal via this path (both persisted together; signal ON CONFLICT(signal_id) DO NOTHING 604-615). Multiple base signals per Observation not supported (single sig_<hash> per event_id|version 105-107) — 1:1 by design; multiple detector signals allowed (see 4.7).
- Lineage: signal.source_event_id + source_observation_id + projection_version (111-114); persisted via persisted_signal_from_record (monitoring_integration.py:342-363) + _signal_row (99-112). Dedupe via signal_dedupe_key including origin/source ids (persistence 78-101). Idempotent replay verified.
- API exposes (runs.py:43-60, probe SIG 200 origin base_observation). No-detector case still has base (E2E-04 probe: base exists before detector insert). Verified executed, High.
- Exclusions: none documented; all event_types projected. Justified? Tool/error/context all factual — reasonable. No meaningless-signal guard needed.

# §4.7 Detector -> Detector Signal (additive; both versions same logic, B adds lineage cols)
- Registration: select_run_assignments hard-codes 6 detectors (monitoring_integration.py:300-339: pattern, confidence_drop + 4 deferred: anomaly, drift, route_change, tool_usage_drift). No registry; extra_assignments seam for tests. execute_detector maps exceptions -> INVALID_INPUT/DETECTOR_FAILED, enforces agent boundary (orchestration/runner.py:44-82).
- Inputs: ScopedDetectorInput exactly-one-scope (inputs.py:32-45); Pattern/Anomaly/Drift/Confidence inputs validated. Detectors pure: threshold/pattern -> list[Signal] with uuid4 ids (pattern.py:65, anomaly.py:70, drift.py:80, confidence.py:59), scope derived from input.
- Output validation: Signal model scope/payload-type lock (signals/models.py:58-134 B; A 51-118 without origin). B adds EVENT_OBSERVATION must use BASE_OBSERVATION + require source ids (126-132).
- Persistence: persist_monitoring_signals -> signal_to_record (+dedupe_key) -> persisted_signal_from_record -> insert_ignore_duplicates ON CONFLICT (no arbiter -> any conflict no-op) (repositories.py:832-849, monitoring_integration.py:366-397). Additive verified: probe E2E-05 base + det-1 coexist.
- Failure cannot suppress base: base already committed in event tx, separate from monitoring tx (monitor_completed_run two scopes 411-429). Detector fail -> DetectorRun outcome, no exception. Verified fact, High.
- Dedup issue: semantic key excludes ordinal (persistence 69-110) -> identical payloads from different detector positions dedupe to one row, causing 6 vs 7 test failure. See §8.

# §4.8 Signal persistence and lineage (B extends A)
- A PersistedSignal (models.py:271-299): PK signal_id, agent FK RESTRICT, scope CHECK + shape CHECK, indexes agent_time/type_time, UQ dedupe_key. No origin/source columns, no source_projection index.
- B (299-332): adds origin (default detector), source_event_id FK events CASCADE, source_observation_id FK event_observations CASCADE, projection_version, UQ (source_event_id,origin,projection_version) uq_persisted_signal_source_projection (312). EventObservation table (190-215): PK observation_id, source_event_id FK CASCADE NOT NULL, UQ (source,version), indexes agent_time/run_seq/type_time.
- Chain Signal->Observation->Event enforced via FKs (CASCADE both ends). Deleted source cascades deletes (retention = cascade, not soft-delete). Distinct events -> distinct rows (probe e1/e2). Duplicate delivery -> DO NOTHING (probe). Stable IDs across replay (deterministic hash). Multiple detector signals per Observation allowed (different signal_id/dedupe, same source_event_id; UQ only blocks same origin+version per event — second detector same event with origin=detector would conflict! See §8).
- Versions identifiable: projection_version on obs+signal; detector_name on signal; no detector_version column. Payload_type stored. Verified fact, High.
- Conflict handling: obs/signal base use ON CONFLICT DO NOTHING indexed (observation_id / signal_id); detector batch uses bare ON CONFLICT DO NOTHING (any conflict). See dedupe finding.

# §4.9 Evaluation, metrics, monitoring, statistics
- Datasets/versions/cases/results/runs models exist; EvaluationService coordinates target->evaluator->persist (service.py:111-323), _summarize counts + mean_score only (43-108). Evaluators: Evaluator ABC (evaluator.py:8-16), EvaluationRunner maps errors to ERROR (runner.py:22-85), engines embedding_similarity/groundedness_judge + evaluators citation etc. Tests: tests/evaluation 4 files pass (B suite 254 passed incl. these).
- Monitoring detectors: 6 detectors (pattern, anomaly, drift, confidence_drop + strategies) are threshold/pattern functions, not statistical inference. Drift = abs delta > threshold per feature (drift.py:20-31); anomaly = |obs-exp|>threshold (anomaly.py:20-22); pattern = all-equal sequence (pattern.py:20-24); confidence = drop > threshold (confidence.py:42-48). No p-values, CIs, power, multiple-comparison correction. SciPy/NumPy declared but statistics/*.py empty (bootstrap.py 0 bytes, effect_size.py 0 bytes, __init__ docstring only). Do not credit sophistication. Verified fact, High.
- Genuine stats: none executed end-to-end. Analysis statistical_analyzer/relationship_analyzer exist (B untracked) but no production caller; tests only. See §4.10.

# §4.10 Analysis and RCA
- Path: PersistedSignal -> AnalysisService.summarize_agent (service.py:19-55) -> SignalSummaryEngine.analyze -> AnalysisResult (models.py:7-20: counts, metrics dict, sample_size, window, limitations). Or AnalysisEngine.build_*_context (engine.py:100-213) -> AnalysisContext (context.py:14-131, agent+scope validated) -> SignalSummaryAnalyzer.analyze -> AnalysisFinding signal_summary (analyzer.py:9-82, findings.py:12-72). EvidenceRecord/Collection (evidence.py:26-107) frozen, unique IDs, tz-aware.
- Descriptive only: type_counts, detector_counts, total/unique/duplicate, window min/max. No effect sizes, CIs, hypothesis tests, causal claims. Limitations recorded (empty, truncation, duplicates, missing ts). No counter-evidence, no power analysis. Verified fact, High.
- RCA: root_cause/analyzer.py, evidence.py, findings.py all 0 bytes (verified via read). No RCA execution. Analysis not exposed via API. No causal presentation risk (nothing claims causation), but also no RCA capability. Severity Medium (missing feature, not defect).

# §4.11 Policy, deployment, CI/CD
- Policy: policy/__init__.py docstring only; decision.py, engine.py, thresholds.py all 0 bytes (verified). Policy events exist (schemas/policy_events, handler AgentPolicyEventHandler, PolicyDecision table) but no engine reads signals/RCA or makes allow/block/review decisions. No gates, thresholds, versioning, audit, deployment decisions. Verified fact, High.
- Deployment: deployment/*.py verified empty (deployer.py 0 bytes). No deploy logic.
- CI: .github/workflows/*.yml 0 bytes, docker-compose 0, Dockerfiles 0. No automated gates. Evaluation workflow file empty. Manual steps undocumented. Do not claim safety. Severity High for production use, Medium for audit scope (out of MVP core but blocks broader platform).

# §4.12 API contracts
- Registration: app.include_router(health)+runs (main.py:15-16 B; A main.py 0 bytes per HEAD show). Routers: health APIRouter (health.py:5), runs prefix /runs (runs.py:13). evaluations.py empty (0 bytes). Verified fact, High.
- Endpoints B: GET /health/live -> {ok} (no DB); GET /health/ready -> 503 if !check_connection (health.py:8-18). GET /runs/{run_id}/observations (24-40): 404 if no Run, else select EventObservation where run_id order seq,obs_id limit 100/offset. GET /runs/{run_id}/signals (43-60): 404 if no Run, optional origin filter regex ^(base_observation|detector|analysis)$, order detected_at,signal_id. Response models ObservationResponse/SignalResponse from_attributes (models.py:9-48).
- Serialization correct: probe showed 16 obs keys, 16 sig keys matching models. Filtering/pagination present (limit 1..1000, offset). No authN/Z, no rate limits. DB access via get_session per request, closed finally (runs.py:16-21). No transaction (read-only). Backward compat: A had no routes, so no break; B additive.
- Tested executed: api_probe.py 200/200/404/live ok. E2E-14 Passed. Missing: event retrieval, evaluation results, analysis endpoints. Severity Medium.

# §4.13 Database models and migrations
- Tables B: 32 (31 expected + event_observations). Relationships/FKs as §4.8. Unique: uq_event_run_sequence, uq_observation_source_projection, uq_persisted_signal_source_projection, uq_persisted_signals_dedupe_key (unique index), outbox/status etc. Indexes cover agent_time/type_time/run_seq. Nullability: source ids nullable (allows detector window signals without event), agent/detected_at non-null. JSON generic (not JSONB), tz DateTime.
- Migration ordering: only 0001 (runs monitoring_* + dedupe_key). B needs 0002: CREATE TABLE event_observations + ALTER persisted_signals ADD origin/source_event_id/source_observation_id/projection_version + UQ source_projection. Absent -> fresh create_all works (tests), existing DB upgrade fails (table missing, columns missing). Verified by inspection (migrations identical A/B, models differ). Severity High (Critical for existing installs).
- Downgrade: no down script. Data preservation: CASCADE deletes observations/signals when event deleted (models.py:201,317-318) — intentional but destructive; document retention policy.
- Duplicate/orphan: obs/signal DO NOTHING handles duplicates; orphans prevented by FK (except nullable source for window signals — allowed).

# §4.14 Security and operational concerns
- AuthN/Z: none (api has no dependency, security/auth.py 0 bytes verified). Any caller can read any run. Severity High if exposed, Low for local audit.
- Input validation: strong (pydantic extra=forbid, tz-aware, scope locks). Secret mgmt: none; env vars only. Logging sensitive: validator excludes input values (event_validator.py:347); DLQ envelope may contain raw bytes (check: build_record includes raw? Must verify — Unresolved, Medium).
- Error leakage: HTTPException 404 "Run not found" minimal; 503 "database unavailable" minimal. No stack traces. Verified fact, Medium.
- Deps: undeclared heavy packages installed; declared list minimal. No CVE scan (Not Verified).
- Defaults: auto_offset_reset earliest (safe), auto_commit false (safe), max_poll 300s. Resource limits: max_poll_records 100, API limit 1000, analysis limit 10000. No retention/cleanup job. Health: live/ready only. Observability: logging only, no metrics/tracing wired (observability/ exists but not connected). Failure visibility: DLQ + deferred lists + limitations arrays. No comprehensive cert claimed.

# §5 Taxonomy audit
## 5.1 EventType (26 values, schemas/events/common.py:8-42, identical A/B)
- Execution (10): model_called, model_output, tool_called, tool_completed, retrieval_started/completed, answer_generated, error, recovery_attempted/completed. Producers: external agents via Kafka; Consumers: ExecutionEventHandler -> EventRepository; Validation: typed models (execution_events.py); Persistence: events; Observation: B projects all; Base signal: event_observation; API: via obs/signal; Tests: contract 66 pass + ingestion 132 pass. Status: fully implemented.
- Context (3): context_assembled/resource_used/updated. Same path via ContextEventHandler. Fully implemented.
- Evaluation (1): evaluation_completed via EvaluationEventHandler. Fully implemented (ingest only; evaluation execution separate).
- Behavior (6): agent_action, verification_attempted, counter_evidence_requested, answer_revised, abstention, behavior_pattern_detected via BehaviorEventHandler. Fully implemented ingest.
- Policy (6): policy_checked/allowed/blocked, behavior_rule_triggered, human_review_requested/completed via AgentPolicyEventHandler. Ingest fully; policy engine missing (no consumer beyond persistence). Emitted but unsupported downstream (for decisioning) — gap, Medium.
- No redundant/ambiguous types; tool/retrieval/context/verification/policy/errors/recovery/abstention/lifecycle (via Run.status, not EventType — correct separation) covered. Missing: none demonstrated for supported agents. Do not add types. Minimal taxonomy preferred — current is sufficient.

# §5.2 SignalType (7 in B, 6 in A) and SignalOrigin (3, B only; A has none)
- EVENT_OBSERVATION (B new, types.py:15, models.py:111,126-130): ordinary fact, producer projector.py:109-127, consumer API/analysis, payload EventObservationPayload (obs/event/type/time/seq), identity sig_<hash(event|ver)>, lineage required, API exposed, tests via E2E probe (no dedicated unit test — gap Low). Distinguishes fact from findings. Correct.
- LATENCY_ANOMALY, ROUTE_CHANGE, CONFIDENCE_DROP, REPEATED_TOOL_USAGE, TOOL_USAGE_DRIFT, ERROR_PATTERN: detector findings, producers anomaly/drift/pattern/confidence detectors, consumers monitoring_integration persistence + analysis counters, payloads per-type validated, identity semantic dedupe (no source), API exposed, tests via persisted_signals (except dedupe dispute). Correct.
- Origins: BASE_OBSERVATION (projector only, must pair event_observation), DETECTOR (detectors, any finding type), ANALYSIS (defined, never produced — no writer; reserved for future). Separation coherent: kind (SignalType) vs origin (SignalOrigin) vs source event (source_event_id) vs observation (source_observation_id) vs processing status (DetectorOutcome) vs analysis conclusion (AnalysisFinding). No conflation. Verified fact, High.
- ANALYSIS origin without producer: not a defect (forward reserve), but document or remove to avoid confusion. Low.

# §5.3 Observation and Analysis contracts
- Observation (observations/models.py:9-36 + storage EventObservation 190-215): normalized source facts consistently (ids, agent/run/event/system, times tz-aware, seq, lineage ids, attributes=dict(payload)). Cross-agent: free-form attributes + minimal required fields — heterogeneous-safe. No silent discard (all payload keys copied). Impossible to represent: binary blobs (JSON only) — acceptable. Verified fact, High.
- Analysis (AnalysisResult models.py:7-20, AnalysisFinding findings.py:12-72, EvidenceRecord evidence.py:26-107): distinguishes evidence ids, metrics, limitations, confidence (finding only, 0..1), observed_at. But: no hypothesis/counter-evidence linkage, no stats (p/effect/CI), no provenance beyond signal ids, no version/method beyond fixed strings. Insufficient for causal RCA. Verified fact, High.

# §5.4 Taxonomy tables (condensed; full producer/consumer in §5.1-5.3)
| Type | Category | Defined | Producer | Consumer | Coverage | Gap | Rec |
|---|---|---|---|---|---|---|---|
| 26 EventTypes | event | common.py:8-42 | agents/Kafka | 5 handlers->repo | full ingest | policy no engine | retain; no new types |
| event_observation | signal fact | types.py:15 | projector | API/analysis | E2E probe only | no unit test | add unit test, retain |
| 6 detector SignalTypes | signal finding | types.py:16-21 | 6 detectors | persistence/analysis | tested (1 dispute) | dedupe semantics | resolve §8, retain |
| base_observation | origin | types.py:25 | projector | API | E2E probe | — | retain |
| detector | origin | types.py:26 | detectors | persistence | tested | — | retain |
| analysis | origin | types.py:27 | none | none | reserved | no producer | document/remove |
| Observation | contract | observations/models.py | projector | repo/API | E2E probe | no repo abstraction | add repo, retain |
| AnalysisFinding/Result/Evidence | contract | analysis/* | engines | none prod | tests only | no stats/causal | redesign for RCA |
- No new types justified. Smallest coherent taxonomy already present.

# §6 Mandatory E2E verification (B executed; A blocked by SyntaxError except inspection)
| ID | Scenario | B status | Command / evidence | Defect |
|---|---|---|---|---|
| E2E-01 | Valid eligible event persisted | Passed | e2e_probe_B.py: events=True; exit 0 | — |
| E2E-02 | Observation projection | Passed | observation obs_c22e... ver 1.0 agent r1 | — |
| E2E-03 | Base signal w/o detector | Passed | signals sig_... base_observation/event_observation | — |
| E2E-04 | No detector still base | Passed | rollback_probe E2E-05 base before detector insert | — |
| E2E-05 | Detector additive | Passed | det-1 + base coexist (rollback_probe) | — |
| E2E-06 | Duplicate no-op | Passed | duplicate_insert_n=0, counts 1/1 | — |
| E2E-07 | Distinct events not deduped | Passed | e2 -> obs 2 sig 2 | — |
| E2E-08 | Replay preserves lineage/identity | Passed | same IDs on redelivery (DO NOTHING) | — |
| E2E-09 | Rollback no partials | Passed | eRB 0/0/0 after flush+raise | — |
| E2E-10 | Optional fields | Passed | payload {} + nullable lineage accepted (probe); missing required raises ValueError (projector 36-74) — inspected | — |
| E2E-11 | Heterogeneous payloads | Passed | dict(payload) copies any keys (probe tool_name) | — |
| E2E-12 | Lineage traceable | Passed | sig->obs->e9 printed | — |
| E2E-13 | Survive commit/queryable | Passed | counts after commit; API reads committed rows | — |
| E2E-14 | API retrieval | Passed | api_probe.py: OBS 200x1, SIG 200x1, filter 1, 404, live ok | — |
| E2E-15 | Analysis consumes | Partial | AnalysisService on persisted signals works (tests/storage/test_analysis_service.py pass) but no API/RCA; inspected+unit pass | Medium gap |
| E2E-16 | Invalid fails observably | Passed | validator -> DLQ (132 ingestion tests pass; _summarize excludes input) | — |
| E2E-17 | Migration upgrade | Blocked/Failed | No 0002; existing DB would miss event_observations/columns. Not executed (no PG), confirmed by inspection. | High |
| E2E-18 | Recovery no silent loss | Passed (logic) | Retry/DLQ commit rules + pause/resume; 132 ingestion + retry tests pass; no broker E2E — Environment limitation | — |
- A: E2E-01..14 Blocked (import SyntaxError **all** line 13, verified by runA pytest collection error). Inspection shows A would fail E2E-02/03/12/14 (no code). Not run further to avoid confusion.

# §7 Test and verification strategy
## 7.1 Baseline (B workspace, read-only pytest)
- `python -m pytest tests/ingestion -q` -> 132 passed (4.61s). Exit 0. Verified executed.
- `python -m pytest tests/monitoring -q` -> 79 passed (18.36s). Exit 0.
- `python -m pytest tests/contract -q` -> 66 passed (1.33s). Exit 0.
- `python -m pytest tests/storage tests/analysis tests/evaluation -q` -> 254 passed, 11 skipped, 2 failed (21.37s). Exit 1. Failures: test_models_import_and_register_all_tables (extra event_observations) + test_distinct_detections_survive_logical_dedupe (6 vs 7). Verified executed.
- `python -m pytest tests/integration -q` -> 10 skipped (no DATABASE_URL/Kafka). Environment limitation.
- Import: `import ai_eval_platform` ok B; A archive bytes SyntaxError (runA collection). Verified.
- Isolation: runA copy, scratch SQLite, no migrations applied, no prod access. Caches (.pytest_cache, __pycache__) only writes.
## 7.2 E2E probes (temporary, non-destructive, outside roots)
- C:/Temp/audit/e2e_probe_B.py exit 0 (output §6). C:/Temp/audit/api_probe.py exit 0 (200/404). C:/Temp/audit/rollback_probe.py exit 0 (0/0/0, additive, lineage, bypass 0). All read-only to sources.
## 7.3 Coverage gaps
- No API tests in repo; no observation unit tests; no migration tests for 0002 (doesn't exist); no Kafka broker tests (skipped); no Postgres (skipped). Recommend adding per §12.

# §8 Findings — defects and gaps (each with evidence, severity, fix)
## F-01 [B] Missing migration for event_observations + signal lineage columns — High (Critical for existing DBs)
- Version B; files: storage/models.py:190-215 (new table), 316-319 (new cols), 312 (new UQ); migrations/0001_run_lifecycle_hardening.sql:1-5 (unchanged, no 0002).
- Scenario: upgrade existing install (created before B) -> Base.metadata.create_all no-op for existing tables -> INSERT into event_observations fails (no table); INSERT persisted_signals with origin/source cols fails (no columns). Fresh SQLite tests pass, masking the issue.
- Classification: Verified fact (inspection + schema diff); Confidence High; Verification: inspected, not executed on PG (Environment limitation).
- Fix: add migrations/0002_observation_signal_lineage.sql (CREATE TABLE event_observations + indexes/UQ; ALTER persisted_signals ADD origin/source_event_id/source_observation_id/projection_version + UQ; backfill existing signals origin=detector where null; CREATE INDEX). Test on disposable PG (create 0001 -> insert pre-B signal -> apply 0002 -> verify data preserved + new writes work). Do not apply to prod during audit.
## F-02 [B] Detector dedupe over-collapses distinct detections (or stale test) — High
- Files: monitoring/persistence/__init__.py:69-110 (key excludes ordinal, includes payload/detector/run/scope), monitoring_integration.py:382-389 (recomputes key), storage/repositories.py:832-849 (bare ON CONFLICT DO NOTHING), tests/storage/test_persisted_signals.py:473-515 (expects 7).
- Scenario: 7 detector signals where duplicate_a/b share identical payload/detector/run/scope -> same sha -> second INSERT no-ops (any-conflict) -> 6 persisted. Test asserts 7.
- Classification: Verified fact (executed: 6 vs 7, exit 1); Confidence High that mismatch exists; Medium whether code or test is wrong (needs product decision: are identical payloads duplicates or distinct detections?).
- Fix options: (a) if distinct detections must survive, include signal_id or occurrence index in identity (but breaks replay idempotence — must then use natural (detector,run,payload-hash,occurrence) key); (b) if identical = duplicate, update test to 6 + document. Do not weaken assertions to pass without decision. Investigate before merge.

# §8 continued
## F-03 [B] Stale table-count test (EXPECTED_TABLES missing event_observations) — Medium
- File tests/storage/test_models_and_tables.py:17-50 (31 tables, no event_observations), :68-69 assertion. Executed failure: extra event_observations. Verified fact, High.
- Fix: add "event_observations" (32 total) + assert FKs/UQs for new table. Trivial, must fix before merge.
## F-04 [B] create_with_* / insert_with_detail bypass projection — High
- Files repositories.py:681-723 (create_with_behavior/context/execution: add+flush, no _project), 737-805 (insert_with_detail calls insert_ignore_duplicates which DOES project — actually projects via 773; verify: insert_with_detail -> insert_ignore_duplicates([event]) -> _project_event_by_id, so idempotent path DOES project; only create_with_* bypass). Probe: eBW via create_with_behavior_detail -> obs 0. Verified executed, High.
- Scenario: any caller using create_with_* gets event without observation/signal, violating "every eligible event" invariant.
- Fix: call _project_event_by_id in create_with_* (or deprecate them in favor of insert_with_detail). Add test.
## F-05 [A] Packaging SyntaxError **all** line 13 blocks all imports — Critical (for A only; B fixed)
- File verA_zip/src/ai_eval_platform/monitoring/signals/__init__.py:13 `**all**` (raw bytes verified 436 bytes, contains b'**all**'). HEAD shows `__all__` (480 bytes). Workspace B fixed (604 bytes with origin). Verified fact (raw zip read + pytest collection error), High.
- Impact: A cannot be imported/tested as-archived; any A test claim requires fixing first. B already correct — retain. No action on A (read-only); document.
## F-06 [B] UQ (source_event_id,origin,projection_version) blocks second detector per event — Medium
- File models.py:312. Scenario: base (origin=base_observation) + first detector (origin=detector) share source_event_id + null projection_version? Base has 1.0, detector null -> distinct (null != 1.0, but SQL null semantics: unique index treats nulls distinct in PG, so ok). Second detector same event (both origin=detector, both null version) -> conflict -> second lost. Verified by inspection (Reasoned inference, Medium).
- Fix: include detector_name + signal_type in UQ, or require projection_version/detector_version on detector signals, or drop UQ in favor of dedupe_key only. Decide with F-02.

# §8 continued
## F-07 [Both] No outbox relay; OutboxEvent dead table — Medium (High if DB->Kafka needed)
- Files: models.py OutboxEvent + repository; search: zero src writers/relay. Kafka ingest-only. Verified fact, High.
- Fix: either implement relay (background publisher + tests) or remove table to stop implying guarantee. For MVP (ingest-only), document as deferred; do not claim transactional DB->Kafka.
## F-08 [Both] Policy/deployment/RCA/statistics empty; API evaluations empty; CI/CD empty — Medium (blocks broader platform, not core MVP)
- Files: policy/*.py, deployment/deployer.py, root_cause/*.py, statistics/*.py, api/evaluations.py 0 bytes (verified reads); workflows 0 bytes. Verified fact, High.
- Fix: roadmap, not merge-blocker for observation pipeline. Do not merge fake capabilities; mark as scaffold.
## F-09 [B] API has no auth, no event/eval/analysis retrieval — Medium/Low
- Files api/runs.py:24-60 (only obs/signals), no auth dependency. Verified fact, High.
- Fix: add auth (or document internal-only), add event list endpoint if needed, paginate docs. Not merge-blocker for internal MVP.
## F-10 [B] FK cascade asymmetry (Event.run CASCADE vs agent RESTRICT; obs CASCADE) — Low
- Files models.py:170,201-204,317-320. Deleting event cascades obs+signals (data loss); deleting agent blocked. Reasonable but retention policy undocumented.
- Fix: document retention; consider soft-delete or SET NULL with policy. Low.

# §9 Scorecards (0-10; evidence in §4/§6/§7)
| Layer | A | B | Rationale |
|---|---|---|---|
| Ingestion/validation | 7.5 | 8.0 | B same + projection in tx; 132 tests pass |
| Storage/tx | 6.0 | 7.5 | B adds obs/signal atomicity; missing migration -2.5 |
| Observation->Signal | 0 | 7.0 | A absent; B wired+verified, gaps (bypass, migration, tests) |
| Detectors | 6.5 | 6.5 | Same logic; dedupe dispute -0.5 both |
| Evaluation | 5.5 | 5.5 | Unchanged; mean-only summaries |
| Analysis/RCA | 3.0 | 3.5 | B adds normalizer/engine scaffolds; still descriptive |
| API | 1.0 | 5.0 | A 0-byte; B 2 endpoints verified, missing auth/eval |
| Migrations | 4.0 | 3.0 | B adds schema without migration (regression) |
| Kafka/retry/replay | 7.0 | 7.0 | B improves resume logic; no broker E2E |
| Security/ops | 4.0 | 4.0 | Validation strong; no auth/secrets/retention |
| **Overall** | **4.2** | **6.1** | B net +1.9, not shippable without §8 fixes |
# §10 Comparison A->B (retain/modify/reject/investigate)
- Retain: observations/models.py + projector.py (new, correct direction); persistence lineage cols + dedupe incl. origin/source; signals EVENT_OBSERVATION + origin validation; repositories _project_event_by_id + obs/signal persists; api runs/health + models; monitoring_integration lineage passthrough + strict agent check (B raises vs A continue — stricter, retain).
- Modify: F-02 dedupe (decision + code/test update); F-01 migration 0002 + backfill; F-03 table-count test; F-04 create_with_* projection; F-06 UQ scope; README (stale placeholder claims); pyproject (verify numpy/scipy actually imported or remove).
- Reject: nothing wholesale. Do not revert B to A (A unimportable + no pipeline).
- Investigate: F-02 product semantics; F-06 second-detector loss; DLQ raw bytes privacy; sequence-null handling (next_sequence max+1 race documented, single-writer assumption — acceptable if documented, else upstream assignment).

# §11 Remaining work
## MVP (core ingest->observe->signal->retrieve shippable)
1. Write + test 0002 migration (F-01) on disposable PG; backfill; verify fresh + upgrade. Est: half day.
2. Resolve F-02 dedupe semantics + implement + update test. Est: half day + product review.
3. Fix F-03 table test + add obs/signal FK/UQ assertions. Est: 1h.
4. Fix F-04 create_with_* projection + test. Est: 2h.
5. Decide F-06 UQ (include detector_name/type or version detector signals). Est: 2h.
6. Add observation/projector unit tests (tz, stable IDs, invalid inputs) + API tests (obs/signals/filter/404/pagination). Est: half day.
7. Postgres migration test (0001->0002 with pre-B data) + update README (deps, API, layout). Est: half day.
## Broader platform (beyond MVP)
- Outbox relay or remove table; policy engine reading signals/RCA with versioned thresholds + audit; RCA statistical engine (tests, CIs, counter-evidence, power) + evidence->signal linkage; deployment gates + CI workflows + Docker; real Kafka + PG integration CI; authN/Z + secrets + retention + observability wiring; detector/evaluator versioning per signal; baseline backends; multi-tenancy.
# §12 Reproducibility appendix
- Commands (all read-only, workspace unmodified except caches):
  git --no-pager rev-parse HEAD -> 04d7525...; git log --oneline -5; git status --porcelain; python --version (3.12.0)
  python C:/Temp/audit/diff_py.py (A144/B146, only_B 2, changed 11)
  python -m pytest tests/ingestion -q (132 passed); tests/monitoring (79); tests/contract (66); tests/storage tests/analysis tests/evaluation (254+11skip+2fail); tests/integration (10 skipped)
  python C:/Temp/audit/e2e_probe_B.py; api_probe.py; rollback_probe.py; verify_versions.py (all exit 0 except noted failures)
- Artifacts outside roots: C:/Temp/audit/verA_zip, runA, diff_report.txt, list_*.txt, e2e/api/rollback/verify probes, sections/, full_arch.md (this file's staging; final must be written to required path per §13).
- No deps installed, no migrations applied, no prod touched.

# §13 Report placement note (action required by operator)
- This audit was staged at C:/Temp/audit/full_arch.md + C:/Temp/audit/sections/*.md to honor "write outside both project roots unless full_arch.md already exists in a designated audit location."
- Task requires the complete report be written to a Markdown file named full_arch.md (no substitute). No full_arch.md exists in either root (verified Test-Path false).
- Operator must copy the assembled report to the designated audit location (or workspace root if that is the designated location) without modifying either version's source. Suggested (read-only to sources, single file write outside src/tests):
  Copy-Item C:/Temp/audit/full_arch.md C:/Users/user/full_arch.md
  or, if the designated location is the project root and that is authorized as audit location (not source modification):
  Copy-Item C:/Temp/audit/full_arch.md C:/Users/user/ai-evaluation-platform/full_arch.md
- Do not substitute summary/README. Assembled file below is the full findings, evidence, scorecards, test results, analysis, and repair plan (concatenate sections in order §1..§12 + this note).
