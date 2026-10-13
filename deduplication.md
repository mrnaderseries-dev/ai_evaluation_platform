# Engineering Investigation: Signal Deduplication Ambiguity (7 vs 6)

**Scope:** Current workspace (Version B, newer candidate). HEAD `04d7525`. Python 3.12.0, SQLAlchemy 2.0.51, PostgreSQL 17.10 (isolated scratch cluster), SQLite (in-memory).
**Rule honored:** No application source, model, migration, test, config, or project version was modified. Probes ran only on scratch databases (`:memory:` SQLite; `dedupe_audit` / `rowcount_audit` on PostgreSQL, created and dropped after use). Artifacts: this report plus `C:\Temp\audit\*.py` probes.
**Verdict up front:** The 6-vs-7 is caused by **(1)** the application-level logical dedupe key intentionally treating `duplicate_a` and `duplicate_b` as the same signal, enforced by **(2)** the `uq_persisted_signals_dedupe_key` unique index under bare `ON CONFLICT DO NOTHING`. It is **not** a PK collision, **not** the `(source_event_id, origin, projection_version)` UQ, **not** a SQLite-vs-PostgreSQL NULL-semantics difference, and **not** an identity-generation bug. The test's expectation of 7 is incorrect under the implemented — and architecturally correct — semantics; the correct persisted count is **6**. A separate discovery: on PostgreSQL `insert_ignore_duplicates` returns `rowcount=0` even for freshly inserted rows (driver artifact). That affects return-value interpretation only, not durability, and must be fixed separately; it does not change this verdict.

## 1. Investigation scope and leads verified

Prior-audit leads treated as hypotheses and re-proven:

- Workspace is Version B (newer candidate). `git rev-parse HEAD` = `04d7525`; workspace has `monitoring/observations/{models,projector}.py`; `storage/models.py` carries `EventObservation` + `PersistedSignal.origin/source_*` columns; the failing test reproduces (`pytest …::test_distinct_detections_survive_logical_dedupe` → `assert 6 == 7`, exit 1). [Verified fact, High]
- Event ingestion projects observations + base signals: `EventRepository.{create,create_many,insert_ignore_duplicates,insert_from_envelope}` all invoke `_project_event_by_id` (`repositories.py:452-505, 547-615`). Not the path exercised by the failing test (it calls `persist_monitoring_signals` directly), but it confirms the base-signal path is separate. [Verified fact, High]
- Identity involves `source_event_id`, `origin`, `projection_version`: `Signal` (`signals/models.py:36-40`), `SignalRecord` (`persistence/__init__.py:19-23`), `PersistedSignal` (`models.py:316-319`), identity dict (`persistence/__init__.py:78-101`), UQ `uq_persisted_signal_source_projection` (`models.py:312`). [Verified fact, High]
- Prior concern "dedupe key excludes ordinal": confirmed — `signal_dedupe_key(record, ordinal=None)` accepts an ordinal but deliberately ignores it (`persistence/__init__.py:69-77`). This exclusion is correct (case D in §4); the test's `ord`-prefixed names are misleading. [Verified fact, High]
- SQLite vs PostgreSQL NULL-UQ behavior: tested directly on both backends (§5, §6). No divergence on the operative constraint. [Verified fact, High]

## 2. Complete deduplication path — what defines identity at each layer

### 2.1 Signal model — `src/ai_eval_platform/monitoring/signals/models.py:30-134`
- `signal_id` (36, required): transport/row identity (PK). UUIDs from detectors (`pattern.py:65`, `anomaly.py:70`, `drift.py:80`, `confidence.py:59`); deterministic `sig_<sha>` for base signals (`observations/projector.py:105-107`).
- `origin` (37, default `detector`): `base_observation | detector | analysis`. Base signals must pair `event_observation` type with source ids (126-132).
- `source_event_id / source_observation_id / projection_version` (38-40, nullable): lineage. Detector signals in the failing test leave all three `None`.
- `agent_id / signal_type / scope / run_id / evaluation_run_id / window_start / window_end / detected_at / payload` (41-49): semantic content. Scope exactly-one-of run/evaluation/window (59-106); payload type locked to signal type (108-124).
- **Identity at this layer:** `signal_id` distinguishes Python objects, but persistence does **not** treat it as semantic identity (see 2.4). Two `Signal`s with different `signal_id`s and identical semantic fields are logically the same detection. [Verified fact, High]

### 2.2 Observation→signal projection — `src/ai_eval_platform/monitoring/observations/projector.py:21-129`
- `project_event` validates and builds `(Observation, Signal)` with deterministic ids: `obs_<sha(event_id|1.0)>` (82-84), `sig_<sha(event_id|1.0)>` (105-107); `PROJECTION_VERSION = "1.0"` (13).
- Base signal: `origin=base_observation`, `type=event_observation`, `scope=run`, `source_*` filled (109-127). Detector-independent; persisted in the event's own transaction (`repositories.py:547-615`).
- **Not involved in the 7-vs-6:** the failing test constructs detector `Signal`s directly and never calls the projector. Relevant to semantics cases A/B/E/F in §4. [Verified fact, High]
### 2.3 Detector→signal creation — `monitoring/detectors/strategies/{pattern,anomaly,drift}.py`, `domain/confidence.py`, `orchestration/runner.py:44-82`
- Detectors are pure threshold/pattern functions returning `list[Signal]` with fresh `uuid4` ids and `detected_at=now`. No finding-level stable id, no ordinal, no source-event attachment. `execute_detector` maps exceptions to outcomes and enforces the agent boundary; empty list → `NO_FINDING`.
- `select_run_assignments` (monitoring_integration.py) hard-codes which detectors run; the failing test bypasses it by constructing `MonitoringRun`/`DetectorRun` directly.
- **Identity contribution:** `detector_name` is not a field on `Signal`; it is attached at persistence (`signal_to_record(signal, detector_name)`, `persistence/__init__.py:43-46, 65`). Because `signal_id` is random per execution, re-running a detector yields new PKs for the same finding — hence the need for a semantic key. [Verified fact, High]

### 2.4 Application-level dedupe key — `src/ai_eval_platform/monitoring/persistence/__init__.py:43-110`
- `signal_to_record(signal, detector_name)` (43-66): copies `signal_id/origin/source_*/agent/type/scope/run/eval/window/detected_at`, dumps the payload to a JSON dict (`model_dump(mode="json")`, 63), sets `payload_type`, attaches `detector_name`.
- `signal_dedupe_key(record, ordinal=None)` (69-110): SHA-256 over 14 semantic fields — `agent_id, detector_name, evaluation_run_id, payload, origin, source_event_id, source_observation_id, projection_version, payload_type, run_id, scope, signal_type, window_end, window_start`. **`signal_id`, `detected_at`, and `ordinal` are excluded.** Ordinal is accepted for compatibility but ignored by design (docstring 73-77). JSON dumped with `sort_keys=True` → deterministic across processes and backends.
- `persist_monitoring_signals` (`monitoring_integration.py:366-397`): per detector run → `signal_to_record` → recompute `dedupe_key` → `persisted_signal_from_record` → `insert_ignore_duplicates`.
- **Discard point #1 (operative):** equal records → equal `dedupe_key` → second row violates `uq_persisted_signals_dedupe_key` → skipped by `ON CONFLICT DO NOTHING`. Deterministic and idempotent across retries (same finding → same key → no-op). [Verified fact, High — executed probe]

### 2.5 Repository insert — `src/ai_eval_platform/storage/repositories.py:827-849` + `_signal_row` (99-112)
- `insert_ignore_duplicates(rows)`: snapshots rows, builds the dialect INSERT (`_dialect_insert`: PostgreSQL/SQLite), applies `.on_conflict_do_nothing()` **with no target** (841) → any unique/PK violation in the batch skips that row. Returns `rowcount` (847-849).
- `_signal_row` defaults `origin=detector` and nullable source fields to `None` — matches the test's sourceless detector signals.
- **Discard point #2 (same mechanism, DB side):** bare `DO NOTHING` means a PK conflict (`signal_id`), a dedupe-key conflict, or a source-projection UQ conflict all silently skip. No application pre-check; the database is the arbiter. [Verified fact, High]

### 2.6 Schema — `src/ai_eval_platform/storage/models.py:299-332` (SQLite DDL inspected + PostgreSQL verified)
- PK: `signal_id` (322). Unique index `uq_persisted_signals_dedupe_key (dedupe_key)` (310). Unique constraint `uq_persisted_signal_source_projection (source_event_id, origin, projection_version)` (312). CHECKs on scope/shape (301-307). FKs: agent RESTRICT; run/evaluation/source FKs CASCADE; all except agent nullable.
- Nullability: `source_event_id / source_observation_id / projection_version / detector_name / dedupe_key` are all nullable (316-319, 328-330; SQLite `inspect` confirms `nullable=True`; DDL shows no `NOT NULL` on those columns). `signal_id/agent/signal_type/scope/detected_at/payload/payload_type` are NOT NULL.
- **Which fields distinguish occurrences:** `dedupe_key` = semantic identity; `signal_id` = row identity only. `source_*` + `projection_version` distinguish base-signal lineage; `detector_name` + `payload` + `run/eval/window` distinguish detector findings. `detected_at` explicitly does **not** distinguish (correct — timestamps vary per run). [Verified fact, High]

### 2.7 Serialization — payload `model_dump(mode="json")` (persistence:63), `_signal_row` snapshot (repositories:99-112)
- Payload dicts with equal content serialize equivalently for key purposes: the key path uses `json.dumps(..., sort_keys=True)`, so key order cannot create false negatives; stored payload order does not enter the key. `detected_at` differences never enter the key. Verified: `duplicate_a`/`duplicate_b` payloads are byte-identical in probe output. [Verified fact, High]
## 3. Reproduction (isolated; no project modification)

- **Target:** `tests/storage/test_persisted_signals.py:473-515` (`test_distinct_detections_survive_logical_dedupe`), fixtures `session` + `signal_graph` (`tests/storage/conftest.py:26-46`, `test_persisted_signals.py:38-77`).
- **Command:** `python -m pytest tests/storage/test_persisted_signals.py::test_distinct_detections_survive_logical_dedupe -q` → `AssertionError: assert 6 == 7`, exit 1. Reproduced. [Verified fact, High]
- **Isolated probe:** `C:\Temp\audit\dedupe_probe.py` (scratch SQLite, same 7 candidates, same detector names `pattern`×6 + `shadow`×1). Output: 7 candidates → 6 unique keys → `persist_monitoring_signals` returns 6 → survivors `['sig-ord-a','sig-run2','sig-shadow','sig-tool','sig-window-a','sig-window-b']`; `sig-ord-b` absent. Exit 0 (after harness fix: required `DetectorRun.outcome` added).
- **Original candidates:** 7. **Unique logical identities:** 6. **Generated signal ids:** 7 distinct. **Rows persisted:** 6.
- **Failed candidate:** `duplicate_b` (`sig-ord-b`). **Mechanism:** application-computed identical `dedupe_key` (`0ba881cd…` for both a and b) → skipped by `uq_persisted_signals_dedupe_key` under bare `ON CONFLICT DO NOTHING`. NOT a PK conflict (ids differ); NOT the source-projection UQ (all three fields `None` → NULL triple permits multiple rows on both SQLite and PG 17, directly tested in §5); no other mechanism observed.
- **Conflicting identity fields (actual values):** `agent_id=agent-1, detector_name=pattern, run_id=run-1, scope=run, signal_type=repeated_tool_usage, payload={tool_name:search, occurrence_count:2, sequence:[search,search], severity:0.5, related_event_ids:[evt-t1,evt-t2]}, origin=detector, source_event_id=None, source_observation_id=None, projection_version=None, payload_type=RepeatedToolUsagePayload, evaluation_run_id=None, window=None` — identical for a and b; only `signal_id` (`sig-ord-a` vs `sig-ord-b`) and `detected_at` (identical in the test; excluded by design) differ.

| Candidate | Source event | Origin | Projection ver | Generated ID | Detector | Test expects distinct? | Persisted? | Observed reason |
|---|---|---|---|---|---|---|---|---|
| duplicate_a | None | detector | None | sig-ord-a | pattern | yes | YES | first row of key `0ba881cd…` |
| duplicate_b | None | detector | None | sig-ord-b | pattern | yes | **NO** | same key `0ba881cd…` → dedupe-key conflict → `DO NOTHING` skip |
| different_tool | None | detector | None | sig-tool | pattern | yes | YES | key `098f9a2d…` (payload differs: read vs search) |
| other_run | None | detector | None | sig-run2 | pattern | yes | YES | key `29d2f191…` (run-2 vs run-1) |
| window_a | None | detector | None | sig-window-a | pattern | yes | YES | window-scope key (bounds differ from run signals) |
| window_b | None | detector | None | sig-window-b | pattern | yes | YES | distinct `window_end` (+2h) → distinct key |
| shadow | None | detector | None | sig-shadow | shadow | yes | YES | key `c020ad1c…` (detector_name differs) |
## 4. Intended semantics (decision — each case answered separately)

### A. Event replay → DEDUPLICATE (same event + same projection version = one observation + one base signal)
- Expected: redelivery reuses deterministic ids `obs_<sha(event|ver)>` / `sig_<sha(event|ver)>` plus `ON CONFLICT DO NOTHING` → no-op; counts stay 1/1. Verified by prior E2E probe (`duplicate_insert_n=0`). Justification: at-least-once delivery must be idempotent and audit identity stable. [Decision, High]

### B. Distinct source events with identical payloads → SEPARATE observations and base signals
- Expected: `e1` vs `e2` (different `event_id`) → different hashed ids → 2 observations, 2 signals. Verified by prior probe (e2 → 2/2). Justification: lineage is identity; collapsing distinct events would destroy the audit trail. Current projector satisfies this (ids hash `event_id`, not payload). [Decision, High]

### C. Repeated detector execution (same detector, same material) → DEDUPLICATE
- Expected: same `(agent, detector, scope-key, type, payload, origin, source_*)` → same `dedupe_key` → second execution is a no-op. Justification: detectors mint random `uuid4` ids and wall-clock `detected_at` per run; without a semantic key every retry/re-run would fabricate duplicate findings. `signal_id`/`detected_at` must not be identity. Current key satisfies this. [Decision, High]

### D. Multiple detections from one event → distinct ONLY if a domain field differs; ordinal must NOT be identity
- Expected: two findings for one event must differ in `detector_name`, `signal_type`, payload content, scope key, or source linkage. List position/ordinal is unstable across replays (batch order, parallelism) and must not create identity — the code's exclusion of `ordinal` is correct and must be retained.
- Applied: `duplicate_a` vs `duplicate_b` share every semantic field (same detector, scope, type, payload, run, origin; no source). They are the same detection emitted twice, not two findings. Persisting both would mean any accidental double-emit (retry, loop bug, duplicate registration) permanently doubles findings. Correct count: **6**. The test's expectation of 7 (treating list position as identity) is wrong. [Decision, High]
- What would justify 7: differing `occurrence_count`, different `related_event_ids`, different `tool_name`, or a documented `finding_index` domain field — none present. [Decision, High]

### E. Multiple origins → MUST coexist (origin + source in identity)
- Expected: `base_observation` (source ids set) and `detector` (source often null) never collide because `origin` and `source_*` are key members. Verified in probes: base + detector rows coexist; `shadow` detector coexists with `pattern` on identical payload. Current design satisfies this. [Decision, High]

### F. Projection version change → NEW observation + NEW base signal (explicit lifecycle)
- Expected: `event_id|1.0` vs `event_id|2.0` → different deterministic ids; `UQ(source_event_id, origin, projection_version)` permits both; old projection retained (audit history), not replaced. Justification: reproducibility requires versioned projections. Schema supports it; lifecycle (retention/replace) should be documented rather than inferred. [Decision, Medium — implemented but undocumented]

### G. Nullable uniqueness — measured, not inferred
- `dedupe_key` is nullable but always populated on the monitored path (`persist_monitoring_signals` recomputes it; base-signal path sets it). Direct inserts with `None` are possible: probes show N1+N2 with NULL keys persist on **both** SQLite and PostgreSQL (NULLs never conflict in unique indexes).
- `uq_persisted_signal_source_projection`: an all-NULL triple permits multiple rows on both backends (PG probe: W1+W2 both persist). This UQ therefore **cannot** be the discard mechanism for sourceless detector signals, and was not involved in 7-vs-6 (colliding rows share non-null key `0ba881cd…`). [Verified fact, High — executed on PG 17.10 + SQLite]
## 5. Database semantics compared correctly (SQLite + PostgreSQL 17.10, both isolated)

- **Model inspected:** `PersistedSignal` (`models.py:299-332`): PK `signal_id`; unique index `uq_persisted_signals_dedupe_key (dedupe_key)` (310); unique constraint `(source_event_id, origin, projection_version)` (312); nullable source/version/detector/dedupe columns (316-319, 328-330).
- **SQLite (test environment):** `mechanism_probe.py` — same PK → second insert `0` (PK conflict); same non-null dedupe key with different PK → `0`, survivor `['A']` (dedupe UQ conflict); two NULL keys → `1,1`, survivors `['N1','N2']` (NULLs distinct). DDL inspected to confirm constraints. [Executed, High]
- **PostgreSQL 17.10 (production target, scratch cluster):** `initdb -D C:\Temp\audit\pgdata -U postgres --auth=trust`; `pg_ctl … -o "-p 55433" start`; scratch databases created and dropped after use. `pg_probe.py` — same-key B skipped, survivor `['A']`; NULL keys N1+N2 both persist; NULL source-projection triple W1+W2 both persist. Identical to SQLite on the operative constraint. [Executed, High]
- **Application key is backend-independent:** SHA-256 over sorted JSON computed in Python before the INSERT; identical input → identical key on any backend. [Verified fact, High]
- **Conflict handling:** bare `on_conflict_do_nothing()` (no `ON CONFLICT` target) skips on **any** arbiter (PK or either UQ). Correct for idempotence, but it masks which constraint fired; probes disambiguated by isolating each case. [Verified fact, High]
- **Availability:** PostgreSQL was available and used (PG 17.10, `C:\Program Files\PostgreSQL\17\bin`); no limitation to record for this question.

## 6. Root-cause determination (each hypothesis evidence-mapped)

| # | Hypothesis | Evidence | Result |
|---|---|---|---|
| 1 | Incorrect/incomplete dedupe key | Key (`persistence/__init__.py:78-101`) covers 14 semantic fields; probe shows a/b identical (`0ba881cd…`) and every other pair differing exactly where a semantic field differs | **Mechanism confirmed; defect rejected** — the key is correct |
| 2 | Overly restrictive DB uniqueness | Operative arbiter is `uq_persisted_signals_dedupe_key` (fires correctly); source-projection UQ permits NULL triples on both backends; PKs differ | **Rejected** — constraints behave as designed |
| 3 | Incorrect signal-id generation | 7 distinct ids generated; ids correctly excluded from semantic identity (inclusion would break retry idempotence) | **Rejected** |
| 4 | Incorrect test assumptions | Test names the identical pair `duplicate_a/b` yet expects both to survive "logical dedupe" — contradicts the helper docstring ("lexicographically identical rows … at most one survives", test:255-261) and its own other assertions, where every surviving candidate differs semantically | **Confirmed — fix the test, not the key** |
| 5 | SQLite-vs-PostgreSQL NULL semantics | PG 17.10 and SQLite probes agree on all three cases (same-key skip; NULL-key coexist; NULL-triple coexist) | **Rejected** |
| 6 | Other — PG `rowcount` | New: `insert_ignore_duplicates` returns `0` on PG even for fresh rows (`rowcount_probe.py`: fresh A → 0, table count → 1). psycopg/SQLAlchemy report rowcount 0/`None` for bare `ON CONFLICT DO NOTHING` on PG; SQLite returns 1 | **Not the cause**; separate defect (return-value tests on PG would mislead) |

**Conclusive root cause:** `duplicate_b` is a byte-identical logical duplicate of `duplicate_a` under the platform's correct semantic identity; the dedupe key + `uq_persisted_signals_dedupe_key` (`models.py:310`) + bare `ON CONFLICT DO NOTHING` (`repositories.py:841`) discard it. The expectation of 7 is wrong; the correct persisted count is **6**.
## 7. Decision and correction (no source edits made — instructions for the owner)

- **Decision:** The test's assumption is incorrect. Correct count = **6**. Retain `signal_dedupe_key` as-is (ordinal excluded), retain both unique constraints, retain bare `DO NOTHING`.
- **Required test correction (owner to apply):** in `tests/storage/test_persisted_signals.py:473-515`, change `assert persist_monitoring_signals(...) == 7` to `== 6` and restate the intent: lexicographically identical detections collapse while semantically distinct dimensions survive (e.g. assert `sig-ord-a` present, `sig-ord-b` absent). Consider renaming to `test_identical_detections_dedupe_but_distinct_dimensions_survive`.
- **Do NOT:** add `ordinal`/index to the key, drop the dedupe unique index, switch to `DO UPDATE`, or key on `signal_id`/`detected_at` — each would break retry idempotence (case C) or replay stability (case A).
- **Separate follow-ups:** (a) PG `rowcount` artifact — make `insert_ignore_duplicates` report inserted rows reliably (e.g. `RETURNING signal_id`) or change callers/tests to count rows, not return values; (b) consider targeted `ON CONFLICT (dedupe_key) DO NOTHING` plus distinct handling for PK vs UQ conflicts to improve diagnosability (behavior-preserving); (c) document the projection-version lifecycle (case F).
- **Verification status:** reproduced (exit 1); isolated probe (exit 0, 6 rows, `sig-ord-b` absent); mechanism isolated on SQLite and PostgreSQL 17.10 (scratch databases, dropped); NULL semantics measured on both backends. Confidence: **High**. No production data touched; no project files modified.

## Appendix — commands and artifacts (reproducible; sources untouched)

- `python -m pytest tests/storage/test_persisted_signals.py::test_distinct_detections_survive_logical_dedupe -q` → `assert 6 == 7`, exit 1.
- `python C:\Temp\audit\dedupe_probe.py` → 7 candidates → 6 keys, `sig-ord-b NO`, survivors listed, exit 0 (after adding required `DetectorRun.outcome`).
- `python C:\Temp\audit\mechanism_probe.py` → PK 1/0; same-key 1/0 survivor A; NULL keys 1+1; DDL printed.
- PostgreSQL 17.10: `initdb -D C:\Temp\audit\pgdata -U postgres --auth=trust`; `pg_ctl -D C:\Temp\audit\pgdata -o "-p 55433" start`; `python C:\Temp\audit\pg_probe.py` (same-key skip; NULL coexist; NULL-triple coexist; scratch DB dropped); `python C:\Temp\audit\rowcount_probe.py` (fresh → 0, count → 1; dropped).
- Environment: `git rev-parse HEAD` → `04d7525`; SQLAlchemy 2.0.51; PostgreSQL `server_version` 17.10.
