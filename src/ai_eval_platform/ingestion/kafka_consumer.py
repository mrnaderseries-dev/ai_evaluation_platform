from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol

from schemas.events.common import AIEvent

from .delivery_classifier import DeliveryAction, DeliveryClassifier
from .dlq import DeadLetterQueue, DlqPublishError, FailureInfo, FailureStage
from .event_router import EventRouter
from .event_validator import (
    EventValidator,
    ValidationFailure,
    ValidationSuccess,
)
from .kafka_record import KafkaRecord
from .producer import PublishError, RecordSource, TopicPosition
from .retry_policy import RetryPolicy
from .retry_publisher import RetryPublisher, RetryPublishError

logger = logging.getLogger(__name__)
INITIAL_ATTEMPT = 0


class KafkaConsumerPort(RecordSource, Protocol):
    """Consumer transport contract used by the orchestrator."""

    def pause(self, record: KafkaRecord) -> None: ...

    def resume(self, record: KafkaRecord) -> None: ...


class SessionFactory(Protocol):
    def __call__(self): ...


@dataclass(frozen=True, slots=True)
class ConsumerConfig:
    poll_timeout_seconds: float = 1.0
    publish_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.poll_timeout_seconds <= 0:
            raise ValueError("poll_timeout_seconds must be positive")

        if self.publish_timeout_seconds <= 0:
            raise ValueError("publish_timeout_seconds must be positive")


@dataclass(frozen=True, slots=True)
class ProcessingOutcome:
    """What happened to one record - assertions are written against this."""

    action: DeliveryAction
    committed: bool
    attempt: int
    reason: str
    dlq_record_id: str | None = None
class KafkaEventConsumer:
    """Drives validate -> route -> handler -> classify -> commit."""

    def __init__(
        self,
        *,
        consumer: KafkaConsumerPort,
        validator: EventValidator,
        router: EventRouter,
        session_factory: Callable[[], object],
        handler_factory: Callable[[object], EventRouter],
        classifier: DeliveryClassifier,
        retry_policy: RetryPolicy,
        retry_publisher: RetryPublisher,
        dlq: DeadLetterQueue,
        config: ConsumerConfig | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._consumer = consumer
        self._validator = validator
        self._router = router
        self._session_factory = session_factory
        self._handler_factory = handler_factory
        self._classifier = classifier
        self._retry_policy = retry_policy
        self._retry_publisher = retry_publisher
        self._dlq = dlq
        self._config = config or ConsumerConfig()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._stopped = False

    def run(
        self,
        *,
        max_records: int | None = None,
        idle_poll_limit: int | None = None,
    ) -> list[ProcessingOutcome]:
     
        outcomes: list[ProcessingOutcome] = []
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

                records = self._consumer.poll(
                    self._config.poll_timeout_seconds
                )

                if not records:
                    idle_polls += 1
                    continue

                idle_polls = 0

                for record in records:
                    outcomes.append(self.process(record))

                    if (
                        max_records is not None
                        and len(outcomes) >= max_records
                    ):
                        break
        finally:
            self._consumer.close()

        return outcomes

    def stop(self) -> None:
      
        self._stopped = True

    def process(self, record: KafkaRecord) -> ProcessingOutcome:
        """Process one record end to end and return its outcome."""
        attempt = self._extract_attempt(record)

        validation = self._validator.validate(record.value)

        if isinstance(validation, ValidationFailure):
            return self._handle_validation_failure(
                record=record,
                failure=validation,
                attempt=attempt,
            )

        assert isinstance(validation, ValidationSuccess)
        event = validation.event

        try:
            self._process_event(event)
        except BaseException as exc: 
            return self._handle_processing_failure(
                record=record,
                event=event,
                error=exc,
                attempt=attempt,
            )

       
        self._commit(record)
        return ProcessingOutcome(
            action=DeliveryAction.ACCEPT,
            committed=True,
            attempt=attempt,
            reason="Event persisted and offset committed.",
        )

    def _commit(self, record: KafkaRecord) -> None:
        self._consumer.commit(
            [
                TopicPosition(
                    topic=record.topic,
                    partition=record.partition,
                    next_offset=record.offset + 1,
                )
            ]
        )

    def _process_event(self, event: AIEvent) -> None:
        
        with self._session_factory() as session:
            self._handler_factory(session).route(event)

    def _handle_validation_failure(
        self,
        *,
        record: KafkaRecord,
        failure: ValidationFailure,
        attempt: int,
    ) -> ProcessingOutcome:
        decision = self._classifier.classify_validation_failure(failure)

        if decision.action is not DeliveryAction.DLQ:
          
            raise RuntimeError(
                "Validation failures must be classified as DLQ, got "
                f"{decision.action}"
            )

        return self._publish_to_dlq(
            record=record,
            attempt=attempt,
            stage=FailureStage.VALIDATION,
            code=failure.code.value,
            error=failure,
            validation_failure=failure,
        )
    def _handle_processing_failure(
        self,
        *,
        record: KafkaRecord,
        event: AIEvent,
        error: BaseException,
        attempt: int,
    ) -> ProcessingOutcome:
        decision = self._classifier.classify(error)

        if decision.action is DeliveryAction.ACCEPT:
            # The classifier ACCEPTs a duplicate event_id: the row already
            # exists and the write was a safe no-op, so the record is done.
            # It must be committed, NOT republished to the retry topic.
            self._commit(record)
            return ProcessingOutcome(
                action=DeliveryAction.ACCEPT,
                committed=True,
                attempt=attempt,
                reason=decision.reason,
            )

        if decision.action is DeliveryAction.DLQ:
            return self._publish_to_dlq(
                record=record,
                attempt=attempt,
                stage=self._stage_for(error),
                code=self._code_for(error),
                error=error,
                event=event,
            )

        retry_decision = self._retry_policy.evaluate(attempt + 1)

        if retry_decision.should_retry:
            due_at = self._now() + timedelta(
                seconds=retry_decision.delay_seconds
            )

            try:
                self._retry_publisher.publish(
                    source=record,
                    attempt=attempt + 1,
                    due_at=due_at,
                )
            except RetryPublishError as exc:
                logger.error(
                    "retry_publish_failed record=%s attempt=%s: %s",
                    record.position,
                    attempt + 1,
                    exc,
                )
                return ProcessingOutcome(
                    action=DeliveryAction.RETRY,
                    committed=False,
                    attempt=attempt,
                    reason=f"Retry publish failed: {exc}",
                )

            self._commit(record)
            return ProcessingOutcome(
                action=DeliveryAction.RETRY,
                committed=True,
                attempt=attempt + 1,
                reason=(
                    "Deferred to retry topic; offset committed after broker "
                    "confirmation."
                ),
            )

        # Exhausted: dead-lettered, never dropped.
        return self._publish_to_dlq(
            record=record,
            attempt=attempt,
            stage=FailureStage.RETRY_EXHAUSTED,
            code="RETRY_EXHAUSTED",
            error=error,
            event=event,
        )

    def _publish_to_dlq(
        self,
        *,
        record: KafkaRecord,
        attempt: int,
        stage: FailureStage,
        code: str,
        error: BaseException,
        event: AIEvent | None = None,
        validation_failure: ValidationFailure | None = None,
    ) -> ProcessingOutcome:
        failure = self._dlq.build_failure_info(
            stage=stage,
            code=code,
            error=error,
            attempt=attempt,
        )

        try:
            envelope = self._dlq.build_record(
                record=record,
                failure=failure,
                event=event,
                validation_failure=validation_failure,
            )
            self._dlq.publish(envelope)
        except DlqPublishError as exc:
            
            logger.error(
                "dlq_publish_failed record=%s stage=%s: %s",
                record.position,
                stage.value,
                exc,
            )
            return ProcessingOutcome(
                action=DeliveryAction.DLQ,
                committed=False,
                attempt=attempt,
                reason=f"DLQ publish failed: {exc}",
            )

        self._commit(record)
        logger.warning(
            "dead_lettered record=%s dlq_id=%s stage=%s code=%s attempt=%s",
            record.position,
            envelope.dlq_id,
            stage.value,
            envelope.failure.code,
            attempt,
        )

        return ProcessingOutcome(
            action=DeliveryAction.DLQ,
            committed=True,
            attempt=attempt,
            reason="DLQ publish confirmed; offset committed.",
            dlq_record_id=envelope.dlq_id,
        )

    def _extract_attempt(self, record: KafkaRecord) -> int:
        """Read the retry attempt carried by the record header.

        Attempt lives in the Kafka header rather than process memory so a
        restarted consumer does not reset the counter and retry a poison
        record forever. A malformed header degrades to 0 (one more retry)
        rather than crashing the loop.
        """
        raw = record.headers.get("attempt")

        if raw is None:
            return INITIAL_ATTEMPT

        try:
            value = int(raw)
        except (TypeError, ValueError):
            logger.warning(
                "malformed_attempt_header record=%s value=%r; treating as 0",
                record.position,
                raw,
            )
            return INITIAL_ATTEMPT

        return max(value, 0)

    @staticmethod
    def _stage_for(error: BaseException) -> FailureStage:
        from .event_router import RouterError
        from .handler import HandlerError

        if isinstance(error, RouterError):
            return FailureStage.ROUTING

        if isinstance(error, HandlerError):
            return FailureStage.HANDLER

        if isinstance(error, PublishError):
            return FailureStage.UNKNOWN

        return FailureStage.PERSISTENCE

    @staticmethod
    def _code_for(error: BaseException) -> str:
        return type(error).__name__.upper()

    def _now(self) -> datetime:
        return self._clock()