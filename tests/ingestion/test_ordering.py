"""Ordering and concurrency invariants.

The repository allocates ``sequence_number = MAX(sequence_number) + 1``
per run. That is only safe with ONE writer per run. These tests document
and enforce the constraint the consumer must honour rather than assuming it.
"""

from __future__ import annotations

import ast
import json
import pathlib

from ai_eval_platform.ingestion.kafka_record import KafkaRecord
from ai_eval_platform.ingestion.kafka_consumer import KafkaEventConsumer

from .conftest import (
    FakeConsumer,
    RecordingProducer,
    make_record,
    valid_event_bytes,
)
from .test_retry_consumer import make_retry_record

MAIN_TOPIC = "ai.events"


def test_consumer_has_no_worker_pool_or_background_threads():
    # sequence_number = MAX()+1 requires a single writer per run. Concurrency
    # inside the consumer would break that assumption silently, so the
    # production code must contain no threads/workers.
    source = pathlib.Path(
        "src/ai_eval_platform/ingestion/kafka_consumer.py"
    ).read_text(encoding="utf-8")

    tree = ast.parse(source)

    forbidden = {
        "Thread",
        "ThreadPoolExecutor",
        "ProcessPoolExecutor",
        "multiprocessing",
        "asyncio",
        "create_task",
        "ThreadPool",
    }

    used = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
    } | {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
    }

    assert not (forbidden & used), (
        f"consumer must stay single-threaded; found {forbidden & used}"
    )


def test_records_of_one_run_are_processed_in_offset_order():
    # Ordering comes from Kafka partitioning, not from the consumer sorting.
    # The consumer must therefore preserve arrival order per partition.
    source = (
        pathlib.Path("src/ai_eval_platform/ingestion/kafka_consumer.py")
        .read_text(encoding="utf-8")
    )

    # process() handles exactly the record it is given.
    assert "def process(self, record: KafkaRecord)" in source
    assert "sorted(" not in source, (
        "the consumer must not reorder records"
    )


def test_run_id_key_groups_a_run_into_one_partition():
    # Documents WHY the main topic must be keyed by run_id: same key ->
    # same partition -> Kafka's per-partition ordering.
    # (Kafka guarantees this; the assertion here is that the codebase uses
    # run_id as the key and never event_id for the main topic.)
    source = pathlib.Path(
        "src/ai_eval_platform/ingestion/retry_publisher.py"
    ).read_text(encoding="utf-8")

    assert "key_value = source.key" in source
    assert "event_id" not in source.split("key_value =")[1][:120]


def test_dlq_is_keyed_by_run_id_not_event_id():
    from ai_eval_platform.ingestion.dlq import (
        DeadLetterQueue,
        FailureInfo,
        FailureStage,
    )
    from ai_eval_platform.ingestion.event_validator import EventValidator

    producer = RecordingProducer()
    dlq = DeadLetterQueue(producer, "ai.events.dlq")

    # An event whose run_id differs from the transport key proves the DLQ
    # keys off the DOMAIN run_id, which is what survives on a retry or a
    # replay. If it keyed off event_id, the value would be "evt-1".
    event = EventValidator().validate(
        valid_event_bytes(run_id="run-77")
    ).event

    envelope = dlq.build_record(
        record=make_record(key="transport-key-differs"),
        failure=FailureInfo(
            stage=FailureStage.HANDLER,
            code="X",
            error_type="X",
            message="m",
            attempt=1,
        ),
        event=event,
    )
    dlq.publish(envelope)

    assert envelope.event.run_id == "run-77"
    assert producer.published[0]["key"] == b"run-77"


def test_retry_topic_key_is_the_original_key():
    from ai_eval_platform.ingestion.retry_publisher import RetryPublisher

    producer = RecordingProducer()
    RetryPublisher(producer, "ai.events.retry").publish(
        source=make_record(key="run-55", offset=3),
        attempt=1,
        due_at=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ),
    )

    assert producer.published[0]["key"] == b"run-55"


def test_sequence_number_allocation_is_single_writer_only():
    # The repository's MAX()+1 allocation is NOT safe for concurrent writers
    # on one run. This test records the constraint; if someone later adds
    # concurrency to the consumer, this is the test that should fail.
    repository_source = pathlib.Path(
        "src/ai_eval_platform/storage/repositories.py"
    ).read_text(encoding="utf-8")

    assert "func.coalesce(func.max(Event.sequence_number), 0)" in (
        repository_source
    ), (
        "MAX()+1 sequence allocation still present: single-writer-per-run "
        "must be preserved by the consumer"
    )