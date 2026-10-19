from __future__ import annotations

from sqlalchemy.orm import Session

from ai_eval_platform.root_cause.domain import RCAReport
from ai_eval_platform.storage.models import Analysis, RootCauseAnalysis, new_id
from ai_eval_platform.storage.repositories import (
    AnalysisRepository,
    EntityNotFoundError,
    RepositoryError,
    RootCauseAnalysisRepository,
)


class RCAReportPersistenceError(RepositoryError):
    pass


class RCAReportService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._analyses = AnalysisRepository(session)
        self._reports = RootCauseAnalysisRepository(session)

    def persist_report(
        self,
        report: RCAReport,
        *,
        analysis_id: str | None = None,
        root_cause_analysis_id: str | None = None,
    ) -> RootCauseAnalysis:
        payload = report.model_dump(mode="json")
        scope = payload.get("scope") or {}
        resolved_analysis_id = analysis_id or payload.get("analysis_id") or new_id()
        existing_analysis = None
        if analysis_id is not None or payload.get("analysis_id"):
            existing_analysis = self._analyses.get(resolved_analysis_id)
        if existing_analysis is None:
            self._analyses.create(
                Analysis(
                    analysis_id=resolved_analysis_id,
                    analysis_type="root_cause_analysis",
                    status="completed" if report.status == "completed" else "running",
                    summary=(
                        f"RCA {report.investigation_id}: {report.conclusion_type} "
                        f"at {report.conclusion_level} "
                        f"from {len(report.hypotheses)} hypothese(s)."
                    ),
                    findings={},
                    metrics={
                        "investigation_id": report.investigation_id,
                        "evidence_fingerprint": report.evidence_fingerprint,
                        "algorithm_name": RCAReportService.algorithm_name(),
                        "algorithm_version": RCAReportService.algorithm_version(),
                    },
                )
            )
        stored = RootCauseAnalysis(
            root_cause_analysis_id=(
                root_cause_analysis_id or f"rca-{report.investigation_id}"
            ),
            analysis_id=resolved_analysis_id,
            investigation_id=report.investigation_id,
            trigger_type=report.trigger_type,
            status=report.status,
            conclusion_type=report.conclusion_type,
            conclusion_level=report.conclusion_level,
            schema_version=report.schema_version,
            algorithm_name=RCAReportService.algorithm_name(),
            algorithm_version=RCAReportService.algorithm_version(),
            evidence_fingerprint=report.evidence_fingerprint,
            summary=None,
            report=payload,
            suspected_causes=[
                {
                    "hypothesis_id": hypothesis.hypothesis_id,
                    "rule_id": hypothesis.rule_ids[0] if hypothesis.rule_ids else None,
                    "title": hypothesis.title,
                    "statement": hypothesis.statement,
                    "status": hypothesis.status,
                    "strength": hypothesis.strength,
                }
                for hypothesis in report.hypotheses
            ],
            evidence_event_ids=sorted(
                {
                    reference.evidence_id
                    for hypothesis in report.hypotheses
                    for reference in hypothesis.evidence
                }
            ),
            affected_slices={
                "domain": scope.get("domain"),
                "subject_type": scope.get("subject_type"),
                "subject_id": scope.get("subject_id"),
                "pipeline_run_id": scope.get("pipeline_run_id"),
                "environment": scope.get("environment"),
            },
            confidence=None,
            conclusion=report.conclusion_type,
        )
        stored_report = RCAReport.model_validate(dict(stored.report))
        if stored_report != report:
            raise RCAReportPersistenceError(
                "Persisted RCA report payload does not round-trip"
            )
        self._session.add(stored)
        self._session.flush()
        return stored

    def get_by_investigation(self, investigation_id: str) -> RCAReport:
        row = self._reports.get_by_investigation(investigation_id)
        if row is None:
            raise EntityNotFoundError(
                f"RCA report '{investigation_id}' does not exist"
            )
        return RCAReport.model_validate(dict(row.report))

    def get_for_analysis(self, analysis_id: str) -> RCAReport:
        row = self._reports.get_for_analysis(analysis_id)
        if row is None:
            raise EntityNotFoundError(
                f"RCA report for analysis '{analysis_id}' does not exist"
            )
        return RCAReport.model_validate(dict(row.report))

    @staticmethod
    def algorithm_name() -> str:
        from ai_eval_platform.root_cause.analyzer import RCAAnalyzer

        return RCAAnalyzer.algorithm_name

    @staticmethod
    def algorithm_version() -> str:
        from ai_eval_platform.root_cause.analyzer import RCAAnalyzer

        return RCAAnalyzer.algorithm_version
