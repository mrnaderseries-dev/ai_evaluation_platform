"""Agent-aware change analysis and evaluation planning.

This package implements the first milestone of the agent-aware evaluation
planning design:

* :mod:`ai_eval_platform.planning.domain` — typed, framework-independent
  contracts for agents, changes, requirements, datasets, and plans.
* :mod:`ai_eval_platform.planning.change_analysis` — a pure, deterministic
  change analyzer over two agent versions.
* :mod:`ai_eval_platform.planning.strategies` — the agent-type strategy
  extension point and its registry.
* :mod:`ai_eval_platform.planning.dataset_matcher` — deterministic dataset
  coverage matching with explicit gaps.
* :mod:`ai_eval_platform.planning.planner` — combines changes, strategies,
  evidence, and dataset matches into an explainable plan.
* :mod:`ai_eval_platform.planning.integration` — adapter layer that connects
  routing to planning, fetches prior evaluation evidence from storage, and
  orchestrates an end-to-end planning workflow.

Planning never executes evaluations and never approves a deployment.
"""
from .change_analysis import (
    AgentVersionSnapshot,
    ChangeAnalyzer,
    CrossAgentComparisonError,
)
from .dataset_matcher import (
    INELIGIBLE_QUALITY_STATUSES,
    DatasetMatcher,
)
from .domain import (
    AgentDescriptor,
    ChangeCompleteness,
    ChangeDescription,
    ChangeOperation,
    ChangeSource,
    ChangeType,
    ComponentChange,
    CoverageGap,
    DatasetDescriptor,
    DatasetMatch,
    EvaluationPlan,
    EvaluationRequirement,
    PlanStatus,
    ReasonKind,
    RiskEvidence,
    RiskLevel,
    make_reason_ref,
    parse_reason_ref,
)
from .planner import (
    DEFAULT_POLICY_VERSION,
    EvaluationPlanner,
    PlanRequest,
    PlanValidationError,
    PlanningError,
    explain_plan,
    validate_plan,
)
from .integration import (
    EvaluationPlanningOrchestrator,
    PriorEvidenceProvider,
    RoutingToPlanningAdapter,
)
from .strategies import (
    AgenticRAGStrategy,
    AgentTypeStrategy,
    DuplicateAgentTypeError,
    InvalidStrategyError,
    StrategyRegistry,
    UnsupportedAgentTypeError,
)

__all__ = [
    "AgentDescriptor",
    "AgenticRAGStrategy",
    "AgentTypeStrategy",
    "AgentVersionSnapshot",
    "ChangeAnalyzer",
    "ChangeCompleteness",
    "ChangeDescription",
    "ChangeOperation",
    "ChangeSource",
    "ChangeType",
    "ComponentChange",
    "CoverageGap",
    "CrossAgentComparisonError",
    "DEFAULT_POLICY_VERSION",
    "DatasetDescriptor",
    "DatasetMatch",
    "DatasetMatcher",
    "DuplicateAgentTypeError",
    "EvaluationPlan",
    "EvaluationPlanner",
    "EvaluationPlanningOrchestrator",
    "EvaluationRequirement",
    "INELIGIBLE_QUALITY_STATUSES",
    "InvalidStrategyError",
    "PlanRequest",
    "PlanStatus",
    "PlanValidationError",
    "PlanningError",
    "PriorEvidenceProvider",
    "ReasonKind",
    "RiskEvidence",
    "RiskLevel",
    "RoutingToPlanningAdapter",
    "StrategyRegistry",
    "UnsupportedAgentTypeError",
    "explain_plan",
    "make_reason_ref",
    "parse_reason_ref",
    "validate_plan",
]
