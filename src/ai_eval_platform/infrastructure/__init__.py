"""Kafka-specific infrastructure implementation.

The application layer (:mod:`ai_eval_platform.ingestion`) depends only on
the ``ConfirmedProducer`` / ``RecordSource`` protocols. Everything that
knows about ``confluent_kafka`` lives here.

``confluent_kafka`` is an optional dependency: importing this package
without it raises a clear error rather than breaking module import, so the
application layer stays testable without a broker.
"""