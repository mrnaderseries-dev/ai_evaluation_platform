from collections.abc import Callable
from contextlib import AbstractContextManager, suppress
from datetime import datetime, timedelta
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ai_eval_platform.monitoring_integration import (
    RunMonitoringResult,
    monitor_completed_run,
)
from ai_eval_platform.storage.models import utc_now
from ai_eval_platform.storage.repositories import RunRepository


class RunStateTransitionError(Exception):
    pass


class MonitoringClaimLostError(Exception):
    pass


class RunLifecycleResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    monitoring: Literal["completed", "skipped", "contended"]
    monitoring_result: RunMonitoringResult | None = None


class RunLifecycleService:

    def __init__(
        self,
        *,
        session_factory: Callable[[], AbstractContextManager[Session]],
        clock: Callable[[], datetime] | None = None,
        monitoring_lease: timedelta = timedelta(minutes=5),
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock or utc_now
        self._monitoring_lease = monitoring_lease

    def complete_run(self, run_id: str) -> RunLifecycleResult:
        claim_id = str(uuid4())

        with self._session_factory() as session:
            repository = RunRepository(session)
            run = repository.get_required(run_id)

            if run.status == "failed":
                raise RunStateTransitionError(
                    f"run '{run_id}' has status 'failed' and cannot be completed."
                )

            if run.status == "completed" and run.monitoring_completed_at is not None:
                return RunLifecycleResult(
                    run_id=run_id,
                    status=run.status,
                    monitoring="skipped",
                )

            now = self._clock()
            claimed = repository.claim_monitoring(
                run_id,
                claim_id,
                claimed_at=now,
                expires_at=now + self._monitoring_lease,
            )

            if not claimed:
                session.expire(run)
                run = repository.get_required(run_id)

                if run.status == "failed":
                    raise RunStateTransitionError(
                        f"run '{run_id}' has status 'failed' and cannot be completed."
                    )

                if run.monitoring_completed_at is not None:
                    return RunLifecycleResult(
                        run_id=run_id,
                        status=run.status,
                        monitoring="skipped",
                    )

                return RunLifecycleResult(
                    run_id=run_id,
                    status=run.status,
                    monitoring="contended",
                )

            if run.status != "completed":
                repository.update_status(
                    run_id,
                    "completed",
                    completed_at=self._clock(),
                )

        try:
            monitoring_result = monitor_completed_run(
                run_id,
                session_factory=self._session_factory,
            )

            with self._session_factory() as session:
                repository = RunRepository(session)
                marked = repository.mark_monitoring_completed(
                    run_id,
                    self._clock(),
                    claim_id,
                )

                if not marked:
                    run = repository.get_required(run_id)

                    if run.monitoring_completed_at is None:
                        raise MonitoringClaimLostError(
                            f"monitoring claim for run '{run_id}' was lost before completion."
                        )

                    return RunLifecycleResult(
                        run_id=run_id,
                        status=run.status,
                        monitoring="skipped",
                    )

            return RunLifecycleResult(
                run_id=run_id,
                status="completed",
                monitoring="completed",
                monitoring_result=monitoring_result,
            )

        except BaseException:
            with suppress(Exception):
                self._release_monitoring_claim(run_id, claim_id)

            raise

    def _release_monitoring_claim(self, run_id: str, claim_id: str) -> None:
        with self._session_factory() as session:
            RunRepository(session).release_monitoring_claim(run_id, claim_id)

    def fail_run(
        self,
        run_id: str,
        *,
        error_type: str,
        error_message: str | None = None,
    ) -> RunLifecycleResult:
        with self._session_factory() as session:
            repository = RunRepository(session)
            run = repository.get_required(run_id)

            if run.status == "completed":
                raise RunStateTransitionError(
                    f"run '{run_id}' has status 'completed' and cannot be failed."
                )

            if run.status == "failed":
                return RunLifecycleResult(
                    run_id=run_id,
                    status=run.status,
                    monitoring="skipped",
                )

            transitioned = repository.mark_failed(
                run_id,
                completed_at=self._clock(),
                error_type=error_type,
                error_message=error_message,
            )

            if not transitioned:
                session.expire(run)
                run = repository.get_required(run_id)

                if run.status == "completed":
                    raise RunStateTransitionError(
                        f"run '{run_id}' has status 'completed' and cannot be failed."
                    )

                return RunLifecycleResult(
                    run_id=run_id,
                    status=run.status,
                    monitoring="skipped",
                )

        return RunLifecycleResult(
            run_id=run_id,
            status="failed",
            monitoring="skipped",
        )


__all__ = [
    "MonitoringClaimLostError",
    "RunLifecycleResult",
    "RunLifecycleService",
    "RunStateTransitionError",
]