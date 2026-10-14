from datetime import datetime, tzinfo
from typing import Any, get_args

from pydantic import ValidationError

from ai_eval_platform.analysis.evidence import (
    EvidenceKind,
    EvidenceRecord,
    EvidenceSource,
)
from ai_eval_platform.storage.models import PersistedEvidenceRecord


class EvidenceAdaptationError(ValueError):
    """Persisted evidence cannot satisfy the analyzer evidence contract."""


class EvidenceAdapter:
    """Convert persisted evidence without inventing absent analyzer inputs.

    SQLite does not retain timezone information for ``DateTime(timezone=True)``.
    A service bound to SQLite may explicitly supply UTC for those materialized
    naive values; other callers reject them unless they provide that policy.
    """

    def __init__(self, naive_timestamp_timezone: tzinfo | None = None) -> None:
        self._naive_timestamp_timezone = naive_timestamp_timezone

    def adapt(self, persisted: PersistedEvidenceRecord) -> EvidenceRecord:
        evidence_id = getattr(persisted, "evidence_id", None)
        try:
            source = self._supported_value(
                getattr(persisted, "source", None),
                get_args(EvidenceSource),
                "source",
            )
            evidence_kind = self._supported_value(
                getattr(persisted, "evidence_kind", None),
                get_args(EvidenceKind),
                "evidence_kind",
            )
            attributes = self._required_mapping(
                getattr(persisted, "attributes", None),
                "attributes",
            )
            source_snapshot = self._required_mapping(
                getattr(persisted, "source_snapshot", None),
                "source_snapshot",
            )
            limitations = getattr(persisted, "limitations", None)
            if not isinstance(limitations, (list, tuple)):
                raise EvidenceAdaptationError(
                    "limitations must be a list or tuple"
                )

            observed_at = self._timestamp(
                getattr(persisted, "observed_at", None),
                "observed_at",
            )
            persisted_at_value = getattr(persisted, "created_at", None)
            persisted_at = (
                self._timestamp(persisted_at_value, "created_at")
                if persisted_at_value is not None
                else None
            )

            return EvidenceRecord(
                evidence_id=evidence_id,
                source=source,
                source_ref=getattr(persisted, "source_ref", None),
                evidence_kind=evidence_kind,
                agent_id=getattr(persisted, "agent_id", None),
                domain=getattr(persisted, "domain", None),
                subject_type=getattr(persisted, "subject_type", None),
                subject_id=getattr(persisted, "subject_id", None),
                system_id=getattr(persisted, "system_id", None),
                system_version=getattr(persisted, "system_version", None),
                run_id=getattr(persisted, "run_id", None),
                evaluation_run_id=getattr(persisted, "evaluation_run_id", None),
                trace_id=getattr(persisted, "trace_id", None),
                span_id=getattr(persisted, "span_id", None),
                parent_span_id=getattr(persisted, "parent_span_id", None),
                component_id=getattr(persisted, "component_id", None),
                observed_at=observed_at,
                persisted_at=persisted_at,
                attributes=attributes,
                source_snapshot=source_snapshot,
                limitations=tuple(limitations),
            )
        except EvidenceAdaptationError as exc:
            raise EvidenceAdaptationError(
                f"Evidence '{evidence_id}' cannot be adapted: {exc}"
            ) from exc
        except ValidationError as exc:
            raise EvidenceAdaptationError(
                f"Evidence '{evidence_id}' violates the EvidenceRecord contract: {exc}"
            ) from exc

    def adapt_many(
        self,
        persisted_records: tuple[PersistedEvidenceRecord, ...]
        | list[PersistedEvidenceRecord],
    ) -> tuple[EvidenceRecord, ...]:
        return tuple(self.adapt(record) for record in persisted_records)

    @staticmethod
    def _supported_value(value: Any, supported: tuple[str, ...], name: str) -> str:
        if not isinstance(value, str) or value not in supported:
            raise EvidenceAdaptationError(
                f"{name} must be one of {', '.join(supported)}"
            )
        return value

    @staticmethod
    def _required_mapping(value: Any, name: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise EvidenceAdaptationError(f"{name} must be a dictionary")
        return dict(value)

    def _timestamp(self, value: Any, name: str) -> datetime:
        if not isinstance(value, datetime):
            raise EvidenceAdaptationError(f"{name} must be a datetime")
        if value.tzinfo is None or value.utcoffset() is None:
            if self._naive_timestamp_timezone is None:
                raise EvidenceAdaptationError(
                    f"{name} must be timezone-aware"
                )
            return value.replace(tzinfo=self._naive_timestamp_timezone)
        return value