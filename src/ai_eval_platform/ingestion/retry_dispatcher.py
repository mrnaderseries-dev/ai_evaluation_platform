"""Retry dispatcher: releases due retry records back to the main topic.

Responsibility split (kept deliberately strict)
------------------------------------------------
``RetryPolicy``     decides WHEN / HOW LONG (delay seconds).
``RetryPublisher``  publishes TO the retry topic (main consumer side).
``RetryDispatcher`` releases DUE retry records (this module).
``KafkaEventConsumer`` processes main-topic events.

Nothing here sleeps. A retry record whose ``due_at`` is in the future is
deferred by *pausing its partition*, so the consumer keeps polling other
partitions and stays inside ``max.poll.interval.ms``.

Head-of-line blocking, and why one retry topic is still correct
----------------------------------------------------------------
Kafka guarantees order *within a partition*. If one retry topic holds

    A (due 15:30), B (due 15:31), C (due 15:05)

then at 15:10 C is due but sits behind A and B. Strict in-order release would
block C for 20 minutes.

The chosen design resolves this **without multiple delay topics**, and the
reason is a property of the key. The retry topic is keyed by the original
record key, which is ``run_id``. So A, B and C share a partition *only if
they belong to the same run*.

That makes the blocking correct rather than merely tolerable: within one run
the events are causally ordered, so C must NOT overtake A - releasing C
first would break the per-run ordering the main topic depends on. C is not
independent of A; it is simply a later event of the same run that is not yet
due.

The cost is therefore bounded head-of-line blocking *within a single run*, not
across the topic: a partition carries many runs, and the dispatcher keeps
polling and releasing whatever is due on other partitions.

Rejected alternatives
---------------------
* **Multiple delay topics** (retry.5s / retry.30s / retry.5m): removes the
  blocking but multiplies topics, partition counts and consumer
  configuration, and splits one run's events across buckets, destroying
  per-run ordering. Not justified for a platform whose retry volume is
  bounded by ``max_attempts``.
* **Sleeping until due**: directly violates ``max.poll.interval.ms`` and
  stalls every partition.
* **External scheduler**: correct but far more machinery than this project
  needs.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable

from pydantic import ValidationError

from .kafka_record import KafkaRecord
from .producer import ConfirmedProducer, PublishError, TopicPosition
from .retry_publisher import RETRY_SCHEMA_VERSION, RetryRecord

logger = logging.getLogger(__name__)

MAX_DLQ_MESSAGE_CHARS = 2_000


class DispatchAction(str, Enum):
    """What the dispatcher did with one retry record."""

    FORWARDED = "forwarded"
    DEFERRED = "deferred"
    DEAD_LETTERED = "dead_lettered"


@dataclass(frozen=True, slots=True)
class DispatchOutcome:
    action: DispatchAction
    record_position: str
    attempt: int | None = None
    reason: str = ""


class MalformedRetryError(Exception):
    """The retry envelope could not be parsed.

    Carries the raw record so the dispatcher can dead-letter it. Malformed
    retries go straight to the DLQ: retrying them would loop forever, because
    the broken thing is precisely the metadata needed to schedule them.
    """
class RetryDispatcher:
    """Releases due retry records to the main topic.

    Forwards a record only when its ``due_at`` has passed. Future-dated
    records are deferred (the caller pauses the partition and continues
    polling); malformed envelopes and exhausted attempts are dead-lettered.
    """

    def __init__(
        self,
        *,
        producer: ConfirmedProducer,
        main_topic: str,
        dlq_topic: str,
        max_attempts: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")

        if not main_topic:
            raise ValueError("main_topic must not be empty")

        if not dlq_topic:
            raise ValueError("dlq_topic must not be empty")

        self._producer = producer
        self._main_topic = main_topic
        self._dlq_topic = dlq_topic
        self._max_attempts = max_attempts
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    # ------------------------------------------------------------------
    # parsing and pure decisions
    # ------------------------------------------------------------------

    def parse(self, record: KafkaRecord) -> RetryRecord:
        """Parse a retry envelope, raising :class:`MalformedRetryError`."""
        try:
            payload = json.loads(record.value.decode("utf-8"))
            return RetryRecord.model_validate(payload)
        except (ValidationError, ValueError, UnicodeDecodeError) as exc:
            raise MalformedRetryError(
                f"Unparseable retry envelope: {exc}"
            ) from exc

    def is_due(self, retry_record: RetryRecord, now: datetime) -> bool:
        return retry_record.due_at <= now

    def is_exhausted(self, retry_record: RetryRecord) -> bool:
        """True once this record has consumed its retry budget.

        ``attempt`` counts retries already performed, so a record at
        ``attempt >= max_attempts`` must be dead-lettered rather than
        forwarded again. This is what stops an inflated or looping attempt
        value from creating an unbounded retry loop.
        """
        return retry_record.attempt >= self._max_attempts

    # ------------------------------------------------------------------
    # dispatch
    # ------------------------------------------------------------------

    def dispatch(self, record: KafkaRecord) -> DispatchOutcome:
        """Decide and act on one retry record.

        Never raises for record-level problems, so the caller's poll loop
        keeps running.
        """
        try:
            retry_record = self.parse(record)
        except MalformedRetryError as exc:
            # Deterministic policy: a retry record we cannot parse goes to
            # the DLQ. It cannot be scheduled (due_at is unknown) and
            # retrying it would loop forever.
            return self._dead_letter_malformed(record, exc)

        if self.is_exhausted(retry_record):
            return self._dead_letter_exhausted(record, retry_record)

        if not self.is_due(retry_record, self._now()):
            # Not due yet: defer. The caller pauses the partition so this
            # record is re-examined later without blocking the loop.
            return DispatchOutcome(
                action=DispatchAction.DEFERRED,
                record_position=record.position,
                attempt=retry_record.attempt,
                reason=(
                    f"Not due until {retry_record.due_at.isoformat()}; "
                    "partition paused."
                ),
            )

        return self._forward(record, retry_record)
    def _forward(
        self,
        record: KafkaRecord,
        retry_record: RetryRecord,
    ) -> DispatchOutcome:
        """Republish the ORIGINAL payload to the main topic.

        Keyed by the original key (``run_id``), so the record lands on the
        same main-topic partition it originally came from, preserving
        per-run ordering.

        The original *partition number* is deliberately NOT reused: partition
        numbers are per-topic, so retry-topic partition N bears no relation to
        main-topic partition N. Reusing it would be meaningless. The key is
        the correct mechanism for landing on the right partition.
        """
        try:
            self._producer.publish(
                topic=self._main_topic,
                value=retry_record.original_bytes(),
                key=(
                    retry_record.original_key.encode("utf-8")
                    if retry_record.original_key
                    else None
                ),
                headers={
                    # Carries the attempt forward so the main consumer's
                    # retry bound survives the retry round-trip.
                    "attempt": str(retry_record.attempt),
                    "retry_id": retry_record.retry_id,
                    "retry_source_topic": retry_record.source_topic,
                    "retry_source_partition": str(
                        retry_record.source_partition
                    ),
                    "retry_source_offset": str(retry_record.source_offset),
                },
            )
        except PublishError as exc:
            # The retry must NOT vanish. The caller leaves the retry offset
            # uncommitted, so this record is redelivered and retried.
            logger.error(
                "retry_forward_failed position=%s retry_id=%s: %s",
                record.position,
                retry_record.retry_id,
                exc,
            )
            return DispatchOutcome(
                action=DispatchAction.DEFERRED,
                record_position=record.position,
                attempt=retry_record.attempt,
                reason=f"Forward to main topic failed: {exc}",
            )

        logger.info(
            "retry_forwarded position=%s retry_id=%s attempt=%s",
            record.position,
            retry_record.retry_id,
            retry_record.attempt,
        )

        return DispatchOutcome(
            action=DispatchAction.FORWARDED,
            record_position=record.position,
            attempt=retry_record.attempt,
            reason="Forwarded to the main topic after broker confirmation.",
        )

    def _dead_letter_malformed(
        self,
        record: KafkaRecord,
        error: MalformedRetryError,
    ) -> DispatchOutcome:
        return self._dead_letter(
            record=record,
            stage="retry_dispatch",
            code="MALFORMED_RETRY_RECORD",
            error=error,
            attempt=None,
        )

    def _dead_letter_exhausted(
        self,
        record: KafkaRecord,
        retry_record: RetryRecord,
    ) -> DispatchOutcome:
        return self._dead_letter(
            record=record,
            stage="retry_exhausted",
            code="RETRY_EXHAUSTED",
            error=RuntimeError(
                f"Retry budget exhausted at attempt {retry_record.attempt}."
            ),
            attempt=retry_record.attempt,
        )
    def _dead_letter(
        self,
        *,
        record: KafkaRecord,
        stage: str,
        code: str,
        error: BaseException,
        attempt: int | None,
    ) -> DispatchOutcome:
        message = (str(error) or type(error).__name__)[:MAX_DLQ_MESSAGE_CHARS]

        payload = {
            "dlq_schema_version": RETRY_SCHEMA_VERSION,
            "source": {
                "topic": record.topic,
                "partition": record.partition,
                "offset": record.offset,
                "key": record.key,
            },
            "failure": {
                "stage": stage,
                "code": code,
                "error_type": type(error).__name__,
                "message": message,
                "attempt": attempt or 0,
            },
            # The unparseable envelope is preserved so the failure can be
            # investigated. No stack trace is stored.
            "raw": {
                "raw_payload_base64": base64.b64encode(record.value).decode(
                    "ascii"
                ),
                "byte_length": len(record.value),
            },
        }

        try:
            self._producer.publish(
                topic=self._dlq_topic,
                value=json.dumps(payload, separators=(",", ":")).encode(
                    "utf-8"
                ),
                key=record.key.encode("utf-8") if record.key else None,
                headers={
                    "failure_code": code,
                    "failure_stage": stage,
                    "source_topic": record.topic,
                },
            )
        except PublishError as exc:
            # DLQ publish failed: do not commit, so this retry record is
            # redelivered rather than silently dropped.
            logger.error(
                "retry_dlq_publish_failed position=%s: %s",
                record.position,
                exc,
            )
            return DispatchOutcome(
                action=DispatchAction.DEFERRED,
                record_position=record.position,
                attempt=attempt,
                reason=f"DLQ publish failed: {exc}",
            )

        logger.warning(
            "retry_dead_lettered position=%s code=%s", record.position, code
        )

        return DispatchOutcome(
            action=DispatchAction.DEAD_LETTERED,
            record_position=record.position,
            attempt=attempt,
            reason=f"Dead-lettered: {code}",
        )

    def commit_position(self, record: KafkaRecord) -> TopicPosition:
        """The retry-topic position to commit once a record is resolved.

        Only call this for FORWARDED or DEAD_LETTERED outcomes - both mean the
        record reached a durable destination. Never for DEFERRED, which means
        "not handled yet".
        """
        return TopicPosition(
            topic=record.topic,
            partition=record.partition,
            next_offset=record.offset + 1,
        )

    def _now(self) -> datetime:
        return self._clock()