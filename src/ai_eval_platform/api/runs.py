from __future__ import annotations

from collections.abc import Generator

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_eval_platform.api.models import ObservationResponse, SignalResponse
from ai_eval_platform.storage.models import EventObservation, PersistedSignal, Run
from ai_eval_platform.storage.postgres import get_session

router = APIRouter(prefix="/runs", tags=["runs"])


def _session() -> Generator[Session, None, None]:
    session = get_session()
    try:
        yield session
    finally:
        session.close()


@router.get("/{run_id}/observations", response_model=list[ObservationResponse])
def list_run_observations(
    run_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(_session),
) -> list[EventObservation]:
    if session.get(Run, run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")
    statement = (
        select(EventObservation)
        .where(EventObservation.run_id == run_id)
        .order_by(EventObservation.sequence_number, EventObservation.observation_id)
        .limit(limit)
        .offset(offset)
    )
    return list(session.scalars(statement))


@router.get("/{run_id}/signals", response_model=list[SignalResponse])
def list_run_signals(
    run_id: str,
    origin: str | None = Query(default=None, pattern="^(base_observation|detector|analysis)$"),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(_session),
) -> list[PersistedSignal]:
    if session.get(Run, run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")
    statement = select(PersistedSignal).where(PersistedSignal.run_id == run_id)
    if origin is not None:
        statement = statement.where(PersistedSignal.origin == origin)
    statement = (
        statement.order_by(PersistedSignal.detected_at, PersistedSignal.signal_id)
        .limit(limit)
        .offset(offset)
    )
    return list(session.scalars(statement))
