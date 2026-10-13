from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from ai_eval_platform.analysis.context import AnalysisContext
from ai_eval_platform.analysis.models import AnalysisResult
from ai_eval_platform.storage.repositories import PersistedSignalRepository


class SignalSummaryEngine:
    def analyze(self, signals, agent_id: str) -> AnalysisResult:
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise ValueError("agent_id must be a non-empty string.")
        items = list(signals) if signals is not None else []
        kept: list[object] = []
        excluded_agent = 0
        excluded_missing_id = 0
        excluded_missing_ts = 0
        for item in items:
            if isinstance(item, dict):
                item_agent = item.get("agent_id")
                item_id = item.get("signal_id")
                item_type = item.get("signal_type")
                item_ts = item.get("detected_at")
                item_detector = item.get("detector_name")
            else:
                item_agent = getattr(item, "agent_id", None)
                item_id = getattr(item, "signal_id", None)
                item_type = getattr(item, "signal_type", None)
                item_ts = getattr(item, "detected_at", None)
                item_detector = getattr(item, "detector_name", None)
            if item_agent != agent_id:
                excluded_agent += 1
                continue
            if item_id is None:
                excluded_missing_id += 1
                continue
            if isinstance(item_id, str) and not item_id.strip():
                excluded_missing_id += 1
                continue
            if not isinstance(item_id, str):
                excluded_missing_id += 1
                continue
            kept.append((str(item_id), item_type, item_ts, item_detector))
        type_counts: dict[str, int] = {}
        detector_counts: dict[str, int] = {}
        aware_times: list[datetime] = []
        for _, item_type, item_ts, item_detector in kept:
            type_key = item_type if isinstance(item_type, str) and item_type.strip() else "unknown"
            detector_key = item_detector if isinstance(item_detector, str) and item_detector.strip() else "unknown"
            type_counts[type_key] = type_counts.get(type_key, 0) + 1
            detector_counts[detector_key] = detector_counts.get(detector_key, 0) + 1
            if item_ts is None:
                excluded_missing_ts += 1
                continue
            if isinstance(item_ts, datetime):
                if item_ts.tzinfo is None or item_ts.utcoffset() is None:
                    aware_times.append(item_ts.replace(tzinfo=timezone.utc))
                else:
                    aware_times.append(item_ts)
        evidence_ids = [signal_id for signal_id, _, _, _ in kept]
        window_start = min(aware_times) if aware_times else None
        window_end = max(aware_times) if aware_times else None
        excluded: dict[str, int] = {}
        if excluded_agent:
            excluded["agent_mismatch"] = excluded_agent
        if excluded_missing_id:
            excluded["missing_signal_id"] = excluded_missing_id
        if excluded_missing_ts:
            excluded["missing_detected_at"] = excluded_missing_ts
        limitations: list[str] = []
        if not kept:
            limitations.append("No persisted signals were available for analysis.")
        if excluded_agent:
            limitations.append(f"Excluded {excluded_agent} signal(s) with agent_mismatch.")
        if excluded_missing_id:
            limitations.append(f"Excluded {excluded_missing_id} signal(s) with missing_signal_id.")
        if excluded_missing_ts:
            limitations.append(f"Excluded {excluded_missing_ts} signal(s) with missing_detected_at from window.")
        metrics: dict[str, object] = {
            "signal_count": len(kept),
            "signal_counts_by_type": dict(type_counts),
            "signal_counts_by_detector": dict(detector_counts),
            "excluded_signals": dict(excluded),
        }
        return AnalysisResult(
            analysis_id=str(uuid4()),
            agent_id=agent_id,
            evidence_signal_ids=list(evidence_ids),
            metrics=metrics,
            sample_size=len(kept),
            window_start=window_start,
            window_end=window_end,
            limitations=list(limitations),
        )


class AnalysisEngine:
    def __init__(self, session: Session) -> None:
        self._signals = PersistedSignalRepository(session)

    def build_run_context(
        self,
        agent_id: str,
        run_id: str,
        *,
        limit: int = 1000,
        offset: int = 0,
    ) -> AnalysisContext:
        self._validate_scope(agent_id, run_id)
        self._validate_pagination(limit, offset)

        signals = self._signals.list_for_run(
            run_id=run_id,
            agent_id=agent_id,
            limit=limit,
            offset=offset,
        )

        signals = tuple(signals)

        return AnalysisContext(
            agent_id=agent_id,
            scope="run",
            signals=signals,
            run_id=run_id,
            limitations=self._pagination_limitations(
                len(signals),
                limit,
                offset,
            ),
        )

    def build_evaluation_context(
        self,
        agent_id: str,
        evaluation_run_id: str,
        *,
        limit: int = 1000,
        offset: int = 0,
    ) -> AnalysisContext:
        self._validate_scope(agent_id, evaluation_run_id)
        self._validate_pagination(limit, offset)

        signals = self._signals.list_for_evaluation_run(
            evaluation_run_id=evaluation_run_id,
            agent_id=agent_id,
            limit=limit,
            offset=offset,
        )

        signals = tuple(signals)

        return AnalysisContext(
            agent_id=agent_id,
            scope="evaluation",
            signals=signals,
            evaluation_run_id=evaluation_run_id,
            limitations=self._pagination_limitations(
                len(signals),
                limit,
                offset,
            ),
        )

    def build_window_context(
        self,
        agent_id: str,
        window_start: datetime,
        window_end: datetime,
        *,
        limit: int = 1000,
        offset: int = 0,
    ) -> AnalysisContext:
        self._validate_scope(agent_id, "window")
        self._validate_pagination(limit, offset)

        if (
            window_start.tzinfo is None
            or window_start.utcoffset() is None
            or window_end.tzinfo is None
            or window_end.utcoffset() is None
        ):
            raise ValueError(
                "Window boundaries must be timezone-aware."
            )

        if window_start >= window_end:
            raise ValueError(
                "window_start must be earlier than window_end."
            )

        signals = self._signals.list_for_window(
            agent_id=agent_id,
            window_start=window_start,
            window_end=window_end,
            limit=limit,
            offset=offset,
        )

        return AnalysisContext(
            agent_id=agent_id,
            scope="window",
            signals=tuple(signals),
            window_start=window_start,
            window_end=window_end,
            limitations=self._pagination_limitations(
                len(signals),
                limit,
                offset,
            ),
        )

    @staticmethod
    def _validate_scope(
        agent_id: str,
        scope_id: str,
    ) -> None:
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise ValueError(
                "agent_id must be a non-empty string."
            )

        if not isinstance(scope_id, str) or not scope_id.strip():
            raise ValueError(
                "Scope identifier must be a non-empty string."
            )

    @staticmethod
    def _validate_pagination(
        limit: int,
        offset: int,
    ) -> None:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or limit <= 0
        ):
            raise ValueError(
                "limit must be a positive integer."
            )

        if (
            isinstance(offset, bool)
            or not isinstance(offset, int)
            or offset < 0
        ):
            raise ValueError(
                "offset must be a non-negative integer."
            )

    @staticmethod
    def _pagination_limitations(
        count: int,
        limit: int,
        offset: int,
    ) -> tuple[str, ...]:
        limitations: list[str] = []

        if count >= limit:
            limitations.append(
                "result_limit_reached; additional signals may exist"
            )

        if offset > 0:
            limitations.append(
                "results_start_at_requested_offset"
            )

        return tuple(limitations)