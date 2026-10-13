# Repository Audit Report

Audit date: 2026-10-10  
Repository: `ai-evaluation-platform`  
Audit basis: the current worktree, including pre-existing modified and untracked files.

## Baseline

Command: `python -m pytest`  
Result: exit code 1; collection interrupted after 8 errors; 1 test skipped; runtime 14.25 seconds. All eight errors are `ModuleNotFoundError: No module named 'ai_eval_platform.analysis.engine'`. The import chain is `tests/analysis/*` or `tests/storage/test_analysis_service.py` -> `ai_eval_platform.analysis.__init__` -> `from .engine import SignalSummaryEngine`; `src/ai_eval_platform/analysis/engine.py` is absent. No test assertions ran for the interrupted suite.

The worktree was already dirty at audit start, including edits to `pyproject.toml`, storage, monitoring, evaluation, and analysis files, plus untracked analysis/lifecycle/migration/test files and miscellaneous probes/documents. Those changes are treated as user-owned; this report and any later repairs must not reset or overwrite them. Environment-file contents were not inspected.

## Execution Paths Observed

- Event schemas in `schemas/events/` are validated by `ingestion.event_validator.EventValidator`, routed by `EventRouter`, handled by `ingestion.handler`, then persisted by `EventRepository`.
- Completed-run monitoring loads persisted events, adapts selected events into detector inputs, runs detectors, and persists detector outputs through `monitoring_integration` and `PersistedSignalRepository`.
- Analysis has persisted-signal summary/context/evidence primitives, but the evidence/relationship path is not connected to a general event-to-observation-to-signal projection.
- The Kafka path uses explicit offset commits after successful processing or confirmed retry/DLQ publishing. A crash after publication and before commit can redeliver and duplicate publication; this is at-least-once behavior, not exactly-once.
- Storage models and repositories are implemented. The single migration is an incremental run-lifecycle hardening script, not an initial schema migration.
- API route files, policy, deployment, and root-cause analyzer files are present in the package tree but are empty placeholders; no API or RCA workflow is established by the current tests.

## Findings

### P1: Test collection is blocked

`src/ai_eval_platform/analysis/__init__.py` exports `SignalSummaryEngine` from missing `analysis.engine`. This is the direct cause of all 8 baseline collection errors and prevents the current analysis and analysis-service tests from running.

### P1: Deferred retry partitions are not resumed

`src/ai_eval_platform/ingestion/retry_consumer.py` (`RetryConsumerLoop._defer`, `RetryConsumerLoop.run`) pauses a Kafka partition for a future-dated record. The loop does not call the source's `resume()` when the record becomes due or after a terminal action. `tests/ingestion/test_retry_lifecycle.py` uses a fresh source/loop for the later pass, so it does not test recovery of the same live paused partition. A real consumer can leave that partition stalled until reassignment or restart.

### P1: The agreed general Signal projection is absent

There is no persisted `Observation` contract or general projection runner that records projection outcomes. The implemented path in `src/ai_eval_platform/monitoring_integration.py` emits detector-produced signals from selected completed-run events only; signal contracts are in `monitoring/signals/types.py` and persistence is in `monitoring/persistence/__init__.py`. Signals do not consistently identify production `origin`, and source lineage is at best payload-level `related_event_ids`, not a normalized or uniformly validated source reference. This does not meet the requested eligible-observation projection, exclusions/outcome recording, replay, and source-lineage requirements.

### P1: Repository-level schemas are required at runtime but excluded from installs

`pyproject.toml` packages only `ai_eval_platform*`, while `src/ai_eval_platform/ingestion/event_validator.py` imports `schemas.events.*`. The README says `schemas/` is intentionally not installed, and pytest adds the repository root to `pythonpath`, masking this requirement in the test environment. A non-editable installation that uses ingestion therefore lacks a required top-level package.

### P2: Migration history cannot bootstrap a clean database

`migrations/0001_run_lifecycle_hardening.sql` alters `runs` and `persisted_signals`, assuming both tables already exist. No initial schema migration or migration runner was identified; `src/ai_eval_platform/storage/postgres.py` describes ORM table creation as local-development/test setup. The migration is also not idempotent, and `tests/storage/test_run_lifecycle_migration.py` starts from ORM-created current tables, so it does not establish clean-database or independently versioned historical-schema coverage.

### P2: API, RCA, policy, and deployment are not implemented workflows

The package files `src/ai_eval_platform/api/main.py`, `api/runs.py`, `api/evaluations.py`, `root_cause/analyzer.py`, `policy/engine.py`, and `deployment/deployer.py` do not provide working end-to-end behavior. No API/RCA tests were identified. Their presence should not be interpreted as shipped capabilities.

### P2: CI and container files do not provide runnable automation

The checked paths `.github/workflows/ci.yml`, `.github/workflows/deploy.yml`, `.github/workflows/evaluation.yml`, `docker-compose.yml`, and `docker/Dockerfile.*` do not establish runnable CI or deployment behavior. The repository has no demonstrated automated check gate. External Kafka and PostgreSQL integration behavior remains unverified unless their services are explicitly configured.

### P2: Signal lineage and evidence independence are incomplete

`src/ai_eval_platform/storage/models.py` (`PersistedSignal`) has a unique dedupe key and detector payloads may include related event IDs, but there is no normalized source-reference relation shared by signal origins. The detector-oriented dedupe identity includes output ordinal, so stable identity depends partly on output ordering. Analysis evidence primitives in `src/ai_eval_platform/analysis/evidence.py` are not connected to persisted source events through an end-to-end workflow; independence of correlated/duplicate evidence is therefore not enforced across the architecture.

## Coverage and Limits

Reviewed tracked/untracked project structure, `pyproject.toml`, README, schemas, ingestion/Kafka/retry/DLQ path, monitoring signals and integration, analysis/evaluation/storage code, tests, migration, Docker files, compose file, and workflow paths. Initial reconnaissance also found no general observation/projection or Outbox implementation. The baseline has 1 skip, but the interrupted collection does not establish the suite's full skip count. No claims are made about live PostgreSQL/Kafka behavior from this run.

The audit is evidence of the current worktree, not a claim that pre-existing uncommitted changes were authored during this audit. Repairs should be staged narrowly, with focused tests, and should not attempt to complete placeholder subsystems without a concrete contract and tests.