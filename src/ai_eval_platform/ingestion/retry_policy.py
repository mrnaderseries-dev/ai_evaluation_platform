from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum
from time import sleep
from typing import Callable


class RetryAction(str, Enum):
    RETRY = "retry"
    EXHAUSTED = "exhausted"


@dataclass(frozen=True, slots=True)
class RetryConfig:
    base_delay: float = 1.0
    max_delay: float = 30.0
    max_attempts: int = 5
    jitter: bool = True

    def __post_init__(self) -> None:
        if self.base_delay <= 0:
            raise ValueError(
                "base_delay must be greater than zero."
            )

        if self.max_delay <= 0:
            raise ValueError(
                "max_delay must be greater than zero."
            )

        if self.base_delay > self.max_delay:
            raise ValueError(
                "base_delay cannot be greater than max_delay."
            )

        if self.max_attempts < 1:
            raise ValueError(
                "max_attempts must be at least 1."
            )


@dataclass(frozen=True, slots=True)
class RetryDecision:
    action: RetryAction
    attempt: int
    delay_seconds: float
    reason: str

    @property
    def should_retry(self) -> bool:
        return self.action is RetryAction.RETRY


class RetryPolicy:
    def __init__(
        self,
        config: RetryConfig | None = None,
        *,
        sleeper: Callable[[float], None] = sleep,
        random_source: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self._config = config or RetryConfig()
        self._sleep = sleeper
        self._random = random_source

    def evaluate(self, attempt: int) -> RetryDecision:
        if attempt < 1:
            raise ValueError(
                "attempt must be greater than or equal to 1."
            )

        if attempt >= self._config.max_attempts:
            return RetryDecision(
                action=RetryAction.EXHAUSTED,
                attempt=attempt,
                delay_seconds=0.0,
                reason="Retry limit exhausted.",
            )

        delay = self._calculate_delay(attempt)

        return RetryDecision(
            action=RetryAction.RETRY,
            attempt=attempt,
            delay_seconds=delay,
            reason="Retry allowed by retry policy.",
        )

    def wait(self, attempt: int) -> RetryDecision:
        decision = self.evaluate(attempt)

        if decision.action is RetryAction.RETRY:
            self._sleep(decision.delay_seconds)

        return decision

    def _calculate_delay(self, attempt: int) -> float:
        exponential_delay = (
            self._config.base_delay
            * (2 ** (attempt - 1))
        )

        capped_delay = min(
            exponential_delay,
            self._config.max_delay,
        )

        if not self._config.jitter:
            return capped_delay

        return self._random(
            0.0,
            capped_delay,
        )