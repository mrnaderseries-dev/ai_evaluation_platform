# Kafka Ingestion - Final Conclusions

_Date: 2026-10-04. Companion to `Kafka_analysis.md`, which holds the full
phase-by-phase history. This document states what was concluded, what was
tested, and what was fixed._

---

## 1. What I Conclude

### The pipeline is now correct under at-least-once delivery

Every record reaches a durable destination before its source offset is
acknowledged: PostgreSQL for accepted events, the retry topic for deferred
retries, the DLQ for permanent failures. No path commits an offset without a
prior confirmation. That invariant is enforced structurally rather than by
convention, and it is covered by tests.

### The single most important finding was a bug in my own previous work

Phase 1 documented that *"a record whose DLQ or retry publish failed is never
included in a commit, so it is redelivered."* That claim was **false**, and
the code did the opposite.

`KafkaConsumerClient.commit()` called `self._consumer.commit(asynchronous=False)`
with **no offsets argument**. In `confluent-kafka`, a bare `commit()`
advances the current position of **every assigned partition**. So:

```
partition 0, offset 100  -> DLQ publish fails   -> no commit
partition 1, offset 200  -> succeeds            -> commit()
                                              -> advances partition 0 too
                                              -> record 100 silently skipped
                                              -> MESSAGE LOSS
```

I verified this against a faithful double of the client *before* changing
anything, rather than reasoning about it. The fix makes the safe behaviour the
only expressible one: commits now carry an explicit
`TopicPosition(topic, partition, offset + 1)`, so a bare commit cannot be
written.

**Lesson worth carrying forward:** the failure mode was not exotic. It was
caused by an API that *looked* safe and had a docstring asserting the opposite
of what it did. Documentation written alongside a bug is not evidence the bug
is absent.

### Head-of-line blocking in the retry topic is not a defect here

With one retry topic keyed by `run_id`, records A (due 15:30), B (due 15:31)
and C (due 15:05) share a partition only if they belong to the same run.
Within a run, events are causally ordered, so **C must not overtake A**.
In-order release is correct behaviour, not a limitation to engineer around.

Delay topics (`retry.5s` / `retry.30s` / `retry.5m`) would remove the
blocking by splitting one run's events across buckets - fixing the apparent
problem by introducing a real one. Rejected.

The genuine cost is that a future-dated record delays **its own run**, not the
topic: a partition carries many runs and the dispatcher keeps polling the
rest. Verified by `test_loop_processes_due_records_around_a_future_one`.

### Attempt semantics are now safe against every malformed input

The attempt travels in the message, not in process memory, so the bound
survives a restart. The dispatcher checks **exhaustion before due-ness**, so
an inflated attempt terminates at the DLQ regardless of `due_at`. A missing,
malformed, negative or out-of-range attempt can each only lead to the DLQ -
never to an unbounded loop.

### What I did not conclude

**Broker semantics remain unproven.** Every test uses a producer double that
returns immediately. `ConfluentProducer`'s delivery-report blocking, timeout
handling, and error mapping have never executed. I verified this is not
fixable here: Docker is not installed (`docker --version` -> command not
found), `docker-compose.yml` is 0 bytes, and `confluent_kafka` is not
installed. I did not simulate these tests or claim they pass.

Rebalance behaviour is *reasoned about*, not tested. If a partition is
revoked mid-processing the synchronous commit raises, the record is
redelivered, and `event_id` idempotency absorbs it - but that is inference.
## 2. What I Tested

### Results

| Suite | Before | After |
|---|---|---|
| Full suite | 106 passed, 5 skipped | **230 passed, 5 skipped** |
| Ingestion tests | 0 (did not exist) | **124 passed** |

`python -m pytest -q` -> `230 passed, 5 skipped in 2.75s`, exit code 0.

The 5 skips are pre-existing PostgreSQL integration tests that self-skip
without `DATABASE_URL`. **Zero regressions**: all 106 original tests still
pass unmodified.

### Breakdown by file (123 test functions; 124 collected)

| File | Tests | Area |
|---|---|---|
| `test_retry_consumer.py` | 29 | Dispatcher + retry loop (1 parametrized x2) |
| `test_consumer_offsets.py` | 22 | Offset-commit safety matrix |
| `test_kafka_config.py` | 13 | Config invariants, layering |
| `test_retry.py` | 13 | Retry policy, retry envelope |
| `test_dlq.py` | 12 | DLQ envelope, security |
| `test_validator.py` | 11 | Validation codes, size limits |
| `test_retry_crash_windows.py` | 7 | Crash windows, shutdown |
| `test_ingestion_db.py` | 6 | End-to-end with real SQLAlchemy |
| `test_ordering.py` | 6 | Partitioning, concurrency constraints |
| `test_retry_lifecycle.py` | 4 | Full main->retry->main->DB cycle |

### What the tests actually prove

**Offset safety (the core claim).** Each row of the commit matrix is tested
independently: success, duplicate, transient->retry, exhaustion->DLQ,
permanent->DLQ, validation->DLQ, and the three no-commit paths (DLQ publish
fails, retry publish fails, DB commit fails).

**The complete retry lifecycle.**
`test_transient_failure_deferred_then_forwarded_then_succeeds` drives the real
consumer, router, handlers and repository against a real SQLite session:
transient failure -> retry topic -> deferred while not due -> forwarded once
due -> reprocessed -> **row persisted**. The transient failure is injected
*below* the handler so the whole path is exercised rather than stubbed.

**Idempotency.** Redelivery of the same `event_id` yields one row and no DLQ
record - asserted against a real database, not a mock.

**Crash windows.** Retry publish confirmed then crash -> duplicate retry
records with distinct `retry_id`s. Retry forwarded then crash -> forwarded
twice, both carrying identical original bytes, so the second is a DB no-op.
DLQ confirmed then crash -> duplicate DLQ records with distinct `dlq_id`s.
DLQ publish failure -> offset not committed.

**Poison records.** Invalid UTF-8, invalid JSON, malformed envelope, schema
violations and malformed retry metadata all terminate deterministically, with
the original bytes preserved for investigation.

**Non-blocking.** `test_loop_does_not_sleep_while_deferring` asserts a record
due in an hour is handled in under a second - a regression guard against
anyone reintroducing `sleep()`.

**Ordering.** An AST-based test fails if `Thread`, `ThreadPoolExecutor`,
`asyncio` or `create_task` appears in the consumer, because concurrency would
silently break the repository's `MAX(sequence_number) + 1` allocation.

### Three real bugs my own tests caught

Worth recording, because each would have shipped from inspection alone:

1. **Duplicate events fell through to RETRY.** The classifier returns ACCEPT
   for a duplicate `event_id`; my consumer branched only on DLQ, so
   duplicates were republished to the retry topic instead of acknowledged.
2. **Busy-loop on an empty source.** `run()` spun forever when `poll()`
   returned nothing - a test hung for 300 s before I killed it. Fixed with
   `idle_poll_limit` and `stop()`.
3. **DLQ key semantics.** My first ordering test asserted the DLQ keyed off
   the transport key; the implementation keys off the domain `run_id`. The
   test was wrong, and the corrected version now proves the domain value is
   used - which is the property that actually matters on replay.
## 3. What I Fixed

### Critical

| # | Defect | Fix | Test |
|---|---|---|---|
| 1 | Bare `commit()` advanced all partitions -> **message loss** | Explicit `TopicPosition` per record | `test_commit_is_explicit_per_partition_not_bare`, `test_failed_record_partition_is_not_committed_by_another_record` |
| 2 | DLQ could not represent a poison record (no raw-bytes path) | `DlqRecord` carries `event` XOR `raw` (base64) | `test_dlq_record_for_validation_failure_keeps_raw_bytes` |
| 3 | Size inversion: validator accepted 1,048,576 B vs a 1,000,000 B broker limit, and DLQ enrichment *grows* records -> **infinite redelivery loop** | Limit derived from the DLQ budget via base64 inflation (4/3) | `test_validator_rejects_limit_that_cannot_be_dead_lettered`, `test_maximum_accepted_record_fits_in_a_dlq_envelope` |
| 4 | `produce()` treated as proof of delivery | `ConfirmedProducer` whose return means broker-acknowledged | `test_dlq_publish_failure_does_not_commit` |

### High

| # | Defect | Fix |
|---|---|---|
| 5 | No consumer orchestration (`kafka_consumer.py` was 0 bytes) | `KafkaEventConsumer` with the commit matrix |
| 6 | Retry lifecycle stopped at the publisher - **nothing consumed the retry topic** | `RetryDispatcher` + `RetryConsumerLoop` |
| 7 | Attempt counter lost on restart -> unbounded retry | Attempt carried in the retry envelope and header |
| 8 | DLQ keyed by `event_id`, scattering a run's failures | Keyed by domain `run_id` |
| 9 | `str(DBAPIError)` leaked compiled SQL and bound parameters | `str(exc.orig)` + truncation |
| 10 | Application layer imported `confluent_kafka` directly | Classification keys off the app's `PublishError` hierarchy |
| 11 | Validator raised on malformed input, losing raw bytes | Returns `ValidationSuccess`/`ValidationFailure` |
| 12 | Failure metadata built outside the DLQ, bypassing scrubbing | `DeadLetterQueue.build_failure_info()` |

### Medium / Low

| # | Defect | Fix |
|---|---|---|
| 13 | `RetryPolicy.py` filename breaks case-sensitive CI | Renamed `retry_policy.py` |
| 14 | Busy-loop on empty poll | `idle_poll_limit` + `stop()` |
| 15 | Duplicate ACCEPT fell through to RETRY | Explicit ACCEPT branch |
| 16 | `pause()` took a record, not a partition | `pause(topic, partition)` |
| 17 | Retry dispatcher could not be configured safely | `retry_group_id` derived to never equal the main group |

### Deliberately not changed

Storage layer, schemas, `event_router.py`, `handler.py`, and `retry_policy.py`
logic are untouched (original timestamps intact). The storage layer was
already correct: `session_scope()` commits, repositories flush only, and
`ON CONFLICT (event_id) DO NOTHING` provides the idempotency that makes
at-least-once safe.

No concurrency was added. `MAX(sequence_number) + 1` requires a single writer
per run, so the consumer stays single-threaded and a test enforces it.

## 4. Honest Limitations

1. **No real-Kafka tests.** Verified, not assumed: Docker not installed,
   compose file 0 bytes, `confluent_kafka` absent. Tests A-H from the brief
   could not run.
2. **No rebalance testing.** Behaviour under partition revocation is
   reasoned about, not proven.
3. **No DLQ replay service.** `DlqRecord.replay_bytes()` provides the
   material; nothing consumes it.
4. **No metrics or alerting.** Structured logs exist; no DLQ-rate alerting.
5. **Paused-partition release is jittery** - a later poll, not exactly
   `due_at`. Correctness needs only "not before" and "eventually".
6. **SQLite, not PostgreSQL**, on the consumer path.
7. **Payload stored unredacted** in the DLQ by decision, not by redaction
   (`security/pii.py` is still 0 bytes).

## 5. Next Step

**Stand up a real broker and write integration tests A-H.** This is the only
item that matters: the delivery-confirmation path is the pipeline's central
correctness claim and it has never executed. Redpanda via testcontainers is
the smallest option; alternatively install Docker and fill in the four 0-byte
compose/Dockerfile placeholders.

Then: rebalance test, DLQ replay tooling, metrics.

Do **not** raise `max.poll.interval.ms` to mask slow processing - bound the
work per poll instead.

## 6. Change Integrity

**Source modified (phase 2):** `ingestion/producer.py`,
`ingestion/kafka_consumer.py`, `infrastructure/kafka/consumer.py`,
`infrastructure/kafka/config.py`; **new:** `ingestion/retry_dispatcher.py`,
`ingestion/retry_consumer.py`.

**Tests added:** `test_retry_consumer.py`, `test_retry_lifecycle.py`,
`test_retry_crash_windows.py`, `test_ordering.py`; `conftest.py` updated for
the new commit contract. **No existing test modified or deleted.**

**Not modified:** `storage/`, `schemas/`, `event_router.py`, `handler.py`,
`retry_policy.py` logic.

| | Result |
|---|---|
| Tests before | 106 passed, 5 skipped |
| Tests after | **230 passed, 5 skipped** |
| Regressions | 0 |