from __future__ import annotations

import pytest

from src.ai_eval_platform.evaluation.domain import (
    Citation,
    ContextSource,
    EvaluationContext,
    EvaluationCriteria,
    EvaluationStatus,
    EvaluationTarget,
    LatencyPhase,
    ModelInfo,
    TokenUsage,
    ToolCall,
)
from src.ai_eval_platform.evaluation.errors import (
    InvalidEvaluationTargetError,
)
from src.ai_eval_platform.evaluation.evaluator import Evaluator
from src.ai_eval_platform.evaluation.runner import EvaluationRunner
from src.ai_eval_platform.evaluation.target_validator import (
    EvaluationTargetValidator,
)
from src.ai_eval_platform.evaluation.evaluators.latency import (
    LatencyEvaluator,
)
from src.ai_eval_platform.evaluation.evaluators.exact_match import (
    ExactMatchEvaluator,
)
from src.ai_eval_platform.evaluation.evaluators.semantic_similarity import (
    SemanticSimilarityEvaluator,
)
from src.ai_eval_platform.evaluation.evaluators.retrieval import (
    RetrievalEvaluator,
)
from src.ai_eval_platform.evaluation.evaluators.citation import (
    CitationEvaluator,
)
from src.ai_eval_platform.evaluation.evaluators.groundedness import (
    GroundednessEvaluator,
)
from src.ai_eval_platform.evaluation.evaluators.tool_usage import (
    ToolUsageEvaluator,
)


class FakeSimilarityEngine:
    def __init__(self, score: float = 0.91):
        self.score = score
        self.calls: list[tuple[str, str]] = []

    def compare(self, text_a: str, text_b: str) -> float:
        self.calls.append((text_a, text_b))
        return self.score


class FailingSimilarityEngine:
    def compare(self, text_a: str, text_b: str) -> float:
        raise RuntimeError("embedding service unavailable")


class FakeGroundednessJudge:
    def __init__(self, score: float = 0.87):
        self.score = score
        self.calls: list[tuple[str, tuple]] = []

    def evaluate(self, answer: str, context: tuple) -> float:
        self.calls.append((answer, context))
        return self.score


class FailingGroundednessJudge:
    def evaluate(self, answer: str, context: tuple) -> float:
        raise RuntimeError("judge model unavailable")


@pytest.fixture
def target() -> EvaluationTarget:
    return EvaluationTarget(
        run_id="run-001",
        context=EvaluationContext(
            input="What is the capital of France?",
            output="The capital of France is Paris.",
            model=ModelInfo(
                name="test-model",
                version="1.0",
                provider="test-provider",
            ),
            token_usage=TokenUsage(
                input_tokens=20,
                output_tokens=8,
                total_tokens=28,
            ),
            latency_ms=820,
            latency_phases=(
                LatencyPhase(
                    name="planning",
                    duration_ms=80,
                    metadata={},
                ),
                LatencyPhase(
                    name="retrieval",
                    duration_ms=180,
                    metadata={
                        "top_k": 3,
                    },
                ),
                LatencyPhase(
                    name="llm",
                    duration_ms=400,
                    metadata={
                        "model": "test-model",
                    },
                ),
                LatencyPhase(
                    name="tool_call",
                    duration_ms=100,
                    metadata={
                        "tool": "search",
                    },
                ),
                LatencyPhase(
                    name="healing",
                    duration_ms=60,
                    metadata={},
                ),
            ),
            context_source=ContextSource(
                source="vector_store",
                method="semantic_search",
                items=(
                    "Paris is the capital of France.",
                    "France is a country in Europe.",
                ),
            ),
            citations=(
                Citation(
                    source="doc-001",
                    location="page-1",
                ),
                Citation(
                    source="doc-002",
                    location="page-3",
                ),
            ),
            tool_calls=(
                ToolCall(
                    name="search",
                    arguments={"query": "capital of France"},
                    result="Paris",
                    latency_ms=100,
                ),
            ),
            metadata={
                "environment": "test",
            },
        ),
        criteria=EvaluationCriteria(
            expected_output="Paris is the capital of France.",
            required_tools=(
                "search",
            ),
            forbidden_tools=(
                "delete_database",
            ),
            max_latency_ms=1000,
            expected_context_sources=(
                "Paris is the capital of France.",
            ),
        ),
    )


class TestEvaluationTargetValidation:

    def test_valid_target_is_accepted(self, target):
        validator = EvaluationTargetValidator()

        validator.validate(target)

    def test_none_target_is_rejected(self):
        validator = EvaluationTargetValidator()

        with pytest.raises(InvalidEvaluationTargetError):
            validator.validate(None)

    def test_empty_run_id_is_rejected(self, target):
        target.run_id = ""

        validator = EvaluationTargetValidator()

        with pytest.raises(InvalidEvaluationTargetError):
            validator.validate(target)

    def test_missing_context_is_rejected(self, target):
        target.context = None

        validator = EvaluationTargetValidator()

        with pytest.raises(InvalidEvaluationTargetError):
            validator.validate(target)


class TestLatencyEvaluator:

    def test_total_latency_is_measured(self, target):
        evaluator = LatencyEvaluator()

        result = evaluator.evaluate(target)

        assert result.metric_name == "latency"
        assert result.status == EvaluationStatus.SUCCESS
        assert result.value == 820.0
        assert result.passed is True

    def test_phase_breakdown_is_preserved(self, target):
        evaluator = LatencyEvaluator()

        result = evaluator.evaluate(target)

        phases = result.metadata["phases"]

        assert phases["planning"]["duration_ms"] == 80
        assert phases["retrieval"]["duration_ms"] == 180
        assert phases["llm"]["duration_ms"] == 400
        assert phases["tool_call"]["duration_ms"] == 100
        assert phases["healing"]["duration_ms"] == 60

    def test_phase_percentage_is_calculated(self, target):
        evaluator = LatencyEvaluator()

        result = evaluator.evaluate(target)

        llm_percentage = result.metadata["phases"]["llm"]["percentage"]

        assert llm_percentage == pytest.approx(
            (400 / 820) * 100
        )

    def test_latency_passes_threshold(self, target):
        evaluator = LatencyEvaluator()

        result = evaluator.evaluate(target)

        assert result.passed is True

    def test_latency_fails_threshold(self, target):
        target.criteria.max_latency_ms = 500

        evaluator = LatencyEvaluator()

        result = evaluator.evaluate(target)

        assert result.status == EvaluationStatus.SUCCESS
        assert result.value == 820.0
        assert result.passed is False

    def test_missing_latency_is_unavailable(self, target):
        target.context.latency_ms = None

        evaluator = LatencyEvaluator()

        result = evaluator.evaluate(target)

        assert result.status == EvaluationStatus.UNAVAILABLE
        assert result.value is None
        assert result.passed is None


class TestExactMatchEvaluator:

    def test_exact_match_passes(self, target):
        evaluator = ExactMatchEvaluator()

        result = evaluator.evaluate(target)

        assert result.metric_name == "exact_match"
        assert result.status == EvaluationStatus.SUCCESS
        assert result.value == 1.0
        assert result.score == 1.0
        assert result.passed is True

    def test_exact_match_fails(self, target):
        target.context.output = "London"

        evaluator = ExactMatchEvaluator()

        result = evaluator.evaluate(target)

        assert result.status == EvaluationStatus.SUCCESS
        assert result.value == 0.0
        assert result.score == 0.0
        assert result.passed is False

    def test_missing_output_is_unavailable(self, target):
        target.context.output = None

        evaluator = ExactMatchEvaluator()

        result = evaluator.evaluate(target)

        assert result.status == EvaluationStatus.UNAVAILABLE
        assert result.passed is None

    def test_missing_expected_output_is_unavailable(self, target):
        target.criteria.expected_output = None

        evaluator = ExactMatchEvaluator()

        result = evaluator.evaluate(target)

        assert result.status == EvaluationStatus.UNAVAILABLE
        assert result.passed is None


class TestSemanticSimilarityEvaluator:

    def test_similarity_uses_engine(self, target):
        engine = FakeSimilarityEngine(score=0.93)

        evaluator = SemanticSimilarityEvaluator(engine)

        result = evaluator.evaluate(target)

        assert result.metric_name == "semantic_similarity"
        assert result.status == EvaluationStatus.SUCCESS
        assert result.value == 0.93
        assert result.score == 0.93

        assert engine.calls == [
            (
                target.context.output,
                target.criteria.expected_output,
            )
        ]

    def test_missing_output_is_unavailable(self, target):
        target.context.output = None

        engine = FakeSimilarityEngine()

        evaluator = SemanticSimilarityEvaluator(engine)

        result = evaluator.evaluate(target)

        assert result.status == EvaluationStatus.UNAVAILABLE
        assert engine.calls == []

    def test_missing_expected_output_is_unavailable(self, target):
        target.criteria.expected_output = None

        engine = FakeSimilarityEngine()

        evaluator = SemanticSimilarityEvaluator(engine)

        result = evaluator.evaluate(target)

        assert result.status == EvaluationStatus.UNAVAILABLE
        assert engine.calls == []

    def test_engine_failure_is_evaluator_error(self, target):
        engine = FailingSimilarityEngine()

        evaluator = SemanticSimilarityEvaluator(engine)

        result = evaluator.evaluate(target)

        assert result.status == EvaluationStatus.ERROR
        assert result.passed is None
        assert result.error_type == "RuntimeError"


class TestRetrievalEvaluator:

    def test_retrieval_matches_expected_sources(self, target):
        evaluator = RetrievalEvaluator()

        result = evaluator.evaluate(target)

        assert result.metric_name == "retrieval"
        assert result.status == EvaluationStatus.SUCCESS
        assert result.metadata["expected_count"] == 1
        assert result.metadata["matched_count"] == 1
        assert result.value == 1.0
        assert result.score == 1.0

    def test_retrieval_reports_missing_sources(self, target):
        target.criteria.expected_context_sources = (
            "Paris is the capital of France.",
            "The Eiffel Tower is in Paris.",
        )

        evaluator = RetrievalEvaluator()

        result = evaluator.evaluate(target)

        assert result.value == pytest.approx(0.5)

        assert "The Eiffel Tower is in Paris." in (
            result.metadata["missing_sources"]
        )

    def test_missing_retrieval_context_is_unavailable(self, target):
        target.context.context_source = None

        evaluator = RetrievalEvaluator()

        result = evaluator.evaluate(target)

        assert result.status == EvaluationStatus.UNAVAILABLE

    def test_retrieval_without_ground_truth_still_measures_observation(
        self,
        target,
    ):
        target.criteria.expected_context_sources = ()

        evaluator = RetrievalEvaluator()

        result = evaluator.evaluate(target)

        assert result.status == EvaluationStatus.SUCCESS
        assert result.value == 2.0
        assert result.passed is None


class TestCitationEvaluator:

    def test_citations_are_measured(self, target):
        evaluator = CitationEvaluator()

        result = evaluator.evaluate(target)

        assert result.metric_name == "citation"
        assert result.status == EvaluationStatus.SUCCESS
        assert result.value == 2.0
        assert result.score == 1.0
        assert result.metadata["citation_count"] == 2
        assert result.metadata["valid_count"] == 2
        assert result.metadata["invalid_count"] == 0

    def test_invalid_citation_reduces_score(self, target):
        target.context.citations = (
            Citation(
                source="doc-001",
                location="page-1",
            ),
            Citation(
                source="",
                location=None,
            ),
        )

        evaluator = CitationEvaluator()

        result = evaluator.evaluate(target)

        assert result.status == EvaluationStatus.SUCCESS
        assert result.value == 2.0
        assert result.score == pytest.approx(0.5)
        assert result.metadata["valid_count"] == 1
        assert result.metadata["invalid_count"] == 1

    def test_no_citations_is_unavailable(self, target):
        target.context.citations = ()

        evaluator = CitationEvaluator()

        result = evaluator.evaluate(target)

        assert result.status == EvaluationStatus.UNAVAILABLE
        assert result.passed is None


class TestGroundednessEvaluator:

    def test_groundedness_uses_judge(self, target):
        judge = FakeGroundednessJudge(score=0.88)

        evaluator = GroundednessEvaluator(judge)

        result = evaluator.evaluate(target)

        assert result.metric_name == "groundedness"
        assert result.status == EvaluationStatus.SUCCESS
        assert result.value == 0.88
        assert result.score == 0.88

        assert len(judge.calls) == 1

        answer, context = judge.calls[0]

        assert answer == target.context.output
        assert context == target.context.context_source.items

    def test_missing_output_is_unavailable(self, target):
        target.context.output = None

        judge = FakeGroundednessJudge()

        evaluator = GroundednessEvaluator(judge)

        result = evaluator.evaluate(target)

        assert result.status == EvaluationStatus.UNAVAILABLE
        assert judge.calls == []

    def test_missing_context_is_unavailable(self, target):
        target.context.context_source = None

        judge = FakeGroundednessJudge()

        evaluator = GroundednessEvaluator(judge)

        result = evaluator.evaluate(target)

        assert result.status == EvaluationStatus.UNAVAILABLE
        assert judge.calls == []

    def test_judge_failure_is_error_not_failed_metric(self, target):
        judge = FailingGroundednessJudge()

        evaluator = GroundednessEvaluator(judge)

        result = evaluator.evaluate(target)

        assert result.status == EvaluationStatus.ERROR
        assert result.passed is None
        assert result.error_type == "RuntimeError"


class TestToolUsageEvaluator:

    def test_required_tool_is_present(self, target):
        evaluator = ToolUsageEvaluator()

        result = evaluator.evaluate(target)

        assert result.metric_name == "tool_usage"
        assert result.status == EvaluationStatus.SUCCESS
        assert result.passed is True

    def test_missing_required_tool_fails(self, target):
        target.criteria.required_tools = (
            "search",
            "calculator",
        )

        evaluator = ToolUsageEvaluator()

        result = evaluator.evaluate(target)

        assert result.status == EvaluationStatus.SUCCESS
        assert result.passed is False

    def test_forbidden_tool_fails(self, target):
        target.context.tool_calls = (
            ToolCall(
                name="delete_database",
                arguments={},
                result=None,
            ),
        )

        evaluator = ToolUsageEvaluator()

        result = evaluator.evaluate(target)

        assert result.status == EvaluationStatus.SUCCESS
        assert result.passed is False


class TestEvaluationRunner:

    def test_runner_executes_valid_evaluator(self, target):
        evaluator = LatencyEvaluator()
        runner = EvaluationRunner()

        result = runner.run(
            evaluator=evaluator,
            target=target,
        )

        assert result.status == EvaluationStatus.SUCCESS
        assert result.metric_name == "latency"

    def test_runner_rejects_invalid_target(self):
        evaluator = LatencyEvaluator()
        runner = EvaluationRunner()

        with pytest.raises(InvalidEvaluationTargetError):
            runner.run(
                evaluator=evaluator,
                target=None,
            )


class TestEvaluatorContract:

    def test_all_evaluators_have_metric_name(self):
        evaluators = [
            LatencyEvaluator(),
            ExactMatchEvaluator(),
            RetrievalEvaluator(),
            CitationEvaluator(),
            ToolUsageEvaluator(),
            SemanticSimilarityEvaluator(
                FakeSimilarityEngine()
            ),
            GroundednessEvaluator(
                FakeGroundednessJudge()
            ),
        ]

        for evaluator in evaluators:
            assert isinstance(evaluator, Evaluator)
            assert evaluator.metric_name
            assert isinstance(evaluator.metric_name, str)

    def test_each_evaluator_returns_one_metric(self, target):
        evaluators = [
            LatencyEvaluator(),
            ExactMatchEvaluator(),
            RetrievalEvaluator(),
            CitationEvaluator(),
            ToolUsageEvaluator(),
            SemanticSimilarityEvaluator(
                FakeSimilarityEngine()
            ),
            GroundednessEvaluator(
                FakeGroundednessJudge()
            ),
        ]

        for evaluator in evaluators:
            result = evaluator.evaluate(target)

            assert isinstance(result.metric_name, str)
            assert result.metric_name == evaluator.metric_name

    def test_unavailable_never_means_failed(self, target):
        target.context.output = None

        evaluators = [
            ExactMatchEvaluator(),
            SemanticSimilarityEvaluator(
                FakeSimilarityEngine()
            ),
            GroundednessEvaluator(
                FakeGroundednessJudge()
            ),
        ]

        for evaluator in evaluators:
            result = evaluator.evaluate(target)

            assert result.status == EvaluationStatus.UNAVAILABLE
            assert result.passed is None

    def test_evaluator_error_never_means_failed(self, target):
        evaluators = [
            SemanticSimilarityEvaluator(
                FailingSimilarityEngine()
            ),
            GroundednessEvaluator(
                FailingGroundednessJudge()
            ),
        ]

        for evaluator in evaluators:
            result = evaluator.evaluate(target)

            assert result.status == EvaluationStatus.ERROR
            assert result.passed is None
