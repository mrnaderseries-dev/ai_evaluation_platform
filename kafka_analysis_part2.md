### M4 — No ingestion tests at all
`tests/` contains only `contract/` (envelope locking) and `storage/` (models + repositories). `tests/unit`, `tests/integration`, `tests/e2e`, `tests/evaluation` are empty — no test ever feeds bytes through `EventValidator`, routes an event through `EventRouter`, calls a handler against a real session, or checks a `DeliveryClassifier` decision. The suite is green (106 passed, 5 skipped) but it does not cover the ingestion path.

### M5 — Duplicate detection is dialect string-matching (fragile by construction)
`DeliveryClassifier._is_duplicate_event` catches `IntegrityError` and greps the message for `"event_id"` plus one of `duplicate / unique constraint / unique violation`. That works for PostgreSQL/SQLite today but is inherently dialect- and driver-message-dependent; a new driver wording silently turns ACCEPT-duplicates into DLQ. It is the right pragmatic choice while persistence goes through `ON CONFLICT DO NOTHING` (which normally raises nothing — see section 3), but it must be called out, not assumed robust.

### M6 — Per-run sequence allocation is `MAX()+1` (single-writer safe only)
`EventRepository.next_sequence_number` allocates via `coalesce(max(sequence_number),0)+1` per `run_id`. Two concurrent ingest workers for the same run can read the same max and collide; the `uq_event_run_sequence` constraint is the backstop (second writer gets `IntegrityError` → DLQ, not silent reorder). The repository docstring already says "only safe with a single writer per run; concurrent ingest should assign the sequence upstream" — correct, but it constrains how the future `kafka_consumer` may parallelize (partition-by-run or upstream sequencing required).

## 2. How each mistake was fixed

| # | Fix | File(s) | Before → After |
|---|-----|---------|----------------|
| M1 | Rewrote every stale payload literal in both contract test files to the relaxed schema fields | `tests/contract/test_event_schema.py`, `tests/contract/test_event_type_locking.py` | `{"model": "qwen2.5:3b"}` → `{"model_id": "qwen2.5:3b"}`; model-output gains required `output_id` and `output_text` (not `output`/`success`); tool events gain `tool_id`; retrieval gains `retriever_id` + `result_count`; answer gains `answer_type`; error gains `error_id`; recovery-attempted drops `error_type`; policy allow/block gain `scope_type`/`scope_id`; review-completed gains `reason` |
| M2 | Removed the stray fragment, leaving a clean `except UnicodeDecodeError as exc:` | `src/ai_eval_platform/ingestion/event_validator.py:173` | `as exc:#law mesh utf_8` → `as exc:` |
| M3 | **Not fixed — documented as the next build step** | `deduplicator.py`, `dlq.py`, `kafka_consumer.py` (still 0 bytes) | Deliberately left empty rather than sketched half-working; section 4 lists the exact contract each must satisfy |
| M4 | **Not fixed — documented with the test plan** | (no new test files added) | Storage/contract coverage recorded as green; section 5 lists the ingestion tests to add first |
| M5 | **Kept, but fenced**: fix is documentation + ordering, not code | `delivery_classifier.py:83-99` | Noted that `insert_ignore_duplicates` / `insert_from_envelope` (`ON CONFLICT (event_id) DO NOTHING`) make redelivery return `0` with no exception, so the string-match path is a backstop for non-idempotent writers only |
| M6 | **Kept, but fenced**: fix is a consumer-design constraint | `storage/repositories.py:362-389,438-453` | `insert_from_envelope` precedence recorded as envelope's own `sequence_number` → explicit argument → `MAX()+1`; future consumer must preserve it and must not parallelize within a run without upstream sequencing |
