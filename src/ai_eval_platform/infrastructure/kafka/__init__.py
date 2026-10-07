"""Kafka transport: producer, consumer, and configuration.

This is the only package that imports ``confluent_kafka``. The application
layer in ``ai_eval_platform.ingestion`` depends solely on the
``ConfirmedProducer`` / ``RecordSource`` protocols declared there.
"""

from .config import (
    KafkaConfigurationError,
    KafkaSettings,
    load_settings,
)
from .consumer import KafkaConsumerClient
from .errors import (
    KafkaUnavailableError,
    missing_client_error,
)
from .producer import ConfluentProducer

__all__ = [
    "ConfirmedProducer",
    "KafkaConfigurationError",
    "KafkaConsumerClient",
    "KafkaSettings",
    "KafkaUnavailableError",
    "ConfluentProducer",
    "load_settings",
    "missing_client_error",
]