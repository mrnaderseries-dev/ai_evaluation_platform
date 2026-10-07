from __future__ import annotations

import importlib

import pytest
from sqlalchemy import CheckConstraint, UniqueConstraint, inspect

from ai_eval_platform.storage import postgres
from ai_eval_platform.storage.models import (
    Agent,
    Base,
    EvaluationResult,
    Event,
    Lineage,
)

# Every table registered by the updated models (30 total).
EXPECTED_TABLES = {
    "agents",
    "agent_versions",
    "runs",
    "policies",
    "policy_versions",
    "agent_policies",
    "events",
    "agent_behavior_events",
    "context_events",
    "context_resources",
    "execution_events",
    "policy_decisions",
    "datasets",
    "dataset_versions",
    "evaluation_cases",
    "evaluation_runs",
    "evaluation_results",
    "ml_models",
    "model_versions",
    "pipeline_versions",
    "pipeline_components",
    "experiments",
    "training_runs",
    "analyses",
    "differential_analyses",
    "drift_analyses",
    "root_cause_analyses",
    "artifacts",
    "lineage",
    "outbox_events",
}


def _unique_constraint_names(table) -> set[str]:
    return {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def _fk_ondelete(table, column_name: str, referred_table: str, referred_column: str) -> str | None:
    for foreign_key in table.c[column_name].foreign_keys:
        if foreign_key.target_fullname == f"{referred_table}.{referred_column}":
            return foreign_key.ondelete
    return None


def test_models_import_and_register_all_tables(engine):
    assert EXPECTED_TABLES == set(inspect(engine).get_table_names())
    assert "outbox_events" in Base.metadata.tables


def test_metadata_attribute_is_named_metadata_json():
    """The ORM attribute must avoid the reserved name; the DB column keeps `metadata`."""
    attribute_names = set(Agent.__mapper__.attrs.keys())
    assert "metadata_json" in attribute_names
    assert "metadata" not in attribute_names
    # The mapped DB column is still named "metadata" (mapped_column first arg).
    assert "metadata" in set(Agent.__table__.c.keys())


def test_event_has_no_surrogate_primary_key():
    primary_key = [column.name for column in Event.__table__.primary_key.columns]
    assert primary_key == ["event_id"]


def test_event_run_sequence_unique_constraint_exists():
    names = _unique_constraint_names(Event.__table__)
    assert "uq_event_run_sequence" in names
    constraint = next(
        c
        for c in Event.__table__.constraints
        if isinstance(c, UniqueConstraint) and c.name == "uq_event_run_sequence"
    )
    assert set(constraint.columns.keys()) == {"run_id", "sequence_number"}


def test_event_parent_and_correlation_columns_exist():
    columns = set(Event.__table__.columns.keys())
    assert {
        "parent_event_id",
        "correlation_id",
        "causation_id",
        "step_id",
        "sequence_number",
        "recorded_at",
    } <= columns

    # parent_event_id is a self-referencing FK with ON DELETE SET NULL.
    assert (
        _fk_ondelete(Event.__table__, "parent_event_id", "events", "event_id")
        == "SET NULL"
    )


def test_event_indexes_exist():
    index_names = {index.name for index in Event.__table__.indexes}
    assert {
        "ix_events_run_time",
        "ix_events_type_time",
        "ix_events_correlation",
    } <= index_names


def test_event_detail_tables_use_event_id_as_primary_key():
    """Behavior / context / execution details are 1:1 children of events."""
    for table_name in ("agent_behavior_events", "context_events", "execution_events"):
        table = Base.metadata.tables[table_name]
        primary_key = [column.name for column in table.primary_key.columns]
        assert primary_key == ["event_id"], table_name
        assert _fk_ondelete(table, "event_id", "events", "event_id") == "CASCADE", table_name


def test_context_resource_foreign_key_and_check_constraint():
    table = Base.metadata.tables["context_resources"]
    assert (
        _fk_ondelete(table, "context_event_id", "context_events", "event_id")
        == "CASCADE"
    )
    check_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert "ck_context_resource_rank_nonnegative" in check_names


def test_policy_table_constraints():
    policy_versions = Base.metadata.tables["policy_versions"]
    agent_policies = Base.metadata.tables["agent_policies"]
    policy_decisions = Base.metadata.tables["policy_decisions"]

    assert "uq_policy_version" in _unique_constraint_names(policy_versions)
    assert "uq_agent_policy_binding" in _unique_constraint_names(agent_policies)
    assert (
        _fk_ondelete(policy_versions, "policy_id", "policies", "policy_id")
        == "CASCADE"
    )
    # Decisions must not lose their version: RESTRICT.
    assert (
        _fk_ondelete(
            policy_decisions,
            "policy_version_id",
            "policy_versions",
            "policy_version_id",
        )
        == "RESTRICT"
    )
    assert (
        _fk_ondelete(policy_decisions, "event_id", "events", "event_id")
        == "SET NULL"
    )


def test_evaluation_result_partial_unique_indexes_exist():
    indexes = EvaluationResult.__table__.indexes
    by_name = {index.name: index for index in indexes}
    assert {
        "uq_evaluation_result_online",
        "uq_evaluation_result_offline",
        "uq_evaluation_result_benchmark",
    } <= set(by_name)
    assert by_name["uq_evaluation_result_online"].unique
    assert by_name["uq_evaluation_result_offline"].unique
    assert by_name["uq_evaluation_result_benchmark"].unique


def test_lineage_unique_constraint_exists():
    names = {constraint.name for constraint in Lineage.__table__.constraints}
    assert "uq_lineage_edge" in names


def test_outbox_table_columns():
    columns = set(Base.metadata.tables["outbox_events"].columns.keys())
    assert {
        "outbox_event_id",
        "event_id",
        "aggregate_type",
        "aggregate_id",
        "topic",
        "message_key",
        "payload",
        "status",
        "attempts",
        "created_at",
        "published_at",
        "last_error",
    } <= columns


def test_postgres_module_imports_without_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    # Re-importing must not raise even though DATABASE_URL is absent.
    module = importlib.reload(postgres)

    with pytest.raises(RuntimeError):
        module.get_database_url()
