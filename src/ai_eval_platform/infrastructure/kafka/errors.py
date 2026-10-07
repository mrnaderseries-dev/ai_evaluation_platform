"""Kafka client error mapping.

Keeps ``confluent_kafka`` exception types from leaking into the application
layer, so ``ingestion`` can classify failures without importing the client
library.
"""

from __future__ import annotations


class KafkaUnavailableError(RuntimeError):
    """The Kafka client library is not installed."""


def missing_client_error(package: str = "confluent-kafka") -> KafkaUnavailableError:
    return KafkaUnavailableError(
        f"{package} is required for the Kafka infrastructure layer but is "
        "not installed. Install it with: pip install 'confluent-kafka'"
    )


def require_confluent_kafka():
    """Import and return the ``confluent_kafka`` module, or raise clearly."""
    try:
        import confluent_kafka  # noqa: PLC0415
    except ImportError as exc:
        raise missing_client_error() from exc

    return confluent_kafka