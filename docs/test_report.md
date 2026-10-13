# Test Report

Date: 2026-10-10  
Environment: Windows, Python 3.12.  
All results below are from commands actually run in the current worktree.

## Baseline Before Repairs

Command: `python -m pytest`  
Result: exit code 1; collection interrupted with **8 errors, 1 skipped** in 14.25 seconds. The eight errors were import failures because `analysis/__init__.py` imported the absent `analysis.engine` module. No test assertions ran after collection stopped.

## Focused Checks After Repairs

- `python -m pytest tests/analysis tests/storage/test_analysis_service.py` -> **77 passed** in 3.58 seconds.
- `python -m pytest tests/ingestion/test_retry_consumer.py` before adding the regression -> **30 passed** in 0.16 seconds.
- `python -m pytest tests/ingestion/test_retry_consumer.py` after adding the same-loop resume regression -> **31 passed** in 0.24 seconds.
- `python -m pytest tests/ingestion/test_retry_consumer.py` after final style cleanup -> **31 passed** in 0.15 seconds.

## Full Suite

- `python -m pytest` before the retry fix -> **532 passed, 21 skipped** in 24.76 seconds.
- Final `python -m pytest` -> **533 passed, 21 skipped** in 26.57 seconds.

The skips are integration-gated tests; live PostgreSQL/Kafka guarantees are not established by this local run. No live external service was asserted to be available.

## Additional Checks

- `python -m compileall -q src schemas tests` -> exit code 0; no syntax errors reported.
- VS Code diagnostics on `analysis/engine.py`, `ingestion/retry_consumer.py`, and `tests/ingestion/test_retry_consumer.py` -> no errors found.
- No formatter, linter, or type checker is configured in `pyproject.toml`, so none was run.