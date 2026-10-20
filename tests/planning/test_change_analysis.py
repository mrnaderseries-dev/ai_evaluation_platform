"""Tests for change analysis and agent version snapshots.

The change analyzer is pure and deterministic: it compares two snapshots
of the same agent version and reports only differences it can point at
concrete evidence for. Declared changes are merged with detected diffs at
a capped confidence.
"""
from __future__ import annotations

import pytest

from ai_eval_platform.planning.change_analysis import (
    AgentVersionSnapshot,
    ChangeAnalyzer,
    CrossAgentComparisonError,
)


class TestAgentVersionSnapshot:
    def test_a_valid_snapshot_is_accepted(self):
        snapshot = AgentVersionSnapshot(
            agent_version_id="v1",
            agent_id="agent-1",
            version="1.0.0",
            code_hash="sha256:abc123",
            prompt_version="p1",
            model_provider="openai",
            model_name="gpt-4",
        )

        assert snapshot.agent_version_id == "v1"
        assert snapshot.agent_id == "agent-1"
        assert snapshot.version == "1.0.0"
        assert snapshot.code_hash == "sha256:abc123"

    def test_defaults_are_empty_or_none(self):
        snapshot = AgentVersionSnapshot(
            agent_version_id="v1",
            agent_id="agent-1",
            version="1.0.0",
        )

        assert snapshot.code_hash is None
        assert snapshot.prompt_version is None
        assert snapshot.model_provider is None
        assert snapshot.model_name is None
        assert snapshot.config == {}
        assert snapshot.declared_changes == {}
        assert snapshot.artifact_fingerprints == {}

    def test_an_empty_agent_version_id_is_rejected(self):
        with pytest.raises(ValueError, match="agent_version_id"):
            AgentVersionSnapshot(
                agent_version_id="  ",
                agent_id="agent-1",
                version="1.0.0",
            )

    def test_an_empty_agent_id_is_rejected(self):
        with pytest.raises(ValueError, match="agent_id"):
            AgentVersionSnapshot(
                agent_version_id="v1",
                agent_id="   ",
                version="1.0.0",
            )

    def test_an_empty_version_is_rejected(self):
        with pytest.raises(ValueError, match="version"):
            AgentVersionSnapshot(
                agent_version_id="v1",
                agent_id="agent-1",
                version="",
            )

    def test_the_snapshot_is_frozen(self):
        snapshot = AgentVersionSnapshot(
            agent_version_id="v1",
            agent_id="agent-1",
            version="1.0.0",
        )

        with pytest.raises(Exception):
            snapshot.version = "2.0.0"


class TestChangeAnalyzerIdentification:
    def test_analyzing_identical_snapshots_reports_no_changes(self):
        before = AgentVersionSnapshot(
            agent_version_id="v1",
            agent_id="agent-1",
            version="1.0.0",
        )
        after = AgentVersionSnapshot(
            agent_version_id="v2",
            agent_id="agent-1",
            version="1.0.0",
        )

        analyzer = ChangeAnalyzer()
        description = analyzer.analyze(before, after)

        assert description.changes == ()
        assert description.agent_id == "agent-1"
        assert description.from_agent_version_id == "v1"
        assert description.to_agent_version_id == "v2"

    def test_analyzing_cross_agent_versions_raises(self):
        before = AgentVersionSnapshot(
            agent_version_id="v1",
            agent_id="agent-1",
            version="1.0.0",
        )
        after = AgentVersionSnapshot(
            agent_version_id="v2",
            agent_id="agent-2",
            version="1.0.0",
        )

        analyzer = ChangeAnalyzer()

        with pytest.raises(CrossAgentComparisonError):
            analyzer.analyze(before, after)

    def test_change_id_defaults_to_arrow_notation(self):
        before = AgentVersionSnapshot(
            agent_version_id="v1",
            agent_id="agent-1",
            version="1.0.0",
        )
        after = AgentVersionSnapshot(
            agent_version_id="v2",
            agent_id="agent-1",
            version="2.0.0",
        )

        analyzer = ChangeAnalyzer()
        description = analyzer.analyze(before, after)

        assert description.change_id == "v1->v2"

    def test_a_custom_change_id_is_honored(self):
        before = AgentVersionSnapshot(
            agent_version_id="v1",
            agent_id="agent-1",
            version="1.0.0",
        )
        after = AgentVersionSnapshot(
            agent_version_id="v2",
            agent_id="agent-1",
            version="2.0.0",
        )

        analyzer = ChangeAnalyzer()
        description = analyzer.analyze(before, after, change_id="custom-1")

        assert description.change_id == "custom-1"


class TestChangeAnalyzerSemanticFields:
    def test_a_code_hash_change_is_detected(self):
        before = AgentVersionSnapshot(
            agent_version_id="v1",
            agent_id="agent-1",
            version="1.0.0",
            code_hash="sha256:before",
        )
        after = AgentVersionSnapshot(
            agent_version_id="v2",
            agent_id="agent-1",
            version="2.0.0",
            code_hash="sha256:after",
        )

        analyzer = ChangeAnalyzer()
        description = analyzer.analyze(before, after)

        code_changes = [
            c for c in description.changes
            if c.component == "code_hash" and c.change_type == "code"
        ]
        assert len(code_changes) == 1
        assert code_changes[0].operation == "modified"

    def test_a_prompt_version_change_is_detected(self):
        before = AgentVersionSnapshot(
            agent_version_id="v1",
            agent_id="agent-1",
            version="1.0.0",
            prompt_version="p1",
        )
        after = AgentVersionSnapshot(
            agent_version_id="v2",
            agent_id="agent-1",
            version="2.0.0",
            prompt_version="p2",
        )

        analyzer = ChangeAnalyzer()
        description = analyzer.analyze(before, after)

        prompt_changes = [
            c for c in description.changes
            if c.component == "prompt_version" and c.change_type == "prompt"
        ]
        assert len(prompt_changes) == 1
        assert prompt_changes[0].operation == "modified"

    def test_a_model_provider_change_is_detected(self):
        before = AgentVersionSnapshot(
            agent_version_id="v1",
            agent_id="agent-1",
            version="1.0.0",
            model_provider="openai",
        )
        after = AgentVersionSnapshot(
            agent_version_id="v2",
            agent_id="agent-1",
            version="2.0.0",
            model_provider="anthropic",
        )

        analyzer = ChangeAnalyzer()
        description = analyzer.analyze(before, after)

        model_changes = [
            c for c in description.changes
            if c.component == "model_provider" and c.change_type == "model"
        ]
        assert len(model_changes) == 1
        assert model_changes[0].operation == "modified"

    def test_a_model_name_change_is_detected(self):
        before = AgentVersionSnapshot(
            agent_version_id="v1",
            agent_id="agent-1",
            version="1.0.0",
            model_name="gpt-4",
        )
        after = AgentVersionSnapshot(
            agent_version_id="v2",
            agent_id="agent-1",
            version="2.0.0",
            model_name="gpt-4o",
        )

        analyzer = ChangeAnalyzer()
        description = analyzer.analyze(before, after)

        model_changes = [
            c for c in description.changes
            if c.component == "model_name" and c.change_type == "model"
        ]
        assert len(model_changes) == 1
        assert model_changes[0].operation == "modified"

    def test_none_on_both_sides_is_not_a_change(self):
        before = AgentVersionSnapshot(
            agent_version_id="v1",
            agent_id="agent-1",
            version="1.0.0",
        )
        after = AgentVersionSnapshot(
            agent_version_id="v2",
            agent_id="agent-1",
            version="2.0.0",
        )

        analyzer = ChangeAnalyzer()
        description = analyzer.analyze(before, after)

        assert description.changes == ()

    def test_none_to_value_is_added_not_removed(self):
        before = AgentVersionSnapshot(
            agent_version_id="v1",
            agent_id="agent-1",
            version="1.0.0",
            prompt_version=None,
        )
        after = AgentVersionSnapshot(
            agent_version_id="v2",
            agent_id="agent-1",
            version="2.0.0",
            prompt_version="p2",
        )

        analyzer = ChangeAnalyzer()
        description = analyzer.analyze(before, after)

        prompt_changes = [
            c for c in description.changes
            if c.component == "prompt_version"
        ]
        assert len(prompt_changes) == 1
        assert prompt_changes[0].operation == "added"

    def test_value_to_none_is_unknown_not_removed(self):
        before = AgentVersionSnapshot(
            agent_version_id="v1",
            agent_id="agent-1",
            version="1.0.0",
            prompt_version="p1",
        )
        after = AgentVersionSnapshot(
            agent_version_id="v2",
            agent_id="agent-1",
            version="2.0.0",
            prompt_version=None,
        )

        analyzer = ChangeAnalyzer()
        description = analyzer.analyze(before, after)

        prompt_changes = [
            c for c in description.changes
            if c.component == "prompt_version"
        ]
        assert len(prompt_changes) == 1
        assert prompt_changes[0].operation == "unknown"


class TestChangeAnalyzerArtifacts:
    def test_an_added_artifact_fingerprint_is_detected(self):
        before = AgentVersionSnapshot(
            agent_version_id="v1",
            agent_id="agent-1",
            version="1.0.0",
        )
        after = AgentVersionSnapshot(
            agent_version_id="v2",
            agent_id="agent-1",
            version="2.0.0",
            artifact_fingerprints={"model.bin": "sha256:new"},
        )

        analyzer = ChangeAnalyzer()
        description = analyzer.analyze(before, after)

        artifact_changes = [
            c for c in description.changes
            if c.component == "model.bin"
        ]
        assert len(artifact_changes) == 1
        assert artifact_changes[0].operation == "added"
        assert artifact_changes[0].after_fingerprint == "sha256:new"

    def test_a_removed_artifact_fingerprint_is_detected(self):
        before = AgentVersionSnapshot(
            agent_version_id="v1",
            agent_id="agent-1",
            version="1.0.0",
            artifact_fingerprints={"model.bin": "sha256:old"},
        )
        after = AgentVersionSnapshot(
            agent_version_id="v2",
            agent_id="agent-1",
            version="2.0.0",
        )

        analyzer = ChangeAnalyzer()
        description = analyzer.analyze(before, after)

        artifact_changes = [
            c for c in description.changes
            if c.component == "model.bin"
        ]
        assert len(artifact_changes) == 1
        assert artifact_changes[0].operation == "removed"
        assert artifact_changes[0].before_fingerprint == "sha256:old"

    def test_a_modified_artifact_fingerprint_is_detected(self):
        before = AgentVersionSnapshot(
            agent_version_id="v1",
            agent_id="agent-1",
            version="1.0.0",
            artifact_fingerprints={"model.bin": "sha256:old"},
        )
        after = AgentVersionSnapshot(
            agent_version_id="v2",
            agent_id="agent-1",
            version="2.0.0",
            artifact_fingerprints={"model.bin": "sha256:new"},
        )

        analyzer = ChangeAnalyzer()
        description = analyzer.analyze(before, after)

        artifact_changes = [
            c for c in description.changes
            if c.component == "model.bin"
        ]
        assert len(artifact_changes) == 1
        assert artifact_changes[0].operation == "modified"

    def test_unchanged_artifacts_are_not_changes(self):
        before = AgentVersionSnapshot(
            agent_version_id="v1",
            agent_id="agent-1",
            version="1.0.0",
            artifact_fingerprints={"model.bin": "sha256:same"},
        )
        after = AgentVersionSnapshot(
            agent_version_id="v2",
            agent_id="agent-1",
            version="2.0.0",
            artifact_fingerprints={"model.bin": "sha256:same"},
        )

        analyzer = ChangeAnalyzer()
        description = analyzer.analyze(before, after)

        artifact_changes = [
            c for c in description.changes
            if c.component == "model.bin"
        ]
        assert artifact_changes == []


class TestChangeAnalyzerConfig:
    def test_an_added_config_key_is_detected(self):
        before = AgentVersionSnapshot(
            agent_version_id="v1",
            agent_id="agent-1",
            version="1.0.0",
            config={},
        )
        after = AgentVersionSnapshot(
            agent_version_id="v2",
            agent_id="agent-1",
            version="2.0.0",
            config={"chunk_size": 512},
        )

        analyzer = ChangeAnalyzer()
        description = analyzer.analyze(before, after)

        chunk_changes = [
            c for c in description.changes
            if c.component == "chunk_size"
        ]
        assert len(chunk_changes) == 1
        assert chunk_changes[0].operation == "added"
        assert chunk_changes[0].change_type == "chunking"

    def test_a_removed_config_key_is_detected(self):
        before = AgentVersionSnapshot(
            agent_version_id="v1",
            agent_id="agent-1",
            version="1.0.0",
            config={"retriever": {"type": "hnsw"}},
        )
        after = AgentVersionSnapshot(
            agent_version_id="v2",
            agent_id="agent-1",
            version="2.0.0",
            config={},
        )

        analyzer = ChangeAnalyzer()
        description = analyzer.analyze(before, after)

        retriever_changes = [
            c for c in description.changes
            if c.component == "retriever.type"
        ]
        assert len(retriever_changes) == 1
        assert retriever_changes[0].operation == "removed"
        assert retriever_changes[0].change_type == "retriever"

    def test_a_modified_config_key_is_detected(self):
        before = AgentVersionSnapshot(
            agent_version_id="v1",
            agent_id="agent-1",
            version="1.0.0",
            config={"chunk_size": 256},
        )
        after = AgentVersionSnapshot(
            agent_version_id="v2",
            agent_id="agent-1",
            version="2.0.0",
            config={"chunk_size": 512},
        )

        analyzer = ChangeAnalyzer()
        description = analyzer.analyze(before, after)

        chunk_changes = [
            c for c in description.changes
            if c.component == "chunk_size"
        ]
        assert len(chunk_changes) == 1
        assert chunk_changes[0].operation == "modified"

    def test_an_undocumented_config_path_gets_configuration_type(self):
        before = AgentVersionSnapshot(
            agent_version_id="v1",
            agent_id="agent-1",
            version="1.0.0",
            config={},
        )
        after = AgentVersionSnapshot(
            agent_version_id="v2",
            agent_id="agent-1",
            version="2.0.0",
            config={"some_custom_key": "value"},
        )

        analyzer = ChangeAnalyzer()
        description = analyzer.analyze(before, after)

        custom_changes = [
            c for c in description.changes
            if c.component == "some_custom_key"
        ]
        assert len(custom_changes) == 1
        assert custom_changes[0].change_type == "configuration"

    def test_a_sensitive_config_key_has_no_fingerprint(self):
        before = AgentVersionSnapshot(
            agent_version_id="v1",
            agent_id="agent-1",
            version="1.0.0",
            config={},
        )
        after = AgentVersionSnapshot(
            agent_version_id="v2",
            agent_id="agent-1",
            version="2.0.0",
            config={"api_key": "sk-1234567890123456"},
        )

        analyzer = ChangeAnalyzer()
        description = analyzer.analyze(before, after)

        key_changes = [
            c for c in description.changes
            if c.component == "api_key"
        ]
        assert len(key_changes) == 1
        assert key_changes[0].before_fingerprint is None
        assert key_changes[0].after_fingerprint is None


class TestChangeAnalyzerDeclared:
    def test_a_declared_change_without_detection_is_kept(self):
        before = AgentVersionSnapshot(
            agent_version_id="v1",
            agent_id="agent-1",
            version="1.0.0",
        )
        after = AgentVersionSnapshot(
            agent_version_id="v2",
            agent_id="agent-1",
            version="2.0.0",
            declared_changes={
                "retriever": {
                    "change_type": "retriever",
                    "operation": "modified",
                    "detail": "Switched to HNSW.",
                },
            },
        )

        analyzer = ChangeAnalyzer()
        description = analyzer.analyze(before, after)

        retriever_changes = [
            c for c in description.changes
            if c.component == "retriever"
        ]
        assert len(retriever_changes) == 1
        assert retriever_changes[0].change_type == "retriever"
        assert retriever_changes[0].operation == "modified"
        assert retriever_changes[0].source == "declared"
        assert retriever_changes[0].confidence == 0.8

    def test_a_declared_change_matching_detected_is_folded(self):
        before = AgentVersionSnapshot(
            agent_version_id="v1",
            agent_id="agent-1",
            version="1.0.0",
            config={"chunk_size": 256},
        )
        after = AgentVersionSnapshot(
            agent_version_id="v2",
            agent_id="agent-1",
            version="2.0.0",
            config={"chunk_size": 512},
            declared_changes={
                "chunk_size": {
                    "change_type": "chunking",
                    "operation": "modified",
                    "detail": "Increased for better recall.",
                },
            },
        )

        analyzer = ChangeAnalyzer()
        description = analyzer.analyze(before, after)

        chunk_changes = [
            c for c in description.changes
            if c.component == "chunk_size"
        ]
        assert len(chunk_changes) == 1
        assert chunk_changes[0].source == "version_diff"
        assert chunk_changes[0].confidence == 1.0
        assert chunk_changes[0].detail == "Increased for better recall."

    def test_an_unknown_declared_change_type_falls_back(self):
        before = AgentVersionSnapshot(
            agent_version_id="v1",
            agent_id="agent-1",
            version="1.0.0",
        )
        after = AgentVersionSnapshot(
            agent_version_id="v2",
            agent_id="agent-1",
            version="2.0.0",
            declared_changes={
                "mystery": {"change_type": "not_a_real_type"},
            },
        )

        analyzer = ChangeAnalyzer()
        description = analyzer.analyze(before, after)

        mystery_changes = [
            c for c in description.changes
            if c.component == "mystery"
        ]
        assert len(mystery_changes) == 1
        assert mystery_changes[0].change_type == "unknown"

    def test_an_unknown_declared_operation_falls_back(self):
        before = AgentVersionSnapshot(
            agent_version_id="v1",
            agent_id="agent-1",
            version="1.0.0",
        )
        after = AgentVersionSnapshot(
            agent_version_id="v2",
            agent_id="agent-1",
            version="2.0.0",
            declared_changes={
                "mystery": {"operation": "not_a_real_operation"},
            },
        )

        analyzer = ChangeAnalyzer()
        description = analyzer.analyze(before, after)

        mystery_changes = [
            c for c in description.changes
            if c.component == "mystery"
        ]
        assert len(mystery_changes) == 1
        assert mystery_changes[0].operation == "unknown"


class TestChangeAnalyzerCompleteness:
    def test_no_code_hash_or_config_means_unknown_completeness(self):
        before = AgentVersionSnapshot(
            agent_version_id="v1",
            agent_id="agent-1",
            version="1.0.0",
        )
        after = AgentVersionSnapshot(
            agent_version_id="v2",
            agent_id="agent-1",
            version="2.0.0",
        )

        analyzer = ChangeAnalyzer()
        description = analyzer.analyze(before, after)

        assert description.completeness == "unknown"

    def test_code_hash_only_makes_it_unknown_completeness(self):
        before = AgentVersionSnapshot(
            agent_version_id="v1",
            agent_id="agent-1",
            version="1.0.0",
            code_hash="sha256:before",
        )
        after = AgentVersionSnapshot(
            agent_version_id="v2",
            agent_id="agent-1",
            version="2.0.0",
            code_hash="sha256:after",
        )

        analyzer = ChangeAnalyzer()
        description = analyzer.analyze(before, after)

        assert description.completeness == "unknown"

    def test_config_only_makes_it_unknown_completeness(self):
        before = AgentVersionSnapshot(
            agent_version_id="v1",
            agent_id="agent-1",
            version="1.0.0",
            config={"chunk_size": 256},
        )
        after = AgentVersionSnapshot(
            agent_version_id="v2",
            agent_id="agent-1",
            version="2.0.0",
            config={"chunk_size": 512},
        )

        analyzer = ChangeAnalyzer()
        description = analyzer.analyze(before, after)

        assert description.completeness == "unknown"


class TestChangeAnalyzerPartialCompleteness:
    def test_undocumented_config_path_makes_it_partial(self):
        before = AgentVersionSnapshot(
            agent_version_id="v1",
            agent_id="agent-1",
            version="1.0.0",
            code_hash="sha256:before",
            config={},
        )
        after = AgentVersionSnapshot(
            agent_version_id="v2",
            agent_id="agent-1",
            version="2.0.0",
            code_hash="sha256:after",
            config={"custom_key": "value"},
        )

        analyzer = ChangeAnalyzer()
        description = analyzer.analyze(before, after)

        assert description.completeness == "partial"

    def test_a_declared_change_without_detection_makes_it_partial(self):
        before = AgentVersionSnapshot(
            agent_version_id="v1",
            agent_id="agent-1",
            version="1.0.0",
            code_hash="sha256:before",
            config={"chunk_size": 256},
        )
        after = AgentVersionSnapshot(
            agent_version_id="v2",
            agent_id="agent-1",
            version="2.0.0",
            code_hash="sha256:after",
            config={"chunk_size": 512},
            declared_changes={
                "retriever": {"change_type": "retriever"},
            },
        )

        analyzer = ChangeAnalyzer()
        description = analyzer.analyze(before, after)

        assert description.completeness == "partial"


class TestChangeAnalyzerLimitationsAndMetadata:
    def test_limitations_include_missing_code_hash(self):
        before = AgentVersionSnapshot(
            agent_version_id="v1",
            agent_id="agent-1",
            version="1.0.0",
        )
        after = AgentVersionSnapshot(
            agent_version_id="v2",
            agent_id="agent-1",
            version="2.0.0",
        )

        analyzer = ChangeAnalyzer()
        description = analyzer.analyze(before, after)

        assert any(
            "Code changes are not observable" in limitation
            for limitation in description.limitations
        )

    def test_limitations_include_missing_config(self):
        before = AgentVersionSnapshot(
            agent_version_id="v1",
            agent_id="agent-1",
            version="1.0.0",
        )
        after = AgentVersionSnapshot(
            agent_version_id="v2",
            agent_id="agent-1",
            version="2.0.0",
        )

        analyzer = ChangeAnalyzer()
        description = analyzer.analyze(before, after)

        assert any(
            "Configuration changes are not observable" in limitation
            for limitation in description.limitations
        )

    def test_metadata_records_from_and_to_versions(self):
        before = AgentVersionSnapshot(
            agent_version_id="v1",
            agent_id="agent-1",
            version="1.0.0",
        )
        after = AgentVersionSnapshot(
            agent_version_id="v2",
            agent_id="agent-1",
            version="2.0.0",
        )

        analyzer = ChangeAnalyzer()
        description = analyzer.analyze(before, after)

        assert description.metadata["from_version"] == "1.0.0"
        assert description.metadata["to_version"] == "2.0.0"

    def test_changes_are_deterministically_ordered(self):
        before = AgentVersionSnapshot(
            agent_version_id="v1",
            agent_id="agent-1",
            version="1.0.0",
            config={"chunk_size": 256, "retriever": {"type": "hnsw"}},
        )
        after = AgentVersionSnapshot(
            agent_version_id="v2",
            agent_id="agent-1",
            version="2.0.0",
            config={"chunk_size": 512, "retriever": {"type": "flat"}},
        )

        analyzer = ChangeAnalyzer()
        description = analyzer.analyze(before, after)
        changes = description.changes

        keys = [(c.component, c.change_type, c.operation) for c in changes]
        assert keys == sorted(keys)
