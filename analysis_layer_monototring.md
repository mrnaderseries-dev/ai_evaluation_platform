# Analysis Layer — Monitoring: Fixes, Analysis, Ratings

Scope: `src/ai_eval_platform/monitoring/` + `tests/monitoring/`.
Baseline: `monitoring.md` audit (2026-10-07). Verified live before repair: both package imports failed, 5 lineage defects reproduced, 66 contract tests green.

## 1. What was fixed

R1 packaging: moved `detectors/{route,confidence,tool_usage}.py` → `detectors/domain/`; added `strategies/__init__.py` + `domain/__init__.py` (with policy exports); fixed `monitoring/__init__.py` + `detectors/__init__.py` re-exports. All five imports verified: root, signals, detectors, strategies, domain.

R2 Signal datetime: added `require_timezone_aware` field validator on `window_start/window_end/detected_at` in `signals/models.py`, mirroring `schemas/events/common.py`. Naive timestamps now rejected; verified by test.

R3 Drift payload lineage: `ToolUsageDriftPayload` gained `drift_metric`, `reference_window_start/end` (tz-validated). `DriftDetector` now emits both reference (payload) and current (signal windows) bounds.

R4 Inputs: new `ScopedDetectorInput` enforces exactly-one-scope (run XOR evaluation XOR window) + ordered tz-aware windows. `Pattern/AnomalyDetectionInput` extend it; `DriftDetectionInput` + `ToolUsageDetectionInput` validate both windows. `severity` and `baseline_window` moved into inputs so detectors stop fabricating `0.0` / `"detector_input"`. `sample_count` added to anomaly input.

R5 Strategies: `deviation_exceeds_threshold`, `is_repeated_sequence`, `drifted_features` extracted as pure reusable helpers. Anomaly/pattern emit RUN/EVALUATION/WINDOW from supplied lineage (no more WINDOW-without-bounds). Drift enforces `minimum_samples`, sorts output for determinism.

R6 Domain contract: `RouteChange/ConfidenceDrop/ToolUsageDriftDetector` now extend `Detector[Input,Policy]` with `enabled` gates. Route receives real windows, enforces `affected_runs >= minimum_samples`. Confidence dropped fabricated `run_id=agent_id`; threshold moved to `ConfidenceDetectorPolicy.drop_threshold`; supports all three scopes. Tool usage delegates to `drifted_features` (no duplication), threshold in policy, full window lineage.

R7 Minimum samples: enforced in anomaly (`sample_count`), pattern (sequence count), drift (feature count), route (`affected_runs`). Documented semantics per detector in report §25/Open Questions of monitoring.md.

R8 Tests: `tests/monitoring/` (4 files, 30 tests) covers RUN/WINDOW/EVALUATION valid+invalid, equal/reversed bounds, payload match/mismatch, naive rejection, disabled detectors, minimum_samples, domain contract, strategy/domain interaction. 30/30 pass. Regression: `tests/contract` 66/66 pass.

## 2. Analysis (what the repair revealed)
A1 The audit was accurate on all load-bearing claims: broken root/detector re-exports, empty `domain/`, fabricated lineage (`run_id=agent_id`, `now()/now()` windows), missing EVALUATION path, naive-datetime acceptance, dead `minimum_samples`, strategy/domain duplication. No audit correction needed; one follow-up gap found during repair (route input lacked window validators — fixed as R9).

A2 The cleanest lever was input contracts, not detector logic. `ScopedDetectorInput.exactly_one_scope` plus per-input window validators moved every scope decision to construction time, so `detect()` only copies already-valid lineage. This removed all five fabrication sites without adding orchestration.

A3 Strategy/domain separation now holds by reuse, not naming: `ToolUsageDriftDetector` delegates to `drifted_features`; `ConfidenceDropDetector` no longer re-implements ad-hoc threshold logic outside a policy. `RouteChangeDetector` stays bespoke (route equality is domain semantics, not anomaly math) — recorded as intentional, not duplication.

A4 Deliberate non-reuse: confidence drop is directional (only decreases trigger) while `deviation_exceeds_threshold` is absolute; wiring them together would silently change semantics, so confidence keeps its own directional check. Documented here instead of forced.

A5 `ERROR_PATTERN` stays intentionally unimplemented: type + payload + strict mapping preserved, no detector emits it, no fake signals. Minimal detector deferred — needs an error-event aggregation source that belongs outside monitoring scope.

A6 Lineage still incomplete by design of this phase: no `agent_version_id`, `event_ids` (except repeated/error payloads), `correlation_id`, `dataset_version_id`, detector version, or signal schema version. Persistence deliberately untouched per instructions.

## 3. Ratings (post-repair, 0–5)
Architecture 3 (was 1): packaging correct, strategy/domain split enforced by composition, scope trichotomy implemented. Not 4: no orchestration/registry/persistence.
Domain model 3 (was 2): scoped inputs + tz + ordered windows + reference lineage. Not 4: version/dataset/correlation lineage missing.
Type safety 4 (was 2): forbid everywhere, exactly-one-scope, tz validators, strict type→payload. Not 5: `signal_id` uniqueness is conventional (uuid4), not enforced.
Detector implementation 3 (was 1): all 6 emit valid signals on all supported scopes, no fabrication, no duplication. Not 4: ERROR_PATTERN absent, no statistical/ML methods.
Integration 1 (was 0): importable + testable + event-shape compatible, but zero adapters/consumers. Unchanged wiring by scope rule.
Persistence readiness 2 (was 1): contract stable enough to design table (hybrid core cols + JSONB), dedup key derivable. No schema written per phase scope.
Testing 3 (was 0): 30 focused tests, all required matrix rows covered. Not 4: no property/fuzz, no integration with real events.
ML/statistical readiness 2 (was 1): pure helpers (`drifted_features`, `deviation_exceeds_threshold`, `is_repeated_sequence`) are swappable for PSI/KS/JSD/models without Signal change. No baselines store yet.
Observability 0: unchanged, no detector metrics/logging.
Production readiness 1 (was 0): imports + valid emission + tests, but no persistence/orchestration/alerting. Not deployable as a loop.
Overall 2.5/5: solid domain contract, working implementation, not production-oriented.

## 4. Deferred / cross-project (not touched)
Event→input adapters, Kafka consumer, Run/Version resolvers, EVALUATION offline runner, reference/baseline store, signal table + repo, Analysis/RCA/Policy wiring, detector registry/orchestration, alerting. Each belongs outside `monitoring/` + `tests/monitoring/` and was left for its own phase.

## 5. Verification
`python -m pytest tests/monitoring tests/contract -q` → 96 passed. Five-package import check passes. `git status` shows only `monitoring/`, `tests/monitoring/`, `monitoring.md`, `analysis_layer_monototring.md` touched — no cross-project edits.
