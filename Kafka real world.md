# Kafka Real World - Actual Implementation and Validation Status

_Date: 2026-10-04. Describes the implementation as it actually is, not as an
idealised architecture. Every claim about what was tested is backed by a
command in section 12._

---

## 1. Layering

```
ai_eval_platform/ingestion/           application
    kafka_record.py                   transport DTO (topic/partition/offset/value)
    producer.py                       ConfirmedProducer, RecordSource, TopicPosition
    event_validator.py                bytes -> AIEvent | ValidationFailure
    event_router.py                   AIEvent -> handler
    handler.py                        handler -> repository
    delivery_classifier.py            WHY (ACCEPT / RETRY / DLQ)
    retry_policy.py                   WHEN / HOW LONG
    retry_publisher.py                publish TO retry topic
    retry_dispatcher.py               HOW TO RELEASE DUE RETRIES
    retry_consumer.py                 poll loop over the retry topic
    dlq.py                            durable failure record
    kafka_consumer.py                 orchestration + offset commit

ai_eval_platform/infrastructure/kafka/ infrastructure
    config.py                         KafkaSettings (env + YAML)
    producer.py                       ConfluentProducer (delivery reports)
    consumer.py                       KafkaConsumerClient (explicit commits)
    errors.py                         client import isolation
```

Dependency direction is `application -> interfaces -> infrastructure`.
Verified by AST, not by grepping text
(`test_architecture.py::test_ingestion_never_imports_confluent_kafka`):

* `ingestion/` has **zero** real `confluent_kafka` imports.
* `ingestion/` does not import `ai_eval_platform.infrastructure`.
* `infrastructure/kafka/` imports `ai_eval_platform.ingestion.producer` and
  `.kafka_record` to implement the application's protocols.

No files were moved in this phase. The previous phase's files were reviewed
against responsibility and already sit in the correct layer: every
`ingestion/` module is transport-agnostic and speaks only to the protocols,
while all client-specific behaviour (delivery callbacks, broker errors,
lifecycle, `Message` -> `KafkaRecord` conversion) is in
`infrastructure/kafka/`.

## 2. Normal runtime flow

```
producer -> ai.events (key = run_id)
              |
              v
   KafkaConsumerClient.poll()        infrastructure/kafka/consumer.py
              |  -> KafkaRecord (raw bytes, no parsing)
              v
   KafkaEventConsumer.process()      ingestion/kafka_consumer.py
              |
              v
   EventValidator.validate()         ingestion/event_validator.py
              |
        +-----+------+
        |            |
   ValidationFailure  ValidationSuccess(AIEvent)
        |            |
        |            v
        |     EventRouter.route()     ingestion/event_router.py
        |            |
        |            v
        |     session_scope()         DB transaction opens
        |            |
        |     Handler -> EventRepository.insert_from_envelope
        |            |                ON CONFLICT (event_id) DO NOTHING
        |            v
        |     session_scope() exits   COMMIT
        |            |
        |            v
        |     DeliveryClassifier.classify(None) -> ACCEPT
        |            |
        |            v
        |     KafkaConsumerClient.commit([TopicPosition(offset + 1)])
        |
        +----> classify_validation_failure() -> DLQ
                  |
                  v
            DeadLetterQueue.publish()  broker-confirmed
                  |
                  v
            commit([TopicPosition(offset + 1)])
```

The committed value is `record.offset + 1`: Kafka's convention that the
committed offset is the *next* record to read.
## 3. Transient failure flow

```
   handler / DB raises a retryable error
              |
              v
   DeliveryClassifier.classify(exc) -> RETRY
              |
              v
   RetryPolicy.evaluate(attempt + 1)          WHEN
              |
        +-----+------+
        |            |
    RETRY       EXHAUSTED
        |            |
        v            v
   RetryPublisher.publish(attempt, due_at)   HOW TO PUBLISH
        |            |
        v            v
   ai.events.retry   DeadLetterQueue -> DLQ
        |            |
        v            v
   broker confirmed  commit offset
        |
        v
   commit source offset

   --- retry consumer ---

   RetryConsumerLoop.run()            ingestion/retry_consumer.py
              |
              v
   RetryDispatcher.dispatch()         ingestion/retry_dispatcher.py
              |
        +-----+---------+------------------+
        |             |                  |
  not due     attempt >= max      unparseable envelope
        |             |                  |
        v             v                  v
   DEFERRED     DEAD-LETTERED      DEAD-LETTERED
   pause        (DLQ confirmed)    (DLQ confirmed)
   partition
   NO commit
        |
   (re-read on a later poll)
        |
      due
        |
        v
   republish ORIGINAL bytes -> ai.events (key = original key)
        |  headers carry attempt forward
        v
   broker confirmed -> commit retry offset
        |
        v
   back to the main consumer
```

## 4. Permanent failure flow

```
   ValidationFailure / RouterError / HandlerError / IntegrityError
              |
              v
   DeliveryClassifier -> DLQ
              |
              v
   DeadLetterQueue.build_record()      provenance + failure metadata
              |
              v
   DeadLetterQueue.publish()          ConfluentProducer blocks on the
              |                        delivery report
        +-----+------+
        |            |
   confirmed    PublishError
        |            |
        v            v
   commit offset   NO COMMIT -> record redelivered
```

## 5. Ownership table

| Responsibility | Owner | File |
|---|---|---|
| Delivery classification (WHY) | application | `delivery_classifier.py` |
| Retry timing (WHEN) | application | `retry_policy.py` |
| Retry orchestration | application | `retry_dispatcher.py`, `retry_consumer.py` |
| DLQ domain contract | application | `dlq.py` |
| Event validation | application | `event_validator.py` |
| Offset commit *decision* | application | `kafka_consumer.py` |
| Offset commit *mechanics* | infrastructure | `infrastructure/kafka/consumer.py` |
| Delivery confirmation | infrastructure | `infrastructure/kafka/producer.py` |
| Broker errors | infrastructure | `infrastructure/kafka/producer.py` |
| Client lifecycle | infrastructure | `infrastructure/kafka/consumer.py` |
| Message -> KafkaRecord | infrastructure | `infrastructure/kafka/consumer.py` |

## 6. Producer delivery confirmation

`infrastructure/kafka/producer.py` blocks until the broker's delivery report
for the specific record arrives:

1. `produce()` with an `on_delivery` callback.
2. `poll()` in a loop until the callback fires or the timeout elapses.
3. `flush()` as a final attempt.
4. A delivery error is mapped: size -> `MessageTooLargeError`, transient ->
   `RetryablePublishError`, unknown topic/auth -> `PermanentPublishError`.

`publish()` returning normally therefore means *broker acknowledged*. Local
enqueue is never treated as confirmation.

**This path has never executed.** `confluent_kafka` is not installed here.

## 7. Consumer offset semantics

`KafkaConsumerClient.commit(positions)` requires explicit
`TopicPosition(topic, partition, next_offset)` values and calls
`self._consumer.commit(offsets=offsets, asynchronous=False)`.

Why explicit positions matter: `confluent-kafka`'s `commit()` with **no**
offsets argument commits the current position of **every assigned
partition**. If partition 0's record failed to reach the DLQ but partition
1's succeeded, a bare commit would advance partition 0 and silently skip the
undurable record. Committing per-partition positions preserves the guarantee.

Commits are synchronous so a failure raises to the caller. Committing an
empty list is a no-op; committing on a closed consumer raises.

A guard test walks the infrastructure AST and fails on any `.commit()` call
lacking the `offsets` keyword.
## 8. Retry attempt propagation

`attempt` lives in the message, never only in process memory:

* `RetryRecord.attempt` in the retry envelope.
* `RetryPublisher` copies it into the `attempt` Kafka header.
* `RetryDispatcher` sets the `attempt` header when forwarding to the main
  topic.
* `KafkaEventConsumer._extract_attempt()` reads that header.

So a consumer restart resumes counting where the message left off. A
missing, malformed or negative header degrades to `0`. A malformed envelope
(invalid `attempt` value, missing `due_at`) is dead-lettered, never retried.

## 9. DLQ metadata

`DlqRecord` is a versioned Pydantic envelope (`dlq_schema_version = "1.0"`):

| Field | Purpose |
|---|---|
| `dlq_id` | identity of this DLQ **occurrence** (not the event) |
| `created_at` | DLQ arrival time, distinct from `event.timestamp` |
| `source.topic/partition/offset/key` | Kafka provenance |
| `event` | `event_id`, `run_id`, `event_type`, `schema_version`, JSON event (when parseable) |
| `raw` | base64 payload + length (when not parseable) |
| `failure.stage/code/error_type/message/attempt` | machine-readable classification |

`event` and `raw` are mutually exclusive. Messages are truncated to 2,000
chars; SQLAlchemy `DBAPIError` uses `str(exc.orig)` so compiled SQL and bound
parameters never appear. No stack traces are stored.

## 10. Partitioning and ordering

* Main topic key: `run_id`. Same run -> same partition -> Kafka ordering.
* Retry topic key: the original key, so retries stay co-located per run and
  return to their original main-topic partition.
* DLQ key: the domain `run_id`, not `event_id` and not the transport key.

**Partition numbers are never copied between topics.** Partition N of the
retry topic is unrelated to partition N of the main topic; the key is what
determines the destination partition.

`EventRepository.next_sequence_number` uses `MAX(sequence_number) + 1` per
run, which requires a single writer per run. The consumer is therefore
single-threaded by design: no thread pool, no worker pool, no asyncio
fan-out. An AST test fails if any appears, because concurrency would break
that assumption silently.

## 11. Crash windows, duplicates and idempotency

| Window | Result | Protection |
|---|---|---|
| DB commit succeeds, crash before offset commit | event redelivered | `ON CONFLICT (event_id) DO NOTHING` |
| Retry publish confirmed, crash before source commit | duplicate retry record | distinct `retry_id`s |
| Retry forwarded, crash before retry commit | forwarded twice | identical original bytes -> same `event_id` -> DB no-op |
| DLQ publish confirmed, crash before source commit | duplicate DLQ record | distinct `dlq_id` per occurrence |
| DLQ publish fails | no commit | redelivered |
| Retry publish fails | no commit | redelivered |

No exactly-once is claimed. Duplicates are expected and absorbed by
`event_id` idempotency.

**Rebalance.** Not validated. Reasoning: a partition revoked mid-processing
causes the synchronous commit to raise; the record is redelivered to the new
owner and `event_id` idempotency absorbs it. This is inference, not a test.

**Shutdown.** `close()` deliberately leaves uncommitted offsets
uncommitted, so an in-flight record is redelivered rather than acknowledged.
## 12. What was actually tested

Commands run on this machine, Windows / Python 3.12.0:

| Command | Result |
|---|---|
| `python -m pytest -q` (no `DATABASE_URL`) | **237 passed, 15 skipped** |
| `python -m pytest -q` (with `DATABASE_URL`) | **251 passed, 1 skipped** |
| `python -m pytest -q` x3 (with `DATABASE_URL`) | 251 passed each run (stable) |
| `python -m pytest tests/integration -q` (with DB) | 9 passed |
| `python -m pytest tests/integration -q -rs` (no DB) | 10 skipped, reasons shown |
| `python -m compileall -q src tests` | exit 0 |

### Real PostgreSQL - VALIDATED

`tests/integration/test_postgres_pipeline.py` (9 tests) runs against a real
PostgreSQL server at `localhost:5432` and validates:

* event persistence through the real handler + router + repository,
* `ON CONFLICT (event_id) DO NOTHING` on the real dialect,
* ten redeliveries producing exactly one row,
* the committed position being `offset + 1`,
* per-run sequence assignment order,
* a validation failure reaching the DLQ without touching the database,
* a DLQ publish failure leaving the offset uncommitted,
* a failed partition never being advanced by another record's commit.

The real dialect matters: SQLite accepts constructs PostgreSQL rejects, so
these assertions carry more weight here than in the unit suite.

### Architecture boundary - VALIDATED

`tests/ingestion/test_architecture.py` (7 tests) parses the AST to prove
`ingestion/` never imports `confluent_kafka`, never imports
`infrastructure`, that the client implements the application protocols, that
no bare `commit()` exists, and that `enable_auto_commit=True` is rejected.

### Real Kafka broker - NOT VALIDATED

**Blocked.** Verified, not assumed:

| Check | Command | Result |
|---|---|---|
| Docker CLI | `docker --version` | `docker is not recognized` |
| Docker daemon | `docker info` | `docker is not recognized` |
| testcontainers | `import testcontainers` | `ModuleNotFoundError` |
| client library | `import confluent_kafka` | `ModuleNotFoundError` |
| package index | `pip download confluent-kafka` | timed out after 60 s |

`tests/integration/test_real_kafka_integration.py` exists and contains real
scenarios (A normal path, C DLQ, E producer failure, H rebalance), written
against the genuine `confluent_kafka` API. It skips at collection time with
the reason `KAFKA_BOOTSTRAP_SERVERS unset. Docker, testcontainers and
confluent-kafka are all unavailable`. It contains **no** doubles and no
simulated broker, so a skip means "unverified", never "passed".

### Not verified with any real broker

* producer delivery reports and timeouts,
* committed offsets as stored by the broker,
* partition assignment and rebalances,
* retry topic behaviour end to end,
* DLQ records as real broker records,
* consumer shutdown against a live group.

Scenarios B, D, F and G (transient retry end-to-end, injected DLQ/retry
publish failure, restart, crash windows) additionally need fault injection
that cannot be produced without a controllable broker. They remain
unverified even once a broker is available.

## 13. Environment blockers and exact remediation

To unblock, in order:

1. `pip install "confluent-kafka>=2.3"` (needs package index access).
2. Start a broker, e.g. `docker run ... apache/kafka:3.7.0` (needs Docker),
   or Redpanda via testcontainers.
3. `$env:KAFKA_BOOTSTRAP_SERVERS = "localhost:9092"`.
4. `python -m pytest tests/integration/test_real_kafka_integration.py -q`.

Topic expectations: `ai.events` (>= 1 partition, keyed by `run_id`),
`ai.events.retry` (>= 1 partition), `ai.events.dlq` (>= 1 partition).
`auto.offset.reset=earliest`, `enable.auto.commit=false`, distinct consumer
group per loop - the retry dispatcher's group defaults to
`<group_id>-retry` via `KafkaSettings.resolved_retry_group_id()`, which can
never equal the main group.

## 14. Findings in this phase

1. **[HIGH, fixed] Test-schema race.** My new integration fixture called
   `Base.metadata.drop_all()` per test against the shared database. The
   pre-existing `tests/storage/test_postgres_integration.py` uses a
   **module-scoped** engine, so my teardown destroyed its tables mid-run,
   producing intermittent `ERROR`s (reproduced: 13 errors on one run, 0 on
   the next). Fixed by giving the new suite a dedicated PostgreSQL schema
   (`it_ingestion_<pid>`) created and dropped around the run. Now stable
   across repeated runs and leaves no schema behind.

2. **[MEDIUM, fixed] `main_record(**overrides)` test bug.** The `value=`
   override was being passed to `valid_event_bytes()` instead of the record,
   so a `bytes` payload reached the DLQ JSON serialiser and raised
   `TypeError: Object of type bytes is not JSON serializable`. Test defect,
   fixed by giving `value` its own parameter.

No production source file was changed in this phase. The defects found were
in the new tests, not in the ingestion implementation.

## 15. Scope

No architecture redesign. No changes to storage, schemas, event routing,
handlers, retry policy, or the DLQ contract. No concurrency introduced. No
metrics, alerting, replay service, or PII redaction - all explicitly out of
scope for real-broker validation.