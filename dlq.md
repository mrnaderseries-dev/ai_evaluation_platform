# DLQ Design - Audit and Proposed Direction

_Status: read-only audit complete; **no source file was modified**. This document records findings and the design I would build toward. Nothing here is implemented yet._

_Date: 2026-10-04. Scope: `src/ai_eval_platform/ingestion/dlq.py` and its seams (`delivery_classifier.py`, `RetryPolicy.py`, `kafka_consumer.py`, `handler.py`, `event_validator.py`, `event_router.py`, `deduplicator.py`), `schemas/events/`, Kafka config and dependency setup, and existing tests._

---

## 1. Verdict

**NEEDS REDESIGN**

The responsibility split is right and worth keeping: `DeadLetterQueue` publishes, `DeliveryClassifier` classifies, `RetryPolicy` owns the retry lifecycle. Nothing leaks across those boundaries.

The design fails on the one guarantee the DLQ exists to provide: **durability before offset commit.** `dlq.py` declares a `DLQProducer` Protocol whose only method is `produce(...) -> None`. There is no delivery callback, no `poll()`, no `flush()`, no broker-error check, and no return value. Under `confluent-kafka` semantics `produce()` is an asynchronous local-buffer enqueue: it raises on almost nothing, and broker-side rejection surfaces only on a later poll. So a returned `publish()` is treated as durable when it is not, and the required invariant *"publish successfully to the DLQ first, then commit the original offset"* is not merely unimplemented - it is **not expressible** through the current interface.

Two further problems make this a redesign rather than a set of patches:

- **The payload cannot carry a poison-pill message.** `publish()` requires `event: dict[str, Any]`, but the dominant DLQ trigger is `EventValidationError`, raised exactly when the bytes are *not* parseable (bad UTF-8, bad JSON, not an object, oversize). There is no dict in those cases and no alternative field.
- **Size inversion creates an unbreakable loop.** `EventValidator` accepts up to `1_048_576` bytes; the DLQ envelope wraps that same event plus metadata, which is strictly larger. Kafka's default `max.message.bytes` is `1_000_000`. A near-limit malformed message is *reliably* rejected on the DLQ topic - and because the consumer correctly must not commit on DLQ failure, that offset is redelivered forever.

The good news: `kafka_consumer.py` is 0 bytes, so **no offset has ever been committed wrongly**. The failure mode is latent, not active, and the rework is bounded - the Protocol is a small abstraction and the envelope additions are purely additive.
## 2. What the repository actually implements

Sizes verified from the working tree:

| Module | Size | State |
|---|---|---|
| `ingestion/dlq.py` | 2167 B | implemented - publisher only |
| `ingestion/delivery_classifier.py` | 3983 B | implemented |
| `ingestion/RetryPolicy.py` | 3030 B | implemented (**PascalCase filename**) |
| `ingestion/event_router.py` | 6696 B | implemented |
| `ingestion/event_validator.py` | 7390 B | implemented |
| `ingestion/handler.py` | 4483 B | implemented, five route handlers |
| `ingestion/kafka_consumer.py` | **0 B** | empty placeholder |
| `ingestion/deduplicator.py` | **absent** | not on disk; only in `SOURCES.txt` |
| `configs/kafka.yaml` | **0 B** | empty |
| `producers/*/producer.py`, `sdk/*` | 0 B | empty |
| `schemas/versioning/schema_policy.py` | 0 B | empty |

`DeadLetterQueue.publish()` validates a non-empty topic and `attempt >= 1`, builds a frozen `DLQMessage`, re-deconstructs that dataclass field-by-field into a plain dict, derives a key from `event["event_id"]`, and calls `produce()` inside a `try/except` that re-raises as `DLQPublishError`. That is genuinely the whole file.

**There is no Kafka client anywhere in the project.** `pyproject.toml` declares only `pydantic`, `SQLAlchemy`, `psycopg`, plus `pytest` for dev. `import confluent_kafka` and `import kafka` both fail. The only reference to a Kafka library in the tree is the guarded optional import in `delivery_classifier.py:148-153`. Therefore **no implementation of `DLQProducer` exists** - nothing converts the dict to bytes, sets headers, or wires a delivery report.

## 3. Responsibility analysis

**Correctly placed and worth preserving:**

- **DLQ publishes only.** No retry logic, no classifier call, no offset handle, no DB session. It receives a pre-computed `reason` and `attempt` rather than consulting `RetryPolicy`/`DeliveryClassifier`.
- **Classification** is entirely in `DeliveryClassifier.classify()`. `dlq.py` contains zero policy.
- **Retry lifecycle** is entirely in `RetryPolicy` - attempt counting, `base_delay * 2**(attempt-1)`, cap at `max_delay`, injected `random_source` for jitter, `RetryAction.EXHAUSTED` at `attempt >= max_attempts`. `dlq.py` neither duplicates nor overrides it.
- **Orchestration** belongs to `KafkaConsumer`, which is empty - nothing to over- or under-reach today.

**Boundary leaks:**

- The Protocol **over-specifies client semantics while under-specifying delivery**. `produce(...) -> None` reads as synchronous success to any implementer, which is the exact false proof we must avoid.
- `dlq.py:51-54` raises a bare `ValueError` on `attempt < 1` from inside `publish()`. A consumer catching only `DLQPublishError` to decide "do not commit" will crash instead of deciding.
## 4. Kafka delivery semantics

**`produce()` is being treated as proof of delivery.** `dlq.py:74-79` calls it and returns; no exception means "success" to any caller. That assumption fails in several independent ways:

- `produce()` raises `BufferError` only when the *local* queue is full. Broker-side failures are asynchronous.
- A record rejected for size, sent to a missing topic, or hitting `NOT_LEADER_FOR_PARTITION` / timeout produces **no exception at all**.
- Without `poll()` or `flush()` nothing ever drains the delivery-report queue - and **delivery reports are the only mechanism that confirms a broker acknowledgement**. This code structurally never calls either.

**Confirmation is impossible with the current interface.** `produce()` returns `None` and has no callback parameter, so an implementation *cannot* signal "the broker said no". The seam itself must change before the invariant can hold.

**Premature offset commit** is the critical consequence. The sequence is: enqueue to local buffer -> `publish()` returns -> consumer commits. If the process dies, the buffer is unsynced, the broker never receives the record, **and the offset is already committed. The message is lost with no trace.** No Kafka configuration prevents this; it is a protocol-shape defect.

**Producer failure.** If `produce()` *does* raise, `DLQPublishError` propagates and a correct consumer skips the commit - that half is right. But only *synchronous* failures are caught, and the wrapper discards context (fixed string, no topic/key/broker error), so DLQ-publish failures are undiagnosable. There is **no fallback**: no local spill, no secondary DLQ, no metric, no cap on consecutive failures.

**Crash after DLQ publish, before offset commit.** The offset is uncommitted -> redelivery -> fails identically -> published to the DLQ a second time. **Duplicate DLQ records.** Only partially safe:

- They are *byte-identical*, because the payload carries no timestamp, no attempt-monotonic sequence, no unique record id.
- `event_id` **is** inside `payload["event"]`, so a replayer *could* dedup on it - but only for records that have an `event` dict at all, i.e. **not** for validation failures.
- On a compacted topic the identical duplicates collapse to one, destroying the duplicate signal entirely.

Note the contrast: **duplicate *processing* is handled correctly** - `insert_from_envelope` -> `ON CONFLICT (event_id) DO NOTHING` returns 1/0 rather than raising (`repositories.py:368-378`, asserted at `tests/storage/test_repositories.py:116-118`). **Duplicate *DLQ delivery* is not.**

**Configuration is entirely absent.** `configs/kafka.yaml` is 0 bytes: no consumer group, no `auto.offset.reset`, no `max.poll.interval.ms`, no `isolation.level`, no DLQ topic. Separately, `RetryPolicy.wait()` sleeps in-process (up to `max_delay=30 s` per attempt) while holding a partition, with `max.poll.interval.ms` unset - a rebalance hazard once a consumer exists.
## 5. DLQ payload audit

**Present** - exactly five fields (`dlq.py:56-70`):

| Field | Source | Assessment |
|---|---|---|
| `event` | full event dict | necessary in principle, **wrong shape** for validation failures |
| `error_type` | `type(error).__name__` | correct |
| `error_message` | `str(error)` | present, but **leaks** |
| `reason` | caller-supplied | correct - the classifier's human-readable verdict |
| `attempt` | caller-supplied `int` | correct, but meaningless for permanent failures |

**Genuinely missing:**

- **`original_topic`, `partition`, `offset`** - not cosmetic. Without them there is no way to trace a record back to its Kafka position or answer "which offsets did I lose during incident X?". *The single most important gap.*
- **DLQ-entry timestamp** - `event.timestamp` is the *event's* time and may be hours stale; useless as arrival time.
- **Stable unique record id** - nothing distinguishes the duplicates above.
- **DLQ schema version** - any future payload change silently breaks every existing replayer.
- **Machine-readable failure stage** - `reason` is prose; distinguishing validation / routing / constraint / exhaustion currently requires string-matching it.
- **`exception_code`** - `EventValidationError` carries a precise `.code` (`INVALID_JSON`, `MESSAGE_TOO_LARGE`, ..., `event_validator.py:87-90`) and **the DLQ discards it**, keeping only free text. *The most valuable diagnostic field in the system is dropped at this boundary.*

**Questionable, not missing:** `run_id`, `event_type`, `schema_version` are all inside `payload["event"]`; duplicating them would be redundant *for events that have a dict*. The real problem is that there is no dict at all for validation failures. Denormalising `run_id` becomes **necessary** precisely because it would be the only reliable key when `event` is absent - which also resolves the keying question.

**Concrete risks:**

1. **Unserialisable payload.** `dlq.py` performs **no `json.dumps` anywhere**; encoding is delegated to an adapter that does not exist. `AIEvent.payload` is `dict[str, Any]` with no value-type constraint, so a `datetime`/`Decimal`/`UUID`/custom object anywhere nested breaks encoding - and if the adapter encodes lazily, that happens **outside** the `try/except`, turning a serialisation failure into an unhandled crash instead of a `DLQPublishError`.
2. **`str(error)` leaks SQL and parameters.** `IntegrityError` -> `DLQ` and `DBAPIError` -> `RETRY` -> `EXHAUSTED` -> `DLQ`. SQLAlchemy's `DBAPIError.__str__` includes the compiled SQL **and bound parameters** - i.e. payload content lands verbatim in the DLQ. Meanwhile `event_validator.py:220` deliberately calls `exc.errors(include_input=False)`; the DLQ undermines that discipline on the DB path. *Inconsistent privacy posture across failure paths.*
3. **Mutable event by reference** - `payload["event"] is event`; `frozen=True` freezes only the binding, so the caller can mutate it after `publish()` returns.

**On stack traces: omit them.** None are stored today and that should stay. Traces amplify the size problem, routinely contain paths/hostnames/local variable values (a second leak path), and are worthless to a replayer running days later on different infrastructure. They belong in consumer logs at failure time, correlated by `event_id`. This is "already correct, do not regress".

**On the full original event:** storing it is defensible for *reprocessing* (that is the DLQ's purpose) but it must be a deliberate decision, not a default, given payload content is unredacted and `security/pii.py` is a 0-byte placeholder. Store no secrets or credentials in any DLQ field.
## 6. Failure scenarios

| Scenario | Behaviour | Hazard |
|---|---|---|
| **Validation failure** | `EventValidationError` -> `DLQ`, but consumer holds `bytes`, not a dict - **no API accepts it** | **loss or ad-hoc shape**; for oversize, DLQ publish *always* fails -> **infinite loop** |
| **Routing failure** | `DLQ`; `AIEvent` exists so a dict is obtainable via `model_dump()` - works, if every call site remembers | call-site obligation |
| **Permanent domain failure** | `DLQ`; dict available; `error_message` is a safe static f-string | lowest-risk path |
| **Retry exhaustion** | `EXHAUSTED` -> DLQ - **not implemented anywhere**; classifier has no `EXHAUSTED` concept | **infinite retry** if the attempt counter is not persisted (reset on every restart) |
| **Database failure** | `RETRY` -> `EXHAUSTED` -> `DLQ`; SQL + params land in `error_message` | data leakage |
| **Kafka failure** | `confluent_kafka` absent => predicate returns `False` => falls through to catch-all `RETRY` | *accidentally* correct; `KafkaError` (`msg.error()`) unclassified |
| **DLQ publish failure** | `DLQPublishError` propagates - correct **for synchronous failures only** | **premature commit -> loss** for async rejections; no fallback, no cap |
| **DB/Kafka consistency** | DB path correct (`session_scope` commit/rollback; repos flush only) | latent - nothing exists yet to violate it; "publish *after* the transaction exits" is undocumented |

**Hazard summary:** message loss **YES** / infinite retry **YES** / premature offset commit **YES** / duplicate processing **NO** (DB-safe) / duplicate DLQ messages **YES** / inconsistent DB/Kafka state **latent**.

## 7. Testing audit

**Existing relevant tests: none.** Verified by directory listing and by running the suite (`106 passed, 5 skipped in 2.76s`):

- `tests/contract/test_event_schema.py`, `test_event_type_locking.py` - envelope schema only.
- `tests/storage/*` - persistence. **Indirectly relevant to the DLQ:** `test_repositories.py:116-118` and `:127-129` assert redelivery/same-batch duplicates are no-ops; `test_postgres_integration.py:124-131` re-verifies on real PostgreSQL. These are the only reason the *DB* half of at-least-once is proven.
- `tests/unit/`, `tests/integration/`, `tests/e2e/`, `tests/evaluation/` - **all empty directories.**
- No test file references `dlq`, `DeadLetterQueue`, `DLQPublishError`, `DeliveryClassifier`, `RetryPolicy`, or `EventValidator`.

`kafka_analysis.md:17-18` (M4) recorded this when `dlq.py` was 0 bytes. **`dlq.py` has since been written with zero tests added** - so the finding has worsened in relative terms: live, untested code now sits on the one path where a silent error loses messages.

**Missing cases, against the requested list:**

| Required case | Status |
|---|---|
| Successful DLQ publish | **MISSING** |
| DLQ publish failure -> `DLQPublishError` | **MISSING** |
| Offset **not** committed when DLQ publish fails | **MISSING** (needs consumer) |
| Retry exhaustion -> DLQ | **MISSING** (`RetryPolicy` untested entirely) |
| Serialization failure | **MISSING** (no serialisation code exists) |
| Duplicate DLQ publication after crash | **MISSING** |
| Correct Kafka key | **MISSING** |
| Preservation of `event_id`/`run_id`/`event_type` | **MISSING** |
| Delivery confirmation | **MISSING** - and **untestable**: the Protocol exposes no delivery signal. *This is a testability consequence of the design defect, not just a coverage gap.* |

**Four cheap tests are possible against `dlq.py` as it stands**, needing no Kafka client, and should be written first: key derivation with/without `event_id`; payload shape and field preservation; `attempt=0` rejection; `produce()` raising -> `DLQPublishError` with the original exception chained as `__cause__`. Everything about delivery confirmation and offset-commit ordering is untestable until the consumer exists and the seam changes.
## 8. Findings

Ordered by severity. Each is evidence-backed; recommendations are conceptual only.

| # | Severity | Location | Finding | Risk |
|---|---|---|---|---|
| 1 | **CRITICAL** | `dlq.py:11-19,74-83` | `DLQProducer.produce(...) -> None` is an async enqueue; no callback/`poll()`/`flush()`; no implementation exists anywhere in the repo | Returned `publish()` treated as durable -> consumer commits an offset for an unacknowledged record -> **message loss**. Also makes a correct adapter impossible to write |
| 2 | **CRITICAL** | `dlq.py:46` | `publish()` requires `event: dict[str, Any]`, but validation failures (the main DLQ trigger) have only `bytes` | The most common DLQ path is unimplementable; caller will drop messages or invent an undocumented shape |
| 3 | **CRITICAL** | `dlq.py:74-83` + `event_validator.py:99` | DLQ envelope is larger than the input, but Kafka's default `max.message.bytes` (1,000,000) < validator limit (1,048,576) | Oversize poison pill can never be DLQ'd; no-commit rule => **infinite redelivery loop**. `RetryPolicy` is not engaged because the classifier said DLQ |
| 4 | **HIGH** | `dlq.py:56-70` | Envelope lacks topic/partition/offset, DLQ timestamp, record id, schema version, failure stage | Cannot locate the source position of a dead lettered record; duplicates undetectable; future payload changes silently break replayers |
| 5 | **HIGH** | `dlq.py` (whole file) | No idempotency guard; redelivery after a crash produces byte-identical duplicate records | Compaction collapses them; replayer cannot distinguish re-dead-lettering from a new failure |
| 6 | **HIGH** | `dlq.py:59` | `error_message=str(error)` on a SQLAlchemy `DBAPIError` embeds compiled SQL **and bound parameters** | Payload content copied into a second topic under different governance; contradicts the validator's deliberate `include_input=False` |
| 7 | **HIGH** | `dlq.py:85-93` | Key is `event_id` (null -> `None`), diverging from the intended main-topic key `run_id` | Breaks per-run ordering and replay locality; `None` key -> sticky partitioner -> redelivery lands in a **different partition each time** |
| 8 | **HIGH** | `event_validator.py:87-90` -> `dlq.py:56-62` | The structured `.code` (`INVALID_JSON`, `MESSAGE_TOO_LARGE`, ...) is discarded; only prose survives | Highest-value diagnostic field dropped; forces string-matching - the same anti-pattern already flagged as M5 for the classifier |
| 9 | **HIGH** | `kafka_consumer.py` (0 B), `deduplicator.py` (absent), `configs/kafka.yaml` (0 B), `pyproject.toml` | No consumer, no attempt ledger, no Kafka config, no Kafka client dependency | Every ordering invariant is unenforced; **attempt counter resets on restart => retry bound is not real** |
| 10 | MEDIUM | `dlq.py:51-54`, `:80-83` | Bare `ValueError` for `attempt < 1`; producer errors wrapped into a constant string | A consumer catching only `DLQPublishError` crashes instead of deciding; failures undiagnosable from logs |
| 11 | MEDIUM | `dlq.py:22-28,56-70` | `DLQMessage` built then immediately deconstructed; `event` stored by reference | Field drift between dataclass and dict; mutable alias means the record can change after `publish()` returns |
| 12 | MEDIUM | `dlq.py` (no `import json`), `common.py:60` | No serialisation in the module; `payload` is unconstrained `dict[str, Any]`; lazy encoding would escape the `try/except` | Non-JSON-native values turn into unhandled crashes rather than `DLQPublishError`, breaking the no-commit rule |
| 13 | MEDIUM | `dlq.py:46` | Accepts only a `dict`, while callers hold `AIEvent` objects | Every call site must remember `model_dump()`; the non-JSON variant of that call breaks serialisation |
| 14 | LOW | `delivery_classifier.py:144-153` | `confluent_kafka` import is optional and the library is absent => predicate is dead code returning `False` | Correct outcome today only by accident of the catch-all; `KafkaError` (`msg.error()`) unclassified |
| 15 | LOW | `ingestion/RetryPolicy.py` | PascalCase filename; works on Windows, fails on Linux CI | Confusing CI-only failure once the consumer imports it; easily misdiagnosed as a missing dependency |
| 16 | LOW | `dlq.py:37-38,51-54` | Validates topic and `attempt` but never the **envelope size** | The one dimension that actually causes failure is the one dimension unchecked |
| 17 | INFO | `event_validator.py:124` | Stray non-English developer comment plus whitespace-only lines | Cosmetic; sits in the branch whose messages become DLQ records |
| 18 | INFO | repo-wide | No DLQ replayer, no DLQ consumer, no DLQ metric/alerting | A DLQ nobody reads is a silent failure sink; a DLQ-failure loop would be invisible |
## 9. The design I would build toward

Conceptual only - **not implemented**. The goal is to keep the boundary split that already works and fix the three things that break the durability guarantee.

### 9.1 Make delivery confirmation representable (fixes F1)

The producer seam is the whole problem. Whatever form is chosen, it must satisfy one rule: **the consumer cannot commit an offset until the DLQ record has been acknowledged by the broker.** Options, in order of preference:

- A delivery-report callback that records per-record success/failure, plus a mandatory flush-and-drain step the consumer awaits before committing. Most explicit, and it maps directly onto `confluent-kafka`.
- A `flush(timeout)`-style call whose result is checked, used only on the DLQ path where latency is irrelevant.
- An adapter whose `produce` contract is documented as "enqueue only, not durable", with a separate `confirm()` step. Weaker - easy to misuse later.

Whatever the shape: `publish()` must not return until confirmation is obtained, and **"not confirmed" must be indistinguishable from "publish raised"** as far as the consumer is concerned.

### 9.2 Give the envelope a real contract (fixes F2, F4, F5, F7, F8)

Settle this **before** writing the consumer, because several other decisions depend on it:

- **Discriminated original payload** - a union of `event` (validated envelope as a dict) and `raw` (base64 or a bounded, clearly-marked string) so an unparseable message can be represented honestly rather than shoehorned into a fake dict.
- **Provenance** - `original_topic`, `partition`, `offset`. Non-negotiable; without these the DLQ cannot answer operational questions about itself.
- **DLQ metadata** - its own entry timestamp (distinct from `event.timestamp`), a stable record id, and a DLQ schema version.
- **Structured failure classification** - carry `EventValidationError.code` and a machine-readable stage, so replayers never parse prose. Extend to an equivalent discriminator per failure family.
- **Key by `run_id`** to match the main topic, preserving per-run ordering and replay locality. This requires `run_id` to survive even when `event` is absent - which is why the raw-payload decision comes first. `event_id` may remain useful as a dedup field, but not as the partition key.
- **Redacted error text** - field/type/location only, never values, matching the validator's existing discipline. No secrets, no credentials.

### 9.3 Close the size hole (fixes F3, F12, F16)

- Decide a **DLQ envelope size budget** and validate it, rather than validating individual fields.
- Reconcile the validator's `max_message_bytes` with the broker's `max.message.bytes` so enrichment can never make a message undeliverable.
- Consider whether `MESSAGE_TOO_LARGE` belongs on the DLQ path at all, or needs its own handling.
- Own serialisation **inside** the DLQ module, inside the guarded block, so any encode failure surfaces as `DLQPublishError` and the no-commit rule holds uniformly.

### 9.4 Make the consumer carry the invariants (fixes F9)

- `enable.auto.commit=false`; commit explicitly, per message, only after the action is durable.
- Ordering per message: process -> DB transaction (`session_scope` commits or rolls back) -> classify -> act (commit offset / skip / DLQ) -> publish to DLQ **after** the transaction has exited, so a failed publish cannot leave an open transaction holding locks.
- `max.poll.interval.ms` set deliberately, given `RetryPolicy.wait()` sleeps in-process.
- **Persist the attempt counter** outside process memory so `max_attempts` is a real bound across restarts. This is what `deduplicator.py` should be for.
- On `DLQPublishError`: no commit, bounded backoff, and an alert - not a hot loop.

### 9.5 Decide, don't leave implicit

- Is duplicate DLQ delivery acceptable? If yes, give records an id/occurrence field and document that replayers must be idempotent on `event_id`. If no, a DLQ-level idempotency mechanism is required.
- Should the full original event be stored at all, given `security/pii.py` is a 0-byte placeholder?
- Should the DLQ-record id be the dedup key for reprocessing?
## 10. Final assessment

**Already correct (with evidence):**

- **The responsibility boundary is right.** `DeadLetterQueue` publishes and nothing else - no retry logic, no classifier call, no offset handle, no DB session. It receives a pre-computed `reason` and `attempt` rather than consulting `RetryPolicy`/`DeliveryClassifier`. This is the part most likely to survive the rework intact.
- **The classifier's ordering is correct and non-shadowing.** `_is_duplicate_event` -> `IntegrityError` -> validation -> routing -> domain -> transient-DB -> transient-Kafka -> catch-all `RETRY`. `IntegrityError` is a `DBAPIError` subclass but is matched at line 38, *before* `_is_transient_database_error` at line 62, so permanent constraint violations are not misread as retryable.
- **The DB side of at-least-once is sound and actually tested.** `session_scope()` (`postgres.py:78-100`) is the single commit point with rollback-on-exception; repositories flush only. `insert_from_envelope` -> `insert_ignore_duplicates` uses dialect-specific `ON CONFLICT (event_id) DO NOTHING` (`repositories.py:389`) returning 1/0 rather than raising - asserted at `tests/storage/test_repositories.py:116-118,127-129` and on real PostgreSQL at `test_postgres_integration.py:124-131`. **This is the strongest existing property in the pipeline.**
- **DB/DLQ ordering is respected in spirit already.** The classifier is invoked on an exception that has already escaped `session_scope`, so rollback precedes classification.
- **`event_validator` takes privacy seriously** (`exc.errors(include_input=False)` at `:220`). The DLQ should match that, not undermine it.
- **No stack traces are stored** - correct as-is; do not regress.
- **No secrets or credentials are handled** anywhere in `dlq.py`.

**Must be fixed before production:**

1. **F1** - the delivery-confirmation seam. Until `produce()`'s no-op return cannot be mistaken for success, the DLQ loses messages. Everything else is secondary.
2. **F2** - a representation for unparseable payloads, without which the most common DLQ path is unimplementable.
3. **F3** - the size inversion that turns a poison pill into an infinite loop. **Resolve jointly with F4**, since adding provenance fields grows the envelope further.
4. **F4 / F8** - the envelope contract: topic/partition/offset, entry timestamp, record id, schema version, structured failure code and stage.
5. **F7** - key by `run_id`, eliminate the `None`-key path. Depends on F2.
6. **F9** - the consumer, its configuration, and a persisted attempt counter so retry bounds survive restarts.
7. **F5** - an explicit decision on whether duplicate DLQ delivery is acceptable, documented with what a replayer must assume.

**Can wait:**

- F12/F13 (serialisation ownership, call-site obligation) - needed before the consumer runs, but straightforward once F2 is settled.
- F10, F11, F16 - diagnosability and maintainability. Important, not blocking.
- F14 (Kafka error taxonomy), F15 (filename case), F17 (stray comment) - opportunistic; **do F15 before the consumer lands**, since it breaks Linux CI.
- F18 (DLQ alerting) - needed before the DLQ is relied on operationally, not before it is written.

**Is the current design safe enough to proceed to `kafka_consumer.py`?**

**No - not as it stands.** The consumer is where these invariants become real, and the current `dlq.py` interface actively prevents two of them from being implemented correctly:

- The `-> None` `produce()` Protocol makes **"confirm before commit" unimplementable**, so the first consumer written against it will commit offsets for unacknowledged DLQ records. This is silent, unrecoverable data loss - and it is invisible in testing, because a mocked producer returns `None` exactly like a successful one.
- The `event: dict[str, Any]` signature makes the **validation-failure DLQ path unimplementable**, forcing the consumer author to invent a payload shape under time pressure.

**But it is not a dead end, and the rework is bounded.** The separation of concerns, the classifier taxonomy, the retry policy, and the entire DB persistence layer are sound and independently valuable; `kafka_consumer.py` being empty means **no offset has ever been committed wrongly** - the failure mode is latent, not active. `DLQProducer` is a small abstraction and the envelope additions are purely additive.

**Recommended sequence:**

1. Settle the envelope contract (F2 + F4 + F7 + F8, with F3 alongside it).
2. Change the producer seam so confirmation is representable (F1).
3. Write the four cheap `dlq.py` unit tests from section 7 to pin the contract.
4. Only then write the consumer, which must carry: publish-before-commit, no-commit-on-DLQ-failure, `EXHAUSTED` -> DLQ, and a persisted attempt counter.

Writing the consumer first would lock in exactly the two bugs this design is currently unable to express.

---

_Audit was strictly read-only: no source file was modified, created, deleted, or reformatted. This document is the only artefact, and it is commentary - the design above is proposed, not implemented._