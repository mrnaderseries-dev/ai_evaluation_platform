"""``confluent_kafka`` producer that confirms broker delivery.

Why this class exists
---------------------
The naive application-level call ``producer.produce(...)`` followed by
``None`` return is NOT proof of delivery. In ``confluent_kafka``:

* ``produce()`` appends to a LOCAL buffer and returns immediately.
* It raises ``BufferError`` only when that local queue is full.
* Broker-side outcomes (message too large, unknown topic, not-leader,
  request timeout) are reported ASYNCHRONOUSLY through delivery reports,
  which are delivered by ``poll()`` / ``flush()``.

So a consumer that treats a returned ``produce()`` as "durably stored", and
then commits the source Kafka offset, can lose the DLQ/retry record while
the offset advances - silent, unrecoverable message loss.

This implementation blocks until the delivery report for the specific
record arrives, then raises on failure. That is what makes
"publish -> confirm -> commit offset" safe.
"""

from __future__ import annotations

from typing import Any

from ai_eval_platform.ingestion.producer import (
    MessageTooLargeError,
    PermanentPublishError,
    PublishError,
    RetryablePublishError,
)

from .config import KafkaSettings
from .errors import require_confluent_kafka


class ConfluentProducer:
    """A :class:`ConfirmedProducer` backed by ``confluent_kafka.Producer``."""

    def __init__(self, settings: KafkaSettings) -> None:
        client = require_confluent_kafka()

        self._client = client
        self._producer = client.Producer(settings.producer_config())
        self._default_timeout = settings.produce_timeout_s

    def publish(
        self,
        *,
        topic: str,
        value: bytes,
        key: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout_s: float | None = None,
    ) -> None:
        """Publish and block until the broker acknowledges the record.

        Raises:
            MessageTooLargeError: the broker/topic rejected the size.
            RetryablePublishError: transient broker failure.
            PermanentPublishError: non-transient failure.
        """
        timeout = self._default_timeout if timeout_s is None else timeout_s

        callback_state: dict[str, Any] = {"error": None, "done": False}

        def on_delivery(err, _msg) -> None:
            callback_state["error"] = err
            callback_state["done"] = True

        try:
            self._producer.produce(
                topic=topic,
                value=value,
                key=key,
                headers=list(headers.items()) if headers else None,
                on_delivery=on_delivery,
            )
        except BufferError as exc:
            # Local queue full: the broker is too slow relative to production.
            raise RetryablePublishError(
                "Producer local queue is full; broker is not keeping up."
            ) from exc
        except self._client.KafkaException as exc:
            raise self._map_enqueue_error(exc) from exc
        except Exception as exc:  # noqa: BLE001
            raise RetryablePublishError(
                f"Failed to enqueue record for topic '{topic}': {exc}"
            ) from exc

        # Drain delivery reports until this record's callback fires. This is
        # the step the application-level Protocol must guarantee.
        remaining = timeout

        while not callback_state["done"] and remaining > 0:
            self._producer.poll(remaining)
            remaining = max(remaining - 0.1, 0.0)

        if not callback_state["done"]:
            # Give the callback a final chance via flush, which also blocks
            # until the outstanding message is reported.
            remaining_messages = self._producer.flush(timeout)
            self._producer.poll(0)

            if not callback_state["done"] or remaining_messages > 0:
                raise RetryablePublishError(
                    f"Timed out after {timeout}s waiting for broker "
                    f"acknowledgement on topic '{topic}'."
                )

        error = callback_state["error"]

        if error is not None:
            raise self._map_delivery_error(error, topic)

    def close(self) -> None:
        """Flush any outstanding records on shutdown."""
        self._producer.flush(10.0)

    @staticmethod
    def _map_enqueue_error(exc: Exception) -> PublishError:
        error_text = str(exc).lower()

        if "topic" in error_text and "not" in error_text:
            return PermanentPublishError(str(exc))

        return RetryablePublishError(str(exc))

    @staticmethod
    def _map_delivery_error(error: object, topic: str) -> PublishError:
        code = getattr(error, "code", lambda: None)()
        name = str(code) if code is not None else str(error)
        lowered = name.lower()

        if "msg_size" in lowered or "message_size" in lowered:
            return MessageTooLargeError(
                f"Broker rejected record for topic '{topic}' as too large."
            )

        if lowered in ("_no_error", "no_error"):
            return RetryablePublishError(f"Unexpected error: {error}")

        if any(
            token in lowered
            for token in ("timed_out", "not_leader", "leader_not_available")
        ):
            return RetryablePublishError(f"Transient broker error: {name}")

        if any(
            token in lowered
            for token in ("unknown_topic", "authorization", "policy")
        ):
            return PermanentPublishError(f"Non-transient broker error: {name}")

        return RetryablePublishError(f"Broker error on '{topic}': {name}")