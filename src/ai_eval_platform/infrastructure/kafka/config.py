"""Kafka configuration, resolved from the environment.

Operational settings are NOT hard-coded in business logic. They are
collected here so the consumer's offset/commit semantics are configured in
one auditable place.

The offset-lifecycle settings matter for correctness:

``enable.auto.commit=False``
    The intended model is an explicit, per-record commit *after* the durable
    destination is confirmed. Auto-commit would commit offsets on a timer,
    which can acknowledge a record whose DB write or DLQ publish has not
    completed - exactly the premature-commit data-loss window this design
    exists to prevent.

``max.poll.interval.ms``
    Upper bound on how long processing may take before the broker considers
    the consumer dead. The loop never sleeps on retry backoff (retries go
    through the retry topic), so this only needs to cover a single record's
    validate + DB + publish time. See ``Kafka_analysis.md``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


class KafkaConfigurationError(RuntimeError):
    """Required Kafka configuration is missing or invalid."""


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)

    if raw is None or raw == "":
        return default

    try:
        return int(raw)
    except ValueError as exc:
        raise KafkaConfigurationError(
            f"Environment variable {name} must be an integer, got {raw!r}"
        ) from exc


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)

    if raw is None or raw == "":
        return default

    return raw.strip().lower() in ("1", "true", "yes", "on")
@dataclass(frozen=True, slots=True)
class KafkaSettings:
    """Validated Kafka client configuration."""

    bootstrap_servers: str
    group_id: str
    input_topic: str
    dlq_topic: str
    retry_topic: str

    enable_auto_commit: bool = False
    auto_offset_reset: str = "earliest"

    max_poll_interval_ms: int = 300_000
    session_timeout_ms: int = 45_000
    heartbeat_interval_ms: int = 3_000
    max_poll_records: int = 100

    message_max_bytes: int = 1_000_000
    produce_timeout_s: float = 30.0
    retry_topic_partitions: int = 1

    #: Consumer group for the retry dispatcher. MUST differ from the main
    #: ingestion group: the two loops read different topics and must not
    #: steal each other's partitions.
    retry_group_id: str | None = None

    #: Retry backoff bounds (seconds). Duplicated here deliberately rather
    #: than shared with RetryConfig so infrastructure settings can be
    #: validated from the environment alone.
    retry_max_attempts: int = 5

    extra_client_config: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "bootstrap_servers",
            "group_id",
            "input_topic",
            "dlq_topic",
            "retry_topic",
        ):
            if not getattr(self, name):
                raise KafkaConfigurationError(f"{name} must not be empty")

        if self.enable_auto_commit:
            # Refusing loudly beats a silent data-loss window.
            raise KafkaConfigurationError(
                "enable_auto_commit must be False: offsets are committed "
                "explicitly only after the durable destination is confirmed."
            )

        if self.auto_offset_reset not in ("earliest", "latest", "none"):
            raise KafkaConfigurationError(
                "auto_offset_reset must be earliest, latest or none"
            )

        # The broker evicts a consumer that does not poll within this
        # window. session_timeout must stay comfortably below it or the
        # group rebalances before the first poll after a long stall.
        if self.session_timeout_ms >= self.max_poll_interval_ms:
            raise KafkaConfigurationError(
                "session_timeout_ms must be less than max_poll_interval_ms"
            )

        if self.heartbeat_interval_ms >= self.session_timeout_ms:
            raise KafkaConfigurationError(
                "heartbeat_interval_ms must be less than session_timeout_ms"
            )

        if self.max_poll_records <= 0:
            raise KafkaConfigurationError(
                "max_poll_records must be positive"
            )

        if self.retry_max_attempts < 1:
            raise KafkaConfigurationError(
                "retry_max_attempts must be at least 1"
            )

    def resolved_retry_group_id(self) -> str:
        """The retry dispatcher's consumer group.

        Defaults to a derived name so it can never silently equal the main
        ingestion group (which would make the two loops compete for each
        other's partitions).
        """
        return self.retry_group_id or f"{self.group_id}-retry"

    def consumer_config(self) -> dict[str, object]:
        """``confluent_kafka.Consumer`` configuration."""
        return {
            "bootstrap.servers": self.bootstrap_servers,
            "group.id": self.group_id,
            "enable.auto.commit": self.enable_auto_commit,
            "auto.offset.reset": self.auto_offset_reset,
            "max.poll.interval.ms": self.max_poll_interval_ms,
            "session.timeout.ms": self.session_timeout_ms,
            "heartbeat.interval.ms": self.heartbeat_interval_ms,
            "max.poll.records": self.max_poll_records,
            **self.extra_client_config,
        }

    def producer_config(self) -> dict[str, object]:
        """``confluent_kafka.Producer`` configuration.

        ``acks=all`` with ``enable.idempotence=True`` is the strongest
        per-producer guarantee available without transactions. It does NOT
        make the end-to-end pipeline exactly-once.
        """
        return {
            "bootstrap.servers": self.bootstrap_servers,
            "acks": "all",
            "enable.idempotence": True,
            "message.max.bytes": self.message_max_bytes,
            **self.extra_client_config,
        }
def load_settings(
    settings_file: str | None = None,
) -> KafkaSettings:
    """Load settings from the environment, optionally seeded by a YAML file.

    Environment variables always win over the file so a deployment can
    override without editing files.
    """
    values: dict[str, object] = {}

    if settings_file:
        values.update(_read_yaml_file(settings_file))

    for field_name, env_name in (
        ("bootstrap_servers", "KAFKA_BOOTSTRAP_SERVERS"),
        ("group_id", "KAFKA_GROUP_ID"),
        ("input_topic", "KAFKA_INPUT_TOPIC"),
        ("dlq_topic", "KAFKA_DLQ_TOPIC"),
        ("retry_topic", "KAFKA_RETRY_TOPIC"),
        ("auto_offset_reset", "KAFKA_AUTO_OFFSET_RESET"),
    ):
        raw = os.getenv(env_name)

        if raw:
            values[field_name] = raw

    for field_name, env_name, default in (
        ("max_poll_interval_ms", "KAFKA_MAX_POLL_INTERVAL_MS", 300_000),
        ("session_timeout_ms", "KAFKA_SESSION_TIMEOUT_MS", 45_000),
        ("heartbeat_interval_ms", "KAFKA_HEARTBEAT_INTERVAL_MS", 3_000),
        ("max_poll_records", "KAFKA_MAX_POLL_RECORDS", 100),
        ("message_max_bytes", "KAFKA_MESSAGE_MAX_BYTES", 1_000_000),
    ):
        if os.getenv(env_name):
            values[field_name] = _env_int(env_name, default)
        else:
            values.setdefault(field_name, default)

    if os.getenv("KAFKA_ENABLE_AUTO_COMMIT") is not None:
        values["enable_auto_commit"] = _env_bool(
            "KAFKA_ENABLE_AUTO_COMMIT", False
        )
    else:
        values.setdefault("enable_auto_commit", False)

    values.setdefault("auto_offset_reset", "earliest")
    values.setdefault("produce_timeout_s", 30.0)
    values.setdefault("retry_topic_partitions", 1)
    values.setdefault("extra_client_config", {})

    missing = [
        name
        for name in (
            "bootstrap_servers",
            "group_id",
            "input_topic",
            "dlq_topic",
            "retry_topic",
        )
        if not values.get(name)
    ]

    if missing:
        raise KafkaConfigurationError(
            "Missing required Kafka settings: "
            + ", ".join(missing)
            + ". Set the corresponding KAFKA_* environment variables or "
            "provide them in the settings file."
        )

    return KafkaSettings(**values)  # type: ignore[arg-type]


def _read_yaml_file(path: str) -> dict[str, object]:
    try:
        import yaml
    except ImportError as exc:
        raise KafkaConfigurationError(
            f"Reading {path} requires PyYAML. Install it or pass settings "
            "via environment variables."
        ) from exc

    with open(path, "r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}

    if not isinstance(loaded, dict):
        raise KafkaConfigurationError(
            f"{path} must contain a mapping at the top level"
        )

    return loaded