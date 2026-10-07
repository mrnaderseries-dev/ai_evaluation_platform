from __future__ import annotations

import pytest

from src.ai_eval_platform.evaluation.domain import (
    ContextSource,
    ErrorInfo,
    EvaluationCriteria,
    LatencyPhase,
    ModelInfo,
    TokenUsage,
    ToolCall,
)
from src.ai_eval_platform.evaluation.target_builder import (
    EvaluationTargetBuilder,
)
from src.ai_eval_platform.evaluation.target_validator import (
    EvaluationTargetValidator,
)
from schemas.events.common import EventType

from ai_eval_platform.storage.models import Event, EvaluationCase, Run


def make_run(**overrides) -> Run:
    fields: dict = {
        "run_id": "run-1",
        "agent_id": "agent-1",
        "input_text": "What is the capital of France?",
        "final_answer": "The capital of France is Paris.",
        "duration_ms": 820,
        "metadata_json": {"environment": "test"},
    }
    fields.update(overrides)
    return Run(**fields)


def make_event(
    event_type: EventType | str,
    sequence_number: int,
    payload: dict,
    run_id: str = "run-1",
) -> Event:
    return Event(
        run_id=run_id,
        sequence_number=sequence_number,
        event_type=getattr(event_type, "value", event_type),
        payload=payload,
    )


@pytest.fixture
def builder() -> EvaluationTargetBuilder:
    return EvaluationTargetBuilder()


class TestRunMapping:

    def test_run_without_events_builds_target(self, builder):
        target = builder.build(make_run(), [])

        assert target.run_id == "run-1"
        assert target.context.input == "What is the capital of France?"
        assert target.context.output == "The capital of France is Paris."
        assert target.context.latency_ms == 820
        assert target.context.model is None
        assert target.context.token_usage is None
        assert target.context.latency_phases == ()
        assert target.context.context_source is None
        assert target.context.tool_calls == ()
        assert target.context.error is None
        assert target.context.metadata == {"environment": "test"}
        assert target.criteria is None

    def test_missing_run_fields_stay_none(self, builder):
        target = builder.build(
            make_run(
                input_text=None,
                final_answer=None,
                duration_ms=None,
                metadata_json=None,
            ),
            [],
        )

        assert target.context.input is None
        assert target.context.output is None
        assert target.context.latency_ms is None
        assert target.context.metadata == {}

    def test_run_error_maps_to_error_info(self, builder):
        target = builder.build(
            make_run(
                error_type="TimeoutError",
                error_message="run exceeded the deadline",
            ),
            [],
        )

        assert target.context.error == ErrorInfo(
            error_type="TimeoutError",
            message="run exceeded the deadline",
        )

    def test_run_without_error_has_no_error_info(self, builder):
        target = builder.build(make_run(), [])

        assert target.context.error is None

    def test_metadata_is_copied_from_run(self, builder):
        run = make_run()
        target = builder.build(run, [])

        assert target.context.metadata is not run.metadata_json

    def test_built_target_passes_target_validation(self, builder):
        target = builder.build(make_run(), [])

        EvaluationTargetValidator().validate(target)


class TestModelMapping:

    def test_model_output_produces_model_info(self, builder):
        events = [
            make_event(
                EventType.MODEL_OUTPUT,
                1,
                {
                    "model_id": "gpt-4o",
                    "model_version": "2024-08-06",
                    "provider": "openai",
                    "output_id": "out-1",
                    "output_text": "Paris.",
                },
            ),
        ]

        target = builder.build(make_run(), events)

        assert target.context.model == ModelInfo(
            name="gpt-4o",
            version="2024-08-06",
            provider="openai",
        )

    def test_model_output_produces_token_usage(self, builder):
        events = [
            make_event(
                EventType.MODEL_OUTPUT,
                1,
                {
                    "model_id": "gpt-4o",
                    "output_id": "out-1",
                    "input_tokens": 12,
                    "output_tokens": 34,
                    "total_tokens": 46,
                },
            ),
        ]

        target = builder.build(make_run(), events)

        assert target.context.token_usage == TokenUsage(
            input_tokens=12,
            output_tokens=34,
            total_tokens=46,
        )

    def test_model_called_is_model_fallback(self, builder):
        events = [
            make_event(
                EventType.MODEL_CALLED,
                1,
                {
                    "model_id": "gpt-3.5-turbo",
                    "model_version": "0125",
                    "provider": "openai",
                    "input_tokens": 9,
                },
            ),
        ]

        target = builder.build(make_run(), events)

        assert target.context.model == ModelInfo(
            name="gpt-3.5-turbo",
            version="0125",
            provider="openai",
        )
        assert target.context.token_usage == TokenUsage(
            input_tokens=9,
        )

    def test_model_output_is_preferred_over_model_called(self, builder):
        events = [
            make_event(
                EventType.MODEL_CALLED,
                1,
                {"model_id": "old-model"},
            ),
            make_event(
                EventType.MODEL_OUTPUT,
                2,
                {"model_id": "new-model", "output_id": "out-1"},
            ),
        ]

        target = builder.build(make_run(), events)

        assert target.context.model == ModelInfo(name="new-model")

    def test_model_called_tokens_used_when_output_has_no_tokens(
        self,
        builder,
    ):
        events = [
            make_event(
                EventType.MODEL_CALLED,
                1,
                {"model_id": "gpt-4o", "input_tokens": 7},
            ),
            make_event(
                EventType.MODEL_OUTPUT,
                2,
                {"model_id": "gpt-4o", "output_id": "out-1"},
            ),
        ]

        target = builder.build(make_run(), events)

        assert target.context.token_usage == TokenUsage(
            input_tokens=7,
        )

    def test_model_output_tokens_win_over_model_called(self, builder):
        events = [
            make_event(
                EventType.MODEL_CALLED,
                1,
                {"model_id": "gpt-4o", "input_tokens": 7},
            ),
            make_event(
                EventType.MODEL_OUTPUT,
                2,
                {
                    "model_id": "gpt-4o",
                    "output_id": "out-1",
                    "output_tokens": 5,
                    "total_tokens": 5,
                },
            ),
        ]

        target = builder.build(make_run(), events)

        assert target.context.token_usage == TokenUsage(
            output_tokens=5,
            total_tokens=5,
        )

    def test_missing_token_information_is_not_fabricated(self, builder):
        events = [
            make_event(
                EventType.MODEL_OUTPUT,
                1,
                {"model_id": "gpt-4o", "output_id": "out-1"},
            ),
        ]

        target = builder.build(make_run(), events)

        assert target.context.token_usage is None


class TestToolCalls:

    def test_call_and_completion_are_correlated_by_call_id(self, builder):
        events = [
            make_event(
                EventType.TOOL_CALLED,
                1,
                {
                    "tool_id": "tool-1",
                    "tool_name": "search",
                    "call_id": "call-1",
                    "arguments": {"query": "capital of France"},
                },
            ),
            make_event(
                EventType.TOOL_COMPLETED,
                2,
                {
                    "tool_id": "tool-1",
                    "tool_name": "search",
                    "call_id": "call-1",
                    "success": True,
                    "result": "Paris",
                    "latency_ms": 120.0,
                },
            ),
        ]

        target = builder.build(make_run(), events)

        assert target.context.tool_calls == (
            ToolCall(
                name="search",
                arguments={"query": "capital of France"},
                result="Paris",
                latency_ms=120,
            ),
        )

    def test_failed_completion_records_error(self, builder):
        events = [
            make_event(
                EventType.TOOL_CALLED,
                1,
                {
                    "tool_id": "tool-1",
                    "tool_name": "search",
                    "call_id": "call-1",
                    "arguments": {},
                },
            ),
            make_event(
                EventType.TOOL_COMPLETED,
                2,
                {
                    "tool_id": "tool-1",
                    "tool_name": "search",
                    "call_id": "call-1",
                    "success": False,
                    "error_message": "connection reset",
                    "latency_ms": 30.0,
                },
            ),
        ]

        target = builder.build(make_run(), events)

        assert target.context.tool_calls == (
            ToolCall(
                name="search",
                arguments={},
                result=None,
                latency_ms=30,
                error="connection reset",
            ),
        )

    def test_call_without_completion_is_incomplete_and_safe(self, builder):
        events = [
            make_event(
                EventType.TOOL_CALLED,
                1,
                {
                    "tool_id": "tool-1",
                    "tool_name": "search",
                    "call_id": "call-1",
                    "arguments": {"query": "capital"},
                },
            ),
        ]

        target = builder.build(make_run(), events)

        assert target.context.tool_calls == (
            ToolCall(
                name="search",
                arguments={"query": "capital"},
                result=None,
                latency_ms=None,
                error=None,
            ),
        )

    def test_completion_without_call_is_represented(self, builder):
        events = [
            make_event(
                EventType.TOOL_COMPLETED,
                1,
                {
                    "tool_id": "tool-1",
                    "tool_name": "search",
                    "call_id": "call-1",
                    "success": True,
                    "result": "Paris",
                },
            ),
        ]

        target = builder.build(make_run(), events)

        assert target.context.tool_calls == (
            ToolCall(
                name="search",
                arguments={},
                result="Paris",
            ),
        )

    def test_calls_keep_sequence_order(self, builder):
        events = [
            make_event(
                EventType.TOOL_CALLED,
                1,
                {
                    "tool_id": "tool-1",
                    "tool_name": "search",
                    "call_id": "call-1",
                    "arguments": {},
                },
            ),
            make_event(
                EventType.TOOL_CALLED,
                2,
                {
                    "tool_id": "tool-2",
                    "tool_name": "fetch",
                    "call_id": "call-2",
                    "arguments": {},
                },
            ),
            make_event(
                EventType.TOOL_COMPLETED,
                3,
                {
                    "tool_id": "tool-1",
                    "tool_name": "search",
                    "call_id": "call-1",
                    "success": True,
                    "result": "Paris",
                },
            ),
        ]

        target = builder.build(make_run(), events)

        assert [call.name for call in target.context.tool_calls] == [
            "search",
            "fetch",
        ]


class TestContextSource:

    def test_context_assembled_produces_context_source(self, builder):
        events = [
            make_event(
                EventType.CONTEXT_ASSEMBLED,
                1,
                {
                    "context_id": "ctx-1",
                    "resource_count": 2,
                    "resources": [
                        {
                            "resource_id": "doc-1",
                            "resource_type": "document",
                            "source": "wiki",
                            "relevance_score": 0.9,
                            "metadata": {},
                        },
                        {
                            "resource_id": "doc-2",
                            "resource_type": "document",
                            "source": "wiki",
                            "relevance_score": 0.8,
                            "metadata": {},
                        },
                    ],
                    "token_count": 256,
                },
            ),
        ]

        target = builder.build(make_run(), events)

        assert target.context.context_source == ContextSource(
            source="ctx-1",
            method=None,
            items=("doc-1", "doc-2"),
        )

    def test_last_context_assembled_by_sequence_wins(self, builder):
        events = [
            make_event(
                EventType.CONTEXT_ASSEMBLED,
                1,
                {"context_id": "ctx-1", "resources": []},
            ),
            make_event(
                EventType.CONTEXT_ASSEMBLED,
                2,
                {"context_id": "ctx-2", "resources": []},
            ),
        ]

        target = builder.build(make_run(), events)

        assert target.context.context_source == ContextSource(
            source="ctx-2",
            method=None,
            items=(),
        )


class TestLatencyPhases:

    def test_latency_fields_map_to_phases(self, builder):
        events = [
            make_event(
                EventType.RETRIEVAL_COMPLETED,
                1,
                {
                    "retrieval_id": "ret-1",
                    "retriever_id": "vector_store",
                    "result_count": 2,
                    "results": [],
                    "latency_ms": 50.0,
                },
            ),
            make_event(
                EventType.TOOL_COMPLETED,
                2,
                {
                    "tool_id": "tool-1",
                    "tool_name": "search",
                    "call_id": "call-1",
                    "success": True,
                    "latency_ms": 100.0,
                },
            ),
            make_event(
                EventType.MODEL_OUTPUT,
                3,
                {
                    "model_id": "gpt-4o",
                    "output_id": "out-1",
                    "latency_ms": 300.0,
                },
            ),
        ]

        target = builder.build(make_run(), events)

        assert target.context.latency_phases == (
            LatencyPhase(
                name="retrieval",
                duration_ms=50,
                metadata={},
            ),
            LatencyPhase(
                name="tool_call",
                duration_ms=100,
                metadata={},
            ),
            LatencyPhase(
                name="llm",
                duration_ms=300,
                metadata={},
            ),
        )

    def test_events_without_latency_fields_produce_no_phases(self, builder):
        events = [
            make_event(
                EventType.MODEL_CALLED,
                1,
                {"model_id": "gpt-4o", "input_tokens": 3},
            ),
            make_event(
                EventType.TOOL_CALLED,
                2,
                {
                    "tool_id": "tool-1",
                    "tool_name": "search",
                    "call_id": "call-1",
                    "arguments": {},
                },
            ),
            make_event(
                EventType.CONTEXT_ASSEMBLED,
                3,
                {"context_id": "ctx-1", "resources": []},
            ),
        ]

        target = builder.build(make_run(), events)

        assert target.context.latency_phases == ()


class TestDeterminism:

    def test_input_order_does_not_change_result(self, builder):
        events = [
            make_event(
                EventType.MODEL_OUTPUT,
                2,
                {
                    "model_id": "gpt-4o",
                    "output_id": "out-1",
                    "input_tokens": 4,
                    "latency_ms": 200.0,
                },
            ),
            make_event(
                EventType.TOOL_CALLED,
                3,
                {
                    "tool_id": "tool-1",
                    "tool_name": "search",
                    "call_id": "call-1",
                    "arguments": {},
                },
            ),
            make_event(
                EventType.TOOL_COMPLETED,
                4,
                {
                    "tool_id": "tool-1",
                    "tool_name": "search",
                    "call_id": "call-1",
                    "success": True,
                    "result": "Paris",
                },
            ),
            make_event(
                EventType.CONTEXT_ASSEMBLED,
                5,
                {"context_id": "ctx-1", "resources": []},
            ),
            make_event(
                EventType.MODEL_CALLED,
                1,
                {"model_id": "gpt-4o", "input_tokens": 4},
            ),
        ]

        first = builder.build(make_run(), events)
        second = builder.build(make_run(), list(reversed(events)))

        assert first.context == second.context
        assert first.run_id == second.run_id

    def test_sequence_number_decides_final_model(self, builder):
        events = [
            make_event(
                EventType.MODEL_OUTPUT,
                2,
                {"model_id": "second-model", "output_id": "out-2"},
            ),
            make_event(
                EventType.MODEL_OUTPUT,
                1,
                {"model_id": "first-model", "output_id": "out-1"},
            ),
        ]

        target = builder.build(make_run(), events)

        assert target.context.model == ModelInfo(name="second-model")


class TestCriteria:

    def test_criteria_is_preserved(self, builder):
        criteria = EvaluationCriteria(
            expected_output="Paris",
            required_tools=("search",),
            max_latency_ms=1000,
        )

        target = builder.build(make_run(), [], criteria=criteria)

        assert target.criteria is criteria

    def test_criteria_defaults_to_none(self, builder):
        target = builder.build(make_run(), [])

        assert target.criteria is None

    def test_criteria_from_case_maps_expected_answer(self):
        case = EvaluationCase(
            case_id="case-1",
            dataset_version_id="dsv-1",
            question="What is the capital of France?",
            expected_answer="Paris",
        )

        criteria = EvaluationTargetBuilder.criteria_from_case(case)

        assert criteria.expected_output == "Paris"
        assert criteria.required_tools == ()
        assert criteria.forbidden_tools == ()
        assert criteria.max_latency_ms is None
        assert criteria.expected_context_sources == ()


class TestIgnoredEvents:

    def test_evaluation_completed_is_not_execution_context(self, builder):
        events = [
            make_event(
                EventType.EVALUATION_COMPLETED,
                1,
                {
                    "evaluation_run_id": "erun-1",
                    "evaluation_case_id": "case-1",
                    "metric": "exact_match",
                    "value": 1.0,
                    "evaluator_type": "deterministic",
                    "passed": True,
                },
            ),
        ]

        target = builder.build(make_run(), events)

        assert target.context.output == "The capital of France is Paris."
        assert target.context.model is None
        assert target.context.token_usage is None
        assert target.context.latency_phases == ()
        assert target.context.error is None

    def test_policy_and_behavior_events_are_ignored(self, builder):
        events = [
            make_event(
                EventType.POLICY_CHECKED,
                1,
                {"policy_id": "policy-1", "outcome": "allowed"},
            ),
            make_event(
                EventType.AGENT_ACTION,
                2,
                {"action": "respond"},
            ),
        ]

        target = builder.build(make_run(), events)

        assert target.context.model is None
        assert target.context.tool_calls == ()
        assert target.context.context_source is None
        assert target.context.latency_phases == ()


class TestAnswerOutput:

    def test_run_final_answer_is_preferred(self, builder):
        events = [
            make_event(
                EventType.ANSWER_GENERATED,
                1,
                {
                    "answer_id": "ans-1",
                    "answer_type": "final",
                    "content": "Generated answer.",
                },
            ),
        ]

        target = builder.build(make_run(), events)

        assert target.context.output == "The capital of France is Paris."

    def test_last_answer_used_when_final_answer_missing(self, builder):
        events = [
            make_event(
                EventType.ANSWER_GENERATED,
                1,
                {
                    "answer_id": "ans-1",
                    "answer_type": "draft",
                    "content": "First attempt.",
                },
            ),
            make_event(
                EventType.ANSWER_GENERATED,
                2,
                {
                    "answer_id": "ans-2",
                    "answer_type": "final",
                    "content": "Final attempt.",
                },
            ),
        ]

        target = builder.build(make_run(final_answer=None), events)

        assert target.context.output == "Final attempt."


class TestRobustness:

    def test_minimal_model_output_does_not_fail(self, builder):
        events = [
            make_event(
                EventType.MODEL_OUTPUT,
                1,
                {"model_id": "gpt-4o", "output_id": "out-1"},
            ),
        ]

        target = builder.build(make_run(), events)

        assert target.context.model == ModelInfo(name="gpt-4o")
        assert target.context.model.version is None
        assert target.context.model.provider is None
        assert target.context.token_usage is None

    def test_empty_payload_does_not_fail(self, builder):
        events = [make_event(EventType.MODEL_CALLED, 1, {})]

        target = builder.build(make_run(), events)

        assert target.context.model is None
        assert target.context.token_usage is None

    def test_builder_does_not_mutate_persisted_rows(self, builder):
        payload = {
            "model_id": "gpt-4o",
            "output_id": "out-1",
            "input_tokens": 3,
        }
        event = make_event(EventType.MODEL_OUTPUT, 1, payload)
        run = make_run()

        target = builder.build(run, [event])

        assert event.payload is payload
        assert event.payload == payload
        assert run.final_answer == "The capital of France is Paris."
        assert target.context.metadata is not run.metadata_json
