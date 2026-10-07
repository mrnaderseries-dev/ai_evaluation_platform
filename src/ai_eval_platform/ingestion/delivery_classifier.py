from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from sqlalchemy.exc import IntegrityError


class DeliveryAction(str, Enum):
    ACCEPT = "accept"
    RETRY = "retry"
    DLQ = "dlq"


@dataclass(frozen=True, slots=True)
class DeliveryDecision:
    action: DeliveryAction
    reason: str


class DeliveryClassifier:
    def classify(
        self,
        error: BaseException | None,
    ) -> DeliveryDecision:
        if error is None:
            return DeliveryDecision(
                DeliveryAction.ACCEPT,
                "Event processed successfully.",
            )

        if self._is_duplicate_event(error):
            return DeliveryDecision(
                DeliveryAction.ACCEPT,
                "Duplicate event ignored safely.",
            )

        if isinstance(error, IntegrityError):
            return DeliveryDecision(
                DeliveryAction.DLQ,
                "Permanent constraint violation.",
            )


        if self._is_routing_error(error):
            return DeliveryDecision(
                DeliveryAction.DLQ,
                "Event has no valid route.",
            )

        if self._is_permanent_domain_error(error):
            return DeliveryDecision(
                DeliveryAction.DLQ,
                "Permanent domain processing error.",
            )

        if self._is_transient_database_error(error):
            return DeliveryDecision(
                DeliveryAction.RETRY,
                "Transient database failure.",
            )

        if self._is_transient_kafka_error(error):
            return DeliveryDecision(
                DeliveryAction.RETRY,
                "Transient Kafka failure.",
            )

        return DeliveryDecision(
            DeliveryAction.RETRY,
            "Unknown error; retry for safety.",
        )

    @staticmethod
    def _is_duplicate_event(
        error: BaseException,
    ) -> bool:
        if not isinstance(error, IntegrityError):
            return False

        message = str(error).lower()

        return (
            "event_id" in message
            and (
                "duplicate" in message
                or "unique constraint" in message
                or "unique violation" in message
            )
        )

    def classify_validation_failure(
        self,
        failure: "ValidationFailure",
    ) -> DeliveryDecision:
        """Every validation failure is permanent and goes straight to the DLQ.

        Validation failures are not retryable: the bytes will fail identically
        on every subsequent attempt, so retrying them would spin forever. They
        are surfaced through the classifier (rather than special-cased in the
        consumer) so that "why" stays in one place.
        """
        return DeliveryDecision(
            DeliveryAction.DLQ,
            (
                f"Event failed validation at stage "
                f"'{failure.stage.value}' with code "
                f"'{failure.code.value}'."
            ),
        )

    @staticmethod
    def _is_routing_error(
        error: BaseException,
    ) -> bool:
        from ai_eval_platform.ingestion.event_router import (
            RouterError,
        )

        return isinstance(error, RouterError)

    @staticmethod
    def _is_permanent_domain_error(
        error: BaseException,
    ) -> bool:
        from ai_eval_platform.ingestion.handler import (
            HandlerError,
        )

        return isinstance(error, HandlerError)

    @staticmethod
    def _is_transient_database_error(
        error: BaseException,
    ) -> bool:
        from sqlalchemy.exc import (
            DBAPIError,
            OperationalError,
        )

        return isinstance(
            error,
            (
                OperationalError,
                DBAPIError,
            ),
        )

    @staticmethod
    def _is_transient_kafka_error(
        error: BaseException,
    ) -> bool:
        """Transient Kafka failure -> RETRY.

        Checks the application's own :class:`PublishError` hierarchy rather
        than importing ``confluent_kafka``. The infrastructure layer maps
        broker-specific errors onto those types, which keeps this module
        free of the Kafka client (see ``test_kafka_config.py``) and means the
        predicate works whether or not the library is installed.
        """
        from ai_eval_platform.ingestion.producer import (
            PublishError,
            RetryablePublishError,
        )

        if isinstance(error, RetryablePublishError):
            return True

        # A PublishError that is not explicitly retryable is treated as
        # transient: losing a record to an over-eager DLQ is worse than
        # retrying, since retries are bounded by RetryPolicy.
        return isinstance(error, PublishError)