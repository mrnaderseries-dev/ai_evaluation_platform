# AI Evaluation Platform

Agent-agnostic AI evaluation and observability platform.

## Installation

Python **3.11+** is required. From the repository root:

```bash
python -m venv .venv
# Windows:     .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Verify the installation:

```bash
python -c "import ai_eval_platform; print('ai_eval_platform import OK')"
```

## Running the tests

```bash
python -m pytest
```

The suite runs against in-memory SQLite by default and needs no external
services. The PostgreSQL integration tests in
`tests/storage/test_postgres_integration.py` skip themselves automatically
unless `DATABASE_URL` is set:

```bash
DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/ai_eval_storage_test python -m pytest
```

> Those tests drop and recreate the schema, so point them at a scratch database.

## Layout

| Path | Installed? | Purpose |
| --- | --- | --- |
| `src/ai_eval_platform/` | yes | The distributable package (`storage/` is implemented) |
| `schemas/` | no | Event schema contracts; a repository-level package |
| `sdk/`, `producers/`, `batch/`, `ml/` | no | Repository-level modules/scripts (currently empty placeholders) |

`schemas/` is deliberately **not** installed: the top-level name `schemas` is
generic enough to collide with unrelated third-party distributions, and it
already shadows stray same-named directories in some environments. It is made
importable for the test suite through `pythonpath = [".", "src"]` in
`pyproject.toml`.

## Dependencies

Declared runtime dependencies are exactly the third-party packages the
current code imports:

| Package | Imported by |
| --- | --- |
| `pydantic>=2.0` | `schemas/events/*` |
| `SQLAlchemy>=2.0` | `src/ai_eval_platform/storage/*` |
| `psycopg[binary]>=3.1` | PostgreSQL driver required by the storage layer to connect |

Development dependencies (`pip install -e ".[dev]"`) are `pytest>=8.0`, which
is the only testing package the suite imports.

Please do not add a dependency to `pyproject.toml` without also adding code
that imports it. Large parts of this repository are still empty placeholders
(`api/`, `ingestion/`, `evaluation/`, `monitoring/`, `policy/`, `ml/`, ...), and
architectural intent alone is not a reason to declare a package.

