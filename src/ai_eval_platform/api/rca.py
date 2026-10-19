from __future__ import annotations

from collections.abc import Generator

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.orm import Session

from ai_eval_platform.api.models import RCAReportResponse
from ai_eval_platform.root_cause.domain import report_limit_violations
from ai_eval_platform.storage.postgres import get_session
from ai_eval_platform.storage.repositories import RootCauseAnalysisRepository

router = APIRouter(prefix="/rca", tags=["rca"])


def _session() -> Generator[Session, None, None]:
    session = get_session()
    try:
        yield session
    finally:
        session.close()


def _response_for_investigation(
    session: Session, investigation_id: str
) -> RCAReportResponse:
    repository = RootCauseAnalysisRepository(session)
    row = repository.get_by_investigation(investigation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="RCA report not found")
    violations = report_limit_violations(dict(row.report))
    if violations:
        raise HTTPException(
            status_code=400,
            detail="RCA report retrieval blocked: " + "; ".join(violations),
        )
    return RCAReportResponse.model_validate(
        {
            **dict(row.report),
            "analysis_id": row.analysis_id,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
    )


@router.get("/reports/{investigation_id}", response_model=RCAReportResponse)
def get_rca_report(
    investigation_id: str = Path(min_length=1, max_length=64),
    session: Session = Depends(_session),
) -> RCAReportResponse:
    return _response_for_investigation(session, investigation_id)


@router.get("/analyses/{analysis_id}/report", response_model=RCAReportResponse)
def get_rca_report_for_analysis(
    analysis_id: str = Path(min_length=1, max_length=64),
    session: Session = Depends(_session),
) -> RCAReportResponse:
    repository = RootCauseAnalysisRepository(session)
    row = repository.get_for_analysis(analysis_id)
    if row is None:
        raise HTTPException(status_code=404, detail="RCA report not found")
    return _response_for_investigation(session, row.investigation_id)
