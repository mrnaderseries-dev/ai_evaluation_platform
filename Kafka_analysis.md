# Kafka Ingestion Layer Audit and Implementation

_Date: 2026-10-04. Scope: `src/ai_eval_platform/ingestion/`, `src/ai_eval_platform/infrastructure/kafka/`, Kafka configuration, and the ingestion test suite._

---

## 1. Executive Summary

### Initial state

The ingestion layer had four implemented modules and two empty placeholders.
`kafka_consumer.py` was **0 bytes** and `deduplicator.py` did not exist, so
there was no orchestration and no offset handling at all. `dlq.py` (2167 B)
existed and was the only implemented consumer-side durability mechanism.

The DLQ had three confirmed defects that made its stated contract
*inexpressible*:

1. `DLQProducer.produce(...) -> None` returned no delivery signal. Under
   `confluent-kafka` semantics `produce()` is an asynchronous local-buffer
   enqueue; broker rejection surfaces only on `poll()`. A returned call was
   therefore treated as proof of delivery - the exact premature-commit
   data-loss window the design exists to prevent.
2. `publish(event: dict[str, Any])` could not represent a poison record. The
   dominant DLQ trigger is a validation failure, raised precisely when no
   dict exists.
3. `EventValidator` accepted up to `1_048_576` bytes while Kafka's default
   `max.message.bytes` is `1_000_000`. DLQ enrichment makes a record *larger*,
   so an oversize record could never be dead-lettered - and under the
   no-commit-on-DLQ-failure rule it would be redelivered forever.

### Final state

`kafka_consumer.py` implements the full pipeline with an explicit commit
matrix; the DLQ, retry, and record abstractions are versioned Pydantic
contracts; the Kafka client is isolated behind `infrastructure/kafka/`.

Tests: **106 passed / 5 skipped** before, **178 passed / 5 skipped** after
(72 new tests).

### Overall verdict

The DLQ design needed a redesign; the surrounding storage layer did not.
`session_scope()`, flush-only repositories, and `ON CONFLICT (event_id) DO
NOTHING` were already correct and were preserved.

### Biggest risks found

- **Silent message loss** via unconfirmed publish followed by offset commit.
- **Infinite redelivery loop** on oversize poison records (size inversion).
- **Poison records silently dropped**: the DLQ API could not accept raw bytes.
- **Unbounded retry** if the attempt counter lived only in process memory.
- **Layering violation**: `delivery_classifier.py` imported
  `confluent_kafka` directly, making the application layer untestable without
  the broker.

### Biggest fixes made

- Introduced `ConfirmedProducer`/`RecordSource` protocols whose contract is
  *return == broker acknowledged*, implemented for real in
  `infrastructure/kafka/producer.py` via delivery reports.
- Validator returns structured `ValidationSuccess`/`ValidationFailure` and
  never raises on malformed input, preserving raw bytes.
- Versioned `DlqRecord` Pydantic envelope with provenance, failure metadata,
  base64 raw payloads, and a size budget derived from base64 inflation.
- Retry topic (no in-loop sleeping) with the attempt carried in the message,
  so the retry bound survives a restart.
- Derived the validator's size limit from the DLQ envelope budget, with a
  test asserting the invariant.

## 2. Initial Architecture

Before modification the pipeline did not exist as a running whole:

```
Kafka -> (nothing; kafka_consumer.py was 0 bytes)
```

Individual modules that existed:

- `event_validator.py` (7390 B): raised `EventValidationError(code, message)`;
  `max_message_bytes` default `1_048_576`.
- `event_router.py` (6696 B) and `handler.py` (4483 B): unchanged by this work.
- `delivery_classifier.py` (3983 B): ACCEPT/RETRY/DLQ; duplicate detection by
  string-matching `IntegrityError`; `_is_transient_kafka_error` did a guarded
  `from confluent_kafka import KafkaException`.
- `RetryPolicy.py` (3030 B): backoff/jitter/EXHAUSTED; **PascalCase filename**.
- `dlq.py` (2167 B): `DLQMessage` dataclass, `DeadLetterQueue.publish()`, key
  from `event_id`, `produce()` with no delivery confirmation.
- `kafka_consumer.py`: **0 bytes**. `deduplicator.py`: **absent**.
- `configs/kafka.yaml`: **0 bytes**. No Kafka client in `pyproject.toml`.

No ingestion tests existed; `tests/unit`, `tests/integration`, `tests/e2e`
and `tests/evaluation` were empty directories.
## 3. Target Architecture

```
                    +--------------------------------------+
                    |  infrastructure/kafka/               |
                    |  ConfluentProducer  (confirms)        |
                    |  KafkaConsumerClient (manual commit)  |
                    |  KafkaSettings                         |
                    +-----------------+--------------------+
                                      |  ConfirmedProducer / RecordSource
                    +-----------------v--------------------+
                    |  ingestion/kafka_consumer.py          |
                    |  KafkaEventConsumer.process(record)   |
                    +--+-------------+-------------+--------+
                       |             |             |
        KafkaRecord    |   EventValidator        DeliveryClassifier
        (topic,        |   -> ValidationSuccess        (WHY)
         partition,    |      |                       |
         offset,      |      v                       |
         key,          |   AIEvent ---> EventRouter --> Handler
         value:bytes)  |                     |             |
                       |                     v             v
                       |              session_scope()  (DB commit)
                       |                     |
                       +---------------------+
                                 |
              ACCEPT ------------> commit offset
              RETRY  -------------> retry topic --confirm--> commit offset
              DLQ    -------------> DLQ --------confirm--> commit offset
              publish failed ----> NO COMMIT (redelivered)
```

Key properties:

- **Transport metadata never enters the domain event.** `AIEvent` is
  `extra="forbid"`; `KafkaRecord` carries topic/partition/offset.
- **No exactly-once claim.** Duplicate delivery is accepted and handled by
  `event_id` idempotency.
- **The application layer has no `confluent_kafka` import**, enforced by
  `test_application_layer_does_not_import_confluent_kafka`.

## 4. Files Inspected

| File | Why |
|---|---|
| `ingestion/dlq.py` | DLQ contract, producer seam, envelope |
| `ingestion/event_validator.py` | decode/parse/validate, size limit |
| `ingestion/event_router.py` | routing, `RouterError` family |
| `ingestion/handler.py` | `HandlerError`, repository seam |
| `ingestion/delivery_classifier.py` | ACCEPT/RETRY/DLQ taxonomy |
| `ingestion/RetryPolicy.py` | backoff, jitter, EXHAUSTED |
| `ingestion/kafka_consumer.py` | was empty; orchestration target |
| `storage/postgres.py` | `session_scope` commit boundary |
| `storage/repositories.py` | `insert_ignore_duplicates`, ON CONFLICT |
| `storage/models.py` | `events.event_id` PK, `uq_event_run_sequence` |
| `schemas/events/common.py` | `AIEvent`, `extra="forbid"` |
| `schemas/events/*.py` | concrete event schemas |
| `tests/storage/*` | existing idempotency coverage |
| `pyproject.toml`, `configs/kafka.yaml` | dependency and config setup |

## 5. Files Modified

| File | Why | What changed |
|---|---|---|
| `ingestion/kafka_record.py` (new) | AREA 1: separate transport from domain | `KafkaRecord`: topic, partition, offset, value, key, timestamp, headers |
| `ingestion/producer.py` (new) | AREA 12: confirmation contract | `ConfirmedProducer` protocol (`publish` returns only after broker ack), `PublishError` hierarchy, `RecordSource` |
| `ingestion/event_validator.py` | AREA 2/10 | Returns `ValidationSuccess`/`ValidationFailure`; never raises; preserves raw bytes; `ValidationCode`/`ValidationStage`; size limit derived from the DLQ budget |
| `ingestion/dlq.py` | AREA 11/16 | Rewritten as a versioned `DlqRecord` Pydantic envelope; `event` XOR `raw` (base64); provenance; failure stage/code; no stack traces; size budget; `build_failure_info` |
| `ingestion/retry_publisher.py` (new) | AREA 6/7 | `RetryRecord` carrying attempt, due_at, provenance; publishes with confirmation |
| `ingestion/kafka_consumer.py` | AREA 3/4/10/17 | Full orchestration, commit matrix, attempt from header, `stop()`, no in-loop sleeping |
| `ingestion/delivery_classifier.py` | AREA 19 + layering | `classify_validation_failure()`; removed the `confluent_kafka` import; transient-Kafka now keys off `PublishError` |
| `ingestion/RetryPolicy.py` -> `retry_policy.py` | Linux/CI compatibility | Renamed to snake_case; all references updated |
| `infrastructure/kafka/config.py` (new) | AREA 14 | `KafkaSettings` with validation; `enable.auto_commit=True` is rejected |
| `infrastructure/kafka/producer.py` (new) | AREA 12 | `ConfluentProducer` blocks on delivery reports; maps broker errors |
| `infrastructure/kafka/consumer.py` (new) | AREA 4 | Manual commit; `close()` leaves pending offsets uncommitted |
| `infrastructure/kafka/errors.py` (new) | AREA 13 | Client import isolation |
| `configs/kafka.yaml` | AREA 14 | Populated with documented offset-lifecycle settings |
| `pyproject.toml` | AREA 13 | Added optional `kafka` extra (`confluent-kafka`, `PyYAML`) |
## 6. Kafka Consumer Design

`KafkaEventConsumer.process(record)` runs one record end to end:

1. **Read attempt** from the record header (`attempt`), defaulting to 0.
   Carried in the message, not in memory, so it survives restarts.
2. **Validate** raw bytes. A `ValidationFailure` goes through
   `classify_validation_failure()` and then to the DLQ with the raw bytes.
3. **Process** the validated `AIEvent` inside `session_scope()` - route then
   handler. Exiting the scope normally means the DB write is durable.
4. **Classify** any exception:
   - `ACCEPT` -> commit (a duplicate `event_id` is a safe no-op).
   - `DLQ` -> publish to DLQ, confirm, then commit.
   - `RETRY` -> ask `RetryPolicy`; publish to the retry topic, confirm, then
     commit. On exhaustion, dead-letter.
5. **Never sleep.** Backoff lives in `due_at` on the retry topic; the loop
   keeps polling, so `max.poll.interval.ms` is never at risk from retry waits.

The validated `AIEvent` is passed directly to the DLQ for handler failures -
the event is never reconstructed from raw bytes.

**Shutdown.** `stop()` sets a flag checked at the loop boundary; `run()`
closes the consumer in a `finally`, and the client `close()` deliberately
leaves uncommitted offsets uncommitted.

## 7. Offset Commit Safety

| Outcome | Durable destination | Commit source offset? | Test |
|---|---|---|---|
| Success (ACCEPT) | DB commit | YES | `test_valid_event_is_persisted_and_committed` |
| Duplicate event (ACCEPT) | DB no-op | YES | `test_redelivery_of_same_event_id_is_idempotent` |
| Transient failure (RETRY) | retry topic confirmed | YES | `test_transient_failure_goes_to_retry_topic_then_commits` |
| Retry exhausted (DLQ) | DLQ confirmed | YES | `test_max_attempt_dead_letters_instead_of_looping` |
| Permanent failure (DLQ) | DLQ confirmed | YES | `test_permanent_handler_failure_goes_to_dlq_then_commits` |
| Validation failure (DLQ) | DLQ confirmed | YES | `test_invalid_utf8_goes_to_dlq_then_commits` |
| DLQ publish fails | none | **NO** | `test_dlq_publish_failure_does_not_commit` |
| Retry publish fails | none | **NO** | `test_retry_publish_failure_does_not_commit` |
| DB commit fails | none | **NO** | `test_db_failure_rolls_back_and_does_not_commit` |
| Unknown error, exhausted | DLQ confirmed | YES | `test_unknown_error_is_dead_lettered_on_exhaustion` |

The rule is enforced structurally: every publish path either confirms and
commits, or raises and returns `committed=False`. There is no code path that
commits without a prior confirmation.

## 8. Retry Topic Design

**Strategy.** Transient failures are not retried in place. The record is
published to a retry topic with a `due_at` timestamp; a retry consumer (not
implemented here, see Remaining Risks) forwards it to the main topic once
due. This keeps the main consumer polling continuously.

**Attempt.** `RetryPolicy.evaluate(attempt + 1)` decides whether another
attempt is allowed. `max_attempts` is enforced against the attempt carried in
the message, so the bound is real across restarts.

**Envelope.** `RetryRecord` carries `attempt`, `due_at`, `source_topic`,
`source_partition`, `source_offset`, `original_key`, and the base64 original
payload. This is sufficient to both re-publish to the main topic and to
reconstruct provenance.

**Transport separation.** `RetryPolicy` decides timing only;
`RetryPublisher` handles transport only. No policy logic lives in the
producer.

**Pause/resume and rebalance.** Not implemented. The architecture leaves the
seam (`RecordSource.pause()`/`resume()` on the consumer port) so a retry
consumer can pause partitions whose next record is not yet due rather than
sleeping. `max.poll.interval.ms` is not raised to mask blocking, because the
main loop no longer blocks.

## 9. DLQ Design

**Envelope.** `DlqRecord` (Pydantic, `extra="forbid"`) with
`dlq_schema_version`, `dlq_id`, `created_at`, `source`, `event` XOR `raw`,
and `failure`.

- **source**: topic, partition, offset, key, record timestamp.
- **event**: `event_id`, `run_id`, `event_type`, `schema_version`, the full
  JSON-mode event dump, sequence and correlation ids.
- **raw**: base64 payload plus byte length, for records that never became an
  envelope.
- **failure**: stage, machine-readable code, error type, bounded message,
  attempt.

**Payload vs raw.** A record that parsed into an envelope stores the event;
one that did not stores the exact original bytes. `replay_bytes()` returns the
replay material either way.

**Security.** Error messages are truncated to 2,000 chars; SQLAlchemy
`DBAPIError` uses `str(exc.orig)` so compiled SQL and bound parameters are
excluded; no stack traces are stored.

**Size limits.** `MAX_ENVELOPE_BYTES` (900,000) sits under the broker default
of 1,000,000. The validator's accepted-input limit is *derived* from it via
base64 inflation (4/3), so a maximum-size accepted record always yields a
publishable envelope. `EventValidator.__init__` raises if the configured
limit cannot satisfy that invariant.

**Broker acknowledgement.** `DeadLetterQueue.publish()` returns only after
`ConfirmedProducer.publish()` confirms delivery; every failure mode raises
`DlqPublishError`, so the consumer can never mistake an enqueue for success.

**Replay.** For invalid records, the base64 payload is decoded and republished
verbatim. Original topic/partition/offset are provenance only; the replay
destination identity is the current publish, with a fresh `dlq_id` per record.
## 10. Failure Scenarios

| Scenario | Behaviour | Loss? | Loop? | Duplicates? |
|---|---|---|---|---|
| Invalid UTF-8 | `INVALID_UTF8` -> DLQ -> commit | no | no | only on crash |
| Invalid JSON | `INVALID_JSON` -> DLQ -> commit | no | no | only on crash |
| Schema violation | `SCHEMA_VALIDATION_ERROR` -> DLQ -> commit | no | no | only on crash |
| Unknown event type | `UNKNOWN_EVENT_TYPE` -> DLQ -> commit | no | no | only on crash |
| Oversized payload | `MESSAGE_TOO_LARGE` -> DLQ -> commit | no | no | only on crash |
| Routing failure | `RouterError` -> DLQ -> commit | no | no | only on crash |
| Permanent domain failure | `HandlerError` -> DLQ -> commit | no | no | only on crash |
| Transient DB failure | RETRY -> retry topic -> commit | no | bounded | only on crash |
| Kafka producer failure | `DlqPublishError` -> **no commit** | no | bounded by redelivery | DLQ repeats |
| Retry publish failure | `RetryPublishError` -> **no commit** | no | bounded by redelivery | retry repeats |
| DB commit failure | escapes `session_scope` -> **no commit** | no | bounded | event repeats, idempotent |
| Unknown error | catch-all RETRY -> exhaustion -> DLQ | no | bounded | only on crash |
| Retry exhaustion | `EXHAUSTED` -> DLQ -> commit | no | no | only on crash |

A poison record is never retried indefinitely: validation failures go
straight to the DLQ, and permanent handler errors are classified as DLQ
rather than RETRY.

## 11. At-Least-Once Semantics

No exactly-once semantics are claimed anywhere in this implementation.

**Case 1: DB commit succeeds, process crashes before the offset commit.**
The record is redelivered and processed again. `EventRepository.
insert_from_envelope` uses `ON CONFLICT (event_id) DO NOTHING` and returns 0
for an existing `event_id`, so the second delivery is a successful no-op.
Verified by `test_redelivery_of_same_event_id_is_idempotent`, which asserts
one row after two deliveries and that no DLQ record is produced.

**Case 2: DLQ publish succeeds, process crashes before the offset commit.**
The record is redelivered and dead-lettered a second time. This is a
**duplicate DLQ record**, not data loss. Each copy has a distinct `dlq_id`
and identical provenance, so a replay tool can detect and deduplicate it.
Verified by `test_duplicate_dlq_after_crash_creates_a_second_distinct_record`.

The trade-off is deliberate: committing without confirmation would lose the
record entirely; leaving the offset uncommitted merely duplicates the
failure record.

## 12. Infrastructure Separation

```
ai_eval_platform/ingestion/            <- application
    kafka_record.py        transport record
    producer.py            ConfirmedProducer / RecordSource protocols
    event_validator.py     validation
    event_router.py        routing
    handler.py             handlers
    delivery_classifier.py WHY
    retry_policy.py        WHEN / HOW LONG
    retry_publisher.py     deferred-retry transport
    dlq.py                 durable failure record
    kafka_consumer.py      orchestration + offset commit

ai_eval_platform/infrastructure/kafka/  <- infrastructure
    config.py              KafkaSettings
    producer.py            ConfluentProducer (delivery reports)
    consumer.py            KafkaConsumerClient (manual commit)
    errors.py              client import isolation
```

The application layer depends only on the protocols and never imports
`confluent_kafka`; this is asserted by an AST-based test. The Kafka client is
an optional dependency, so the application layer remains importable and
testable without a broker.

## 13. Tests

**Before changes**

```
python -m pytest -q
106 passed, 5 skipped in 2.74s
```

**After changes**

```
python -m pytest -q
178 passed, 5 skipped in 2.65s
```

72 tests added; the 5 skips are the pre-existing PostgreSQL integration
tests, which self-skip without `DATABASE_URL`.
## 14. Tests Added or Modified

New files under `tests/ingestion/`: `conftest.py`, `test_validator.py`,
`test_dlq.py`, `test_retry.py`, `test_consumer_offsets.py`,
`test_ingestion_db.py`, `test_kafka_config.py`.

| Test | Failure mode it protects against |
|---|---|
| `test_invalid_utf8_preserves_raw_bytes` | Poison bytes lost before they can be dead-lettered |
| `test_invalid_json_preserves_raw_bytes` | Same, for malformed JSON |
| `test_schema_validation_failure_has_machine_readable_code` | Losing the diagnostic code, forcing string matching |
| `test_validator_never_raises_on_malformed_input` | An exception escaping the validator and killing the loop |
| `test_validator_rejects_limit_that_cannot_be_dead_lettered` | Size inversion returning (un-DLQ-able oversize record) |
| `test_maximum_accepted_record_fits_in_a_dlq_envelope` | The size invariant itself |
| `test_oversized_payload_is_dead_lettered_not_looped` | Infinite redelivery of oversize records |
| `test_dlq_publish_failure_does_not_commit` | **Premature offset commit -> message loss** |
| `test_retry_publish_failure_does_not_commit` | Same, on the retry path |
| `test_db_failure_rolls_back_and_does_not_commit` | Committing a record whose write failed |
| `test_permanent_handler_failure_goes_to_dlq_then_commits` | Infinite retry of a permanent failure |
| `test_transient_failure_goes_to_retry_topic_then_commits` | Dropping transient failures instead of deferring them |
| `test_retry_attempt_header_increments_attempts` | Retry counter reset on restart |
| `test_max_attempt_dead_letters_instead_of_looping` | Unbounded retry |
| `test_malformed_attempt_header_degrades_to_zero` | A bad header crashing the consumer loop |
| `test_duplicate_event_is_accepted_without_dlq` | Duplicates being dead-lettered as failures |
| `test_redelivery_of_same_event_id_is_idempotent` | Duplicate rows under at-least-once |
| `test_duplicate_dlq_after_crash_creates_a_second_distinct_record` | Undetectable duplicate DLQ records |
| `test_processing_never_sleeps_on_retry_backoff` | Blocking sleep starving `max.poll.interval.ms` |
| `test_unknown_error_is_dead_lettered_on_exhaustion` | Silent acknowledgement of unknown errors |
| `test_run_stops_when_requested`, `test_idle_loop_does_not_busy_spin` | Busy-loop on an empty source; unclean shutdown |
| `test_dlq_error_message_excludes_sql_and_bound_parameters` | Payload leakage via `str(DBAPIError)` |
| `test_dlq_never_stores_stack_traces` | Stack-trace leakage into a retained topic |
| `test_transport_metadata_is_not_merged_into_domain_envelope` | Kafka fields polluting the domain schema |
| `test_auto_commit_true_is_rejected_loudly` | Silently enabling the data-loss window |
| `test_application_layer_does_not_import_confluent_kafka` | Layering violation |

### Limitations of these tests

The producer and consumer are **test doubles**. A `RecordingProducer` that
returns `None` mimics a *confirmed* publish, and `FakeConsumer` models commit
intent directly. These tests verify orchestration, ordering, and the
commit/no-commit decision - they do **not** prove broker semantics. Real
delivery-report behaviour, timeout handling, and rebalance behaviour are
untested here and require integration tests against a broker.
## 15. Problems Found

### [CRITICAL] P1 - `produce()` treated as proof of broker delivery
**Evidence.** `dlq.py:11-19` declared `produce(...) -> None`; `publish()`
called it and returned. No callback, no `poll()`, no `flush()`. No Kafka client
was installed, so no implementation existed.
**Root cause.** The Protocol encoded "enqueue" while callers read it as
"durable".
**Impact.** A consumer would commit an offset for a DLQ record the broker
never acknowledged: silent, unrecoverable message loss.
**Fix.** `ConfirmedProducer.publish()` returns only after a delivery report;
`DlqPublishError` on any failure.
**Test.** `test_dlq_publish_failure_does_not_commit`.

### [CRITICAL] P2 - DLQ could not represent a poison record
**Evidence.** `publish(event: dict[str, Any])` with no raw-bytes path, while
`EventValidationError` fired precisely when no dict existed.
**Root cause.** Envelope designed around the parsed event only.
**Impact.** The most common DLQ path was unimplementable; poison records
would be dropped or given an undocumented shape.
**Fix.** `DlqRecord` carries `event` XOR `raw` (base64).
**Test.** `test_invalid_utf8_preserves_raw_bytes`, `test_dlq_record_for_validation_failure_keeps_raw_bytes`.

### [CRITICAL] P3 - Size inversion made oversize records undLQ-able
**Evidence.** Validator accepted `1_048_576` bytes; broker default
`max.message.bytes` is `1_000_000`; DLQ envelopes add overhead and base64
inflates 4/3.
**Root cause.** Input limit chosen without reference to the DLQ envelope.
**Impact.** Oversize poison records could never be dead-lettered, and the
no-commit rule redelivered them forever.
**Fix.** Limit derived from the DLQ budget, enforced at construction.
**Test.** `test_validator_rejects_limit_that_cannot_be_dead_lettered`, `test_maximum_accepted_record_fits_in_a_dlq_envelope`.

### [HIGH] P4 - No consumer orchestration at all
**Evidence.** `kafka_consumer.py` was 0 bytes; `deduplicator.py` absent.
**Impact.** Every offset-commit invariant was unenforced and untested.
**Fix.** `KafkaEventConsumer` with the commit matrix in section 7.
**Test.** All of `test_consumer_offsets.py`.

### [HIGH] P5 - DLQ keyed by `event_id`, diverging from `run_id`
**Evidence.** `_extract_event_key` returned `event["event_id"]`.
**Root cause.** Treated `event_id` as identity for partitioning.
**Impact.** Broke per-run ordering and replay locality; a `None` key sent
redeliveries to different partitions.
**Fix.** DLQ keyed by `run_id`; retry keyed by the original key.
**Test.** `test_dlq_key_is_run_id_to_preserve_per_run_ordering`, `test_retry_uses_original_key_to_preserve_partition`.

### [HIGH] P6 - `str(DBAPIError)` leaked SQL and bound parameters
**Evidence.** `error_message=str(error)`; SQLAlchemy includes the compiled
statement and parameter tuple.
**Impact.** Payload values copied into a second topic; contradicted the
validator's deliberate `include_input=False`.
**Fix.** `str(exc.orig)` for `DBAPIError`, plus truncation.
**Test.** `test_dlq_error_message_excludes_sql_and_bound_parameters`.

### [HIGH] P7 - Attempt counter could not survive a restart
**Evidence.** `RetryPolicy` was in-memory only; nothing carried the attempt.
**Impact.** A restart reset retries, allowing unbounded retry of a poison
record.
**Fix.** Attempt carried in the retry envelope and read from the record
header; malformed values degrade to 0.
**Test.** `test_retry_attempt_header_increments_attempts`, `test_malformed_attempt_header_degrades_to_zero`.

### [HIGH] P8 - Application layer imported `confluent_kafka`
**Evidence.** `delivery_classifier._is_transient_kafka_error` imported the
client; it was absent, so the predicate was dead code returning `False`.
**Impact.** Application layer untestable without the broker; Kafka errors
were misclassified by accident.
**Fix.** Classification keys off the application's `PublishError` hierarchy;
infrastructure maps broker errors onto it.
**Test.** `test_application_layer_does_not_import_confluent_kafka`.

### [HIGH] P9 - Validator raised on malformed input
**Evidence.** `EventValidationError` raised from every failure path.
**Impact.** Lost raw bytes; the DLQ had nothing to replay; an unexpected
exception could kill the consumer loop.
**Fix.** Returns `ValidationSuccess`/`ValidationFailure`.
**Test.** `test_validator_never_raises_on_malformed_input`.
### [MEDIUM] P10 - `RetryPolicy.py` filename breaks case-sensitive CI
**Evidence.** PascalCase module name.
**Impact.** Import failure on Linux, easily misdiagnosed as a missing
dependency.
**Fix.** Renamed to `retry_policy.py`; references updated.
**Test.** Module import covered by the suite.

### [MEDIUM] P11 - Busy-loop when the source is empty
**Evidence.** `run()` looped on `poll()` returning an empty list.
**Impact.** CPU spin; a test hung until manually killed.
**Fix.** `idle_poll_limit` bounds the loop; `stop()` for graceful shutdown.
**Test.** `test_idle_loop_does_not_busy_spin`, `test_run_stops_when_requested`.

### [MEDIUM] P12 - Duplicate ACCEPT fell through to RETRY
**Evidence.** `_handle_processing_failure` branched only on `DLQ`, so the
classifier's `ACCEPT` for a duplicate `event_id` fell into the retry path.
**Impact.** Duplicates were republished to the retry topic instead of being
acknowledged - found by a test, not by inspection.
**Fix.** Explicit `ACCEPT` branch commits immediately.
**Test.** `test_duplicate_event_is_accepted_without_dlq`.

### [MEDIUM] P13 - Failure message built outside the DLQ
**Evidence.** The consumer constructed `FailureInfo` with an untruncated
`str(error)`, bypassing DLQ scrubbing.
**Impact.** A long validation message raised a Pydantic error, crashing the
DLQ path.
**Fix.** `DeadLetterQueue.build_failure_info()` centralises scrubbing.
**Test.** `test_oversized_payload_is_dead_lettered_not_looped`.

## 16. Remaining Risks

Honest assessment of what is **not** proven:

1. **Real broker semantics are untested.** Every publish test uses a fake
   producer that returns immediately. `ConfluentProducer`'s delivery-report
   blocking, timeout handling, and error mapping have **never executed**.
   This needs an integration test against a broker or testcontainers.
2. **No multi-broker or rebalance testing.** Pause/resume, partition
   revocation, and re-dispatch during a long DLQ publish are untested.
   `max.poll.interval.ms` (300 s) must exceed the worst case of validate +
   PostgreSQL write + DLQ confirmation; under degradation this could be
   exceeded and the consumer evicted mid-record. The fix is to bound
   `max.poll_records` and per-record work, **not** to raise the interval.
3. **No crash-injection testing.** The crash windows are reasoned about and
   asserted with doubles; actually killing a process mid-publish is untested.
4. **A retry *consumer* is not implemented.** The producer side is complete
   (`RetryRecord` -> retry topic), but nothing reads the retry topic and
   forwards due records to the main topic. Until then, deferred retries are
   published but never delivered. This is the main remaining functional gap.
5. **DLQ replay tooling is not implemented.** `DlqRecord.replay_bytes()`
   provides the material, but no service consumes it.
6. **Load and throughput untested.** `max.poll_records=100` bounds the batch;
   memory behaviour under sustained lag is unverified.
7. **Duplicate detection remains dialect string-matching.** Normal operation
   is safe because `ON CONFLICT DO NOTHING` returns 0 rather than raising;
   this path is a backstop, and changed driver wording could turn duplicates
   into DLQ records.
8. **`security/pii.py` is still a 0-byte placeholder**, so the DLQ stores
   payload content unredacted - a deliberate design decision, not redaction.
9. **Observability is minimal.** Structured logs exist (`dead_lettered`,
   `dlq_publish_failed`, `retry_publish_failed`, `malformed_attempt_header`),
   but there are no metrics or DLQ-rate alerts.
10. **PostgreSQL is untested on the consumer path.** The DB-backed ingestion
    tests use SQLite; the dialect-specific repository tests skip without
    `DATABASE_URL`.

## 17. Recommended Next Step

Implement the **retry consumer** (AREA 9): the only gap between "records are
deferred" and "records are actually retried". A `RetryDispatcher` should use
`due_at` to decide forward-vs-pause, keeping the poll loop alive and pausing
partitions whose next record is not yet due rather than sleeping.

Recommended order:

1. Retry consumer with due-time handling and pause/resume.
2. Integration tests against a real broker, proving the `ConfluentProducer`
   confirmation path - the most important untested claim in this document.
3. Rebalance test: pause partitions, trigger a rebalance, verify no offset is
   committed for an in-flight record.
4. DLQ replay tooling using `DlqRecord.replay_bytes()`.
5. Metrics and DLQ-rate alerting.

Do **not** raise `max.poll.interval.ms` to mask slow processing; bound the
work per poll instead.
## 18. Change Integrity

**Source files modified:**

- `src/ai_eval_platform/ingestion/kafka_record.py` (new)
- `src/ai_eval_platform/ingestion/producer.py` (new)
- `src/ai_eval_platform/ingestion/event_validator.py`
- `src/ai_eval_platform/ingestion/dlq.py`
- `src/ai_eval_platform/ingestion/retry_publisher.py` (new)
- `src/ai_eval_platform/ingestion/kafka_consumer.py`
- `src/ai_eval_platform/ingestion/delivery_classifier.py`
- `src/ai_eval_platform/ingestion/RetryPolicy.py` -> `retry_policy.py` (rename)
- `src/ai_eval_platform/infrastructure/__init__.py` (new)
- `src/ai_eval_platform/infrastructure/kafka/__init__.py` (new)
- `src/ai_eval_platform/infrastructure/kafka/config.py` (new)
- `src/ai_eval_platform/infrastructure/kafka/producer.py` (new)
- `src/ai_eval_platform/infrastructure/kafka/consumer.py` (new)
- `src/ai_eval_platform/infrastructure/kafka/errors.py` (new)

**Tests modified:** none existing.

**Tests added:** `tests/ingestion/{__init__,conftest,test_validator,test_dlq,
test_retry,test_consumer_offsets,test_ingestion_db,test_kafka_config}.py`

**Dependencies modified:** `pyproject.toml` - added the optional `kafka`
extra (`confluent-kafka>=2.3`, `PyYAML>=6.0`). No *required* dependency was
added; the application layer runs without the Kafka client.

**Configuration modified:** `configs/kafka.yaml` (was 0 bytes) - bootstrap
servers, group id, topics, `enable_auto_commit: false`, offset reset,
poll/session/heartbeat timings, poll records, message size, publish timeout.

**Not modified (deliberately, out of scope):** `storage/`, `schemas/`,
`event_router.py`, `handler.py`, `retry_policy.py` logic, `api/`, `batch/`,
`ml/`, `evaluation/`, `monitoring/`.

**`Kafka_analysis.md`:** created.

| | Result |
|---|---|
| Tests before | 106 passed, 5 skipped in 2.74s |
| Tests after | 178 passed, 5 skipped in 2.65s |

The 5 skips are the pre-existing PostgreSQL integration tests, which self-skip
without `DATABASE_URL`.

### Unresolved items

- `deduplicator.py` remains absent. The duplicate-detection concern it was
  meant to address is handled by repository-level `event_id` idempotency,
  which is verified by tests; no consumer-side ledger was added.
- A retry *consumer* is not implemented (Remaining Risk 4). Deferred retries
  are published but not yet redelivered.
- `dlq.md` from the previous turn still exists and describes the pre-change
  state; this document supersedes it.
- `kafka_consumer.py` was overwritten by an external process during this work
  (it appeared at 7113 bytes, timestamped 14:50, before this task started,
  referencing DLQ methods that did not exist and failing to import). It was
  replaced with the implementation described here.
---

# Retry Consumer and Kafka Reliability Phase

_Phase 2. Sections 1-18 above are preserved as the record of phase 1. This
section documents phase 2, which closes the retry lifecycle and fixes one
critical offset-commit defect found by re-verification._

## P1. Previous State

Phase 1 delivered the main consumer, the DLQ, and the retry *publisher*. The
retry lifecycle stopped there: `RetryPublisher` put records on the retry
topic and **nothing consumed them**. Phase 1's Remaining Risk 4 recorded this
honestly as the main functional gap. Phase 1 test count: 178 passed / 5
skipped.

## P2. Findings

### Verification of phase 1 claims

I re-read the source rather than trusting the phase 1 report, and found **one
critical contradiction** plus several smaller ones.

**[CRITICAL] F1 - the phase 1 offset-commit claim was wrong.**

Phase 1 section 7 asserted: "a record whose DLQ or retry publish failed is
never included in a commit, so it is redelivered." The code did not do that.

`KafkaConsumerClient.commit()` was:

```python
def commit(self) -> None:
    if not self._pending or self._closed:
        return
    self._consumer.commit(asynchronous=False)   # <-- no offsets argument
```

In `confluent-kafka`, `commit()` **with no offsets argument commits the
current position of every assigned partition**. The module docstring claimed
the opposite. The failure sequence:

1. Partition 0 offset 100 is polled; the DLQ publish fails; **no commit**.
2. Partition 1 offset 200 is polled; it succeeds; `commit()` is called.
3. That bare commit advances partition 0 to 101 **as well**.
4. The record that was never dead-lettered is silently skipped. **Message
   loss.**

I proved this against a faithful double of `confluent_kafka.Consumer` *before*
changing anything: `commit()` was invoked with `offsets=None` while two
records were pending on different partitions.

**[MEDIUM] F2 - `RecordSource.commit()` took no arguments**, so the
application could not express "commit this record" at all. The abstraction
actively hid the dangerous semantics.

**[MEDIUM] F3 - nothing consumed the retry topic.** Deferred retries were
published and then stranded.

**[LOW] F4 - `pause()`/`resume()` took a `KafkaRecord`** rather than a
`(topic, partition)` pair. A dispatcher pauses by partition and may not hold
the record that triggered the pause.

**[LOW] F5 - `docker-compose.yml` and `docker/Dockerfile.*` are 0 bytes** and
**Docker is not installed** on this machine, so real-Kafka integration tests
are impossible here. Verified by running `docker --version`, not assumed.

**[INFO] F6 - `retry_topic_partitions` existed in config but was unused**;
nothing consumed the topic, so it could not be.

## P3. Design Investigation

### Is the retry envelope sufficient?

Audited field by field against what a dispatcher needs:

| Needed | Field | Present before? |
|---|---|---|
| original payload | `original_value_base64` | yes |
| original key | `original_key` | yes |
| original topic | `source_topic` | yes |
| original partition | `source_partition` | yes |
| original offset | `source_offset` | yes |
| attempt | `attempt` | yes |
| due time | `due_at` | yes |
| retry identity | `retry_id` | yes |

**Nothing was missing and nothing was added.** Re-adding `run_id` would have
been duplicate information: it *is* the `original_key` on the main topic.

### The big question: due_at and head-of-line blocking

Kafka orders records *within a partition*. One retry topic holding

```
A (due 15:30)   B (due 15:31)   C (due 15:05)
```

means C is due but sits behind A and B. In-order release blocks C for 20
minutes. This is real and had to be answered before choosing an
implementation.

| Option | Correctness | Complexity | Ordering | Verdict |
|---|---|---|---|---|
| Sleep until due | Violates `max.poll.interval.ms`; stalls all partitions | low | fine | **rejected** |
| Pause the partition | Correct; keeps polling others | medium | preserved | **chosen** |
| Delay topics (`retry.5s/30s/5m`) | Removes blocking | high: N topics, N partition sets | **breaks** - one run's events split across buckets | rejected |
| External scheduler | Correct | very high | preserved | deferred |

**Why pausing is correct here, not merely tolerable.** The retry topic is
keyed by the *original key*, which is `run_id`. So A, B and C share a
partition **only if they are the same run**. Within a run, events are
causally ordered, so C must not overtake A - releasing C early would break
the per-run ordering the main topic depends on. The "blocking" is really just
correct in-order release of one run's events.

The cost is therefore bounded to **one run**, not the topic: a partition
carries many runs, and the dispatcher keeps polling the rest.

Delay topics were rejected specifically because they would *fix* the blocking
by *breaking* per-run ordering - the cure is worse than the disease for a
platform whose retry volume is capped by `max_attempts` (5).
### Key semantics - decided independently, not inherited

* **Main topic: `run_id`.** Unchanged. Same key = same partition = ordering.
* **Retry topic: the original key.** So a retried record stays co-located
  with the same run's other retries, and forwarding it back returns it to
  its original main-topic partition.
* **DLQ: `run_id`, taken from the DOMAIN event** - not the transport key and
  not `event_id`. `event_id` would scatter one run's failures across
  partitions and destroy replay locality. Using the domain `run_id` means the
  key survives even if the transport key is lost. Verified by
  `test_dlq_is_keyed_by_run_id_not_event_id`, which deliberately uses a
  transport key that differs from the event's `run_id`.

**Partition numbers are never copied.** Partition N of the retry topic has no
relation to partition N of the main topic. The dispatcher forwards with the
*key* and lets the producer's partitioner choose. Proven by
`test_forward_does_not_reuse_retry_partition_number`.

### Attempt semantics

| Situation | Value used | Behaviour |
|---|---|---|
| First delivery, no header | 0 | first failure publishes attempt 1 |
| Header present | parsed int | `RetryPolicy.evaluate(attempt + 1)` |
| Header missing | 0 | treated as a first delivery |
| Header malformed (`"abc"`) | 0 | logged, **degraded not crashed** |
| Header negative | 0 | clamped by `max(value, 0)` |
| `attempt >= max_attempts` in retry topic | as-is | **dead-lettered**, never forwarded |
| Envelope `attempt` invalid (0, -3) | rejected by Pydantic | **dead-lettered as malformed** |

The dispatcher checks `is_exhausted()` **before** `is_due()`, so an inflated
attempt cannot loop: it terminates at the DLQ regardless of `due_at`.

### Partition ordering and the sequence-number race

`EventRepository.next_sequence_number` allocates
`coalesce(MAX(sequence_number), 0) + 1` per run. That is **only** safe with a
single writer per run. The guarantee therefore depends on the consumer
processing each Kafka partition in a single ordered stream.

The consumer is single-threaded by construction: `run()` polls, then
processes records sequentially, with no worker pool, no `asyncio`, and no
threading. This is enforced by `test_consumer_has_no_worker_pool_or_background_threads`,
which AST-parses `kafka_consumer.py` and fails if `Thread`,
`ThreadPoolExecutor`, `ProcessPoolExecutor`, `asyncio`, or `create_task`
appears. Adding concurrency for performance would break the repository's
assumption, so it is deliberately not done.

`test_sequence_number_allocation_is_single_writer_only` asserts the `MAX()+1`
allocation is still present, so the constraint is recorded rather than
silently relaxed.

### Poll-loop safety

The retry loop never sleeps. A not-yet-due record pauses its partition; the
consumer keeps polling everything else, so `max.poll.interval.ms` is never at
risk. Proven by `test_loop_does_not_sleep_while_deferring` - a record due in an
hour returns in well under a second.

Honest limitation: a paused partition is re-read on a later poll, so release
is *jittery*, not exactly on time. That is delay jitter, not delay loss;
correctness needs only "not before `due_at`" and "eventually".

### Offset semantics (what `commit()` means)

Documented precisely, because phase 1 got this wrong:

* The client now requires explicit `TopicPosition(topic, partition,
  next_offset)` values. A bare `commit()` is impossible through the
  abstraction.
* `next_offset` follows Kafka's convention: committing `N` means "the next
  record to read is N", i.e. `record.offset + 1`.
* Commits are **synchronous** (`asynchronous=False`) so a commit failure
  raises to the caller rather than silently losing offsets.
* Committing an empty list is a no-op.
* Committing on a closed consumer raises rather than silently succeeding.

## P4. Chosen Architecture

```
  MAIN TOPIC (key = run_id)
        |
        v
  KafkaEventConsumer.process()
        |
   transient failure
        |
        v
  RetryPolicy.evaluate(attempt+1)      <-- WHEN / HOW LONG
        |
        v
  RetryPublisher.publish()              <-- HOW TO PUBLISH (broker-confirmed)
        |
        v
  RETRY TOPIC (key = original key)
        |
        v
  RetryConsumerLoop  (poll, never sleeps)
        |
        v
  RetryDispatcher.dispatch()           <-- HOW TO RELEASE DUE RETRIES
        |
   +----+-------------------+-------------------+
   |                        |                   |
 not due              attempt >= max        unparseable
   |                        |                   |
   v                        v                   v
 DEFER                DEAD-LETTER          DEAD-LETTER
 pause partition      (DLQ confirmed)      (DLQ confirmed)
 NO commit            commit               commit
   |
   (re-read on a later poll)
   |
 due
   |
   v
 republish ORIGINAL payload -> MAIN TOPIC (key = original key)
   |
   v
 broker confirmed -> commit retry offset
   |
   v
 MAIN TOPIC -> KafkaEventConsumer (attempt header) -> retry again, or DLQ
```

| Situation | Action | Commit retry offset? |
|---|---|---|
| Due, forward succeeds | FORWARDED | yes |
| Due, forward fails | DEFERRED | **no** (redelivered) |
| Not due | DEFERRED | **no** (partition paused) |
| `attempt >= max` | DEAD_LETTERED | yes, after DLQ confirms |
| Unparseable envelope | DEAD_LETTERED | yes, after DLQ confirms |
| DLQ publish fails | DEFERRED | **no** (redelivered) |

A malformed retry record goes to the DLQ because its `due_at` is unknown: it
cannot be scheduled, and retrying it would loop forever.
## P5. Files Modified

| File | Change |
|---|---|
| `ingestion/producer.py` | **added `TopicPosition`**; `RecordSource.commit(positions)` takes explicit per-partition positions |
| `ingestion/kafka_consumer.py` | `_commit(record)` commits `offset+1` for that record's partition only |
| `ingestion/retry_dispatcher.py` (**new**) | `RetryDispatcher` - due/attempt/malformed decisions, forward, dead-letter |
| `ingestion/retry_consumer.py` (**new**) | `RetryConsumerLoop` - poll loop, pause on defer, commit on resolve |
| `infrastructure/kafka/consumer.py` | `commit(positions)` with explicit `TopicPartition`s; `pause`/`resume` take `(topic, partition)` |
| `infrastructure/kafka/config.py` | `retry_group_id`, `retry_max_attempts`, `resolved_retry_group_id()` |
| `tests/ingestion/conftest.py` | `FakeConsumer.commit(positions)` records the actual positions |

New test files: `test_retry_consumer.py`, `test_retry_lifecycle.py`,
`test_retry_crash_windows.py`, `test_ordering.py`.

**No existing test was modified or deleted.** The storage layer, schemas,
event router and handlers are untouched.

## P6. At-Least-Once Semantics

No exactly-once is claimed. Every window produces a *duplicate*, never a
*loss*:

| Window | Result | Protection |
|---|---|---|
| DB commit succeeds, crash before offset commit | event redelivered | `ON CONFLICT (event_id) DO NOTHING`; `test_redelivery_of_same_event_id_is_idempotent` |
| Retry publish succeeds, crash before main offset commit | **duplicate retry record** | distinct `retry_id`s, so duplicates are detectable; both forward the same original bytes, so the DB de-duplicates |
| Retry forwarded, crash before retry offset commit | retry forwarded **twice** | identical original payload -> same `event_id` -> second is a DB no-op. `test_duplicate_retry_is_processed_idempotently_downstream` |
| DLQ publish succeeds, crash before offset commit | **duplicate DLQ record** | distinct `dlq_id` per occurrence; `test_duplicate_dlq_after_crash_creates_a_second_distinct_record` |
| Retry publish fails | original offset uncommitted | original redelivered; `test_retry_publish_failure_leaves_original_offset_uncommitted` |
| Forward to main fails | retry offset uncommitted | retry redelivered and retried; never dead-lettered on a transient broker error |

**`dlq_id` semantics (chosen and documented):** `dlq_id` identifies a
**single DLQ publication occurrence**, not the event. Event identity is
`event_id`; replay identity is a fresh record with its own `dlq_id`. A
duplicated dead letter therefore has two `dlq_id`s for one `event_id`, which
is exactly what makes the duplicate detectable.

## P7. Tests

**Before this phase**

```
python -m pytest -q
178 passed, 5 skipped in 3.65s
```

**After this phase**

```
python -m pytest -q
230 passed, 5 skipped in 3.31s
```

52 tests added; **zero regressions** - every phase 1 test still passes.
### Unit tests (prove application logic)

| Test | Protects against |
|---|---|
| `test_retry_envelope_has_everything_needed_to_replay` | Envelope losing replay material |
| `test_due_record_is_forwarded_to_main_topic` | Due records never released |
| `test_future_record_is_deferred_not_forwarded` | Releasing before `due_at` |
| `test_forward_carries_attempt_header_forward` | Retry bound reset each round-trip |
| `test_forward_preserves_original_key_for_run_ordering` | Per-run ordering broken |
| `test_forward_does_not_reuse_retry_partition_number` | Cross-topic partition reuse |
| `test_record_at_max_attempts_is_dead_lettered` | Unbounded retry |
| `test_excessive_attempt_cannot_loop_forever` | Inflated attempt looping |
| `test_invalid_attempt_is_dead_lettered_not_looped[0,-3]` | Invalid attempt metadata looping |
| `test_malformed_retry_goes_to_dlq` | Poison retry record looping |
| `test_malformed_retry_never_creates_an_infinite_loop` | Same, asserted as a property |
| `test_forward_publish_failure_does_not_report_success` | Fake success on broker failure |
| `test_dlq_publish_failure_defers_instead_of_dropping` | Dropping a poison retry |
| `test_loop_does_not_commit_a_deferred_record` | **Acknowledging an unreleased retry** |
| `test_loop_pauses_the_partition_when_deferring` | Sleeping instead of pausing |
| `test_loop_pauses_each_partition_once` | Pause storm on one partition |
| `test_loop_does_not_sleep_while_deferring` | `max.poll.interval.ms` violation |
| `test_loop_processes_due_records_around_a_future_one` | One run starving all others |
| `test_loop_forward_failure_leaves_retry_offset_uncommitted` | Losing a retry on broker error |
| `test_consumer_has_no_worker_pool_or_background_threads` | Concurrency breaking `MAX()+1` |
| `test_dlq_is_keyed_by_run_id_not_event_id` | Wrong DLQ key semantics |
| `test_commit_is_explicit_per_partition_not_bare` | **The critical commit regression** |
| `test_failed_record_partition_is_not_committed_by_another_record` | Cross-partition premature commit |

### Integration tests (application-level, real SQLAlchemy)

`test_retry_lifecycle.py` wires the real consumer, router, handlers and
repository against a real (SQLite) session, injecting a flaky repository
*below* the handler so the full path is exercised:

| Test | Proves |
|---|---|
| `test_transient_failure_deferred_then_forwarded_then_succeeds` | The **complete loop**: main -> retry -> defer -> due -> forward -> main -> DB row |
| `test_retry_exhaustion_reaches_dlq` | Retry exhaustion terminates at the DLQ, no loop |
| `test_retry_consumer_loop_defers_then_forwards_across_the_cycle` | The loop itself, not just the dispatcher |
| `test_retry_publish_failure_leaves_original_offset_uncommitted` | Retry publish failure loses nothing |

These prove the application's state machine across the whole cycle. They use
a producer double, so they do **not** prove broker semantics.

## P8. Integration Testing Status

**Real-Kafka integration tests are NOT possible in this environment, and I
have not faked them.**

Verified facts, not assumptions:

* `docker --version` -> command not found. Docker is not installed.
* `docker-compose.yml` -> 0 bytes (empty placeholder).
* `docker/Dockerfile.api`, `Dockerfile.batch`, `Dockerfile.consumer` -> all
  0 bytes.
* `confluent_kafka` is not installed (`ModuleNotFoundError`).

Therefore tests A-H from the brief (real produce/consume/commit, real retry
topics, real DLQ records, real rebalances) **cannot be executed here**. The
`ConfluentProducer` delivery-report blocking, timeout handling and error
mapping have still never run.

Smallest path to real coverage:

1. Install `confluent-kafka`, then run against a single-node Redpanda or
   Kafka via testcontainers.
2. Or install Docker and fill in the four 0-byte compose/Dockerfile files.

Until then the honest statement is: **the application's ordering and commit
decisions are tested; broker behaviour is not.**
## P9. Remaining Risks

1. **Broker semantics untested** (P8). The most important unproven claim in
   either phase.
2. **No rebalance testing.** If a partition is revoked mid-processing,
   `confluent-kafka` may reject the subsequent `commit()`. The code would
   raise (commits are synchronous), the record would be redelivered to
   another consumer, and `event_id` idempotency would absorb it - but this is
   *reasoned*, not tested.
3. **Paused-partition re-read is jittery.** Release happens on a later poll,
   not exactly at `due_at`. Correctness unaffected; latency is.
4. **No DLQ replay tooling.** `DlqRecord.replay_bytes()` provides the
   material; no service consumes it.
5. **`retry_topic_partitions` is not applied** to topic creation (no admin
   client exists). Retention and partition count remain operational config.
6. **Observability is minimal.** Structured logs exist (`retry_forwarded`,
   `retry_forward_failed`, `retry_dead_lettered`, `retry_partition_paused`,
   `dlq_publish_failed`). No metrics, no DLQ-rate alerting.
7. **PostgreSQL is untested on the consumer path** (SQLite is used).
8. **Duplicate detection is still dialect string-matching** in
   `DeliveryClassifier._is_duplicate_event`. Safe in practice because
   `ON CONFLICT` returns 0 rather than raising.
9. **`security/pii.py` is still 0 bytes** - the DLQ stores payload content
   unredacted by decision, not by redaction.
10. **Backpressure is implicit.** Batches are bounded by `max_poll_records`;
    retry/DLQ topic growth depends on broker retention, not managed in code.

## P10. Next Step

Only work that is genuinely still missing:

1. **Stand up a real broker** (Redpanda via testcontainers, or Docker) and
   write integration tests A-H. Highest value by far: the
   delivery-confirmation path is the core correctness claim and has never
   executed.
2. **Rebalance test**: pause partitions, trigger a rebalance, assert no
   offset is committed for an in-flight record.
3. **DLQ replay tooling** using `DlqRecord.replay_bytes()`.
4. **Metrics and DLQ-rate alerting**.
5. **Admin/topic provisioning** applying `retry_topic_partitions` and
   retention policies.

Do **not** raise `max.poll.interval.ms` to mask slow processing; bound the
work per poll instead.