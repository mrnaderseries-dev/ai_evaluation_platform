from datetime import datetime, timezone

from ..components.types import ComponentType
from .models import RawTelemetry, TelemetryKind


class TelemetryCollector:
    def __init__(self):
        self._items: list[RawTelemetry] = []

    def record(
        self,
        *,
        telemetry_id: str,
        component: ComponentType,
        name: str,
        kind: TelemetryKind,
        value: float | None = None,
        unit: str | None = None,
        metadata: dict | None = None,
        timestamp: datetime | None = None,
    ) -> RawTelemetry:

        telemetry = RawTelemetry(
            telemetry_id=telemetry_id,
            component=component,
            name=name,
            kind=kind,
            value=value,
            unit=unit,
            metadata=metadata or {},
            timestamp=timestamp or datetime.now(timezone.utc),
        )

        self._items.append(telemetry)

        return telemetry

    def snapshot(self) -> list[RawTelemetry]:
        return list(self._items)