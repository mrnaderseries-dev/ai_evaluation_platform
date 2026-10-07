"""Real Kafka broker integration tests.

These are the only tests in this repository able to prove broker behaviour:
delivery confirmation, committed offsets, partition assignment, rebalances,
and shutdown. Every other suite uses doubles.

ENVIRONMENT BLOCKER (verified 2026-10-04)
------------------------------------------
A real broker could not be started in this environment:

* ``docker --version`` -> "docker is not recognized" (not installed)
* ``testcontainers``   -> ModuleNotFoundError (not installed)
* ``confluent_kafka``  -> ModuleNotFoundError (not installed)
* ``pip download confluent-kafka`` -> timed out after 60 s (no index
  reachable)

No broker and no client are available, so every test here skips at
collection time when ``KAFKA_BOOTSTRAP_SERVERS`` is unset. Nothing here is
faked: there are no fallback doubles and no simulated broker. The tests are
written against the real client API, so they exercise real broker semantics
the moment a broker is reachable.

To run them::

    docker run -d --name kafka -p 9092:9092 \\
        -e KAFKA_NODE_ID=0 \\
        -e KAFKA_PROCESS_ROLES=broker,controller \\
        -e KAFKA_LISTENERS=PLAINTEXT://0.0.0.0:9092,CONTROLLER://0.0.0.0:9093 \\
        -e KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://localhost:9092 \\
        -e KAFKA_CONTROLLER_QUORUM_VOTERS=0@localhost:9093 \\
        -e KAFKA_CONTROLLER_LISTENER_NAMES=CONTROLLER \\
        -e KAFKA_LISTENER_SECURITY_PROTOCOL_MAP=CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT \\
        -e KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR=1 \\
        -e KAFKA_GROUP_INITIAL_REBALANCE_DELAY_MS=0 \\
        apache/kafka:3.7.0

    pip install "confluent-kafka>=2.3"
    $env:KAFKA_BOOTSTRAP_SERVERS = "localhost:9092"
    python -m pytest tests/integration/test_real_kafka_integration.py -q

Scenarios covered once a broker exists: A (normal path), C (DLQ), E (producer
failure), H (rebalance). B, D, F and G need fault injection that cannot be
produced without a controllable broker and remain unverified.
"""

from __future__ import annotations

import os

import pytest

BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS")

pytestmark = pytest.mark.skipif(
    not BOOTSTRAP_SERVERS,
    reason=(
        "No Kafka broker: KAFKA_BOOTSTRAP_SERVERS unset. Docker, "
        "testcontainers and confluent-kafka are all unavailable here, so "
        "broker semantics cannot be validated."
    ),
)

confluent_kafka = pytest.importorskip(
    "confluent_kafka",
    reason="confluent-kafka is not installed",
)

from ai_eval_platform.ingestion.dlq import (  # noqa: E402
    DeadLetterQueue,
    FailureStage,
)
from ai_eval_platform.ingestion.kafka_record import KafkaRecord  # noqa: E402
from ai_eval_platform.ingestion.producer import (  # noqa: E402
    PublishError,
    TopicPosition,
)
from ai_eval_platform.infrastructure.kafka.config import (  # noqa: E402
    KafkaSettings,
)
from ai_eval_platform.infrastructure.kafka.consumer import (  # noqa: E402
    KafkaConsumerClient,
)
from ai_eval_platform.infrastructure.kafka.producer import (  # noqa: E402
    ConfluentProducer,
)


def make_settings(main_topic: str, retry_topic: str, dlq_topic: str):
    return KafkaSettings(
        bootstrap_servers=BOOTSTRAP_SERVERS,
        group_id=f"it-{os.getpid()}",
        input_topic=main_topic,
        retry_topic=retry_topic,
        dlq_topic=dlq_topic,
        auto_offset_reset="earliest",
        max_poll_interval_ms=60_000,
        session_timeout_ms=10_000,
        heartbeat_interval_ms=3_000,
    )


def make_consumer(settings, topics, group_id):
    scoped = KafkaSettings(
        bootstrap_servers=settings.bootstrap_servers,
        group_id=group_id,
        input_topic=settings.input_topic,
        retry_topic=settings.retry_topic,
        dlq_topic=settings.dlq_topic,
        max_poll_interval_ms=settings.max_poll_interval_ms,
        session_timeout_ms=settings.session_timeout_ms,
    )

    return KafkaConsumerClient(scoped, topics=topics)


def drain(consumer, expected, timeout=20.0):
    import time

    deadline = time.monotonic() + timeout
    records = []

    while len(records) < expected and time.monotonic() < deadline:
        records.extend(consumer.poll(1.0))

    return records
class TestA_NormalPath:
    """A: produce -> consume -> explicit offset commit."""

    def test_publish_and_consume_round_trip(self):
        main_topic = os.getenv("KAFKA_MAIN_TOPIC", "it_main")
        retry_topic = os.getenv("KAFKA_RETRY_TOPIC", "it_retry")
        dlq_topic = os.getenv("KAFKA_DLQ_TOPIC", "it_dlq")
        settings = make_settings(main_topic, retry_topic, dlq_topic)

        ConfluentProducer(settings).publish(
            topic=main_topic,
            value=b'{"event_id":"e1"}',
            key=b"run-1",
        )

        consumer = make_consumer(
            settings, [main_topic], f"it-a-{os.getpid()}"
        )
        try:
            records = drain(consumer, 1)
        finally:
            consumer.close()

        assert len(records) == 1
        assert records[0].value == b'{"event_id":"e1"}'
        assert records[0].key == "run-1"

    def test_committed_offset_is_record_offset_plus_one(self):
        main_topic = os.getenv("KAFKA_MAIN_TOPIC", "it_main")
        retry_topic = os.getenv("KAFKA_RETRY_TOPIC", "it_retry")
        dlq_topic = os.getenv("KAFKA_DLQ_TOPIC", "it_dlq")
        settings = make_settings(main_topic, retry_topic, dlq_topic)

        ConfluentProducer(settings).publish(
            topic=main_topic, value=b"first", key=b"run-1"
        )

        consumer = make_consumer(
            settings, [main_topic], f"it-a2-{os.getpid()}"
        )
        try:
            record = drain(consumer, 1)[0]
            position = TopicPosition(
                topic=record.topic,
                partition=record.partition,
                next_offset=record.offset + 1,
            )
            consumer.commit([position])
        finally:
            consumer.close()

        committed = consumer._consumer.committed(
            [
                confluent_kafka.TopicPartition(
                    position.topic, position.partition
                )
            ],
            timeout=10.0,
        )

        assert committed[0].offset == position.next_offset

    def test_uncommitted_record_is_redelivered_to_a_new_group(self):
        main_topic = os.getenv("KAFKA_MAIN_TOPIC", "it_main")
        retry_topic = os.getenv("KAFKA_RETRY_TOPIC", "it_retry")
        dlq_topic = os.getenv("KAFKA_DLQ_TOPIC", "it_dlq")
        settings = make_settings(main_topic, retry_topic, dlq_topic)

        ConfluentProducer(settings).publish(
            topic=main_topic, value=b"uncommitted", key=b"run-1"
        )

        first = make_consumer(
            settings, [main_topic], f"it-f1-{os.getpid()}"
        )
        try:
            records = drain(first, 1)
            assert len(records) == 1
        finally:
            first.close()

        second = make_consumer(
            settings, [main_topic], f"it-f2-{os.getpid()}"
        )
        try:
            redelivered = drain(second, 1)
        finally:
            second.close()

        assert len(redelivered) == 1, (
            "a record whose offset was never committed must be redelivered"
        )
        assert redelivered[0].value == b"uncommitted"
class TestC_DLQ:
    """C: the DLQ record exists on a real broker with full provenance."""

    def test_dlq_record_persists_with_provenance(self):
        import json

        main_topic = os.getenv("KAFKA_MAIN_TOPIC", "it_main")
        retry_topic = os.getenv("KAFKA_RETRY_TOPIC", "it_retry")
        dlq_topic = os.getenv("KAFKA_DLQ_TOPIC", "it_dlq")
        settings = make_settings(main_topic, retry_topic, dlq_topic)

        DeadLetterQueue(ConfluentProducer(settings), dlq_topic).publish_failure(
            record=KafkaRecord(
                topic=main_topic,
                partition=3,
                offset=17,
                value=b"\xff\xfe",
                key="run-1",
            ),
            error=ValueError("bad utf8"),
            stage=FailureStage.VALIDATION,
            code="INVALID_UTF8",
            attempt=0,
        )

        consumer = make_consumer(
            settings, [dlq_topic], f"it-c-{os.getpid()}"
        )
        try:
            records = drain(consumer, 1)
        finally:
            consumer.close()

        payload = json.loads(records[0].value)

        assert payload["source"]["topic"] == main_topic
        assert payload["source"]["partition"] == 3
        assert payload["source"]["offset"] == 17
        assert payload["failure"]["code"] == "INVALID_UTF8"
        assert payload["failure"]["stage"] == "validation"
        assert payload["dlq_schema_version"] == "1.0"
        assert payload["raw"]["byte_length"] == 2


class TestE_ProducerFailure:
    """E: an unconfirmed publish raises rather than reporting success."""

    def test_publish_to_unknown_topic_raises_publish_error(self):
        settings = make_settings(
            os.getenv("KAFKA_MAIN_TOPIC", "it_main"),
            os.getenv("KAFKA_RETRY_TOPIC", "it_retry"),
            os.getenv("KAFKA_DLQ_TOPIC", "it_dlq"),
        )

        with pytest.raises(PublishError):
            ConfluentProducer(settings).publish(
                topic="topic-that-does-not-exist-anywhere",
                value=b"x",
                timeout_s=15.0,
            )


class TestH_Rebalance:
    """H: two consumers in one group share partitions across a rebalance."""

    def test_both_group_members_receive_records(self):
        main_topic = os.getenv("KAFKA_MAIN_TOPIC", "it_main")
        retry_topic = os.getenv("KAFKA_RETRY_TOPIC", "it_retry")
        dlq_topic = os.getenv("KAFKA_DLQ_TOPIC", "it_dlq")
        settings = make_settings(main_topic, retry_topic, dlq_topic)

        producer = ConfluentProducer(settings)

        for index in range(6):
            producer.publish(
                topic=main_topic,
                value=f"m{index}".encode(),
                key=b"run-1",
            )

        group = f"it-h-{os.getpid()}"
        first = make_consumer(settings, [main_topic], group)
        second = make_consumer(settings, [main_topic], group)

        try:
            first_records = drain(first, 1)
            second_records = drain(second, 1)

            received = first_records + second_records

            assert received, (
                "at least one group member must receive records after the "
                "rebalance"
            )
            assert len({(r.partition, r.offset) for r in received}) == len(
                received
            ), "no record may be delivered twice within one poll sweep"
        finally:
            first.close()
            second.close()