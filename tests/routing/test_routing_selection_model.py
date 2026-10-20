"""Tests for the routing dataset selection value object.

A selection records the exact dataset version ids plus the reasons and
limitations behind the choice, so a routing decision can be reviewed and
reproduced. The object is frozen and validates its fields on construction.
"""
from __future__ import annotations

import pytest

from ai_eval_platform.routing.models import DatasetSelection


class TestDatasetSelection:
    def test_a_valid_selection_is_accepted(self):
        selection = DatasetSelection(
            dataset_ids=("ds-1", "ds-2"),
            dataset_version="1.0.0",
            strategy_name="agentic_rag",
            rationale=("Because.",),
            limitations=("None.",),
        )

        assert selection.dataset_ids == ("ds-1", "ds-2")

    def test_dataset_ids_must_not_contain_duplicates(self):
        with pytest.raises(ValueError) as excinfo:
            DatasetSelection(
                dataset_ids=("ds-1", "ds-1"),
                dataset_version="1.0.0",
                strategy_name="agentic_rag",
            )

        assert "duplicate" in str(excinfo.value).lower()

    def test_an_empty_dataset_version_is_rejected(self):
        with pytest.raises(ValueError):
            DatasetSelection(
                dataset_ids=("ds-1",),
                dataset_version="",
                strategy_name="agentic_rag",
            )

    def test_an_empty_strategy_name_is_rejected(self):
        with pytest.raises(ValueError):
            DatasetSelection(
                dataset_ids=("ds-1",),
                dataset_version="1.0.0",
                strategy_name="",
            )

    def test_dataset_ids_must_be_a_tuple(self):
        with pytest.raises(ValueError):
            DatasetSelection(
                dataset_ids=["ds-1"],
                dataset_version="1.0.0",
                strategy_name="agentic_rag",
            )

    def test_an_empty_dataset_id_is_rejected(self):
        with pytest.raises(ValueError):
            DatasetSelection(
                dataset_ids=("  ",),
                dataset_version="1.0.0",
                strategy_name="agentic_rag",
            )

    def test_rationale_must_be_a_tuple(self):
        with pytest.raises(ValueError):
            DatasetSelection(
                dataset_ids=("ds-1",),
                dataset_version="1.0.0",
                strategy_name="agentic_rag",
                rationale=["Because."],
            )

    def test_an_empty_selection_is_allowed(self):
        selection = DatasetSelection(
            dataset_ids=(),
            dataset_version="1.0.0",
            strategy_name="agentic_rag",
        )

        assert selection.dataset_ids == ()

    def test_the_selection_is_frozen(self):
        selection = DatasetSelection(
            dataset_ids=("ds-1",),
            dataset_version="1.0.0",
            strategy_name="agentic_rag",
        )

        with pytest.raises(Exception):
            selection.strategy_name = "other"
