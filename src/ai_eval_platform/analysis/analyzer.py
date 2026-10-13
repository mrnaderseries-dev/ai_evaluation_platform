from collections import Counter
from datetime import datetime, timezone
from uuid import uuid4

from ai_eval_platform.analysis.context import AnalysisContext
from ai_eval_platform.analysis.findings import AnalysisFinding


class SignalSummaryAnalyzer:
    def analyze(self, context: AnalysisContext) -> list[AnalysisFinding]:
        signals = context.signals

        signal_ids = tuple(
            dict.fromkeys(signal.signal_id for signal in signals)
        )

        type_counts = Counter(
            signal.signal_type for signal in signals
        )

        detector_counts = Counter(
            signal.detector_name or "unknown"
            for signal in signals
        )

        timestamps = [
            signal.detected_at
            for signal in signals
            if signal.detected_at is not None
        ]

        limitations = list(context.limitations)

        if not signals:
            limitations.append(
                "No persisted signals were available for analysis."
            )

        if len(signal_ids) != len(signals):
            limitations.append(
                "Duplicate signal IDs were encountered."
            )

        if len(timestamps) != len(signals):
            limitations.append(
                "Some signals have no detected_at timestamp."
            )

        window_start = min(timestamps) if timestamps else None
        window_end = max(timestamps) if timestamps else None

        metrics = {
            "total_signals": len(signals),
            "unique_signals": len(signal_ids),
            "duplicate_signals": len(signals) - len(signal_ids),
            "signal_type_counts": dict(type_counts),
            "detector_counts": dict(detector_counts),
            "signals_with_timestamp": len(timestamps),
            "signals_without_timestamp": len(signals) - len(timestamps),
            "scope": context.scope,
        }

        finding = AnalysisFinding(
            finding_id=str(uuid4()),
            agent_id=context.agent_id,
            finding_type="signal_summary",
            severity="info",
            title="Persisted signal summary",
            description=(
                f"Analyzed {len(signals)} persisted signals for "
                f"agent '{context.agent_id}'. "
                f"Found {len(type_counts)} signal types and "
                f"{len(detector_counts)} detector categories."
            ),
            evidence_signal_ids=signal_ids,
            metrics=metrics,
            confidence=None,
            observed_at=datetime.now(timezone.utc),
            limitations=tuple(dict.fromkeys(limitations)),
        )

        return [finding]