from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from schemas.events.common import AIEvent, EventType


class StrictPayload(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )


class PolicyCheckedPayload(StrictPayload):
    policy_id: str = Field(..., min_length=1)
    policy_version: str = Field(..., min_length=1)
    scope_type: str = Field(..., min_length=1)
    scope_id: str = Field(..., min_length=1)
    rule_count: int = Field(..., ge=0)


class PolicyCheckedEvent(AIEvent):
    event_type: Literal[EventType.POLICY_CHECKED] = EventType.POLICY_CHECKED
    payload: PolicyCheckedPayload


class PolicyAllowedPayload(StrictPayload):
    policy_id: str = Field(..., min_length=1)
    policy_version: str = Field(..., min_length=1)
    scope_type: str = Field(..., min_length=1)
    scope_id: str = Field(..., min_length=1)
    decision: Literal["allow"] = "allow"
    reason_codes: list[str] = Field(default_factory=list)
    triggered_rule_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)


class PolicyAllowedEvent(AIEvent):
    event_type: Literal[EventType.POLICY_ALLOWED] = EventType.POLICY_ALLOWED
    payload: PolicyAllowedPayload


class PolicyBlockedPayload(StrictPayload):
    policy_id: str = Field(..., min_length=1)
    policy_version: str = Field(..., min_length=1)
    scope_type: str = Field(..., min_length=1)
    scope_id: str = Field(..., min_length=1)
    decision: Literal["block"] = "block"
    reason_codes: list[str] = Field(default_factory=list)
    triggered_rule_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)


class PolicyBlockedEvent(AIEvent):
    event_type: Literal[EventType.POLICY_BLOCKED] = EventType.POLICY_BLOCKED
    payload: PolicyBlockedPayload


class BehaviorRuleTriggeredPayload(StrictPayload):
    policy_id: str = Field(..., min_length=1)
    rule_id: str = Field(..., min_length=1)
    behavior_type: str = Field(..., min_length=1)
    action: Literal["allow", "block", "review"]
    evidence_event_ids: list[str] = Field(default_factory=list)


class BehaviorRuleTriggeredEvent(AIEvent):
    event_type: Literal[EventType.BEHAVIOR_RULE_TRIGGERED] = (
        EventType.BEHAVIOR_RULE_TRIGGERED
    )
    payload: BehaviorRuleTriggeredPayload


class HumanReviewRequestedPayload(StrictPayload):
    review_id: str = Field(..., min_length=1)
    policy_id: str | None = Field(default=None, min_length=1)
    reason_code: str = Field(..., min_length=1)
    requested_action: str = Field(..., min_length=1)


class HumanReviewRequestedEvent(AIEvent):
    event_type: Literal[EventType.HUMAN_REVIEW_REQUESTED] = (
        EventType.HUMAN_REVIEW_REQUESTED
    )
    payload: HumanReviewRequestedPayload


class HumanReviewCompletedPayload(StrictPayload):
    review_id: str = Field(..., min_length=1)
    decision: Literal["approved", "rejected", "modified"]
    reviewer_type: Literal["human", "automated"]
    reason: str = Field(..., min_length=1)


class HumanReviewCompletedEvent(AIEvent):
    event_type: Literal[EventType.HUMAN_REVIEW_COMPLETED] = (
        EventType.HUMAN_REVIEW_COMPLETED
    )
    payload: HumanReviewCompletedPayload


AgentPolicyEvent = (
    PolicyCheckedEvent
    | PolicyAllowedEvent
    | PolicyBlockedEvent
    | BehaviorRuleTriggeredEvent
    | HumanReviewRequestedEvent
    | HumanReviewCompletedEvent
)