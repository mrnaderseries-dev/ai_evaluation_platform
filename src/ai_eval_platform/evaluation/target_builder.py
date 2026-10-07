from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from schemas.events.common import EventType

from ai_eval_platform.storage.models import Event, EvaluationCase, Run

from .domain import (
    ContextSource,
    ErrorInfo,
    EvaluationContext,
    EvaluationCriteria,
    EvaluationTarget,
    LatencyPhase,
    ModelInfo,
    TokenUsage,
    ToolCall,
)


def _model_name(payload: dict[str, Any]) -> str | None:
    value = payload.get("model_id")
    return value if isinstance(value, str) and value else None


def _call_id(payload: dict[str, Any]) -> str | None:
    value = payload.get("call_id")
    return value if isinstance(value, str) and value else None


def _model_info(payload: dict[str, Any] | None) -> ModelInfo | None:
    if payload is None:
        return None

    name = _model_name(payload)
    if name is None:
        return None

    version = payload.get("model_version")
    provider = payload.get("provider")

    return ModelInfo(
        name=name,
        version=version if isinstance(version, str) else None,
        provider=provider if isinstance(provider, str) else None,
    )


def _token_usage(
    model_output: dict[str, Any] | None,
    model_called: dict[str, Any] | None,
) -> TokenUsage | None:
    if model_output is not None:
        values = {
            field: model_output.get(field)
            for field in (
                "input_tokens",
                "output_tokens",
                "total_tokens",
            )
        }

        if any(value is not None for value in values.values()):
            return TokenUsage(**values)

    if model_called is not None:
        input_tokens = model_called.get("input_tokens")

        if input_tokens is not None:
            return TokenUsage(input_tokens=input_tokens)

    return None


def _latency_phase(
    name: str,
    payload: dict[str, Any],
) -> LatencyPhase | None:
    latency_ms = payload.get("latency_ms")

    if not isinstance(latency_ms, (int, float)):
        return None

    if latency_ms < 0:
        return None

    return LatencyPhase(
        name=name,
        duration_ms=latency_ms,
        metadata={},
    )


def _context_source(
    payload: dict[str, Any] | None,
) -> ContextSource | None:
    if payload is None:
        return None

    context_id = payload.get("context_id")

    if not isinstance(context_id, str) or not context_id:
        return None

    resources = payload.get("resources") or []

    items = tuple(
        resource["resource_id"]
        for resource in resources
        if isinstance(resource, dict)
        and isinstance(resource.get("resource_id"), str)
        and resource["resource_id"]
    )

    return ContextSource(
        source=context_id,
        method=None,
        items=items,
    )


class EvaluationTargetBuilder:
    @staticmethod
    def criteria_from_case(
        case: EvaluationCase,
    ) -> EvaluationCriteria:
        return EvaluationCriteria(
            expected_output=case.expected_answer,
        )

    def build(
        self,
        run: Run,
        events: Sequence[Event],
        criteria: EvaluationCriteria | None = None,
    ) -> EvaluationTarget:
        ordered_events = sorted(
            events,
            key=lambda event: (
                event.sequence_number is None,
                event.sequence_number
                if event.sequence_number is not None
                else 0,
                event.occurred_at,
                event.event_id,
            ),
        )

        model_output: dict[str, Any] | None = None
        model_called: dict[str, Any] | None = None
        answer_content: str | None = None
        context_assembled: dict[str, Any] | None = None

        tool_calls: dict[str, dict[str, Any]] = {}
        tool_order: list[str] = []

        latency_phases: list[LatencyPhase] = []

        for event in ordered_events:
            payload = event.payload or {}
            event_type = event.event_type

            if event_type == EventType.MODEL_OUTPUT.value:
                if _model_name(payload) is not None:
                    model_output = payload

                phase = _latency_phase("llm", payload)

                if phase is not None:
                    latency_phases.append(phase)

            elif event_type == EventType.MODEL_CALLED.value:
                if _model_name(payload) is not None:
                    model_called = payload

            elif event_type == EventType.ANSWER_GENERATED.value:
                content = payload.get("content")

                if isinstance(content, str):
                    answer_content = content

            elif event_type == EventType.TOOL_CALLED.value:
                call_id = _call_id(payload)
                tool_name = payload.get("tool_name")

                if (
                    call_id is None
                    or not isinstance(tool_name, str)
                    or not tool_name
                ):
                    continue

                if call_id not in tool_calls:
                    arguments = payload.get("arguments")

                    tool_calls[call_id] = {
                        "name": tool_name,
                        "arguments": (
                            dict(arguments)
                            if isinstance(arguments, dict)
                            else {}
                        ),
                        "result": None,
                        "latency_ms": None,
                        "error": None,
                    }

                    tool_order.append(call_id)

            elif event_type == EventType.TOOL_COMPLETED.value:
                call_id = _call_id(payload)
                tool_name = payload.get("tool_name")

                if (
                    call_id is None
                    or not isinstance(tool_name, str)
                    or not tool_name
                ):
                    continue

                call = tool_calls.get(call_id)

                if call is None:
                    call = {
                        "name": tool_name,
                        "arguments": {},
                        "result": None,
                        "latency_ms": None,
                        "error": None,
                    }

                    tool_calls[call_id] = call
                    tool_order.append(call_id)

                call["result"] = payload.get("result")

                latency = _latency_phase("tool_call", payload)

                if latency is not None:
                    call["latency_ms"] = latency.duration_ms
                    latency_phases.append(latency)

                error_message = payload.get("error_message")

                if isinstance(error_message, str) and error_message:
                    call["error"] = error_message

            elif event_type == EventType.RETRIEVAL_COMPLETED.value:
                phase = _latency_phase("retrieval", payload)

                if phase is not None:
                    latency_phases.append(phase)

            elif event_type == EventType.CONTEXT_ASSEMBLED.value:
                context_id = payload.get("context_id")

                if isinstance(context_id, str) and context_id:
                    context_assembled = payload

        error: ErrorInfo | None = None

        if run.error_type:
            error = ErrorInfo(
                error_type=run.error_type,
                message=run.error_message,
            )

        output = (
            run.final_answer
            if run.final_answer is not None
            else answer_content
        )

        context = EvaluationContext(
            input=run.input_text,
            output=output,
            model=(
                _model_info(model_output)
                or _model_info(model_called)
            ),
            token_usage=_token_usage(
                model_output=model_output,
                model_called=model_called,
            ),
            latency_ms=run.duration_ms,
            latency_phases=tuple(latency_phases),
            context_source=_context_source(context_assembled),
            tool_calls=tuple(
                ToolCall(
                    name=tool_calls[call_id]["name"],
                    arguments=tool_calls[call_id]["arguments"],
                    result=tool_calls[call_id]["result"],
                    latency_ms=tool_calls[call_id]["latency_ms"],
                    error=tool_calls[call_id]["error"],
                )
                for call_id in tool_order
            ),
            error=error,
            metadata=dict(run.metadata_json or {}),
        )

        return EvaluationTarget(
            run_id=run.run_id,
            context=context,
            criteria=criteria,
        )