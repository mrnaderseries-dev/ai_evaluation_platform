"""Architecture-boundary tests.

The dependency direction must be:

    application (ingestion) -> interfaces -> infrastructure (infrastructure/kafka)

``ingestion`` must never import ``confluent_kafka``. Everything it needs is
expressed through the ``ConfirmedProducer`` / ``RecordSource`` protocols, and
the concrete client lives in ``infrastructure/kafka``.

These tests parse the AST rather than grepping text, so a mention of the
library name inside a docstring cannot mask a real import.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

SRC = pathlib.Path("src/ai_eval_platform")
INGESTION = SRC / "ingestion"
INFRASTRUCTURE = SRC / "infrastructure" / "kafka"


def imported_modules(path: pathlib.Path) -> list[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[str, int]] = []

    for node in ast.walk(tree):
        names: list[str] = []

        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]

        for name in names:
            found.append((name, node.lineno))

    return found


def test_ingestion_never_imports_confluent_kafka():
    offenders: list[str] = []

    for path in sorted(INGESTION.glob("*.py")):
        for module, lineno in imported_modules(path):
            if module.split(".")[0] == "confluent_kafka":
                offenders.append(f"{path.name}:{lineno}")

    assert offenders == [], (
        f"application layer must not import confluent_kafka: {offenders}"
    )


def test_ingestion_does_not_import_infrastructure():
    offenders: list[str] = []

    for path in sorted(INGESTION.glob("*.py")):
        for module, lineno in imported_modules(path):
            if module.startswith("ai_eval_platform.infrastructure"):
                offenders.append(f"{path.name}:{lineno} -> {module}")

    assert offenders == [], (
        f"application must depend on interfaces, not implementations: "
        f"{offenders}"
    )


def test_infrastructure_implements_the_application_protocols():
    consumer_imports = {
        module
        for path in INFRASTRUCTURE.glob("*.py")
        for module, _ in imported_modules(path)
    }

    assert "ai_eval_platform.ingestion.producer" in consumer_imports, (
        "the Kafka client must implement the application's producer/"
        "record-source interfaces"
    )


def test_kafka_client_lives_only_in_infrastructure():
    infrastructure_imports = {
        module
        for path in INFRASTRUCTURE.glob("*.py")
        for module, _ in imported_modules(path)
    }

    assert any(
        module.split(".")[0] == "confluent_kafka"
        for module in infrastructure_imports
    ), (
        "the Kafka client must be imported by infrastructure, otherwise the "
        "delivery-confirmation path is unreachable"
    )


def test_bare_commit_is_not_reachable_from_the_infrastructure_client():
    """A commit with no offsets argument advances every assigned partition.

    The client must only ever commit explicit positions, so the AST must not
    contain a ``commit()`` call without the ``offsets`` keyword.
    """
    offenders: list[int] = []

    for path in INFRASTRUCTURE.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "commit"
            ):
                keywords = {kw.arg for kw in node.keywords}

                if "offsets" not in keywords:
                    offenders.append(node.lineno)

    assert offenders == [], (
        f"bare commit() advances all assigned partitions; found at lines "
        f"{offenders}"
    )


def test_consumer_config_forbids_auto_commit():
    from ai_eval_platform.infrastructure.kafka.config import (
        KafkaConfigurationError,
        KafkaSettings,
    )

    with pytest.raises(KafkaConfigurationError):
        KafkaSettings(
            bootstrap_servers="localhost:9092",
            group_id="g",
            input_topic="t",
            dlq_topic="d",
            retry_topic="r",
            enable_auto_commit=True,
        )


def test_producer_config_requires_full_acknowledge_set():
    from ai_eval_platform.infrastructure.kafka.config import KafkaSettings

    config = KafkaSettings(
        bootstrap_servers="localhost:9092",
        group_id="g",
        input_topic="t",
        dlq_topic="d",
        retry_topic="r",
    ).producer_config()

    assert config["acks"] == "all"
    assert config["enable.idempotence"] is True


import pytest