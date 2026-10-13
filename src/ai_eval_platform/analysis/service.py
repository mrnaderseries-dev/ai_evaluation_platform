from sqlalchemy.orm import Session

from ai_eval_platform.analysis.engine import SignalSummaryEngine
from ai_eval_platform.analysis.models import AnalysisResult
from ai_eval_platform.storage.repositories import PersistedSignalRepository


class AnalysisService:
    """Loads persisted evidence, then delegates analysis to a pure engine."""

    def __init__(
        self,
        session: Session,
        engine: SignalSummaryEngine | None = None,
    ) -> None:
        self._signals = PersistedSignalRepository(session)
        self._engine = engine or SignalSummaryEngine()

    def summarize_agent(
        self,
        agent_id: str,
        *,
        limit: int = 1000,
        offset: int = 0,
    ) -> AnalysisResult:
        if limit < 1 or limit > 10000:
            raise ValueError("limit must be between 1 and 10000")

        if offset < 0:
            raise ValueError("offset must be non-negative")

        if not agent_id or not str(agent_id).strip():
            raise ValueError("agent_id must not be empty")

        self._signals.session.flush()

        signals = self._signals.list_for_agent(
            agent_id,
            limit=limit + 1,
            offset=offset,
        )

        truncated = len(signals) > limit
        page = signals[:limit]

        result = self._engine.analyze(
            page,
            agent_id=agent_id,
        )

        if truncated:
            result.limitations.append(
                f"Result truncated at {limit} signal(s); additional records may exist."
            )

        return result
