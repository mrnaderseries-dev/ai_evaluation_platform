from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class EvaluationStatus(StrEnum):
    SUCCESS = "success"
    UNAVAILABLE = "unavailable"
    ERROR = "error"

@dataclass(frozen=True)
class LatencyPhase:
    name:str
    duration_ms:int
    metadata:dict[str,Any]

@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class ModelInfo:
    name: str
    version: str | None = None
    provider: str | None = None


@dataclass(frozen=True)
class ContextSource:
    source: str
    method: str | None = None
    items: tuple[Any, ...] = ()


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    latency_ms: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class ErrorInfo:
    error_type: str
    message: str | None = None
    stage: str | None = None

@dataclass(frozen=True)
class Citation:
    source:str
    location:str|None=None



@dataclass
class EvaluationContext:
    input: str | None = None
    output: str | None = None
    model: ModelInfo | None = None
    token_usage: TokenUsage | None = None
    latency_ms: int | None = None
    latency_phases:tuple[LatencyPhase,...]=()
    citations:tuple[Citation,...]=()
    context_source: ContextSource | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    error: ErrorInfo | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvaluationCriteria:
    expected_output: str | None = None
    required_tools: tuple[str, ...] = ()
    forbidden_tools: tuple[str, ...] = ()
    max_latency_ms: int | None = None
    expected_context_sources: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvaluationTarget:
    run_id: str
    context: EvaluationContext
    criteria: EvaluationCriteria | None = None


@dataclass(frozen=True)
class EvaluationMeasurement:
    metric_name: str
    status: EvaluationStatus

    value: float | None = None
    score: float | None = None
    passed: bool | None = None

    feedback: str | None = None
    error_type: str | None = None

    metadata: dict[str, Any] = field(default_factory=dict)