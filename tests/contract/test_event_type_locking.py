from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from schemas.events.agent_behavior_events import (
    AbstentionEvent,
    AgentActionEvent,
    AnswerRevisedEvent,
    BehaviorPatternDetectedEvent,
    CounterEvidenceRequestedEvent,
    VerificationAttemptedEvent,
)
from schemas.events.common import EventType
from schemas.events.context_events import (
    ContextAssembledEvent,
    ContextResourceUsedEvent,
    ContextUpdatedEvent,
)
from schemas.events.evaluation_events import EvaluationCompletedEvent
from schemas.events.execution_events import (
    AnswerGeneratedEvent,
    ErrorEvent,
    ModelCalledEvent,
    ModelOutputEvent,
    RecoveryAttemptedEvent,
    RecoveryCompletedEvent,
    RetrievalCompletedEvent,
    RetrievalStartedEvent,
    ToolCalledEvent,
    ToolCompletedEvent,
)
from schemas.events.policy_events import (
    BehaviorRuleTriggeredEvent,
    HumanReviewCompletedEvent,
    HumanReviewRequestedEvent,
    PolicyAllowedEvent,
    PolicyBlockedEvent,
    PolicyCheckedEvent,
)


def base_fields() -> dict:
    return {
        "event_id": "event-001",
        "schema_version": "1.0",
        "system_id": "agent-001",
        "system_version": "1.0.0",
        "run_id": "run-001",
        "timestamp": datetime.now(timezone.utc),
    }


CASES = [
    (ModelCalledEvent, EventType.MODEL_CALLED, {"model_id": "m"}),
    (ModelOutputEvent, EventType.MODEL_OUTPUT, {"model_id": "m", "output_id": "o"}),
    (ToolCalledEvent, EventType.TOOL_CALLED, {"tool_id": "t", "tool_name": "t", "call_id": "c"}),
    (ToolCompletedEvent, EventType.TOOL_COMPLETED, {"tool_id": "t", "tool_name": "t", "call_id": "c", "success": True}),
    (RetrievalStartedEvent, EventType.RETRIEVAL_STARTED, {"retrieval_id": "r", "retriever_id": "s", "query": "q", "top_k": 1}),
    (RetrievalCompletedEvent, EventType.RETRIEVAL_COMPLETED, {"retrieval_id": "r", "retriever_id": "s", "result_count": 0}),
    (AnswerGeneratedEvent, EventType.ANSWER_GENERATED, {"answer_id": "a", "answer_type": "final"}),
    (ErrorEvent, EventType.ERROR, {"error_id": "e", "error_type": "E", "message": "m"}),
    (RecoveryAttemptedEvent, EventType.RECOVERY_ATTEMPTED, {"recovery_id": "r", "strategy": "s", "attempt_number": 1}),
    (RecoveryCompletedEvent, EventType.RECOVERY_COMPLETED, {"recovery_id": "r", "success": True, "strategy": "s", "attempt_number": 1}),
    (EvaluationCompletedEvent, EventType.EVALUATION_COMPLETED, {"evaluation_run_id": "r", "evaluation_case_id": "c", "metric": "m", "value": 1.0, "evaluator_type": "human"}),
    (ContextAssembledEvent, EventType.CONTEXT_ASSEMBLED, {"context_id": "c", "resource_count": 0}),
    (ContextResourceUsedEvent, EventType.CONTEXT_RESOURCE_USED, {"context_id": "c", "resource_id": "r", "resource_type": "t", "usage_type": "u"}),
    (ContextUpdatedEvent, EventType.CONTEXT_UPDATED, {"context_id": "c", "previous_resource_count": 0, "current_resource_count": 1}),
    (PolicyCheckedEvent, EventType.POLICY_CHECKED, {"policy_id": "p", "policy_version": "v", "scope_type": "s", "scope_id": "i", "rule_count": 0}),
    (PolicyAllowedEvent, EventType.POLICY_ALLOWED, {"policy_id": "p", "policy_version": "v", "scope_type": "s", "scope_id": "i", "decision": "allow"}),
    (PolicyBlockedEvent, EventType.POLICY_BLOCKED, {"policy_id": "p", "policy_version": "v", "scope_type": "s", "scope_id": "i", "decision": "block"}),
    (BehaviorRuleTriggeredEvent, EventType.BEHAVIOR_RULE_TRIGGERED, {"policy_id": "p", "rule_id": "r", "behavior_type": "b", "action": "allow"}),
    (HumanReviewRequestedEvent, EventType.HUMAN_REVIEW_REQUESTED, {"review_id": "r", "reason_code": "c", "requested_action": "a"}),
    (HumanReviewCompletedEvent, EventType.HUMAN_REVIEW_COMPLETED, {"review_id": "r", "decision": "approved", "reviewer_type": "human", "reason": "ok"}),
    (AgentActionEvent, EventType.AGENT_ACTION, {"action_id": "a", "action_type": "t"}),
    (VerificationAttemptedEvent, EventType.VERIFICATION_ATTEMPTED, {"verification_id": "v", "verification_method": "m"}),
    (CounterEvidenceRequestedEvent, EventType.COUNTER_EVIDENCE_REQUESTED, {"verification_id": "v"}),
    (AnswerRevisedEvent, EventType.ANSWER_REVISED, {"answer_id": "a", "previous_answer_id": "p", "revision_type": "r"}),
    (AbstentionEvent, EventType.ABSTENTION, {"reason_code": "r"}),
    (BehaviorPatternDetectedEvent, EventType.BEHAVIOR_PATTERN_DETECTED, {"pattern_id": "p", "pattern_type": "t", "window_size": 1, "occurrence_count": 1}),
]


@pytest.mark.parametrize("cls,expected,payload", CASES)
def test_correct_event_type_is_accepted(cls, expected, payload):
    event = cls(**base_fields(), payload=payload)
    assert event.event_type == expected


@pytest.mark.parametrize("cls,expected,payload", CASES)
def test_wrong_event_type_is_rejected_for_all(cls, expected, payload):
    wrong = EventType.ERROR if expected != EventType.ERROR else EventType.MODEL_CALLED
    with pytest.raises(ValidationError):
        cls(**base_fields(), event_type=wrong, payload=payload)
