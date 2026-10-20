"""Pure, deterministic change analysis between two agent versions.

The analyzer compares two snapshots of an agent version and reports *known*
differences. It never executes the agent, never guesses semantics from
arbitrary config keys, and never infers a behavioural change from a version
label alone.

Key behaviours:

* Missing values are missing evidence, not removals. A field that is
  ``None`` on both sides is "not recorded"; a field that is ``None`` on one
  side and set on the other is an *addition*, not a removal.
* Unknown config paths stay generic (``configuration``) or ``unknown``.
* Secrets are redacted; only fingerprints are emitted.
* Output ordering is deterministic.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from .domain import (
    ChangeCompleteness,
    ChangeDescription,
    ComponentChange,
)

#: Config paths with documented, agreed semantics. Only these are mapped to
#: a specific change type; every other path stays generic.
DOCUMENTED_CONFIG_PATHS: Mapping[str, str] = {
    "embedding_model": "embedding",
    "embedding.model": "embedding",
    "embedding_model_name": "embedding",
    "vector_store": "vector_store",
    "vector_store.index": "vector_store",
    "vector_store.type": "vector_store",
    "retriever": "retriever",
    "retriever.type": "retriever",
    "retriever.top_k": "retriever",
    "retriever.embedding_model": "embedding",
    "chunk_size": "chunking",
    "chunk_overlap": "chunking",
    "chunking.chunk_size": "chunking",
    "chunking.chunk_overlap": "chunking",
    "tools": "tool",
    "tool_config": "tool",
    "context": "context",
    "context.window": "context",
    "policy": "policy",
    "policy.version": "policy",
}

#: Config paths whose values are secret-bearing and must never be emitted.
SENSITIVE_CONFIG_PATHS: frozenset[str] = frozenset(
    {
        "api_key",
        "apikey",
        "secret",
        "token",
        "password",
        "passwd",
        "credential",
        "credentials",
        "authorization",
        "private_key",
        "access_key",
        "cookie",
        "bearer",
        "signing_key",
    }
)

_SENSITIVE_KEY_TOKENS = (
    "api_key",
    "apikey",
    "secret",
    "token",
    "password",
    "passwd",
    "credential",
    "authorization",
    "private_key",
    "access_key",
    "cookie",
    "bearer",
    "signing_key",
)

_SECRET_VALUE_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{16,}"),
)

REDACTED = "[REDACTED]"

#: Version fields compared directly, with their change type.
SEMANTIC_VERSION_FIELDS: Mapping[str, str] = {
    "code_hash": "code",
    "prompt_version": "prompt",
    "model_provider": "model",
    "model_name": "model",
}

#: Change types that may be declared explicitly.
_KNOWN_CHANGE_TYPES: frozenset[str] = frozenset(
    {
        "code",
        "model",
        "prompt",
        "retriever",
        "embedding",
        "vector_store",
        "chunking",
        "tool",
        "context",
        "policy",
        "dependency",
        "configuration",
        "unknown",
    }
)

#: Operations that may be declared explicitly.
_KNOWN_CHANGE_OPERATIONS: frozenset[str] = frozenset(
    {"added", "removed", "modified", "unknown"}
)


class CrossAgentComparisonError(ValueError):
    """Raised when two versions belonging to different agents are compared."""


def _is_sensitive_key(key: str) -> bool:
    """Return ``True`` when a config key names a secret-bearing value.

    Matching is deliberately precise: ``tokenizer`` must not be mistaken for
    ``token``, so exact segment matches and explicit compound suffixes are
    used instead of a naive substring test.
    """
    lowered = key.lower()

    if lowered in SENSITIVE_CONFIG_PATHS:
        return True

    return lowered.endswith(
        (
            "_api_key",
            "_secret",
            "_password",
            "_token",
            "_credential",
            "_credentials",
            "_private_key",
            "_access_key",
            "_signing_key",
        )
    )


def _is_sensitive_path(path: str) -> bool:
    """Return ``True`` when any segment of a dotted config path is secret."""
    return any(
        _is_sensitive_key(segment) for segment in path.split(".") if segment
    )



def contains_secret(value: Any) -> bool:
    """Return ``True`` when ``value`` looks like it carries a credential."""
    if isinstance(value, str):
        return any(pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS)

    if isinstance(value, Mapping):
        return any(
            _is_sensitive_key(str(key)) or contains_secret(item)
            for key, item in value.items()
        )

    if isinstance(value, (list, tuple)):
        return any(contains_secret(item) for item in value)

    return False


def fingerprint(value: Any) -> str:
    """Return a stable, non-reversible fingerprint for ``value``.

    The fingerprint is safe to persist and log: it identifies *that* a value
    changed without exposing the value itself.
    """
    if isinstance(value, str):
        payload: Any = value
    else:
        try:
            payload = json.dumps(value, sort_keys=True, default=str)
        except (TypeError, ValueError):
            payload = repr(value)

    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    return f"sha256:{digest[:32]}"


def redact(value: Any) -> Any:
    """Return a redacted, log-safe representation of ``value``."""
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}

        for key, item in value.items():
            key_str = str(key)

            if _is_sensitive_key(key_str):
                redacted[key_str] = REDACTED
            else:
                redacted[key_str] = redact(item)

        return redacted

    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]

    if isinstance(value, str) and contains_secret(value):
        return REDACTED

    return value



@dataclass(frozen=True)
class AgentVersionSnapshot:
    """Minimal, framework-independent view of one agent version.

    This mirrors the persisted ``AgentVersion`` storage model without
    depending on SQLAlchemy, so the analyzer stays a pure function.
    """

    agent_version_id: str
    agent_id: str
    version: str
    code_hash: str | None = None
    prompt_version: str | None = None
    model_provider: str | None = None
    model_name: str | None = None
    config: Mapping[str, Any] = field(default_factory=dict)
    declared_changes: Mapping[str, Any] = field(default_factory=dict)
    artifact_fingerprints: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.agent_version_id.strip():
            raise ValueError("agent_version_id must not be empty.")

        if not self.agent_id.strip():
            raise ValueError("agent_id must not be empty.")

        if not self.version.strip():
            raise ValueError("version must not be empty.")


def _compare_semantic_field(
    name: str,
    change_type: str,
    before: Any,
    after: Any,
    source: str,
) -> ComponentChange | None:
    """Compare one field with known semantics.

    ``None`` on both sides means "not recorded", which is not a change.
    ``None`` on one side means the field was *added*; it is never reported
    as a removal because the snapshot cannot distinguish "absent" from
    "unrecorded".
    """
    before_present = before is not None
    after_present = after is not None

    if not before_present and not after_present:
        return None

    if before_present and after_present and before == after:
        return None

    if before_present and not after_present:
        operation = "unknown"
    elif not before_present and after_present:
        operation = "added"
    else:
        operation = "modified"

    return ComponentChange(
        component=name,
        change_type=change_type,
        operation=operation,
        source=source,
        evidence_refs=(f"{source}:{name}",),
        before_fingerprint=(fingerprint(before) if before_present else None),
        after_fingerprint=(fingerprint(after) if after_present else None),
        confidence=1.0 if before_present and after_present else 0.5,
        detail=None,
    )



def _flatten_config(
    config: Mapping[str, Any],
    prefix: str = "",
) -> dict[str, Any]:
    """Flatten a nested config mapping into dotted paths."""
    flat: dict[str, Any] = {}

    for key, value in config.items():
        path = f"{prefix}{key}"

        if isinstance(value, Mapping):
            nested = _flatten_config(value, f"{path}.")

            if nested:
                flat.update(nested)
            else:
                flat[path] = {}
        else:
            flat[path] = value

    return flat


class ChangeAnalyzer:
    """Deterministic analyzer for differences between two agent versions.

    The analyzer is intentionally conservative:

    * It reports only differences it can point at concrete evidence for.
    * Declared (human-stated) changes are merged with detected diffs, and a
      declared change without a matching detection is kept at a lower
      confidence rather than being dropped or silently upgraded.
    * When evidence is partial, ``completeness`` says so, and the plan built
      on top of it inherits that uncertainty.
    """

    def __init__(
        self,
        documented_config_paths: Mapping[str, str] | None = None,
    ) -> None:
        self._config_paths: Mapping[str, str] = dict(
            DOCUMENTED_CONFIG_PATHS
            if documented_config_paths is None
            else documented_config_paths
        )

    def analyze(
        self,
        before: AgentVersionSnapshot,
        after: AgentVersionSnapshot,
        *,
        change_id: str | None = None,
    ) -> ChangeDescription:
        """Compare two versions of the same agent.

        Raises:
            CrossAgentComparisonError: when the snapshots belong to
                different agents. Comparing across agents is never a
                "change" of the same agent.
        """
        if before.agent_id != after.agent_id:
            raise CrossAgentComparisonError(
                "cannot compare versions of different agents "
                f"({before.agent_id!r} != {after.agent_id!r})."
            )

        detected = self._detect_changes(before, after)
        declared = self._declared_changes(after)

        merged = self._merge(detected, declared)
        limitations = self._limitations(before, after, detected, declared)
        completeness = self._completeness(
            before, after, detected, declared, limitations
        )

        return ChangeDescription(
            change_id=change_id or f"{before.agent_version_id}->{after.agent_version_id}",
            agent_id=before.agent_id,
            from_agent_version_id=before.agent_version_id,
            to_agent_version_id=after.agent_version_id,
            changes=merged,
            completeness=completeness,
            limitations=limitations,
            metadata={
                "from_version": before.version,
                "to_version": after.version,
                "detected_change_count": len(detected),
                "declared_change_count": len(declared),
            },
        )

    def _detect_changes(
        self,
        before: AgentVersionSnapshot,
        after: AgentVersionSnapshot,
    ) -> list[ComponentChange]:
        """Detect differences with concrete evidence."""
        changes: list[ComponentChange] = []

        for name, change_type in sorted(SEMANTIC_VERSION_FIELDS.items()):
            change = _compare_semantic_field(
                name,
                change_type,
                getattr(before, name, None),
                getattr(after, name, None),
                "version_diff",
            )

            if change is not None:
                changes.append(change)

        changes.extend(self._detect_artifact_changes(before, after))
        changes.extend(self._detect_config_changes(before, after))

        return changes

    def _detect_artifact_changes(
        self,
        before: AgentVersionSnapshot,
        after: AgentVersionSnapshot,
    ) -> list[ComponentChange]:
        """Compare recorded artifact fingerprints."""
        changes: list[ComponentChange] = []

        all_names = sorted(
            set(before.artifact_fingerprints) | set(after.artifact_fingerprints)
        )

        for name in all_names:
            before_value = before.artifact_fingerprints.get(name)
            after_value = after.artifact_fingerprints.get(name)

            if before_value == after_value:
                continue

            if before_value is None:
                operation = "added"
            elif after_value is None:
                operation = "removed"
            else:
                operation = "modified"

            changes.append(
                ComponentChange(
                    component=name,
                    change_type="code" if name == "code" else "unknown",
                    operation=operation,
                    source="artifact_diff",
                    evidence_refs=(f"artifact_diff:{name}",),
                    before_fingerprint=before_value,
                    after_fingerprint=after_value,
                    confidence=1.0,
                    detail=None,
                )
            )

        return changes

    def _detect_config_changes(
        self,
        before: AgentVersionSnapshot,
        after: AgentVersionSnapshot,
    ) -> list[ComponentChange]:
        """Compare configuration trees.

        A key present on one side only is reported as ``added`` or
        ``removed`` at *low* confidence, because an absent key may simply
        mean the value was never recorded.
        """
        before_flat = _flatten_config(before.config)
        after_flat = _flatten_config(after.config)
        changes: list[ComponentChange] = []

        for path in sorted(set(before_flat) | set(after_flat)):
            in_before = path in before_flat
            in_after = path in after_flat
            before_value = before_flat.get(path)
            after_value = after_flat.get(path)

            if in_before and in_after and before_value == after_value:
                continue

            change_type = self._classify_config_path(path)
            sensitive = _is_sensitive_path(path)

            if in_before and not in_after:
                operation = "removed"
                confidence = 0.5
            elif not in_before and in_after:
                operation = "added"
                confidence = 0.5
            else:
                operation = "modified"
                confidence = 1.0

            changes.append(
                ComponentChange(
                    component=path,
                    change_type=change_type,
                    operation=operation,
                    source="version_diff",
                    evidence_refs=(f"config:{path}",),
                    before_fingerprint=(
                        None
                        if sensitive or not in_before
                        else fingerprint(before_value)
                    ),
                    after_fingerprint=(
                        None
                        if sensitive or not in_after
                        else fingerprint(after_value)
                    ),
                    confidence=confidence,
                    detail=None,
                )
            )

        return changes

    def _declared_changes(
        self,
        after: AgentVersionSnapshot,
    ) -> list[ComponentChange]:
        """Normalise human-declared changes into component changes.

        Declared changes are statements of intent. They are trusted as
        *evidence that something changed*, but not as proof of the exact
        before/after values, so their confidence is capped.
        """
        changes: list[ComponentChange] = []

        for component, payload in sorted(after.declared_changes.items()):
            if isinstance(payload, Mapping):
                raw_type = str(payload.get("change_type", "unknown"))
                raw_operation = str(payload.get("operation", "unknown"))
                detail = payload.get("detail")
            else:
                raw_type = "unknown"
                raw_operation = "unknown"
                detail = None

            change_type = (
                raw_type if raw_type in _KNOWN_CHANGE_TYPES else "unknown"
            )
            operation = (
                raw_operation
                if raw_operation in _KNOWN_CHANGE_OPERATIONS
                else "unknown"
            )

            changes.append(
                ComponentChange(
                    component=component,
                    change_type=change_type,
                    operation=operation,
                    source="declared",
                    evidence_refs=(f"declared:{component}",),
                    before_fingerprint=None,
                    after_fingerprint=None,
                    confidence=0.8,
                    detail=(
                        str(detail) if detail is not None else None
                    ),
                )
            )

        return changes

    def _classify_config_path(self, path: str) -> str:
        """Map a config path to a change type using documented semantics."""
        if path in self._config_paths:
            return self._config_paths[path]

        return "configuration"



    def _merge(
        self,
        detected: list[ComponentChange],
        declared: list[ComponentChange],
    ) -> tuple[ComponentChange, ...]:
        """Merge detected and declared changes, keeping the result sorted.

        A declared change that matches a detected change (same component and
        change type) is folded into the detected entry: the detected entry
        wins on evidence, while the declared entry contributes its detail.
        A declared change with no detected counterpart is kept, since a
        human may know about a change the diff cannot see.
        """
        merged: dict[tuple[str, str], ComponentChange] = {}

        for change in detected:
            merged[(change.component, change.change_type)] = change

        for change in declared:
            key = (change.component, change.change_type)

            if key in merged:
                existing = merged[key]
                merged[key] = existing.model_copy(
                    update={
                        "detail": change.detail or existing.detail,
                        "evidence_refs": tuple(
                            sorted(
                                set(existing.evidence_refs)
                                | set(change.evidence_refs)
                            )
                        ),
                    }
                )
            else:
                merged[key] = change

        return tuple(
            sorted(
                merged.values(),
                key=lambda item: (
                    item.component,
                    item.change_type,
                    item.operation,
                    item.source,
                    item.after_fingerprint or "",
                ),
            )
        )

    def _limitations(
        self,
        before: AgentVersionSnapshot,
        after: AgentVersionSnapshot,
        detected: list[ComponentChange],
        declared: list[ComponentChange],
    ) -> tuple[str, ...]:
        """Collect explicit statements about what the analysis could not see."""
        limitations: list[str] = []

        if not before.code_hash and not after.code_hash:
            limitations.append(
                "Code changes are not observable: no code hash is recorded "
                "for either version."
            )

        if not before.config and not after.config:
            limitations.append(
                "Configuration changes are not observable: no configuration "
                "is recorded for either version."
            )

        unclassified = [
            change.component
            for change in detected
            if change.change_type == "configuration"
        ]

        if unclassified:
            limitations.append(
                "Undocumented configuration paths were observed without a "
                "documented semantic mapping: " + ", ".join(sorted(set(unclassified)))
            )

        declared_only = [
            change.component
            for change in declared
            if not any(
                detected_change.component == change.component
                and detected_change.change_type == change.change_type
                for detected_change in detected
            )
        ]

        if declared_only:
            limitations.append(
                "Declared changes without a corroborating diff: "
                + ", ".join(sorted(set(declared_only)))
            )

        return tuple(limitations)

    def _completeness(
        self,
        before: AgentVersionSnapshot,
        after: AgentVersionSnapshot,
        detected: list[ComponentChange],
        declared: list[ComponentChange],
        limitations: tuple[str, ...],
    ) -> ChangeCompleteness:
        """Grade how complete the change picture is.

        ``complete`` requires both versions to carry an observable code
        identity and a configuration tree, and no unclassified or
        uncorroborated findings.
        """
        observable = bool(before.code_hash or after.code_hash) and bool(
            before.config or after.config
        )

        if not observable:
            return "unknown"

        has_unclassified = any(
            change.change_type == "configuration" for change in detected
        )
        has_declared_only = any(
            "Declared changes without a corroborating diff" in limitation
            for limitation in limitations
        )

        if has_unclassified or has_declared_only:
            return "partial"

        return "complete"

