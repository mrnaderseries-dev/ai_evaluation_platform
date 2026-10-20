"""Tests for validation of the dataset selection returned by a strategy.

A routing selection is only trustworthy if it is internally consistent: the
dataset ids must be distinct, the version and strategy name must be
present, and the rationale and limitations must be well-formed strings.
"""
from __future__ import annotations

import pytest

from ai_eval_platform.routing.dispatcher import (
    AgentRoutingDispatcher,
    RoutingValidationError,
)
from ai_eval_platform.routing.models import DatasetSelection
from ai_eval_platform.routing.registry import AgentRoutingRegistry


class TestSelectionValidation:
    """The models validate on construction.

    These tests cover the dispatcher's own re-validation, which is the
    second line of defence for a selection that was mutated after it was
    built.
    """

    def test_duplicate_dataset_ids_are_rejected(
        self, descriptor, strategy_base
    ):
        class DuplicateIds(strategy_base):
            def select_dataset(self, profile):
                selection = DatasetSelection(
                    dataset_ids=("ds-1", "ds-2"),
                    dataset_version="1.0.0",
                    strategy_name="stub",
                )
                object.__setattr__(selection, "dataset_ids", ("ds-1", "ds-1"))

                return selection

        dispatcher = AgentRoutingDispatcher(
            AgentRoutingRegistry([DuplicateIds()])
        )

        with pytest.raises(RoutingValidationError) as excinfo:
            dispatcher.dispatch(descriptor)

        assert "duplicate" in str(excinfo.value).lower()

    def test_a_non_selection_result_is_rejected(self, descriptor, strategy_base):
        class NotASelection(strategy_base):
            def select_dataset(self, profile):
                return ["ds-1"]

        dispatcher = AgentRoutingDispatcher(
            AgentRoutingRegistry([NotASelection()])
        )

        with pytest.raises(RoutingValidationError):
            dispatcher.dispatch(descriptor)

    def test_a_blank_dataset_version_is_rejected(
        self, descriptor, strategy_base
    ):
        class BlankVersion(strategy_base):
            def select_dataset(self, profile):
                selection = DatasetSelection(
                    dataset_ids=("ds-1",),
                    dataset_version="1.0.0",
                    strategy_name="stub",
                )
                object.__setattr__(selection, "dataset_version", "  ")

                return selection

        dispatcher = AgentRoutingDispatcher(
            AgentRoutingRegistry([BlankVersion()])
        )

        with pytest.raises(RoutingValidationError):
            dispatcher.dispatch(descriptor)

    def test_a_blank_dataset_id_is_rejected(self, descriptor, strategy_base):
        class BlankId(strategy_base):
            def select_dataset(self, profile):
                selection = DatasetSelection(
                    dataset_ids=("ds-1",),
                    dataset_version="1.0.0",
                    strategy_name="stub",
                )
                object.__setattr__(selection, "dataset_ids", ("ds-1", "  "))

                return selection

        dispatcher = AgentRoutingDispatcher(
            AgentRoutingRegistry([BlankId()])
        )

        with pytest.raises(RoutingValidationError):
            dispatcher.dispatch(descriptor)

    def test_dataset_ids_must_be_a_tuple(self, descriptor, strategy_base):
        class ListIds(strategy_base):
            def select_dataset(self, profile):
                selection = DatasetSelection(
                    dataset_ids=("ds-1",),
                    dataset_version="1.0.0",
                    strategy_name="stub",
                )
                object.__setattr__(selection, "dataset_ids", ["ds-1"])

                return selection

        dispatcher = AgentRoutingDispatcher(
            AgentRoutingRegistry([ListIds()])
        )

        with pytest.raises(RoutingValidationError) as excinfo:
            dispatcher.dispatch(descriptor)

        assert "tuple" in str(excinfo.value)

    def test_a_blank_rationale_entry_is_rejected(
        self, descriptor, strategy_base
    ):
        class BlankRationale(strategy_base):
            def select_dataset(self, profile):
                selection = DatasetSelection(
                    dataset_ids=("ds-1",),
                    dataset_version="1.0.0",
                    strategy_name="stub",
                )
                object.__setattr__(selection, "rationale", ("  ",))

                return selection

        dispatcher = AgentRoutingDispatcher(
            AgentRoutingRegistry([BlankRationale()])
        )

        with pytest.raises(RoutingValidationError):
            dispatcher.dispatch(descriptor)
