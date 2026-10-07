"""Kafka configuration and infrastructure-separation tests.

These are pure-configuration tests: no broker is involved. They lock in the
offset-lifecycle invariants that the consumer depends on.
"""

from __future__ import annotations

import pytest

from ai_eval_platform.infrastructure.kafka.config import (
    KafkaConfigurationError,
    KafkaSettings,
)


def settings(**overrides) -> KafkaSettings:
    values = {
        "bootstrap_servers": "localhost:9092",
        "group_id": "ai-eval-ingestion",
        "input_topic": "events",
        "dlq_topic": "events.dlq",
        "retry_topic": "events.retry",
    }
    values.update(overrides)
    return KafkaSettings(**values)


def test_defaults_disable_auto_commit():
    # The manual offset lifecycle REQUIRES auto-commit off: auto-commit can
    # acknowledge a record before its DB write or DLQ publish completes.
    assert settings().consumer_config()["enable.auto.commit"] is False


def test_auto_commit_true_is_rejected_loudly():
    # Failing at construction beats a silent data-loss window in production.
    with pytest.raises(KafkaConfigurationError):
        settings(enable_auto_commit=True)


def test_session_timeout_must_be_below_max_poll_interval():
    with pytest.raises(KafkaConfigurationError):
        settings(session_timeout_ms=400_000, max_poll_interval_ms=300_000)


def test_heartbeat_must_be_below_session_timeout():
    with pytest.raises(KafkaConfigurationError):
        settings(heartbeat_interval_ms=60_000, session_timeout_ms=45_000)


def test_missing_required_settings_are_reported():
    with pytest.raises(KafkaConfigurationError):
        KafkaSettings(
            bootstrap_servers="",
            group_id="g",
            input_topic="t",
            dlq_topic="d",
            retry_topic="r",
        )


def test_producer_uses_acks_all_and_idempotence():
    config = settings().producer_config()

    assert config["acks"] == "all"
    assert config["enable.idempotence"] is True


def test_message_max_bytes_matches_broker_default():
    assert settings().message_max_bytes == 1_000_000


def test_auto_offset_reset_must_be_valid():
    with pytest.raises(KafkaConfigurationError):
        settings(auto_offset_reset="sometimes")


def test_max_poll_records_must_be_positive():
    with pytest.raises(KafkaConfigurationError):
        settings(max_poll_records=0)


def test_retry_group_id_never_equals_main_group():
    # The retry dispatcher reads a different topic; sharing a group id would
    # make the two loops steal each other's partitions.
    from ai_eval_platform.infrastructure.kafka.config import KafkaSettings

    config = KafkaSettings(
        bootstrap_servers="localhost:9092",
        group_id="ai-eval-ingestion",
        input_topic="ai.events",
        dlq_topic="ai.events.dlq",
        retry_topic="ai.events.retry",
    )

    assert config.resolved_retry_group_id() == "ai-eval-ingestion-retry"
    assert config.resolved_retry_group_id() != config.group_id


def test_explicit_retry_group_id_is_honoured():
    from ai_eval_platform.infrastructure.kafka.config import KafkaSettings

    config = KafkaSettings(
        bootstrap_servers="localhost:9092",
        group_id="main",
        input_topic="t",
        dlq_topic="d",
        retry_topic="r",
        retry_group_id="retry-dispatcher",
    )

    assert config.resolved_retry_group_id() == "retry-dispatcher"


def test_retry_max_attempts_must_be_positive():
    with pytest.raises(KafkaConfigurationError):
        settings(retry_max_attempts=0)


def test_application_layer_does_not_import_confluent_kafka():
    # Infrastructure separation: the ingestion package must stay free of the
    # Kafka client so it remains testable without a broker. This inspects
    # real import statements, not docstring mentions.
    import ast
    import pathlib

    ingestion = pathlib.Path("src/ai_eval_platform/ingestion")

    offenders: list[str] = []

    for path in sorted(ingestion.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))

        for node in ast.walk(tree):
            names: list[str] = []

            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]

            if any(name.split(".")[0] == "confluent_kafka" for name in names):
                offenders.append(f"{path.name}:{node.lineno}")

    assert offenders == [], (
        f"ingestion/ must not import confluent_kafka directly: {offenders}"
    )