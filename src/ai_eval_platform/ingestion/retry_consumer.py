"""Retry consumer loop: polls the retry topic and releases due records.

This is the piece that was missing. It wires :class:`RetryDispatcher` to a
real poll loop.

Deferral without sleeping
--------------------------
A record whose ``due_at`` is in the future must not be processed *and* must
not be committed. The loop:

1. polls a record,
2. dispatches it,
3. on ``DEFERRED``: **pauses the record's partition** so the record is
   re-read once it becomes due,
4. commits only on ``FORWARDED`` / ``DEAD_LETTERED``.

Because only that one partition is paused, the consumer keeps polling the
others and stays inside ``max.poll.interval.ms``. No ``sleep()`` is used for
scheduling.

One caveat, stated plainly: a paused partition is only re-examined when the
consumer polls again. A record deferred for 30 s is revisited on a later poll
cycle, not at exactly 15:30. That is *delay jitter*, not delay loss -
correctness depends only on "not before ``due_at``" and "eventually".

A deferred partition's offset is deliberately NOT committed, so a restart
redelivers the deferred record rather than skipping it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Protocol

from .kafka_record import KafkaRecord
from .producer import TopicPosition
from .retry_dispatcher import DispatchAction, DispatchOutcome, RetryDispatcher

logger = logging.getLogger(__name__)


class RetryRecordSource(Protocol):
    """The consumer transport the retry loop needs."""

    def poll(self, timeout_s: float) -> list[KafkaRecord]: ...

    def commit(self, positions: list[TopicPosition]) -> None: ...

    def pause(self, topic: str, partition: int) -> None: ...

    def resume(self, topic: str, partition: int) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class RetryConsumerConfig:
    poll_timeout_seconds: float = 1.0
    max_defer_polls: int = 1

    def __post_init__(self) -> None:
        if self.poll_timeout_seconds <= 0:
            raise ValueError("poll_timeout_seconds must be positive")

        if self.max_defer_polls < 1:
            raise ValueError("max_defer_polls must be at least 1")


@dataclass(frozen=True, slots=True)
class RetryDispatchSummary:
    forwarded: int = 0
    deferred: int = 0
    dead_lettered: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "forwarded": self.forwarded,
            "deferred": self.deferred,
            "dead_lettered": self.dead_lettered,
        }
class RetryConsumerLoop:
    """Polls the retry topic and releases due records to the main topic."""

    def __init__(
        self,
        *,
        source: RetryRecordSource,
        dispatcher: RetryDispatcher,
        config: RetryConsumerConfig | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._source = source
        self._dispatcher = dispatcher
        self._config = config or RetryConsumerConfig()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._stopped = False
        # Partitions currently paused because their head record is not due.
        self._paused: set[tuple[str, int]] = set()

    def stop(self) -> None:
        self._stopped = True

    @property
    def paused_partitions(self) -> set[tuple[str, int]]:
        """Currently paused (topic, partition) pairs."""
        return set(self._paused)

    def run(
        self,
        *,
        max_records: int | None = None,
        idle_poll_limit: int | None = None,
    ) -> list[DispatchOutcome]:
        """Drain the retry topic until stopped or bounded."""
        outcomes: list[DispatchOutcome] = []
        idle_polls = 0

        try:
            while max_records is None or len(outcomes) < max_records:
                if self._stopped:
                    break

                if (
                    idle_poll_limit is not None
                    and idle_polls >= idle_poll_limit
                ):
                    break

                records = self._source.poll(
                    self._config.poll_timeout_seconds
                )

                if not records:
                    idle_polls += 1
                    continue

                idle_polls = 0

                for record in records:
                    outcomes.append(self.handle(record))

                    if (
                        max_records is not None
                        and len(outcomes) >= max_records
                    ):
                        break
        finally:
            self._source.close()

        return outcomes

    def handle(self, record: KafkaRecord) -> DispatchOutcome:
        """Dispatch one retry record and act on the outcome."""
        outcome = self._dispatcher.dispatch(record)

        if outcome.action is DispatchAction.DEFERRED:
            # Not durable yet: pause the partition and DO NOT commit, so the
            # record is re-read (and redelivered after a restart) rather than
            # skipped.
            self._defer(record)
            return outcome

        # FORWARDED or DEAD_LETTERED: the record reached a durable
        # destination, so the retry-topic offset may be committed.
        self._source.commit([self._dispatcher.commit_position(record)])
        self._paused.discard((record.topic, record.partition))

        return outcome

    def _defer(self, record: KafkaRecord) -> None:
        key = (record.topic, record.partition)

        if key not in self._paused:
            self._source.pause(record.topic, record.partition)
            self._paused.add(key)

            logger.debug(
                "retry_partition_paused topic=%s partition=%s offset=%s",
                record.topic,
                record.partition,
                record.offset,
            )

    def summary(
        self,
        outcomes: list[DispatchOutcome],
    ) -> RetryDispatchSummary:
        return RetryDispatchSummary(
            forwarded=sum(
                1 for o in outcomes if o.action is DispatchAction.FORWARDED
            ),
            deferred=sum(
                1 for o in outcomes if o.action is DispatchAction.DEFERRED
            ),
            dead_lettered=sum(
                1
                for o in outcomes
                if o.action is DispatchAction.DEAD_LETTERED
            ),
        )