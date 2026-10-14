from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from uuid import uuid4

from sqlalchemy.orm import Session

from ai_eval_platform.analysis.evidence import EvidenceCollection
from ai_eval_platform.analysis.evidence_adapter import EvidenceAdapter
from ai_eval_platform.analysis.relationship_analyzer import (
    EvidenceRelationship,
    RelationshipAnalyzer,
)
from ai_eval_platform.storage.models import (
    Analysis,
    PersistedRelationship,
    RelationshipEvidence,
    new_id,
)
from ai_eval_platform.storage.repositories import (
    AnalysisRepository,
    EvidenceRepository,
    RelationshipRepository,
)


class AnalysisReplayConflictError(ValueError):
    """A requested Analysis ID already belongs to different input evidence."""


@dataclass(frozen=True)
class RelationshipAnalysisResult:
    analysis_id: str
    relationships: tuple[PersistedRelationship, ...]
    evidence_count: int


class RelationshipAnalysisService:
    """Analyze persisted evidence without taking ownership of the transaction.

    A normal invocation creates a new Analysis for history. Supplying an
    existing ``analysis_id`` requests an idempotent replay and is accepted only
    when scope, evidence fingerprint, and analyzer version match.
    """

    ANALYSIS_TYPE = "relationship_analysis"
    ALGORITHM_NAME = "relationship_analyzer"
    ALGORITHM_VERSION = "1.0.0"
    PAGE_SIZE = 250

    def __init__(
        self,
        session: Session,
        *,
        analyzer: RelationshipAnalyzer | None = None,
        adapter: EvidenceAdapter | None = None,
        page_size: int = PAGE_SIZE,
    ) -> None:
        if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 1000:
            raise ValueError("page_size must be an integer between 1 and 1000")
        self._session = session
        self._evidence = EvidenceRepository(session)
        self._analyses = AnalysisRepository(session)
        self._relationships = RelationshipRepository(session)
        self._analyzer = analyzer or RelationshipAnalyzer()
        self._page_size = page_size
        if adapter is None:
            dialect_name = session.get_bind().dialect.name
            naive_timezone = timezone.utc if dialect_name == "sqlite" else None
            adapter = EvidenceAdapter(naive_timestamp_timezone=naive_timezone)
        self._adapter = adapter

    def analyze_subject(
        self,
        *,
        domain: str,
        subject_type: str,
        subject_id: str,
        observed_after: datetime | None = None,
        observed_before: datetime | None = None,
        analysis_id: str | None = None,
    ) -> RelationshipAnalysisResult:
        self._validate_text(domain, "domain")
        self._validate_text(subject_type, "subject_type")
        self._validate_text(subject_id, "subject_id")
        if analysis_id is not None:
            self._validate_text(analysis_id, "analysis_id")
            if len(analysis_id) > 64:
                raise ValueError("analysis_id must not exceed 64 characters")
        self._validate_window(observed_after, observed_before)

        self._session.flush()
        persisted_records = self._load_all(
            subject_type=subject_type,
            subject_id=subject_id,
            domain=domain,
            observed_after=observed_after,
            observed_before=observed_before,
        )
        evidence = self._adapter.adapt_many(persisted_records)
        collection = EvidenceCollection(records=evidence)
        generated = self._analyzer.analyze(collection)

        analysis_scope = {
            "domain": domain,
            "subject_type": subject_type,
            "subject_id": subject_id,
            "observed_after": (
                observed_after.isoformat()
                if observed_after is not None
                else None
            ),
            "observed_before": (
                observed_before.isoformat()
                if observed_before is not None
                else None
            ),
        }
        evidence_payload = [record.model_dump(mode="json") for record in evidence]
        evidence_digest = hashlib.sha256(
            json.dumps(
                evidence_payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        analysis_metrics = {
            "scope": analysis_scope,
            "evidence_count": len(evidence),
            "evidence_ids": [record.evidence_id for record in evidence],
            "evidence_digest": evidence_digest,
            "relationship_count": len(generated),
            "algorithm_name": self.ALGORITHM_NAME,
            "algorithm_version": self.ALGORITHM_VERSION,
        }

        requested_analysis_id = analysis_id or new_id()
        existing_analysis = (
            self._analyses.get(requested_analysis_id)
            if analysis_id is not None
            else None
        )
        if existing_analysis is not None:
            existing_metrics = existing_analysis.metrics or {}
            if (
                existing_analysis.analysis_type != self.ANALYSIS_TYPE
                or existing_metrics.get("scope") != analysis_scope
                or existing_metrics.get("evidence_digest") != evidence_digest
                or existing_metrics.get("algorithm_version")
                != self.ALGORITHM_VERSION
            ):
                raise AnalysisReplayConflictError(
                    f"Analysis '{requested_analysis_id}' cannot be replayed "
                    "with different scope, evidence, or algorithm version."
                )

        self._ensure_sqlite_outer_transaction()
        with self._session.begin_nested():
            if existing_analysis is None:
                analysis = self._analyses.create(
                    Analysis(
                        analysis_id=requested_analysis_id,
                        analysis_type=self.ANALYSIS_TYPE,
                        summary=(
                            f"Generated {len(generated)} relationship candidate(s) "
                            f"from {len(evidence)} evidence record(s)."
                        ),
                        metrics=analysis_metrics,
                    )
                )
            else:
                analysis = existing_analysis

            persisted_relationships = tuple(
                self._persist_relationship(analysis.analysis_id, relationship)
                for relationship in generated
            )

        return RelationshipAnalysisResult(
            analysis_id=analysis.analysis_id,
            relationships=persisted_relationships,
            evidence_count=len(evidence),
        )

    def _ensure_sqlite_outer_transaction(self) -> None:
        """Make SAVEPOINT rollback atomic under sqlite's legacy transaction mode."""
        connection = self._session.connection()
        if connection.dialect.name != "sqlite":
            return

        driver_connection = connection.connection.driver_connection
        if not driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN")

    def _load_all(
        self,
        *,
        subject_type: str,
        subject_id: str,
        domain: str,
        observed_after: datetime | None,
        observed_before: datetime | None,
    ):
        records = []
        offset = 0
        while True:
            page = self._evidence.list_for_subject(
                subject_type,
                subject_id,
                domain=domain,
                observed_after=observed_after,
                observed_before=observed_before,
                limit=self._page_size,
                offset=offset,
            )
            records.extend(page)
            if len(page) < self._page_size:
                return records
            offset += len(page)

    def _persist_relationship(
        self,
        analysis_id: str,
        relationship: EvidenceRelationship,
    ) -> PersistedRelationship:
        existing = self._relationships.get_by_analysis_key(
            analysis_id,
            relationship.relationship_id,
        )
        persisted_id = (
            existing.relationship_id
            if existing is not None
            else str(uuid4())
        )
        persisted = PersistedRelationship(
            relationship_id=persisted_id,
            analysis_id=analysis_id,
            relationship_key=relationship.relationship_id,
            relationship_type=relationship.relationship_type,
            basis=relationship.basis,
            status="candidate",
            algorithm_name=self.ALGORITHM_NAME,
            algorithm_version=self.ALGORITHM_VERSION,
            scope={
                "agent_id": relationship.agent_id,
                "system_id": relationship.system_id,
                "system_version": relationship.system_version,
                "evaluation_run_id": relationship.evaluation_run_id,
                "run_id": relationship.run_id,
            },
            attributes=relationship.attributes,
            limitations=list(relationship.limitations),
        )
        links = [
            RelationshipEvidence(
                relationship_evidence_id=new_id(),
                relationship_id=persisted_id,
                evidence_id=evidence_id,
                role="supporting",
                evidence_order=index,
            )
            for index, evidence_id in enumerate(relationship.evidence_ids)
        ]
        stored, _ = self._relationships.insert_with_evidence_ignore_duplicates(
            persisted,
            links,
        )
        return stored

    @staticmethod
    def _validate_text(value: str, name: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")

    @staticmethod
    def _validate_window(
        observed_after: datetime | None,
        observed_before: datetime | None,
    ) -> None:
        for name, value in (
            ("observed_after", observed_after),
            ("observed_before", observed_before),
        ):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{name} must be timezone-aware")
        if (
            observed_after is not None
            and observed_before is not None
            and observed_after > observed_before
        ):
            raise ValueError("observed_after must not be later than observed_before")