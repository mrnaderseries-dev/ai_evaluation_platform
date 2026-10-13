from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from ai_eval_platform.storage.models import PersistedSignal


class AnalysisContext(BaseModel):
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
    )

    agent_id: str = Field(min_length=1)

    scope: Literal["run", "evaluation", "window"]

    signals: tuple[PersistedSignal, ...] = ()

    run_id: str | None = None
    evaluation_run_id: str | None = None

    window_start: datetime | None = None
    window_end: datetime | None = None

    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_scope(self) -> "AnalysisContext":
        if self.scope == "run":
            if not self.run_id or self.evaluation_run_id is not None:
                raise ValueError(
                    "Run scope requires run_id and excludes "
                    "evaluation_run_id."
                )

            if (
                self.window_start is not None
                or self.window_end is not None
            ):
                raise ValueError(
                    "Run scope cannot include window boundaries."
                )

        elif self.scope == "evaluation":
            if (
                not self.evaluation_run_id
                or self.run_id is not None
            ):
                raise ValueError(
                    "Evaluation scope requires evaluation_run_id "
                    "and excludes run_id."
                )

            if (
                self.window_start is not None
                or self.window_end is not None
            ):
                raise ValueError(
                    "Evaluation scope cannot include window boundaries."
                )

        elif self.scope == "window":
            if (
                self.window_start is None
                or self.window_end is None
            ):
                raise ValueError(
                    "Window scope requires both window boundaries."
                )

            if self.window_start >= self.window_end:
                raise ValueError(
                    "window_start must be earlier than window_end."
                )

            if (
                self.run_id is not None
                or self.evaluation_run_id is not None
            ):
                raise ValueError(
                    "Window scope excludes run and evaluation identifiers."
                )

        return self

    @model_validator(mode="after")
    def validate_signals(self) -> "AnalysisContext":
        for signal in self.signals:
            if signal.agent_id != self.agent_id:
                raise ValueError(
                    "Every signal must belong to the specified agent."
                )

            if self.scope == "run":
                if signal.run_id != self.run_id:
                    raise ValueError(
                        "Every signal must belong to the specified run."
                    )

            elif self.scope == "evaluation":
                if signal.evaluation_run_id != self.evaluation_run_id:
                    raise ValueError(
                        "Every signal must belong to the specified "
                        "evaluation run."
                    )

            elif self.scope == "window":
                detected_at = signal.detected_at

                if detected_at is None:
                    raise ValueError(
                        "Window-scoped signals must have detected_at."
                    )

                if not (
                    self.window_start
                    <= detected_at
                    < self.window_end
                ):
                    raise ValueError(
                        "Every signal must fall within the time window."
                    )

        return self