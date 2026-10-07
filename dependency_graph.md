# Dependency Graph Trace - ai-evaluation-platform

Generated: 2026-10-07T15:24:59+00:00
Generated read-only: no existing project file was modified or created except this report.

Legend: status `yes` = file is NOT empty (size > 0); status `false` = file IS empty (0 bytes).
Scope: every file in the repository (build caches, `.git`, `.pytest_cache`, `*.egg-info` excluded).
Dependencies are traced from Python `import`/`from ... import` statements (AST, relative imports resolved) plus runtime file-path references.
Note: legacy test imports written as `src.ai_eval_platform...` (`tests/evaluation/*`) are canonicalized to `ai_eval_platform...` — same files, `src` is only a path alias.

## 1. Summary

- Files total: 206 (yes: 130, false: 76)
- Python files: 175 (yes: 113, false: 62)
- Top-level units: `src/ai_eval_platform` (package), `schemas` (package), `tests` (suite), plus `batch`, `producers`, `sdk`, `ml`, `configs`, `docker`, `.github`, docs
- Unit-level dependency edges (internal): 25
- Module-level internal import edges: 320
- Unit-level import cycles: 0
- Third-party roots imported: confluent_kafka, pydantic, pytest, sqlalchemy, yaml
- stdlib roots imported: 21
- Unresolved internal imports: 0

## 2. Unit-level dependency graph (internal, `A -> B` means A imports B)

- `ai_eval_platform`
  - -> `ai_eval_platform.monitoring`
  - -> `ai_eval_platform.storage`
  - -> `schemas.events`
- `ai_eval_platform.evaluation`
  - -> `ai_eval_platform.storage`
  - -> `schemas.events`
- `ai_eval_platform.infrastructure`
  - -> `ai_eval_platform.ingestion`
- `ai_eval_platform.ingestion`
  - -> `schemas.events`
- `tests.contract`
  - -> `schemas.events`
- `tests.evaluation`
  - -> `ai_eval_platform.evaluation`
  - -> `ai_eval_platform.storage`
  - -> `schemas.events`
- `tests.ingestion`
  - -> `ai_eval_platform.infrastructure`
  - -> `ai_eval_platform.ingestion`
  - -> `ai_eval_platform.storage`
  - -> `schemas.events`
- `tests.integration`
  - -> `ai_eval_platform.infrastructure`
  - -> `ai_eval_platform.ingestion`
  - -> `ai_eval_platform.storage`
  - -> `tests.ingestion`
- `tests.monitoring`
  - -> `ai_eval_platform.monitoring`
  - -> `ai_eval_platform.monitoring_integration`
  - -> `ai_eval_platform.storage`
  - -> `schemas.events`
- `tests.storage`
  - -> `ai_eval_platform.storage`
  - -> `schemas.events`

Directional shape: `tests.*` -> `ai_eval_platform.*` / `schemas.*`; `ai_eval_platform.*` -> `schemas.*`; `schemas` imports only within itself; nothing imports `tests`.

## 3. Module-level internal import edges (full trace)

| source file | imported internal module | resolved to |
|---|---|---|
| `schemas\events\agent_behavior_events.py` | `schemas.events.common` | `schemas\events\common.py` |
| `schemas\events\context_events.py` | `schemas.events.common` | `schemas\events\common.py` |
| `schemas\events\evaluation_events.py` | `schemas.events.common` | `schemas\events\common.py` |
| `schemas\events\execution_events.py` | `schemas.events.common` | `schemas\events\common.py` |
| `schemas\events\policy_events.py` | `schemas.events.common` | `schemas\events\common.py` |
| `src\ai_eval_platform\evaluation\evaluator.py` | `ai_eval_platform.evaluation.domain` | `src\ai_eval_platform\evaluation\domain.py` |
| `src\ai_eval_platform\evaluation\evaluators\citation.py` | `ai_eval_platform.evaluation.domain` | `src\ai_eval_platform\evaluation\domain.py` |
| `src\ai_eval_platform\evaluation\evaluators\citation.py` | `ai_eval_platform.evaluation.evaluator` | `src\ai_eval_platform\evaluation\evaluator.py` |
| `src\ai_eval_platform\evaluation\evaluators\exact_match.py` | `ai_eval_platform.evaluation.domain` | `src\ai_eval_platform\evaluation\domain.py` |
| `src\ai_eval_platform\evaluation\evaluators\exact_match.py` | `ai_eval_platform.evaluation.evaluator` | `src\ai_eval_platform\evaluation\evaluator.py` |
| `src\ai_eval_platform\evaluation\evaluators\groundedness.py` | `ai_eval_platform.evaluation.domain` | `src\ai_eval_platform\evaluation\domain.py` |
| `src\ai_eval_platform\evaluation\evaluators\groundedness.py` | `ai_eval_platform.evaluation.evaluator` | `src\ai_eval_platform\evaluation\evaluator.py` |
| `src\ai_eval_platform\evaluation\evaluators\latency.py` | `ai_eval_platform.evaluation.domain` | `src\ai_eval_platform\evaluation\domain.py` |
| `src\ai_eval_platform\evaluation\evaluators\latency.py` | `ai_eval_platform.evaluation.evaluator` | `src\ai_eval_platform\evaluation\evaluator.py` |
| `src\ai_eval_platform\evaluation\evaluators\retrieval.py` | `ai_eval_platform.evaluation.domain` | `src\ai_eval_platform\evaluation\domain.py` |
| `src\ai_eval_platform\evaluation\evaluators\retrieval.py` | `ai_eval_platform.evaluation.evaluator` | `src\ai_eval_platform\evaluation\evaluator.py` |
| `src\ai_eval_platform\evaluation\evaluators\semantic_similarity.py` | `ai_eval_platform.evaluation.domain` | `src\ai_eval_platform\evaluation\domain.py` |
| `src\ai_eval_platform\evaluation\evaluators\semantic_similarity.py` | `ai_eval_platform.evaluation.engines.embedding_similarity` | `src\ai_eval_platform\evaluation\engines\embedding_similarity.py` |
| `src\ai_eval_platform\evaluation\evaluators\semantic_similarity.py` | `ai_eval_platform.evaluation.evaluator` | `src\ai_eval_platform\evaluation\evaluator.py` |
| `src\ai_eval_platform\evaluation\evaluators\tool_usage.py` | `ai_eval_platform.evaluation.domain` | `src\ai_eval_platform\evaluation\domain.py` |
| `src\ai_eval_platform\evaluation\evaluators\tool_usage.py` | `ai_eval_platform.evaluation.evaluator` | `src\ai_eval_platform\evaluation\evaluator.py` |
| `src\ai_eval_platform\evaluation\result_mapper.py` | `ai_eval_platform.evaluation.domain` | `src\ai_eval_platform\evaluation\domain.py` |
| `src\ai_eval_platform\evaluation\result_mapper.py` | `ai_eval_platform.storage.models` | `src\ai_eval_platform\storage\models.py` |
| `src\ai_eval_platform\evaluation\runner.py` | `ai_eval_platform.evaluation.domain` | `src\ai_eval_platform\evaluation\domain.py` |
| `src\ai_eval_platform\evaluation\runner.py` | `ai_eval_platform.evaluation.errors` | `src\ai_eval_platform\evaluation\errors.py` |
| `src\ai_eval_platform\evaluation\runner.py` | `ai_eval_platform.evaluation.evaluator` | `src\ai_eval_platform\evaluation\evaluator.py` |
| `src\ai_eval_platform\evaluation\runner.py` | `ai_eval_platform.evaluation.target_validator` | `src\ai_eval_platform\evaluation\target_validator.py` |
| `src\ai_eval_platform\evaluation\service.py` | `ai_eval_platform.evaluation.domain` | `src\ai_eval_platform\evaluation\domain.py` |
| `src\ai_eval_platform\evaluation\service.py` | `ai_eval_platform.evaluation.errors` | `src\ai_eval_platform\evaluation\errors.py` |
| `src\ai_eval_platform\evaluation\service.py` | `ai_eval_platform.evaluation.evaluator` | `src\ai_eval_platform\evaluation\evaluator.py` |
| `src\ai_eval_platform\evaluation\service.py` | `ai_eval_platform.evaluation.result_mapper` | `src\ai_eval_platform\evaluation\result_mapper.py` |
| `src\ai_eval_platform\evaluation\service.py` | `ai_eval_platform.evaluation.runner` | `src\ai_eval_platform\evaluation\runner.py` |
| `src\ai_eval_platform\evaluation\service.py` | `ai_eval_platform.evaluation.target_builder` | `src\ai_eval_platform\evaluation\target_builder.py` |
| `src\ai_eval_platform\evaluation\service.py` | `ai_eval_platform.storage.models` | `src\ai_eval_platform\storage\models.py` |
| `src\ai_eval_platform\evaluation\service.py` | `ai_eval_platform.storage.repositories` | `src\ai_eval_platform\storage\repositories.py` |
| `src\ai_eval_platform\evaluation\target_builder.py` | `ai_eval_platform.evaluation.domain` | `src\ai_eval_platform\evaluation\domain.py` |
| `src\ai_eval_platform\evaluation\target_builder.py` | `ai_eval_platform.storage.models` | `src\ai_eval_platform\storage\models.py` |
| `src\ai_eval_platform\evaluation\target_builder.py` | `schemas.events.common` | `schemas\events\common.py` |
| `src\ai_eval_platform\evaluation\target_validator.py` | `ai_eval_platform.evaluation.domain` | `src\ai_eval_platform\evaluation\domain.py` |
| `src\ai_eval_platform\evaluation\target_validator.py` | `ai_eval_platform.evaluation.errors` | `src\ai_eval_platform\evaluation\errors.py` |
| `src\ai_eval_platform\infrastructure\kafka\__init__.py` | `ai_eval_platform.infrastructure.kafka.config` | `src\ai_eval_platform\infrastructure\kafka\config.py` |
| `src\ai_eval_platform\infrastructure\kafka\__init__.py` | `ai_eval_platform.infrastructure.kafka.consumer` | `src\ai_eval_platform\infrastructure\kafka\consumer.py` |
| `src\ai_eval_platform\infrastructure\kafka\__init__.py` | `ai_eval_platform.infrastructure.kafka.errors` | `src\ai_eval_platform\infrastructure\kafka\errors.py` |
| `src\ai_eval_platform\infrastructure\kafka\__init__.py` | `ai_eval_platform.infrastructure.kafka.producer` | `src\ai_eval_platform\infrastructure\kafka\producer.py` |
| `src\ai_eval_platform\infrastructure\kafka\consumer.py` | `ai_eval_platform.infrastructure.kafka.config` | `src\ai_eval_platform\infrastructure\kafka\config.py` |
| `src\ai_eval_platform\infrastructure\kafka\consumer.py` | `ai_eval_platform.infrastructure.kafka.errors` | `src\ai_eval_platform\infrastructure\kafka\errors.py` |
| `src\ai_eval_platform\infrastructure\kafka\consumer.py` | `ai_eval_platform.ingestion.kafka_record` | `src\ai_eval_platform\ingestion\kafka_record.py` |
| `src\ai_eval_platform\infrastructure\kafka\consumer.py` | `ai_eval_platform.ingestion.producer` | `src\ai_eval_platform\ingestion\producer.py` |
| `src\ai_eval_platform\infrastructure\kafka\producer.py` | `ai_eval_platform.infrastructure.kafka.config` | `src\ai_eval_platform\infrastructure\kafka\config.py` |
| `src\ai_eval_platform\infrastructure\kafka\producer.py` | `ai_eval_platform.infrastructure.kafka.errors` | `src\ai_eval_platform\infrastructure\kafka\errors.py` |
| `src\ai_eval_platform\infrastructure\kafka\producer.py` | `ai_eval_platform.ingestion.producer` | `src\ai_eval_platform\ingestion\producer.py` |
| `src\ai_eval_platform\ingestion\delivery_classifier.py` | `ai_eval_platform.ingestion.event_router` | `src\ai_eval_platform\ingestion\event_router.py` |
| `src\ai_eval_platform\ingestion\delivery_classifier.py` | `ai_eval_platform.ingestion.handler` | `src\ai_eval_platform\ingestion\handler.py` |
| `src\ai_eval_platform\ingestion\delivery_classifier.py` | `ai_eval_platform.ingestion.producer` | `src\ai_eval_platform\ingestion\producer.py` |
| `src\ai_eval_platform\ingestion\dlq.py` | `ai_eval_platform.ingestion.event_validator` | `src\ai_eval_platform\ingestion\event_validator.py` |
| `src\ai_eval_platform\ingestion\dlq.py` | `ai_eval_platform.ingestion.kafka_record` | `src\ai_eval_platform\ingestion\kafka_record.py` |
| `src\ai_eval_platform\ingestion\dlq.py` | `ai_eval_platform.ingestion.producer` | `src\ai_eval_platform\ingestion\producer.py` |
| `src\ai_eval_platform\ingestion\dlq.py` | `schemas.events.common` | `schemas\events\common.py` |
| `src\ai_eval_platform\ingestion\event_router.py` | `schemas.events.common` | `schemas\events\common.py` |
| `src\ai_eval_platform\ingestion\event_validator.py` | `schemas.events.agent_behavior_events` | `schemas\events\agent_behavior_events.py` |
| `src\ai_eval_platform\ingestion\event_validator.py` | `schemas.events.common` | `schemas\events\common.py` |
| `src\ai_eval_platform\ingestion\event_validator.py` | `schemas.events.context_events` | `schemas\events\context_events.py` |
| `src\ai_eval_platform\ingestion\event_validator.py` | `schemas.events.evaluation_events` | `schemas\events\evaluation_events.py` |
| `src\ai_eval_platform\ingestion\event_validator.py` | `schemas.events.execution_events` | `schemas\events\execution_events.py` |
| `src\ai_eval_platform\ingestion\event_validator.py` | `schemas.events.policy_events` | `schemas\events\policy_events.py` |
| `src\ai_eval_platform\ingestion\handler.py` | `schemas.events.common` | `schemas\events\common.py` |
| `src\ai_eval_platform\ingestion\kafka_consumer.py` | `ai_eval_platform.ingestion.delivery_classifier` | `src\ai_eval_platform\ingestion\delivery_classifier.py` |
| `src\ai_eval_platform\ingestion\kafka_consumer.py` | `ai_eval_platform.ingestion.dlq` | `src\ai_eval_platform\ingestion\dlq.py` |
| `src\ai_eval_platform\ingestion\kafka_consumer.py` | `ai_eval_platform.ingestion.event_router` | `src\ai_eval_platform\ingestion\event_router.py` |
| `src\ai_eval_platform\ingestion\kafka_consumer.py` | `ai_eval_platform.ingestion.event_validator` | `src\ai_eval_platform\ingestion\event_validator.py` |
| `src\ai_eval_platform\ingestion\kafka_consumer.py` | `ai_eval_platform.ingestion.handler` | `src\ai_eval_platform\ingestion\handler.py` |
| `src\ai_eval_platform\ingestion\kafka_consumer.py` | `ai_eval_platform.ingestion.kafka_record` | `src\ai_eval_platform\ingestion\kafka_record.py` |
| `src\ai_eval_platform\ingestion\kafka_consumer.py` | `ai_eval_platform.ingestion.producer` | `src\ai_eval_platform\ingestion\producer.py` |
| `src\ai_eval_platform\ingestion\kafka_consumer.py` | `ai_eval_platform.ingestion.retry_policy` | `src\ai_eval_platform\ingestion\retry_policy.py` |
| `src\ai_eval_platform\ingestion\kafka_consumer.py` | `ai_eval_platform.ingestion.retry_publisher` | `src\ai_eval_platform\ingestion\retry_publisher.py` |
| `src\ai_eval_platform\ingestion\kafka_consumer.py` | `schemas.events.common` | `schemas\events\common.py` |
| `src\ai_eval_platform\ingestion\retry_consumer.py` | `ai_eval_platform.ingestion.kafka_record` | `src\ai_eval_platform\ingestion\kafka_record.py` |
| `src\ai_eval_platform\ingestion\retry_consumer.py` | `ai_eval_platform.ingestion.producer` | `src\ai_eval_platform\ingestion\producer.py` |
| `src\ai_eval_platform\ingestion\retry_consumer.py` | `ai_eval_platform.ingestion.retry_dispatcher` | `src\ai_eval_platform\ingestion\retry_dispatcher.py` |
| `src\ai_eval_platform\ingestion\retry_dispatcher.py` | `ai_eval_platform.ingestion.kafka_record` | `src\ai_eval_platform\ingestion\kafka_record.py` |
| `src\ai_eval_platform\ingestion\retry_dispatcher.py` | `ai_eval_platform.ingestion.producer` | `src\ai_eval_platform\ingestion\producer.py` |
| `src\ai_eval_platform\ingestion\retry_dispatcher.py` | `ai_eval_platform.ingestion.retry_publisher` | `src\ai_eval_platform\ingestion\retry_publisher.py` |
| `src\ai_eval_platform\ingestion\retry_publisher.py` | `ai_eval_platform.ingestion.kafka_record` | `src\ai_eval_platform\ingestion\kafka_record.py` |
| `src\ai_eval_platform\ingestion\retry_publisher.py` | `ai_eval_platform.ingestion.producer` | `src\ai_eval_platform\ingestion\producer.py` |
| `src\ai_eval_platform\monitoring\__init__.py` | `ai_eval_platform.monitoring.detectors.domain` | `src\ai_eval_platform\monitoring\detectors\domain\__init__.py` |
| `src\ai_eval_platform\monitoring\adapters\__init__.py` | `ai_eval_platform.monitoring.adapters.records` | `src\ai_eval_platform\monitoring\adapters\records.py` |
| `src\ai_eval_platform\monitoring\adapters\__init__.py` | `ai_eval_platform.monitoring.adapters.run_adapters` | `src\ai_eval_platform\monitoring\adapters\run_adapters.py` |
| `src\ai_eval_platform\monitoring\adapters\run_adapters.py` | `ai_eval_platform.monitoring.adapters.records` | `src\ai_eval_platform\monitoring\adapters\records.py` |
| `src\ai_eval_platform\monitoring\adapters\run_adapters.py` | `ai_eval_platform.monitoring.detectors.inputs` | `src\ai_eval_platform\monitoring\detectors\inputs.py` |
| `src\ai_eval_platform\monitoring\detectors\__init__.py` | `ai_eval_platform.monitoring.detectors.strategies` | `src\ai_eval_platform\monitoring\detectors\strategies\__init__.py` |
| `src\ai_eval_platform\monitoring\detectors\base.py` | `ai_eval_platform.monitoring.signals` | `src\ai_eval_platform\monitoring\signals\__init__.py` |
| `src\ai_eval_platform\monitoring\detectors\domain\__init__.py` | `ai_eval_platform.monitoring.detectors.domain.confidence` | `src\ai_eval_platform\monitoring\detectors\domain\confidence.py` |
| `src\ai_eval_platform\monitoring\detectors\domain\__init__.py` | `ai_eval_platform.monitoring.detectors.domain.route` | `src\ai_eval_platform\monitoring\detectors\domain\route.py` |
| `src\ai_eval_platform\monitoring\detectors\domain\__init__.py` | `ai_eval_platform.monitoring.detectors.domain.tool_usage` | `src\ai_eval_platform\monitoring\detectors\domain\tool_usage.py` |
| `src\ai_eval_platform\monitoring\detectors\domain\confidence.py` | `ai_eval_platform.monitoring.detectors.base` | `src\ai_eval_platform\monitoring\detectors\base.py` |
| `src\ai_eval_platform\monitoring\detectors\domain\confidence.py` | `ai_eval_platform.monitoring.detectors.inputs` | `src\ai_eval_platform\monitoring\detectors\inputs.py` |
| `src\ai_eval_platform\monitoring\detectors\domain\confidence.py` | `ai_eval_platform.monitoring.signals` | `src\ai_eval_platform\monitoring\signals\__init__.py` |
| `src\ai_eval_platform\monitoring\detectors\domain\route.py` | `ai_eval_platform.monitoring.detectors.base` | `src\ai_eval_platform\monitoring\detectors\base.py` |
| `src\ai_eval_platform\monitoring\detectors\domain\route.py` | `ai_eval_platform.monitoring.detectors.inputs` | `src\ai_eval_platform\monitoring\detectors\inputs.py` |
| `src\ai_eval_platform\monitoring\detectors\domain\route.py` | `ai_eval_platform.monitoring.signals` | `src\ai_eval_platform\monitoring\signals\__init__.py` |
| `src\ai_eval_platform\monitoring\detectors\domain\tool_usage.py` | `ai_eval_platform.monitoring.detectors.base` | `src\ai_eval_platform\monitoring\detectors\base.py` |
| `src\ai_eval_platform\monitoring\detectors\domain\tool_usage.py` | `ai_eval_platform.monitoring.detectors.inputs` | `src\ai_eval_platform\monitoring\detectors\inputs.py` |
| `src\ai_eval_platform\monitoring\detectors\domain\tool_usage.py` | `ai_eval_platform.monitoring.detectors.strategies.drift` | `src\ai_eval_platform\monitoring\detectors\strategies\drift.py` |
| `src\ai_eval_platform\monitoring\detectors\domain\tool_usage.py` | `ai_eval_platform.monitoring.signals` | `src\ai_eval_platform\monitoring\signals\__init__.py` |
| `src\ai_eval_platform\monitoring\detectors\strategies\__init__.py` | `ai_eval_platform.monitoring.detectors.strategies.anomaly` | `src\ai_eval_platform\monitoring\detectors\strategies\anomaly.py` |
| `src\ai_eval_platform\monitoring\detectors\strategies\__init__.py` | `ai_eval_platform.monitoring.detectors.strategies.drift` | `src\ai_eval_platform\monitoring\detectors\strategies\drift.py` |
| `src\ai_eval_platform\monitoring\detectors\strategies\__init__.py` | `ai_eval_platform.monitoring.detectors.strategies.pattern` | `src\ai_eval_platform\monitoring\detectors\strategies\pattern.py` |
| `src\ai_eval_platform\monitoring\detectors\strategies\anomaly.py` | `ai_eval_platform.monitoring.detectors.base` | `src\ai_eval_platform\monitoring\detectors\base.py` |
| `src\ai_eval_platform\monitoring\detectors\strategies\anomaly.py` | `ai_eval_platform.monitoring.detectors.inputs` | `src\ai_eval_platform\monitoring\detectors\inputs.py` |
| `src\ai_eval_platform\monitoring\detectors\strategies\anomaly.py` | `ai_eval_platform.monitoring.signals` | `src\ai_eval_platform\monitoring\signals\__init__.py` |
| `src\ai_eval_platform\monitoring\detectors\strategies\drift.py` | `ai_eval_platform.monitoring.detectors.base` | `src\ai_eval_platform\monitoring\detectors\base.py` |
| `src\ai_eval_platform\monitoring\detectors\strategies\drift.py` | `ai_eval_platform.monitoring.detectors.inputs` | `src\ai_eval_platform\monitoring\detectors\inputs.py` |
| `src\ai_eval_platform\monitoring\detectors\strategies\drift.py` | `ai_eval_platform.monitoring.signals` | `src\ai_eval_platform\monitoring\signals\__init__.py` |
| `src\ai_eval_platform\monitoring\detectors\strategies\pattern.py` | `ai_eval_platform.monitoring.detectors.base` | `src\ai_eval_platform\monitoring\detectors\base.py` |
| `src\ai_eval_platform\monitoring\detectors\strategies\pattern.py` | `ai_eval_platform.monitoring.detectors.inputs` | `src\ai_eval_platform\monitoring\detectors\inputs.py` |
| `src\ai_eval_platform\monitoring\detectors\strategies\pattern.py` | `ai_eval_platform.monitoring.signals` | `src\ai_eval_platform\monitoring\signals\__init__.py` |
| `src\ai_eval_platform\monitoring\orchestration\__init__.py` | `ai_eval_platform.monitoring.orchestration.runner` | `src\ai_eval_platform\monitoring\orchestration\runner.py` |
| `src\ai_eval_platform\monitoring\orchestration\runner.py` | `ai_eval_platform.monitoring.detectors.base` | `src\ai_eval_platform\monitoring\detectors\base.py` |
| `src\ai_eval_platform\monitoring\orchestration\runner.py` | `ai_eval_platform.monitoring.signals` | `src\ai_eval_platform\monitoring\signals\__init__.py` |
| `src\ai_eval_platform\monitoring\signals\__init__.py` | `ai_eval_platform.monitoring.signals.models` | `src\ai_eval_platform\monitoring\signals\models.py` |
| `src\ai_eval_platform\monitoring\signals\__init__.py` | `ai_eval_platform.monitoring.signals.types` | `src\ai_eval_platform\monitoring\signals\types.py` |
| `src\ai_eval_platform\monitoring\signals\models.py` | `ai_eval_platform.monitoring.signals.types` | `src\ai_eval_platform\monitoring\signals\types.py` |
| `src\ai_eval_platform\monitoring_integration.py` | `ai_eval_platform.monitoring.adapters` | `src\ai_eval_platform\monitoring\adapters\__init__.py` |
| `src\ai_eval_platform\monitoring_integration.py` | `ai_eval_platform.monitoring.detectors.base` | `src\ai_eval_platform\monitoring\detectors\base.py` |
| `src\ai_eval_platform\monitoring_integration.py` | `ai_eval_platform.monitoring.detectors.domain` | `src\ai_eval_platform\monitoring\detectors\domain\__init__.py` |
| `src\ai_eval_platform\monitoring_integration.py` | `ai_eval_platform.monitoring.detectors.strategies` | `src\ai_eval_platform\monitoring\detectors\strategies\__init__.py` |
| `src\ai_eval_platform\monitoring_integration.py` | `ai_eval_platform.monitoring.orchestration` | `src\ai_eval_platform\monitoring\orchestration\__init__.py` |
| `src\ai_eval_platform\monitoring_integration.py` | `ai_eval_platform.monitoring.signals.models` | `src\ai_eval_platform\monitoring\signals\models.py` |
| `src\ai_eval_platform\monitoring_integration.py` | `ai_eval_platform.storage.models` | `src\ai_eval_platform\storage\models.py` |
| `src\ai_eval_platform\monitoring_integration.py` | `ai_eval_platform.storage.repositories` | `src\ai_eval_platform\storage\repositories.py` |
| `src\ai_eval_platform\monitoring_integration.py` | `schemas.events.common` | `schemas\events\common.py` |
| `src\ai_eval_platform\storage\postgres.py` | `ai_eval_platform.storage.models` | `src\ai_eval_platform\storage\models.py` |
| `src\ai_eval_platform\storage\repositories.py` | `ai_eval_platform.storage.models` | `src\ai_eval_platform\storage\models.py` |
| `tests\contract\test_event_schema.py` | `schemas.events.common` | `schemas\events\common.py` |
| `tests\contract\test_event_schema.py` | `schemas.events.execution_events` | `schemas\events\execution_events.py` |
| `tests\contract\test_event_type_locking.py` | `schemas.events.agent_behavior_events` | `schemas\events\agent_behavior_events.py` |
| `tests\contract\test_event_type_locking.py` | `schemas.events.common` | `schemas\events\common.py` |
| `tests\contract\test_event_type_locking.py` | `schemas.events.context_events` | `schemas\events\context_events.py` |
| `tests\contract\test_event_type_locking.py` | `schemas.events.evaluation_events` | `schemas\events\evaluation_events.py` |
| `tests\contract\test_event_type_locking.py` | `schemas.events.execution_events` | `schemas\events\execution_events.py` |
| `tests\contract\test_event_type_locking.py` | `schemas.events.policy_events` | `schemas\events\policy_events.py` |
| `tests\evaluation\conftest.py` | `ai_eval_platform.storage.models` | `src\ai_eval_platform\storage\models.py` |
| `tests\evaluation\conftest.py` | `schemas.events.common` | `schemas\events\common.py` |
| `tests\evaluation\test_evaluation_layer.py` | `ai_eval_platform.evaluation.domain` | `src\ai_eval_platform\evaluation\domain.py` |
| `tests\evaluation\test_evaluation_layer.py` | `ai_eval_platform.evaluation.errors` | `src\ai_eval_platform\evaluation\errors.py` |
| `tests\evaluation\test_evaluation_layer.py` | `ai_eval_platform.evaluation.evaluator` | `src\ai_eval_platform\evaluation\evaluator.py` |
| `tests\evaluation\test_evaluation_layer.py` | `ai_eval_platform.evaluation.evaluators.citation` | `src\ai_eval_platform\evaluation\evaluators\citation.py` |
| `tests\evaluation\test_evaluation_layer.py` | `ai_eval_platform.evaluation.evaluators.exact_match` | `src\ai_eval_platform\evaluation\evaluators\exact_match.py` |
| `tests\evaluation\test_evaluation_layer.py` | `ai_eval_platform.evaluation.evaluators.groundedness` | `src\ai_eval_platform\evaluation\evaluators\groundedness.py` |
| `tests\evaluation\test_evaluation_layer.py` | `ai_eval_platform.evaluation.evaluators.latency` | `src\ai_eval_platform\evaluation\evaluators\latency.py` |
| `tests\evaluation\test_evaluation_layer.py` | `ai_eval_platform.evaluation.evaluators.retrieval` | `src\ai_eval_platform\evaluation\evaluators\retrieval.py` |
| `tests\evaluation\test_evaluation_layer.py` | `ai_eval_platform.evaluation.evaluators.semantic_similarity` | `src\ai_eval_platform\evaluation\evaluators\semantic_similarity.py` |
| `tests\evaluation\test_evaluation_layer.py` | `ai_eval_platform.evaluation.evaluators.tool_usage` | `src\ai_eval_platform\evaluation\evaluators\tool_usage.py` |
| `tests\evaluation\test_evaluation_layer.py` | `ai_eval_platform.evaluation.runner` | `src\ai_eval_platform\evaluation\runner.py` |
| `tests\evaluation\test_evaluation_layer.py` | `ai_eval_platform.evaluation.target_validator` | `src\ai_eval_platform\evaluation\target_validator.py` |
| `tests\evaluation\test_evaluation_service.py` | `ai_eval_platform.evaluation.domain` | `src\ai_eval_platform\evaluation\domain.py` |
| `tests\evaluation\test_evaluation_service.py` | `ai_eval_platform.evaluation.errors` | `src\ai_eval_platform\evaluation\errors.py` |
| `tests\evaluation\test_evaluation_service.py` | `ai_eval_platform.evaluation.evaluator` | `src\ai_eval_platform\evaluation\evaluator.py` |
| `tests\evaluation\test_evaluation_service.py` | `ai_eval_platform.evaluation.evaluators.exact_match` | `src\ai_eval_platform\evaluation\evaluators\exact_match.py` |
| `tests\evaluation\test_evaluation_service.py` | `ai_eval_platform.evaluation.evaluators.latency` | `src\ai_eval_platform\evaluation\evaluators\latency.py` |
| `tests\evaluation\test_evaluation_service.py` | `ai_eval_platform.evaluation.service` | `src\ai_eval_platform\evaluation\service.py` |
| `tests\evaluation\test_evaluation_service.py` | `ai_eval_platform.storage.models` | `src\ai_eval_platform\storage\models.py` |
| `tests\evaluation\test_evaluation_service.py` | `ai_eval_platform.storage.repositories` | `src\ai_eval_platform\storage\repositories.py` |
| `tests\evaluation\test_result_mapper.py` | `ai_eval_platform.evaluation.domain` | `src\ai_eval_platform\evaluation\domain.py` |
| `tests\evaluation\test_result_mapper.py` | `ai_eval_platform.evaluation.result_mapper` | `src\ai_eval_platform\evaluation\result_mapper.py` |
| `tests\evaluation\test_target_builder.py` | `ai_eval_platform.evaluation.domain` | `src\ai_eval_platform\evaluation\domain.py` |
| `tests\evaluation\test_target_builder.py` | `ai_eval_platform.evaluation.target_builder` | `src\ai_eval_platform\evaluation\target_builder.py` |
| `tests\evaluation\test_target_builder.py` | `ai_eval_platform.evaluation.target_validator` | `src\ai_eval_platform\evaluation\target_validator.py` |
| `tests\evaluation\test_target_builder.py` | `ai_eval_platform.storage.models` | `src\ai_eval_platform\storage\models.py` |
| `tests\evaluation\test_target_builder.py` | `schemas.events.common` | `schemas\events\common.py` |
| `tests\ingestion\conftest.py` | `ai_eval_platform.ingestion.dlq` | `src\ai_eval_platform\ingestion\dlq.py` |
| `tests\ingestion\conftest.py` | `ai_eval_platform.ingestion.kafka_record` | `src\ai_eval_platform\ingestion\kafka_record.py` |
| `tests\ingestion\conftest.py` | `ai_eval_platform.ingestion.producer` | `src\ai_eval_platform\ingestion\producer.py` |
| `tests\ingestion\conftest.py` | `ai_eval_platform.ingestion.retry_publisher` | `src\ai_eval_platform\ingestion\retry_publisher.py` |
| `tests\ingestion\test_architecture.py` | `ai_eval_platform.infrastructure.kafka.config` | `src\ai_eval_platform\infrastructure\kafka\config.py` |
| `tests\ingestion\test_consumer_offsets.py` | `ai_eval_platform.ingestion.delivery_classifier` | `src\ai_eval_platform\ingestion\delivery_classifier.py` |
| `tests\ingestion\test_consumer_offsets.py` | `ai_eval_platform.ingestion.dlq` | `src\ai_eval_platform\ingestion\dlq.py` |
| `tests\ingestion\test_consumer_offsets.py` | `ai_eval_platform.ingestion.event_validator` | `src\ai_eval_platform\ingestion\event_validator.py` |
| `tests\ingestion\test_consumer_offsets.py` | `ai_eval_platform.ingestion.handler` | `src\ai_eval_platform\ingestion\handler.py` |
| `tests\ingestion\test_consumer_offsets.py` | `ai_eval_platform.ingestion.kafka_consumer` | `src\ai_eval_platform\ingestion\kafka_consumer.py` |
| `tests\ingestion\test_consumer_offsets.py` | `ai_eval_platform.ingestion.producer` | `src\ai_eval_platform\ingestion\producer.py` |
| `tests\ingestion\test_consumer_offsets.py` | `ai_eval_platform.ingestion.retry_policy` | `src\ai_eval_platform\ingestion\retry_policy.py` |
| `tests\ingestion\test_consumer_offsets.py` | `ai_eval_platform.ingestion.retry_publisher` | `src\ai_eval_platform\ingestion\retry_publisher.py` |
| `tests\ingestion\test_consumer_offsets.py` | `tests.ingestion.conftest` | `tests\ingestion\conftest.py` |
| `tests\ingestion\test_dlq.py` | `ai_eval_platform.ingestion.dlq` | `src\ai_eval_platform\ingestion\dlq.py` |
| `tests\ingestion\test_dlq.py` | `ai_eval_platform.ingestion.event_validator` | `src\ai_eval_platform\ingestion\event_validator.py` |
| `tests\ingestion\test_dlq.py` | `ai_eval_platform.ingestion.kafka_record` | `src\ai_eval_platform\ingestion\kafka_record.py` |
| `tests\ingestion\test_dlq.py` | `ai_eval_platform.ingestion.producer` | `src\ai_eval_platform\ingestion\producer.py` |
| `tests\ingestion\test_dlq.py` | `schemas.events.common` | `schemas\events\common.py` |
| `tests\ingestion\test_dlq.py` | `tests.ingestion.conftest` | `tests\ingestion\conftest.py` |
| `tests\ingestion\test_ingestion_db.py` | `ai_eval_platform.ingestion.delivery_classifier` | `src\ai_eval_platform\ingestion\delivery_classifier.py` |
| `tests\ingestion\test_ingestion_db.py` | `ai_eval_platform.ingestion.dlq` | `src\ai_eval_platform\ingestion\dlq.py` |
| `tests\ingestion\test_ingestion_db.py` | `ai_eval_platform.ingestion.event_router` | `src\ai_eval_platform\ingestion\event_router.py` |
| `tests\ingestion\test_ingestion_db.py` | `ai_eval_platform.ingestion.event_validator` | `src\ai_eval_platform\ingestion\event_validator.py` |
| `tests\ingestion\test_ingestion_db.py` | `ai_eval_platform.ingestion.handler` | `src\ai_eval_platform\ingestion\handler.py` |
| `tests\ingestion\test_ingestion_db.py` | `ai_eval_platform.ingestion.kafka_consumer` | `src\ai_eval_platform\ingestion\kafka_consumer.py` |
| `tests\ingestion\test_ingestion_db.py` | `ai_eval_platform.ingestion.producer` | `src\ai_eval_platform\ingestion\producer.py` |
| `tests\ingestion\test_ingestion_db.py` | `ai_eval_platform.ingestion.retry_policy` | `src\ai_eval_platform\ingestion\retry_policy.py` |
| `tests\ingestion\test_ingestion_db.py` | `ai_eval_platform.ingestion.retry_publisher` | `src\ai_eval_platform\ingestion\retry_publisher.py` |
| `tests\ingestion\test_ingestion_db.py` | `ai_eval_platform.storage.models` | `src\ai_eval_platform\storage\models.py` |
| `tests\ingestion\test_ingestion_db.py` | `ai_eval_platform.storage.repositories` | `src\ai_eval_platform\storage\repositories.py` |
| `tests\ingestion\test_ingestion_db.py` | `tests.ingestion.conftest` | `tests\ingestion\conftest.py` |
| `tests\ingestion\test_kafka_config.py` | `ai_eval_platform.infrastructure.kafka.config` | `src\ai_eval_platform\infrastructure\kafka\config.py` |
| `tests\ingestion\test_ordering.py` | `ai_eval_platform.ingestion.dlq` | `src\ai_eval_platform\ingestion\dlq.py` |
| `tests\ingestion\test_ordering.py` | `ai_eval_platform.ingestion.event_validator` | `src\ai_eval_platform\ingestion\event_validator.py` |
| `tests\ingestion\test_ordering.py` | `ai_eval_platform.ingestion.kafka_consumer` | `src\ai_eval_platform\ingestion\kafka_consumer.py` |
| `tests\ingestion\test_ordering.py` | `ai_eval_platform.ingestion.kafka_record` | `src\ai_eval_platform\ingestion\kafka_record.py` |
| `tests\ingestion\test_ordering.py` | `ai_eval_platform.ingestion.retry_publisher` | `src\ai_eval_platform\ingestion\retry_publisher.py` |
| `tests\ingestion\test_ordering.py` | `tests.ingestion.conftest` | `tests\ingestion\conftest.py` |
| `tests\ingestion\test_ordering.py` | `tests.ingestion.test_retry_consumer` | `tests\ingestion\test_retry_consumer.py` |
| `tests\ingestion\test_retry.py` | `ai_eval_platform.ingestion.producer` | `src\ai_eval_platform\ingestion\producer.py` |
| `tests\ingestion\test_retry.py` | `ai_eval_platform.ingestion.retry_policy` | `src\ai_eval_platform\ingestion\retry_policy.py` |
| `tests\ingestion\test_retry.py` | `ai_eval_platform.ingestion.retry_publisher` | `src\ai_eval_platform\ingestion\retry_publisher.py` |
| `tests\ingestion\test_retry.py` | `tests.ingestion.conftest` | `tests\ingestion\conftest.py` |
| `tests\ingestion\test_retry_consumer.py` | `ai_eval_platform.ingestion.kafka_record` | `src\ai_eval_platform\ingestion\kafka_record.py` |
| `tests\ingestion\test_retry_consumer.py` | `ai_eval_platform.ingestion.producer` | `src\ai_eval_platform\ingestion\producer.py` |
| `tests\ingestion\test_retry_consumer.py` | `ai_eval_platform.ingestion.retry_consumer` | `src\ai_eval_platform\ingestion\retry_consumer.py` |
| `tests\ingestion\test_retry_consumer.py` | `ai_eval_platform.ingestion.retry_dispatcher` | `src\ai_eval_platform\ingestion\retry_dispatcher.py` |
| `tests\ingestion\test_retry_consumer.py` | `ai_eval_platform.ingestion.retry_publisher` | `src\ai_eval_platform\ingestion\retry_publisher.py` |
| `tests\ingestion\test_retry_consumer.py` | `tests.ingestion.conftest` | `tests\ingestion\conftest.py` |
| `tests\ingestion\test_retry_crash_windows.py` | `ai_eval_platform.ingestion.producer` | `src\ai_eval_platform\ingestion\producer.py` |
| `tests\ingestion\test_retry_crash_windows.py` | `ai_eval_platform.ingestion.retry_dispatcher` | `src\ai_eval_platform\ingestion\retry_dispatcher.py` |
| `tests\ingestion\test_retry_crash_windows.py` | `ai_eval_platform.ingestion.retry_publisher` | `src\ai_eval_platform\ingestion\retry_publisher.py` |
| `tests\ingestion\test_retry_crash_windows.py` | `tests.ingestion.conftest` | `tests\ingestion\conftest.py` |
| `tests\ingestion\test_retry_crash_windows.py` | `tests.ingestion.test_retry_consumer` | `tests\ingestion\test_retry_consumer.py` |
| `tests\ingestion\test_retry_lifecycle.py` | `ai_eval_platform.ingestion.delivery_classifier` | `src\ai_eval_platform\ingestion\delivery_classifier.py` |
| `tests\ingestion\test_retry_lifecycle.py` | `ai_eval_platform.ingestion.dlq` | `src\ai_eval_platform\ingestion\dlq.py` |
| `tests\ingestion\test_retry_lifecycle.py` | `ai_eval_platform.ingestion.event_router` | `src\ai_eval_platform\ingestion\event_router.py` |
| `tests\ingestion\test_retry_lifecycle.py` | `ai_eval_platform.ingestion.event_validator` | `src\ai_eval_platform\ingestion\event_validator.py` |
| `tests\ingestion\test_retry_lifecycle.py` | `ai_eval_platform.ingestion.handler` | `src\ai_eval_platform\ingestion\handler.py` |
| `tests\ingestion\test_retry_lifecycle.py` | `ai_eval_platform.ingestion.kafka_consumer` | `src\ai_eval_platform\ingestion\kafka_consumer.py` |
| `tests\ingestion\test_retry_lifecycle.py` | `ai_eval_platform.ingestion.kafka_record` | `src\ai_eval_platform\ingestion\kafka_record.py` |
| `tests\ingestion\test_retry_lifecycle.py` | `ai_eval_platform.ingestion.producer` | `src\ai_eval_platform\ingestion\producer.py` |
| `tests\ingestion\test_retry_lifecycle.py` | `ai_eval_platform.ingestion.retry_consumer` | `src\ai_eval_platform\ingestion\retry_consumer.py` |
| `tests\ingestion\test_retry_lifecycle.py` | `ai_eval_platform.ingestion.retry_dispatcher` | `src\ai_eval_platform\ingestion\retry_dispatcher.py` |
| `tests\ingestion\test_retry_lifecycle.py` | `ai_eval_platform.ingestion.retry_policy` | `src\ai_eval_platform\ingestion\retry_policy.py` |
| `tests\ingestion\test_retry_lifecycle.py` | `ai_eval_platform.ingestion.retry_publisher` | `src\ai_eval_platform\ingestion\retry_publisher.py` |
| `tests\ingestion\test_retry_lifecycle.py` | `ai_eval_platform.storage.models` | `src\ai_eval_platform\storage\models.py` |
| `tests\ingestion\test_retry_lifecycle.py` | `ai_eval_platform.storage.repositories` | `src\ai_eval_platform\storage\repositories.py` |
| `tests\ingestion\test_retry_lifecycle.py` | `tests.ingestion.conftest` | `tests\ingestion\conftest.py` |
| `tests\ingestion\test_retry_lifecycle.py` | `tests.ingestion.test_retry_consumer` | `tests\ingestion\test_retry_consumer.py` |
| `tests\ingestion\test_validator.py` | `ai_eval_platform.ingestion.dlq` | `src\ai_eval_platform\ingestion\dlq.py` |
| `tests\ingestion\test_validator.py` | `ai_eval_platform.ingestion.event_validator` | `src\ai_eval_platform\ingestion\event_validator.py` |
| `tests\ingestion\test_validator.py` | `tests.ingestion.conftest` | `tests\ingestion\conftest.py` |
| `tests\integration\test_postgres_pipeline.py` | `ai_eval_platform.ingestion.delivery_classifier` | `src\ai_eval_platform\ingestion\delivery_classifier.py` |
| `tests\integration\test_postgres_pipeline.py` | `ai_eval_platform.ingestion.dlq` | `src\ai_eval_platform\ingestion\dlq.py` |
| `tests\integration\test_postgres_pipeline.py` | `ai_eval_platform.ingestion.event_router` | `src\ai_eval_platform\ingestion\event_router.py` |
| `tests\integration\test_postgres_pipeline.py` | `ai_eval_platform.ingestion.event_validator` | `src\ai_eval_platform\ingestion\event_validator.py` |
| `tests\integration\test_postgres_pipeline.py` | `ai_eval_platform.ingestion.handler` | `src\ai_eval_platform\ingestion\handler.py` |
| `tests\integration\test_postgres_pipeline.py` | `ai_eval_platform.ingestion.kafka_consumer` | `src\ai_eval_platform\ingestion\kafka_consumer.py` |
| `tests\integration\test_postgres_pipeline.py` | `ai_eval_platform.ingestion.kafka_record` | `src\ai_eval_platform\ingestion\kafka_record.py` |
| `tests\integration\test_postgres_pipeline.py` | `ai_eval_platform.ingestion.producer` | `src\ai_eval_platform\ingestion\producer.py` |
| `tests\integration\test_postgres_pipeline.py` | `ai_eval_platform.ingestion.retry_policy` | `src\ai_eval_platform\ingestion\retry_policy.py` |
| `tests\integration\test_postgres_pipeline.py` | `ai_eval_platform.ingestion.retry_publisher` | `src\ai_eval_platform\ingestion\retry_publisher.py` |
| `tests\integration\test_postgres_pipeline.py` | `ai_eval_platform.storage.models` | `src\ai_eval_platform\storage\models.py` |
| `tests\integration\test_postgres_pipeline.py` | `ai_eval_platform.storage.postgres` | `src\ai_eval_platform\storage\postgres.py` |
| `tests\integration\test_postgres_pipeline.py` | `ai_eval_platform.storage.repositories` | `src\ai_eval_platform\storage\repositories.py` |
| `tests\integration\test_postgres_pipeline.py` | `tests.ingestion.conftest` | `tests\ingestion\conftest.py` |
| `tests\integration\test_real_kafka_integration.py` | `ai_eval_platform.infrastructure.kafka.config` | `src\ai_eval_platform\infrastructure\kafka\config.py` |
| `tests\integration\test_real_kafka_integration.py` | `ai_eval_platform.infrastructure.kafka.consumer` | `src\ai_eval_platform\infrastructure\kafka\consumer.py` |
| `tests\integration\test_real_kafka_integration.py` | `ai_eval_platform.infrastructure.kafka.producer` | `src\ai_eval_platform\infrastructure\kafka\producer.py` |
| `tests\integration\test_real_kafka_integration.py` | `ai_eval_platform.ingestion.dlq` | `src\ai_eval_platform\ingestion\dlq.py` |
| `tests\integration\test_real_kafka_integration.py` | `ai_eval_platform.ingestion.kafka_record` | `src\ai_eval_platform\ingestion\kafka_record.py` |
| `tests\integration\test_real_kafka_integration.py` | `ai_eval_platform.ingestion.producer` | `src\ai_eval_platform\ingestion\producer.py` |
| `tests\monitoring\__init__.py` | `ai_eval_platform.monitoring.detectors.domain` | `src\ai_eval_platform\monitoring\detectors\domain\__init__.py` |
| `tests\monitoring\test_contracts_a.py` | `ai_eval_platform.monitoring.detectors.domain` | `src\ai_eval_platform\monitoring\detectors\domain\__init__.py` |
| `tests\monitoring\test_contracts_a.py` | `ai_eval_platform.monitoring.detectors.inputs` | `src\ai_eval_platform\monitoring\detectors\inputs.py` |
| `tests\monitoring\test_contracts_a.py` | `ai_eval_platform.monitoring.detectors.strategies` | `src\ai_eval_platform\monitoring\detectors\strategies\__init__.py` |
| `tests\monitoring\test_contracts_a.py` | `ai_eval_platform.monitoring.signals.models` | `src\ai_eval_platform\monitoring\signals\models.py` |
| `tests\monitoring\test_contracts_a.py` | `ai_eval_platform.monitoring.signals.types` | `src\ai_eval_platform\monitoring\signals\types.py` |
| `tests\monitoring\test_contracts_b.py` | `ai_eval_platform.monitoring.detectors.domain` | `src\ai_eval_platform\monitoring\detectors\domain\__init__.py` |
| `tests\monitoring\test_contracts_b.py` | `ai_eval_platform.monitoring.detectors.inputs` | `src\ai_eval_platform\monitoring\detectors\inputs.py` |
| `tests\monitoring\test_contracts_b.py` | `ai_eval_platform.monitoring.detectors.strategies` | `src\ai_eval_platform\monitoring\detectors\strategies\__init__.py` |
| `tests\monitoring\test_contracts_b.py` | `ai_eval_platform.monitoring.signals.types` | `src\ai_eval_platform\monitoring\signals\types.py` |
| `tests\monitoring\test_contracts_b.py` | `tests.monitoring.test_contracts_a` | `tests\monitoring\test_contracts_a.py` |
| `tests\monitoring\test_contracts_c.py` | `ai_eval_platform.monitoring` | `src\ai_eval_platform\monitoring\__init__.py` |
| `tests\monitoring\test_contracts_c.py` | `ai_eval_platform.monitoring.detectors` | `src\ai_eval_platform\monitoring\detectors\__init__.py` |
| `tests\monitoring\test_contracts_c.py` | `ai_eval_platform.monitoring.detectors.domain` | `src\ai_eval_platform\monitoring\detectors\domain\__init__.py` |
| `tests\monitoring\test_contracts_c.py` | `ai_eval_platform.monitoring.detectors.strategies` | `src\ai_eval_platform\monitoring\detectors\strategies\__init__.py` |
| `tests\monitoring\test_contracts_c.py` | `ai_eval_platform.monitoring.signals` | `src\ai_eval_platform\monitoring\signals\__init__.py` |
| `tests\monitoring\test_contracts_c.py` | `ai_eval_platform.monitoring.signals.models` | `src\ai_eval_platform\monitoring\signals\models.py` |
| `tests\monitoring\test_contracts_c.py` | `ai_eval_platform.monitoring.signals.types` | `src\ai_eval_platform\monitoring\signals\types.py` |
| `tests\monitoring\test_contracts_c.py` | `tests.monitoring.test_contracts_a` | `tests\monitoring\test_contracts_a.py` |
| `tests\monitoring\test_contracts_d.py` | `ai_eval_platform.monitoring.detectors.base` | `src\ai_eval_platform\monitoring\detectors\base.py` |
| `tests\monitoring\test_contracts_d.py` | `ai_eval_platform.monitoring.detectors.domain` | `src\ai_eval_platform\monitoring\detectors\domain\__init__.py` |
| `tests\monitoring\test_contracts_d.py` | `ai_eval_platform.monitoring.detectors.strategies.drift` | `src\ai_eval_platform\monitoring\detectors\strategies\drift.py` |
| `tests\monitoring\test_contracts_d.py` | `ai_eval_platform.monitoring.signals.types` | `src\ai_eval_platform\monitoring\signals\types.py` |
| `tests\monitoring\test_integration_e.py` | `ai_eval_platform.monitoring.adapters` | `src\ai_eval_platform\monitoring\adapters\__init__.py` |
| `tests\monitoring\test_integration_e.py` | `ai_eval_platform.monitoring.adapters.records` | `src\ai_eval_platform\monitoring\adapters\records.py` |
| `tests\monitoring\test_integration_e.py` | `ai_eval_platform.monitoring.baselines` | `src\ai_eval_platform\monitoring\baselines\__init__.py` |
| `tests\monitoring\test_integration_e.py` | `ai_eval_platform.monitoring.detectors.strategies` | `src\ai_eval_platform\monitoring\detectors\strategies\__init__.py` |
| `tests\monitoring\test_integration_e.py` | `ai_eval_platform.monitoring.orchestration` | `src\ai_eval_platform\monitoring\orchestration\__init__.py` |
| `tests\monitoring\test_integration_e.py` | `ai_eval_platform.monitoring.persistence` | `src\ai_eval_platform\monitoring\persistence\__init__.py` |
| `tests\monitoring\test_integration_e.py` | `ai_eval_platform.monitoring.signals.types` | `src\ai_eval_platform\monitoring\signals\types.py` |
| `tests\monitoring\test_run_completion_f.py` | `ai_eval_platform.monitoring.adapters` | `src\ai_eval_platform\monitoring\adapters\__init__.py` |
| `tests\monitoring\test_run_completion_f.py` | `ai_eval_platform.monitoring.adapters.records` | `src\ai_eval_platform\monitoring\adapters\records.py` |
| `tests\monitoring\test_run_completion_f.py` | `ai_eval_platform.monitoring.adapters.run_adapters` | `src\ai_eval_platform\monitoring\adapters\run_adapters.py` |
| `tests\monitoring\test_run_completion_f.py` | `ai_eval_platform.monitoring.detectors.base` | `src\ai_eval_platform\monitoring\detectors\base.py` |
| `tests\monitoring\test_run_completion_f.py` | `ai_eval_platform.monitoring.detectors.domain` | `src\ai_eval_platform\monitoring\detectors\domain\__init__.py` |
| `tests\monitoring\test_run_completion_f.py` | `ai_eval_platform.monitoring.detectors.domain.confidence` | `src\ai_eval_platform\monitoring\detectors\domain\confidence.py` |
| `tests\monitoring\test_run_completion_f.py` | `ai_eval_platform.monitoring.detectors.inputs` | `src\ai_eval_platform\monitoring\detectors\inputs.py` |
| `tests\monitoring\test_run_completion_f.py` | `ai_eval_platform.monitoring.detectors.strategies` | `src\ai_eval_platform\monitoring\detectors\strategies\__init__.py` |
| `tests\monitoring\test_run_completion_f.py` | `ai_eval_platform.monitoring.detectors.strategies.pattern` | `src\ai_eval_platform\monitoring\detectors\strategies\pattern.py` |
| `tests\monitoring\test_run_completion_f.py` | `ai_eval_platform.monitoring.orchestration` | `src\ai_eval_platform\monitoring\orchestration\__init__.py` |
| `tests\monitoring\test_run_completion_f.py` | `ai_eval_platform.monitoring.orchestration.runner` | `src\ai_eval_platform\monitoring\orchestration\runner.py` |
| `tests\monitoring\test_run_completion_f.py` | `ai_eval_platform.monitoring.signals.models` | `src\ai_eval_platform\monitoring\signals\models.py` |
| `tests\monitoring\test_run_completion_f.py` | `ai_eval_platform.monitoring.signals.types` | `src\ai_eval_platform\monitoring\signals\types.py` |
| `tests\monitoring\test_run_completion_f.py` | `ai_eval_platform.monitoring_integration` | `src\ai_eval_platform\monitoring_integration.py` |
| `tests\monitoring\test_run_completion_f.py` | `ai_eval_platform.storage.models` | `src\ai_eval_platform\storage\models.py` |
| `tests\monitoring\test_run_completion_f.py` | `ai_eval_platform.storage.repositories` | `src\ai_eval_platform\storage\repositories.py` |
| `tests\monitoring\test_run_completion_f.py` | `schemas.events.common` | `schemas\events\common.py` |
| `tests\storage\conftest.py` | `ai_eval_platform.storage.models` | `src\ai_eval_platform\storage\models.py` |
| `tests\storage\test_models_and_tables.py` | `ai_eval_platform.storage` | `src\ai_eval_platform\storage\__init__.py` |
| `tests\storage\test_models_and_tables.py` | `ai_eval_platform.storage.models` | `src\ai_eval_platform\storage\models.py` |
| `tests\storage\test_postgres_integration.py` | `ai_eval_platform.storage.models` | `src\ai_eval_platform\storage\models.py` |
| `tests\storage\test_postgres_integration.py` | `ai_eval_platform.storage.repositories` | `src\ai_eval_platform\storage\repositories.py` |
| `tests\storage\test_repositories.py` | `ai_eval_platform.storage.models` | `src\ai_eval_platform\storage\models.py` |
| `tests\storage\test_repositories.py` | `ai_eval_platform.storage.repositories` | `src\ai_eval_platform\storage\repositories.py` |
| `tests\storage\test_repositories.py` | `schemas.events.common` | `schemas\events\common.py` |
| `tests\storage\test_repositories.py` | `schemas.events.execution_events` | `schemas\events\execution_events.py` |

## 4. Import cycles (unit level)

None detected among unit-level edges.

## 5. Third-party and declared dependencies

Declared in `pyproject.toml`:
- `pydantic>=2.0`
- `SQLAlchemy>=2.0`
- `psycopg[binary]>=3.1`
- `pytest>=8.0`
- `confluent-kafka>=2.3`
- `PyYAML>=6.0`

Observed as imports (by unit):
- `ai_eval_platform`: pydantic, sqlalchemy
- `ai_eval_platform.evaluation`: sqlalchemy
- `ai_eval_platform.infrastructure`: confluent_kafka, yaml
- `ai_eval_platform.ingestion`: pydantic, sqlalchemy
- `ai_eval_platform.monitoring`: pydantic
- `ai_eval_platform.storage`: sqlalchemy
- `schemas.events`: pydantic
- `tests.contract`: pydantic, pytest
- `tests.evaluation`: pytest, sqlalchemy
- `tests.ingestion`: pydantic, pytest, sqlalchemy
- `tests.integration`: pytest, sqlalchemy
- `tests.monitoring`: pydantic, pytest, sqlalchemy
- `tests.storage`: pytest, sqlalchemy

## 6. Runtime file references (non-import dependencies)

- `tests\ingestion\test_ordering.py` -> `src/ai_eval_platform/ingestion/kafka_consumer.py`
- `tests\ingestion\test_ordering.py` -> `src/ai_eval_platform/ingestion/retry_publisher.py`
- `tests\ingestion\test_ordering.py` -> `src/ai_eval_platform/storage/repositories.py`

## 7. Empty files (status `false`)

- `.env.example` | 0 | false
- `.github\workflows\ci.yml` | 0 | false
- `.github\workflows\deploy.yml` | 0 | false
- `.github\workflows\evaluation.yml` | 0 | false
- `.gitignore` | 0 | false
- `batch\daily_pipeline.py` | 0 | false
- `batch\data_quality.py` | 0 | false
- `batch\feature_pipeline.py` | 0 | false
- `batch\register_model.py` | 0 | false
- `batch\train_anomaly.py` | 0 | false
- `batch\update_baseline.py` | 0 | false
- `configs\evaluation.yaml` | 0 | false
- `configs\monitoring.yaml` | 0 | false
- `configs\platform.yaml` | 0 | false
- `configs\policies.yaml` | 0 | false
- `docker-compose.yml` | 0 | false
- `docker\Dockerfile.api` | 0 | false
- `docker\Dockerfile.batch` | 0 | false
- `docker\Dockerfile.consumer` | 0 | false
- `Makefile` | 0 | false
- `ml\anomaly\isolation_forest.py` | 0 | false
- `ml\drift\distribution_detector.py` | 0 | false
- `ml\route_prediction\route_model.py` | 0 | false
- `producers\agentic_rag\producer.py` | 0 | false
- `producers\gaia\producer.py` | 0 | false
- `schemas\versioning\schema_policy.py` | 0 | false
- `sdk\ai_eval_sdk\client.py` | 0 | false
- `sdk\ai_eval_sdk\emitter.py` | 0 | false
- `sdk\ai_eval_sdk\events.py` | 0 | false
- `src\ai_eval_platform\analysis\__init__.py` | 0 | false
- `src\ai_eval_platform\analysis\analyzer.py` | 0 | false
- `src\ai_eval_platform\analysis\context.py` | 0 | false
- `src\ai_eval_platform\analysis\findings.py` | 0 | false
- `src\ai_eval_platform\api\evaluations.py` | 0 | false
- `src\ai_eval_platform\api\health.py` | 0 | false
- `src\ai_eval_platform\api\main.py` | 0 | false
- `src\ai_eval_platform\api\models.py` | 0 | false
- `src\ai_eval_platform\api\runs.py` | 0 | false
- `src\ai_eval_platform\deployment\deployer.py` | 0 | false
- `src\ai_eval_platform\deployment\health.py` | 0 | false
- `src\ai_eval_platform\deployment\registry.py` | 0 | false
- `src\ai_eval_platform\deployment\rollback.py` | 0 | false
- `src\ai_eval_platform\evaluation\engines\__init__.py` | 0 | false
- `src\ai_eval_platform\evaluation\evaluators\__init__.py` | 0 | false
- `src\ai_eval_platform\features\aggregations.py` | 0 | false
- `src\ai_eval_platform\features\builder.py` | 0 | false
- `src\ai_eval_platform\features\feature_store.py` | 0 | false
- `src\ai_eval_platform\monitoring\alerts.py` | 0 | false
- `src\ai_eval_platform\monitoring\anomaly.py` | 0 | false
- `src\ai_eval_platform\monitoring\cost.py` | 0 | false
- `src\ai_eval_platform\monitoring\drift.py` | 0 | false
- `src\ai_eval_platform\monitoring\latency.py` | 0 | false
- `src\ai_eval_platform\monitoring\route_drift.py` | 0 | false
- `src\ai_eval_platform\monitoring\system\__init__.py` | 0 | false
- `src\ai_eval_platform\monitoring\system\metrics.py` | 0 | false
- `src\ai_eval_platform\monitoring\system\status.py` | 0 | false
- `src\ai_eval_platform\observability\logging.py` | 0 | false
- `src\ai_eval_platform\observability\metrics.py` | 0 | false
- `src\ai_eval_platform\observability\tracing.py` | 0 | false
- `src\ai_eval_platform\policy\decision.py` | 0 | false
- `src\ai_eval_platform\policy\engine.py` | 0 | false
- `src\ai_eval_platform\policy\thresholds.py` | 0 | false
- `src\ai_eval_platform\root_cause\__init__.py` | 0 | false
- `src\ai_eval_platform\root_cause\analyzer.py` | 0 | false
- `src\ai_eval_platform\root_cause\evidence.py` | 0 | false
- `src\ai_eval_platform\root_cause\findings.py` | 0 | false
- `src\ai_eval_platform\security\auth.py` | 0 | false
- `src\ai_eval_platform\security\authorization.py` | 0 | false
- `src\ai_eval_platform\security\pii.py` | 0 | false
- `src\ai_eval_platform\security\secrets.py` | 0 | false
- `src\ai_eval_platform\statistics\bootstrap.py` | 0 | false
- `src\ai_eval_platform\statistics\confidence.py` | 0 | false
- `src\ai_eval_platform\statistics\effect_size.py` | 0 | false
- `src\ai_eval_platform\statistics\hypothesis.py` | 0 | false
- `tests\ingestion\__init__.py` | 0 | false
- `tests\integration\__init__.py` | 0 | false

## 8. Full file inventory with emptiness status

| file | bytes | status |
|---|---:|---|
| `.env.example` | 0 | false |
| `.github\workflows\ci.yml` | 0 | false |
| `.github\workflows\deploy.yml` | 0 | false |
| `.github\workflows\evaluation.yml` | 0 | false |
| `.gitignore` | 0 | false |
| `analysis_layer_monototring.md` | 6784 | yes |
| `batch\daily_pipeline.py` | 0 | false |
| `batch\data_quality.py` | 0 | false |
| `batch\feature_pipeline.py` | 0 | false |
| `batch\register_model.py` | 0 | false |
| `batch\train_anomaly.py` | 0 | false |
| `batch\update_baseline.py` | 0 | false |
| `configs\evaluation.yaml` | 0 | false |
| `configs\kafka.yaml` | 2154 | yes |
| `configs\monitoring.yaml` | 0 | false |
| `configs\platform.yaml` | 0 | false |
| `configs\policies.yaml` | 0 | false |
| `dlq.md` | 29931 | yes |
| `docker-compose.yml` | 0 | false |
| `docker\Dockerfile.api` | 0 | false |
| `docker\Dockerfile.batch` | 0 | false |
| `docker\Dockerfile.consumer` | 0 | false |
| `evaluation.md` | 22546 | yes |
| `evaluation_layer.md` | 25292 | yes |
| `event analysis.md` | 17703 | yes |
| `fix_storage.md` | 41638 | yes |
| `Kafka real world.md` | 17085 | yes |
| `Kafka_analysis.md` | 54238 | yes |
| `kafka_analysis_part2.md` | 3704 | yes |
| `kafka_final_analysis.md` | 12753 | yes |
| `layer.md` | 29528 | yes |
| `Makefile` | 0 | false |
| `ml\anomaly\isolation_forest.py` | 0 | false |
| `ml\drift\distribution_detector.py` | 0 | false |
| `ml\route_prediction\route_model.py` | 0 | false |
| `monitoring.md` | 35214 | yes |
| `monitoring_2.md` | 33783 | yes |
| `producers\agentic_rag\producer.py` | 0 | false |
| `producers\gaia\producer.py` | 0 | false |
| `pyproject.toml` | 2114 | yes |
| `README.md` | 2346 | yes |
| `schemas\__init__.py` | 358 | yes |
| `schemas\events\__init__.py` | 59 | yes |
| `schemas\events\agent_behavior_events.py` | 2780 | yes |
| `schemas\events\common.py` | 2560 | yes |
| `schemas\events\context_events.py` | 1640 | yes |
| `schemas\events\evaluation_events.py` | 1332 | yes |
| `schemas\events\execution_events.py` | 6353 | yes |
| `schemas\events\policy_events.py` | 3602 | yes |
| `schemas\versioning\__init__.py` | 59 | yes |
| `schemas\versioning\schema_policy.py` | 0 | false |
| `sdk\ai_eval_sdk\client.py` | 0 | false |
| `sdk\ai_eval_sdk\emitter.py` | 0 | false |
| `sdk\ai_eval_sdk\events.py` | 0 | false |
| `src\ai_eval_platform\__init__.py` | 64 | yes |
| `src\ai_eval_platform\analysis\__init__.py` | 0 | false |
| `src\ai_eval_platform\analysis\analyzer.py` | 0 | false |
| `src\ai_eval_platform\analysis\context.py` | 0 | false |
| `src\ai_eval_platform\analysis\findings.py` | 0 | false |
| `src\ai_eval_platform\api\__init__.py` | 18 | yes |
| `src\ai_eval_platform\api\evaluations.py` | 0 | false |
| `src\ai_eval_platform\api\health.py` | 0 | false |
| `src\ai_eval_platform\api\main.py` | 0 | false |
| `src\ai_eval_platform\api\models.py` | 0 | false |
| `src\ai_eval_platform\api\runs.py` | 0 | false |
| `src\ai_eval_platform\deployment\__init__.py` | 33 | yes |
| `src\ai_eval_platform\deployment\deployer.py` | 0 | false |
| `src\ai_eval_platform\deployment\health.py` | 0 | false |
| `src\ai_eval_platform\deployment\registry.py` | 0 | false |
| `src\ai_eval_platform\deployment\rollback.py` | 0 | false |
| `src\ai_eval_platform\evaluation\__init__.py` | 65 | yes |
| `src\ai_eval_platform\evaluation\domain.py` | 2492 | yes |
| `src\ai_eval_platform\evaluation\engines\__init__.py` | 0 | false |
| `src\ai_eval_platform\evaluation\engines\embedding_similarity.py` | 406 | yes |
| `src\ai_eval_platform\evaluation\engines\groundedness_judge.py` | 438 | yes |
| `src\ai_eval_platform\evaluation\errors.py` | 693 | yes |
| `src\ai_eval_platform\evaluation\evaluator.py` | 323 | yes |
| `src\ai_eval_platform\evaluation\evaluators\__init__.py` | 0 | false |
| `src\ai_eval_platform\evaluation\evaluators\citation.py` | 1494 | yes |
| `src\ai_eval_platform\evaluation\evaluators\exact_match.py` | 1542 | yes |
| `src\ai_eval_platform\evaluation\evaluators\groundedness.py` | 1963 | yes |
| `src\ai_eval_platform\evaluation\evaluators\latency.py` | 1710 | yes |
| `src\ai_eval_platform\evaluation\evaluators\retrieval.py` | 2428 | yes |
| `src\ai_eval_platform\evaluation\evaluators\semantic_similarity.py` | 2020 | yes |
| `src\ai_eval_platform\evaluation\evaluators\tool_usage.py` | 1494 | yes |
| `src\ai_eval_platform\evaluation\result_mapper.py` | 1684 | yes |
| `src\ai_eval_platform\evaluation\runner.py` | 2446 | yes |
| `src\ai_eval_platform\evaluation\service.py` | 9739 | yes |
| `src\ai_eval_platform\evaluation\target_builder.py` | 9122 | yes |
| `src\ai_eval_platform\evaluation\target_validator.py` | 726 | yes |
| `src\ai_eval_platform\features\__init__.py` | 46 | yes |
| `src\ai_eval_platform\features\aggregations.py` | 0 | false |
| `src\ai_eval_platform\features\builder.py` | 0 | false |
| `src\ai_eval_platform\features\feature_store.py` | 0 | false |
| `src\ai_eval_platform\infrastructure\__init__.py` | 449 | yes |
| `src\ai_eval_platform\infrastructure\kafka\__init__.py` | 768 | yes |
| `src\ai_eval_platform\infrastructure\kafka\config.py` | 9204 | yes |
| `src\ai_eval_platform\infrastructure\kafka\consumer.py` | 6179 | yes |
| `src\ai_eval_platform\infrastructure\kafka\errors.py` | 905 | yes |
| `src\ai_eval_platform\infrastructure\kafka\producer.py` | 5786 | yes |
| `src\ai_eval_platform\ingestion\__init__.py` | 69 | yes |
| `src\ai_eval_platform\ingestion\delivery_classifier.py` | 5032 | yes |
| `src\ai_eval_platform\ingestion\dlq.py` | 14152 | yes |
| `src\ai_eval_platform\ingestion\event_router.py` | 6696 | yes |
| `src\ai_eval_platform\ingestion\event_validator.py` | 13206 | yes |
| `src\ai_eval_platform\ingestion\handler.py` | 4483 | yes |
| `src\ai_eval_platform\ingestion\kafka_consumer.py` | 12511 | yes |
| `src\ai_eval_platform\ingestion\kafka_record.py` | 1320 | yes |
| `src\ai_eval_platform\ingestion\producer.py` | 3940 | yes |
| `src\ai_eval_platform\ingestion\retry_consumer.py` | 6630 | yes |
| `src\ai_eval_platform\ingestion\retry_dispatcher.py` | 13982 | yes |
| `src\ai_eval_platform\ingestion\retry_policy.py` | 3030 | yes |
| `src\ai_eval_platform\ingestion\retry_publisher.py` | 5538 | yes |
| `src\ai_eval_platform\monitoring\__init__.py` | 520 | yes |
| `src\ai_eval_platform\monitoring\adapters\__init__.py` | 639 | yes |
| `src\ai_eval_platform\monitoring\adapters\records.py` | 2097 | yes |
| `src\ai_eval_platform\monitoring\adapters\run_adapters.py` | 2565 | yes |
| `src\ai_eval_platform\monitoring\alerts.py` | 0 | false |
| `src\ai_eval_platform\monitoring\anomaly.py` | 0 | false |
| `src\ai_eval_platform\monitoring\baselines\__init__.py` | 1363 | yes |
| `src\ai_eval_platform\monitoring\cost.py` | 0 | false |
| `src\ai_eval_platform\monitoring\detectors\__init__.py` | 434 | yes |
| `src\ai_eval_platform\monitoring\detectors\base.py` | 692 | yes |
| `src\ai_eval_platform\monitoring\detectors\domain\__init__.py` | 525 | yes |
| `src\ai_eval_platform\monitoring\detectors\domain\confidence.py` | 2440 | yes |
| `src\ai_eval_platform\monitoring\detectors\domain\route.py` | 2512 | yes |
| `src\ai_eval_platform\monitoring\detectors\domain\tool_usage.py` | 3369 | yes |
| `src\ai_eval_platform\monitoring\detectors\inputs.py` | 3556 | yes |
| `src\ai_eval_platform\monitoring\detectors\strategies\__init__.py` | 319 | yes |
| `src\ai_eval_platform\monitoring\detectors\strategies\anomaly.py` | 2906 | yes |
| `src\ai_eval_platform\monitoring\detectors\strategies\drift.py` | 2945 | yes |
| `src\ai_eval_platform\monitoring\detectors\strategies\pattern.py` | 2824 | yes |
| `src\ai_eval_platform\monitoring\drift.py` | 0 | false |
| `src\ai_eval_platform\monitoring\latency.py` | 0 | false |
| `src\ai_eval_platform\monitoring\orchestration\__init__.py` | 233 | yes |
| `src\ai_eval_platform\monitoring\orchestration\runner.py` | 2884 | yes |
| `src\ai_eval_platform\monitoring\persistence\__init__.py` | 1205 | yes |
| `src\ai_eval_platform\monitoring\route_drift.py` | 0 | false |
| `src\ai_eval_platform\monitoring\signals\__init__.py` | 502 | yes |
| `src\ai_eval_platform\monitoring\signals\models.py` | 4010 | yes |
| `src\ai_eval_platform\monitoring\signals\types.py` | 2410 | yes |
| `src\ai_eval_platform\monitoring\system\__init__.py` | 0 | false |
| `src\ai_eval_platform\monitoring\system\metrics.py` | 0 | false |
| `src\ai_eval_platform\monitoring\system\status.py` | 0 | false |
| `src\ai_eval_platform\monitoring_integration.py` | 9362 | yes |
| `src\ai_eval_platform\observability\__init__.py` | 52 | yes |
| `src\ai_eval_platform\observability\logging.py` | 0 | false |
| `src\ai_eval_platform\observability\metrics.py` | 0 | false |
| `src\ai_eval_platform\observability\tracing.py` | 0 | false |
| `src\ai_eval_platform\policy\__init__.py` | 48 | yes |
| `src\ai_eval_platform\policy\decision.py` | 0 | false |
| `src\ai_eval_platform\policy\engine.py` | 0 | false |
| `src\ai_eval_platform\policy\thresholds.py` | 0 | false |
| `src\ai_eval_platform\root_cause\__init__.py` | 0 | false |
| `src\ai_eval_platform\root_cause\analyzer.py` | 0 | false |
| `src\ai_eval_platform\root_cause\evidence.py` | 0 | false |
| `src\ai_eval_platform\root_cause\findings.py` | 0 | false |
| `src\ai_eval_platform\security\__init__.py` | 61 | yes |
| `src\ai_eval_platform\security\auth.py` | 0 | false |
| `src\ai_eval_platform\security\authorization.py` | 0 | false |
| `src\ai_eval_platform\security\pii.py` | 0 | false |
| `src\ai_eval_platform\security\secrets.py` | 0 | false |
| `src\ai_eval_platform\statistics\__init__.py` | 73 | yes |
| `src\ai_eval_platform\statistics\bootstrap.py` | 0 | false |
| `src\ai_eval_platform\statistics\confidence.py` | 0 | false |
| `src\ai_eval_platform\statistics\effect_size.py` | 0 | false |
| `src\ai_eval_platform\statistics\hypothesis.py` | 0 | false |
| `src\ai_eval_platform\storage\__init__.py` | 93 | yes |
| `src\ai_eval_platform\storage\models.py` | 36279 | yes |
| `src\ai_eval_platform\storage\postgres.py` | 3574 | yes |
| `src\ai_eval_platform\storage\repositories.py` | 58940 | yes |
| `storage.md` | 58308 | yes |
| `tests\contract\test_event_schema.py` | 5585 | yes |
| `tests\contract\test_event_type_locking.py` | 5125 | yes |
| `tests\evaluation\conftest.py` | 5124 | yes |
| `tests\evaluation\test_evaluation_layer.py` | 20118 | yes |
| `tests\evaluation\test_evaluation_service.py` | 23853 | yes |
| `tests\evaluation\test_result_mapper.py` | 4034 | yes |
| `tests\evaluation\test_target_builder.py` | 25041 | yes |
| `tests\ingestion\__init__.py` | 0 | false |
| `tests\ingestion\conftest.py` | 5118 | yes |
| `tests\ingestion\test_architecture.py` | 4923 | yes |
| `tests\ingestion\test_consumer_offsets.py` | 15607 | yes |
| `tests\ingestion\test_dlq.py` | 6816 | yes |
| `tests\ingestion\test_ingestion_db.py` | 7833 | yes |
| `tests\ingestion\test_kafka_config.py` | 4736 | yes |
| `tests\ingestion\test_ordering.py` | 5040 | yes |
| `tests\ingestion\test_retry.py` | 4769 | yes |
| `tests\ingestion\test_retry_consumer.py` | 16814 | yes |
| `tests\ingestion\test_retry_crash_windows.py` | 5417 | yes |
| `tests\ingestion\test_retry_lifecycle.py` | 13191 | yes |
| `tests\ingestion\test_validator.py` | 4701 | yes |
| `tests\integration\__init__.py` | 0 | false |
| `tests\integration\test_postgres_pipeline.py` | 10205 | yes |
| `tests\integration\test_real_kafka_integration.py` | 11095 | yes |
| `tests\monitoring\__init__.py` | 459 | yes |
| `tests\monitoring\test_contracts_a.py` | 1913 | yes |
| `tests\monitoring\test_contracts_b.py` | 3485 | yes |
| `tests\monitoring\test_contracts_c.py` | 5227 | yes |
| `tests\monitoring\test_contracts_d.py` | 4339 | yes |
| `tests\monitoring\test_integration_e.py` | 5069 | yes |
| `tests\monitoring\test_run_completion_f.py` | 15836 | yes |
| `tests\storage\conftest.py` | 3163 | yes |
| `tests\storage\test_models_and_tables.py` | 6779 | yes |
| `tests\storage\test_postgres_integration.py` | 8026 | yes |
| `tests\storage\test_repositories.py` | 25986 | yes |

