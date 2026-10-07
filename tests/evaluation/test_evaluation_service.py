from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import Session

from src.ai_eval_platform.evaluation.domain import (
    EvaluationCriteria,
    EvaluationMeasurement,
    EvaluationStatus,
    EvaluationTarget,
)
from src.ai_eval_platform.evaluation.errors import (
    EvaluationConfigurationError,
    InvalidEvaluationTargetError,
)
from src.ai_eval_platform.evaluation.evaluator import Evaluator
from src.ai_eval_platform.evaluation.evaluators.exact_match import (
    ExactMatchEvaluator,
)
from src.ai_eval_platform.evaluation.evaluators.latency import (
    LatencyEvaluator,
)
from src.ai_eval_platform.evaluation.service import EvaluationService

from ai_eval_platform.storage.models import (
    EvaluationCase,
    EvaluationResult,
    EvaluationRun,
)
from ai_eval_platform.storage.repositories import EntityNotFoundError

FRANCE_ANSWER = "The capital of France is Paris."
T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 1, 1, 12, 30, 0, tzinfo=timezone.utc)


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value


class SequenceEvaluator(Evaluator):
    def __init__(self, metric_name: str, measurements: list):
        self.metric_name = metric_name
        self._measurements = list(measurements)
        self.calls = 0

    def evaluate(self, target):
        self.calls += 1
        return self._measurements.pop(0)


class RaisingEvaluator(Evaluator):
    metric_name = "exploding"

    def __init__(self):
        self.calls = 0

    def evaluate(self, target):
        self.calls += 1
        raise RuntimeError("evaluator exploded")


class UnserializableEvaluator(Evaluator):
    metric_name = "unserializable"

    def evaluate(self, target):
        return EvaluationMeasurement(
            metric_name=self.metric_name,
            status=EvaluationStatus.SUCCESS,
            score=1.0,
            metadata={"payload": object()},
        )


class InvalidTargetBuilder:
    def build(self, run, events, criteria=None):
        return EvaluationTarget(run_id=run.run_id, context=None)


class MutableClock:
    def __init__(self, current: datetime):
        self.current = current

    def __call__(self) -> datetime:
        return self.current


def make_service(session_factory, evaluators, **overrides):
    return EvaluationService(
        session_factory=session_factory,
        evaluators=evaluators,
        **overrides,
    )


@contextmanager
def open_session(engine):
    with Session(engine, expire_on_commit=False) as session:
        yield session


@pytest.fixture()
def read_results(engine):
    def _read():
        with Session(engine, expire_on_commit=False) as session:
            statement = select(EvaluationResult).order_by(
                EvaluationResult.metric_name
            )
            return list(session.scalars(statement))

    return _read


@pytest.fixture()
def read_evaluation_run(engine):
    def _read(evaluation_run_id: str):
        with Session(engine, expire_on_commit=False) as session:
            return session.get(EvaluationRun, evaluation_run_id)

    return _read


class TestOnlineEvaluation:

    def test_online_evaluation_persists_result_for_run(
        self,
        seeded,
        session_factory,
        read_results,
    ):
        service = make_service(session_factory, [LatencyEvaluator()])

        measurements = service.evaluate_run("run-1")

        assert len(measurements) == 1
        assert measurements[0].status == EvaluationStatus.SUCCESS

        results = read_results()
        assert len(results) == 1

        row = results[0]
        assert row.run_id == "run-1"
        assert row.metric_name == "latency"
        assert row.evaluation_run_id is None
        assert row.case_id is None
        assert row.latency_ms == 820
        assert row.predicted_answer == FRANCE_ANSWER
        assert row.token_usage == {
            "input_tokens": 20,
            "output_tokens": 8,
            "total_tokens": 28,
        }
        assert row.passed is None
        assert row.score is None
        assert row.failure_category is None
        assert row.metrics["status"] == "success"
        assert row.metrics["value"] == 820.0
        assert row.metrics["total_latency_ms"] == 820

    def test_online_evaluation_supports_multiple_metrics(
        self,
        seeded,
        session_factory,
        read_results,
    ):
        service = make_service(
            session_factory,
            [LatencyEvaluator(), ExactMatchEvaluator()],
        )

        measurements = service.evaluate_run(
            "run-1",
            metrics=["latency", "exact_match"],
            criteria=EvaluationCriteria(expected_output=FRANCE_ANSWER),
        )

        assert {
            measurement.metric_name for measurement in measurements
        } == {"latency", "exact_match"}

        rows = read_results()
        assert {row.metric_name for row in rows} == {
            "latency",
            "exact_match",
        }

        exact_row = next(
            row for row in rows if row.metric_name == "exact_match"
        )
        assert exact_row.passed is True
        assert exact_row.score == 1.0

    def test_same_run_evaluated_twice_updates_row_in_place(
        self,
        seeded,
        session_factory,
        read_results,
    ):
        evaluator = SequenceEvaluator(
            "consistency",
            [
                EvaluationMeasurement(
                    metric_name="consistency",
                    status=EvaluationStatus.SUCCESS,
                    score=0.5,
                ),
                EvaluationMeasurement(
                    metric_name="consistency",
                    status=EvaluationStatus.SUCCESS,
                    score=0.9,
                ),
            ],
        )
        service = make_service(session_factory, [evaluator])

        service.evaluate_run("run-1")
        first = read_results()
        assert len(first) == 1
        first_id = first[0].evaluation_result_id

        service.evaluate_run("run-1")
        second = read_results()
        assert len(second) == 1
        assert second[0].score == 0.9
        assert second[0].evaluation_result_id == first_id
        assert evaluator.calls == 2

    def test_missing_run_raises_entity_not_found(
        self,
        seeded,
        session_factory,
        read_results,
    ):
        service = make_service(session_factory, [LatencyEvaluator()])

        with pytest.raises(EntityNotFoundError):
            service.evaluate_run("missing-run")

        assert read_results() == []


class TestOfflineEvaluation:

    def test_offline_evaluation_scopes_result_to_evaluation_run(
        self,
        seeded,
        session_factory,
        read_results,
    ):
        service = make_service(session_factory, [ExactMatchEvaluator()])

        service.evaluate_run(
            "run-1",
            evaluation_run_id="erun-offline",
            criteria=EvaluationCriteria(expected_output=FRANCE_ANSWER),
        )

        rows = read_results()
        assert len(rows) == 1
        assert rows[0].evaluation_run_id == "erun-offline"
        assert rows[0].case_id is None
        assert rows[0].run_id == "run-1"
        assert rows[0].passed is True

    def test_same_run_evaluated_by_different_evaluation_runs(
        self,
        seeded,
        session_factory,
        read_results,
    ):
        service = make_service(session_factory, [ExactMatchEvaluator()])
        criteria = EvaluationCriteria(expected_output=FRANCE_ANSWER)

        service.evaluate_run(
            "run-1",
            evaluation_run_id="erun-offline",
            criteria=criteria,
        )
        service.evaluate_run(
            "run-1",
            evaluation_run_id="erun-other",
            criteria=criteria,
        )

        rows = read_results()
        assert len(rows) == 2
        assert {row.evaluation_run_id for row in rows} == {
            "erun-offline",
            "erun-other",
        }

    def test_missing_evaluation_run_raises_entity_not_found(
        self,
        seeded,
        session_factory,
        read_results,
    ):
        service = make_service(session_factory, [ExactMatchEvaluator()])

        with pytest.raises(EntityNotFoundError):
            service.evaluate_run(
                "run-1",
                evaluation_run_id="erun-missing",
                criteria=EvaluationCriteria(expected_output=FRANCE_ANSWER),
            )

        assert read_results() == []


class TestBenchmarkEvaluation:

    def test_benchmark_evaluation_links_case_and_run(
        self,
        seeded,
        session_factory,
        read_results,
    ):
        service = make_service(session_factory, [ExactMatchEvaluator()])

        measurements = service.evaluate_run(
            "run-1",
            evaluation_run_id="erun-bench",
            case_id="case-1",
        )

        assert measurements[0].status == EvaluationStatus.SUCCESS
        assert measurements[0].passed is True

        rows = read_results()
        assert len(rows) == 1
        assert rows[0].evaluation_run_id == "erun-bench"
        assert rows[0].case_id == "case-1"
        assert rows[0].run_id == "run-1"
        assert rows[0].passed is True
        assert rows[0].predicted_answer == FRANCE_ANSWER

    def test_benchmark_result_preserves_dataset_lineage(
        self,
        seeded,
        session_factory,
        engine,
        read_results,
    ):
        service = make_service(session_factory, [ExactMatchEvaluator()])

        service.evaluate_run(
            "run-1",
            evaluation_run_id="erun-bench",
            case_id="case-1",
        )

        row = read_results()[0]

        with open_session(engine) as session:
            evaluation_run = session.get(EvaluationRun, "erun-bench")
            case = session.get(EvaluationCase, "case-1")

        assert evaluation_run.dataset_version_id == "dsv-1"
        assert case.dataset_version_id == "dsv-1"
        assert row.evaluation_run_id == evaluation_run.evaluation_run_id
        assert row.case_id == case.case_id
        assert row.run_id == "run-1"

    def test_case_from_other_dataset_is_rejected(
        self,
        seeded,
        session_factory,
        read_results,
    ):
        service = make_service(session_factory, [ExactMatchEvaluator()])

        with pytest.raises(InvalidEvaluationTargetError):
            service.evaluate_run(
                "run-1",
                evaluation_run_id="erun-bench",
                case_id="case-other",
            )

        assert read_results() == []

    def test_case_without_evaluation_run_is_rejected(
        self,
        seeded,
        session_factory,
        read_results,
    ):
        service = make_service(session_factory, [ExactMatchEvaluator()])

        with pytest.raises(InvalidEvaluationTargetError):
            service.evaluate_run("run-1", case_id="case-1")

        assert read_results() == []

    def test_missing_case_raises_entity_not_found(
        self,
        seeded,
        session_factory,
        read_results,
    ):
        service = make_service(session_factory, [ExactMatchEvaluator()])

        with pytest.raises(EntityNotFoundError):
            service.evaluate_run(
                "run-1",
                evaluation_run_id="erun-bench",
                case_id="case-missing",
            )

        assert read_results() == []


class TestEvaluatorSelection:

    def test_unknown_metric_rejected_before_evaluation(
        self,
        seeded,
        session_factory,
        read_results,
    ):
        service = make_service(session_factory, [LatencyEvaluator()])

        with pytest.raises(EvaluationConfigurationError):
            service.evaluate_run("run-1", metrics=["nope"])

        assert read_results() == []

    def test_no_configured_evaluators_rejected(
        self,
        seeded,
        session_factory,
        read_results,
    ):
        service = make_service(session_factory, [])

        with pytest.raises(EvaluationConfigurationError):
            service.evaluate_run("run-1")

        assert read_results() == []

    def test_empty_metric_selection_rejected(
        self,
        seeded,
        session_factory,
        read_results,
    ):
        service = make_service(session_factory, [LatencyEvaluator()])

        with pytest.raises(EvaluationConfigurationError):
            service.evaluate_run("run-1", metrics=[])

        assert read_results() == []

    def test_duplicate_metric_configuration_rejected(
        self,
        session_factory,
    ):
        with pytest.raises(EvaluationConfigurationError):
            make_service(
                session_factory,
                [LatencyEvaluator(), LatencyEvaluator()],
            )

    def test_metric_selection_runs_only_requested_evaluators(
        self,
        seeded,
        session_factory,
        read_results,
    ):
        service = make_service(
            session_factory,
            [LatencyEvaluator(), ExactMatchEvaluator()],
        )

        service.evaluate_run("run-1", metrics=["latency"])

        rows = read_results()
        assert [row.metric_name for row in rows] == ["latency"]


class TestErrorSemantics:

    def test_missing_metric_data_persists_unavailable(
        self,
        seeded,
        session_factory,
        read_results,
    ):
        service = make_service(session_factory, [ExactMatchEvaluator()])

        measurements = service.evaluate_run("run-1")

        assert measurements[0].status == EvaluationStatus.UNAVAILABLE

        row = read_results()[0]
        assert row.metrics["status"] == "unavailable"
        assert row.passed is None
        assert row.score is None
        assert row.failure_category is None

    def test_failed_criterion_is_not_evaluator_failure(
        self,
        seeded,
        session_factory,
        read_results,
    ):
        service = make_service(session_factory, [ExactMatchEvaluator()])

        measurements = service.evaluate_run(
            "run-1",
            criteria=EvaluationCriteria(expected_output="London"),
        )

        assert measurements[0].status == EvaluationStatus.SUCCESS
        assert measurements[0].passed is False

        row = read_results()[0]
        assert row.passed is False
        assert row.score == 0.0
        assert row.metrics["status"] == "success"
        assert row.failure_category is None

    def test_evaluator_execution_failure_persists_error(
        self,
        seeded,
        session_factory,
        read_results,
    ):
        service = make_service(session_factory, [RaisingEvaluator()])

        measurements = service.evaluate_run("run-1")

        assert measurements[0].status == EvaluationStatus.ERROR
        assert measurements[0].error_type == "RuntimeError"

        row = read_results()[0]
        assert row.metrics["status"] == "error"
        assert row.failure_category == "RuntimeError"
        assert row.passed is None
        assert row.score is None

    def test_invalid_target_aborts_before_persistence(
        self,
        seeded,
        session_factory,
        read_results,
    ):
        service = make_service(
            session_factory,
            [LatencyEvaluator()],
            target_builder=InvalidTargetBuilder(),
        )

        with pytest.raises(InvalidEvaluationTargetError):
            service.evaluate_run("run-1")

        assert read_results() == []

    def test_persistence_failure_rolls_back(
        self,
        seeded,
        session_factory,
        read_results,
    ):
        service = make_service(
            session_factory,
            [UnserializableEvaluator()],
        )

        with pytest.raises(StatementError):
            service.evaluate_run("run-1")

        assert read_results() == []


class TestEvaluationRunLifecycle:

    def test_start_sets_running_and_preserves_started_at(
        self,
        seeded,
        session_factory,
        read_evaluation_run,
    ):
        clock = MutableClock(T0)
        service = make_service(
            session_factory,
            [LatencyEvaluator()],
            clock=clock,
        )

        started = service.start_evaluation_run("erun-offline")

        assert started.status == "running"
        assert started.started_at == T0

        stored = read_evaluation_run("erun-offline")
        assert stored.status == "running"
        assert as_utc(stored.started_at) == T0

        clock.current = T1
        service.start_evaluation_run("erun-offline")

        restarted = read_evaluation_run("erun-offline")
        assert as_utc(restarted.started_at) == T0

    def test_complete_evaluation_run_finalizes_summary(
        self,
        seeded,
        session_factory,
        read_evaluation_run,
    ):
        clock = MutableClock(T0)
        service = make_service(
            session_factory,
            [ExactMatchEvaluator()],
            clock=clock,
        )

        service.start_evaluation_run("erun-offline")
        service.evaluate_run(
            "run-1",
            evaluation_run_id="erun-offline",
            criteria=EvaluationCriteria(expected_output=FRANCE_ANSWER),
        )
        service.evaluate_run(
            "run-2",
            evaluation_run_id="erun-offline",
            criteria=EvaluationCriteria(expected_output=FRANCE_ANSWER),
        )

        clock.current = T1
        completed = service.complete_evaluation_run("erun-offline")

        assert completed.status == "completed"
        assert completed.completed_at == T1

        stored = read_evaluation_run("erun-offline")
        assert stored.status == "completed"
        assert as_utc(stored.started_at) == T0
        assert as_utc(stored.completed_at) == T1
        assert stored.total_cases == 2
        assert stored.passed_cases == 1
        assert stored.failed_cases == 1
        assert stored.metrics["result_count"] == 2
        assert stored.metrics["status_counts"] == {
            "success": 2,
            "unavailable": 0,
            "error": 0,
        }
        assert stored.metrics["metric_summaries"]["exact_match"] == {
            "count": 2,
            "passed": 1,
            "failed": 1,
            "mean_score": 0.5,
        }

    def test_unavailable_results_do_not_count_as_failed(
        self,
        seeded,
        session_factory,
        read_evaluation_run,
    ):
        clock = MutableClock(T0)
        service = make_service(
            session_factory,
            [ExactMatchEvaluator()],
            clock=clock,
        )

        service.start_evaluation_run("erun-offline")
        service.evaluate_run(
            "run-1",
            evaluation_run_id="erun-offline",
        )

        completed = service.complete_evaluation_run("erun-offline")

        assert completed.status == "completed"
        assert completed.total_cases == 1
        assert completed.passed_cases == 0
        assert completed.failed_cases == 0
        assert completed.metrics["status_counts"]["unavailable"] == 1

    def test_partial_case_failures_still_complete(
        self,
        seeded,
        session_factory,
        read_evaluation_run,
    ):
        clock = MutableClock(T0)
        service = make_service(
            session_factory,
            [ExactMatchEvaluator()],
            clock=clock,
        )

        service.start_evaluation_run("erun-bench")
        service.evaluate_run(
            "run-1",
            evaluation_run_id="erun-bench",
            case_id="case-1",
        )
        service.evaluate_run(
            "run-1",
            evaluation_run_id="erun-bench",
            case_id="case-2",
        )

        completed = service.complete_evaluation_run("erun-bench")

        assert completed.status == "completed"
        assert completed.total_cases == 2
        assert completed.passed_cases == 1
        assert completed.failed_cases == 1

        stored = read_evaluation_run("erun-bench")
        assert stored.status == "completed"

    def test_failed_evaluation_run_keeps_partial_results(
        self,
        seeded,
        session_factory,
        read_results,
    ):
        clock = MutableClock(T0)
        service = make_service(
            session_factory,
            [ExactMatchEvaluator()],
            clock=clock,
        )

        service.start_evaluation_run("erun-offline")
        service.evaluate_run(
            "run-1",
            evaluation_run_id="erun-offline",
            criteria=EvaluationCriteria(expected_output=FRANCE_ANSWER),
        )

        failed = service.fail_evaluation_run("erun-offline")

        assert failed.status == "failed"
        assert failed.completed_at == T0
        assert failed.total_cases == 1

        rows = read_results()
        assert len(rows) == 1
        assert rows[0].evaluation_run_id == "erun-offline"

    def test_summary_only_counts_own_evaluation_run(
        self,
        seeded,
        session_factory,
        read_evaluation_run,
    ):
        service = make_service(session_factory, [ExactMatchEvaluator()])
        criteria = EvaluationCriteria(expected_output=FRANCE_ANSWER)

        service.evaluate_run(
            "run-1",
            evaluation_run_id="erun-offline",
            criteria=criteria,
        )
        service.evaluate_run(
            "run-1",
            evaluation_run_id="erun-other",
            criteria=criteria,
        )

        completed = service.complete_evaluation_run("erun-offline")

        assert completed.total_cases == 1
        assert completed.metrics["result_count"] == 1

        other = read_evaluation_run("erun-other")
        assert other.status == "pending"
        assert other.total_cases == 0

    def test_complete_missing_evaluation_run_raises(
        self,
        seeded,
        session_factory,
    ):
        service = make_service(session_factory, [ExactMatchEvaluator()])

        with pytest.raises(EntityNotFoundError):
            service.complete_evaluation_run("erun-missing")


class TestTransactionBoundary:

    def test_service_never_commits_outside_session_scope(
        self,
        seeded,
        engine,
        read_results,
    ):
        @contextmanager
        def non_committing_factory():
            session = Session(
                engine,
                expire_on_commit=False,
                autoflush=False,
            )

            try:
                yield session
            finally:
                session.rollback()
                session.close()

        service = EvaluationService(
            session_factory=non_committing_factory,
            evaluators=[LatencyEvaluator()],
        )

        service.evaluate_run("run-1")

        assert read_results() == []





