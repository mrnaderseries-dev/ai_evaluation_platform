
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


RCA_ARTIFACT_COUNT_LIMIT = 256

RCA_ATTRIBUTE_STRING_LIMIT = 512

RCA_ATTRIBUTE_ITEMS_LIMIT = 64

RCA_ATTRIBUTE_DEPTH_LIMIT = 6

RCA_ATTRIBUTES_BYTES_LIMIT = 16384

RCA_REPORT_BYTES_LIMIT = 1_048_576

RCA_HYPOTHESES_LIMIT = 256

RCA_HYPOTHESIS_EVIDENCE_LIMIT = 512

RCA_REPORT_LIMITATIONS_LIMIT = 512

RCA_REPORT_STRING_LIMIT = 4096

RCA_REPORT_DEPTH_LIMIT = 8

REDACTION_MARKER = "[REDACTED]"

TRUNCATION_MARKER = "[TRUNCATED]"

SENSITIVE_KEY_TOKENS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "credential",
    "authorization",
    "private_key",
    "cookie",
    "bearer",
    "access_key",
    "signing_key",
)

PROTECTED_IDENTIFIER_KEYS = frozenset(
    {
        "source_event_id",
        "source_observation_id",
        "correlation_id",
        "causation_id",
        "parent_event_id",
        "trace_id",
        "span_id",
        "parent_span_id",
        "run_id",
        "evaluation_run_id",
        "pipeline_run_id",
        "baseline_evaluation_run_id",
        "candidate_evaluation_run_id",
        "commit_sha",
        "commit",
        "agent_id",
        "system_id",
        "system_version",
        "environment",
        "dataset_version",
        "relationship_id",
        "relationship_key",
        "policy_version",
        "projection_version",
        "metric_name",
        "metric",
        "drift_metric",
        "analysis_id",
    }
)

_SECRET_VALUE_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{16,}"),
)


RCAStatus = Literal["open", "analyzing", "completed"]

RCATriggerType = Literal[
    "regression", "anomaly", "drift", "incident", "manual"
]

HypothesisStatus = Literal[
    "candidate", "supported", "inconclusive", "rejected", "validated"
]

EvidenceRole = Literal["supporting", "counter", "context"]

EvidenceSourceType = Literal[
    "signal",
    "observation",
    "event",
    "trace",
    "metric",
    "log",
    "analysis_result",
    "statistical_analysis",
    "differential_analysis",
    "relationship",
    "ci_cd_result",
    "validation_artifact",
]

EvidenceStrength = Literal["weak", "moderate", "strong"]

ValidationMethod = Literal[
    "controlled_experiment",
    "reproduction",
    "rollback_test",
    "independent_verification",
    "human_review",
]

ValidationOutcome = Literal["confirmed", "refuted", "inconclusive"]

ConclusionLevel = Literal[
    "descriptive",
    "associational",
    "mechanistically_supported",
    "validated_causal",
]

ConclusionType = Literal[
    "probable_cause",
    "multiple_contributing_factors",
    "no_supported_cause",
    "insufficient_evidence",
]


def _clean_non_blank(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    return normalized


def _require_aware_datetime(
    value: datetime,
    field_name: str,
) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


def _validate_unique(
    values: tuple[str, ...],
    field_name: str,
) -> tuple[str, ...]:
    normalized = tuple(
        _clean_non_blank(item, field_name) for item in values
    )
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} must be unique")
    return normalized


class DomainModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


class InvestigationScope(DomainModel):
    domain: str
    subject_type: str
    subject_id: str

    agent_id: str | None = None
    system_id: str | None = None
    system_version: str | None = None
    run_id: str | None = None
    evaluation_run_id: str | None = None
    pipeline_run_id: str | None = None
    commit_sha: str | None = None
    dataset_version: str | None = None
    environment: str | None = None

    observed_after: datetime | None = None
    observed_before: datetime | None = None

    @field_validator("domain", "subject_type", "subject_id")
    @classmethod
    def required_text(cls, value: str) -> str:
        return _clean_non_blank(value, "scope field")

    @field_validator(
        "agent_id",
        "system_id",
        "system_version",
        "run_id",
        "evaluation_run_id",
        "pipeline_run_id",
        "commit_sha",
        "dataset_version",
        "environment",
    )
    @classmethod
    def optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _clean_non_blank(value, "optional scope field")

    @field_validator("observed_after", "observed_before")
    @classmethod
    def aware_times(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _require_aware_datetime(value, "scope timestamp")

    @model_validator(mode="after")
    def valid_window(self) -> InvestigationScope:
        if (
            self.observed_after is not None
            and self.observed_before is not None
            and self.observed_after >= self.observed_before
        ):
            raise ValueError(
                "observed_after must be earlier than observed_before"
            )
        return self


class RCAEvidenceReference(DomainModel):
    evidence_id: str
    source_type: EvidenceSourceType
    source_ref: str
    role: EvidenceRole
    rationale: str

    relationship_id: str | None = None
    differential_analysis_id: str | None = None
    source_event_id: str | None = None
    source_observation_id: str | None = None

    @field_validator("evidence_id", "source_ref", "rationale")
    @classmethod
    def required_text(cls, value: str) -> str:
        return _clean_non_blank(value, "evidence field")

    @field_validator(
        "relationship_id",
        "differential_analysis_id",
        "source_event_id",
        "source_observation_id",
    )
    @classmethod
    def optional_reference(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _clean_non_blank(value, "evidence reference")

    @model_validator(mode="after")
    def consistent_source_reference(self) -> RCAEvidenceReference:
        if self.source_type == "differential_analysis":
            if self.differential_analysis_id is None:
                raise ValueError(
                    "differential_analysis evidence requires "
                    "differential_analysis_id"
                )
            if self.source_ref != self.differential_analysis_id:
                raise ValueError(
                    "source_ref must match differential_analysis_id"
                )
        elif self.differential_analysis_id is not None:
            raise ValueError(
                "differential_analysis_id is only valid for "
                "differential_analysis evidence"
            )

        if self.source_type == "relationship":
            if self.relationship_id is None:
                raise ValueError(
                    "relationship evidence requires relationship_id"
                )
            if self.source_ref != self.relationship_id:
                raise ValueError(
                    "source_ref must match relationship_id"
                )
        elif self.relationship_id is not None:
            raise ValueError(
                "relationship_id is only valid for relationship evidence"
            )

        return self


class RCAValidationArtifact(DomainModel):
    artifact_id: str
    hypothesis_id: str

    method: ValidationMethod
    outcome: ValidationOutcome

    performed_at: datetime
    recorded_at: datetime

    evidence: tuple[RCAEvidenceReference, ...] = Field(min_length=1)

    validator_id: str | None = None
    procedure_ref: str | None = None
    experiment_id: str | None = None

    limitations: tuple[str, ...] = ()
    metadata: dict[str, str | int | float | bool | None] = Field(
        default_factory=dict
    )

    @field_validator("artifact_id", "hypothesis_id")
    @classmethod
    def required_ids(cls, value: str) -> str:
        return _clean_non_blank(value, "validation artifact ID")

    @field_validator(
        "validator_id",
        "procedure_ref",
        "experiment_id",
    )
    @classmethod
    def optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _clean_non_blank(value, "validation field")

    @field_validator("performed_at", "recorded_at")
    @classmethod
    def aware_timestamps(cls, value: datetime) -> datetime:
        return _require_aware_datetime(value, "validation timestamp")

    @field_validator("limitations")
    @classmethod
    def clean_limitations(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        return tuple(
            _clean_non_blank(value, "limitation")
            for value in values
        )

    @model_validator(mode="after")
    def validate_artifact(self) -> RCAValidationArtifact:
        evidence_ids = [item.evidence_id for item in self.evidence]

        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError(
                "Validation artifact evidence IDs must be unique"
            )

        if self.recorded_at < self.performed_at:
            raise ValueError(
                "recorded_at cannot be earlier than performed_at"
            )

        if (
            self.outcome == "confirmed"
            and not any(
                item.role == "supporting"
                for item in self.evidence
            )
        ):
            raise ValueError(
                "Confirmed validation requires supporting evidence"
            )

        if (
            self.outcome == "refuted"
            and not any(
                item.role == "counter"
                for item in self.evidence
            )
        ):
            raise ValueError(
                "Refuted validation requires counter-evidence"
            )

        return self


class HypothesisValidation(DomainModel):
    artifact_id: str
    method: ValidationMethod
    outcome: ValidationOutcome
    performed_at: datetime

    evidence: tuple[RCAEvidenceReference, ...] = Field(min_length=1)
    limitations: tuple[str, ...] = ()

    @field_validator("artifact_id")
    @classmethod
    def required_artifact_id(cls, value: str) -> str:
        return _clean_non_blank(value, "artifact_id")

    @field_validator("performed_at")
    @classmethod
    def aware_performed_at(cls, value: datetime) -> datetime:
        return _require_aware_datetime(value, "performed_at")

    @field_validator("limitations")
    @classmethod
    def clean_limitations(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        return tuple(
            _clean_non_blank(value, "limitation")
            for value in values
        )

    @model_validator(mode="after")
    def unique_evidence(self) -> HypothesisValidation:
        ids = [item.evidence_id for item in self.evidence]
        if len(ids) != len(set(ids)):
            raise ValueError("Validation evidence IDs must be unique")

        if (
            self.outcome == "confirmed"
            and not any(
                item.role == "supporting"
                for item in self.evidence
            )
        ):
            raise ValueError(
                "Confirmed validation requires supporting evidence"
            )

        if (
            self.outcome == "refuted"
            and not any(
                item.role == "counter"
                for item in self.evidence
            )
        ):
            raise ValueError(
                "Refuted validation requires counter-evidence"
            )

        return self


class RCAHypothesis(DomainModel):
    hypothesis_id: str
    title: str
    statement: str
    mechanism: str

    status: HypothesisStatus = "candidate"
    strength: EvidenceStrength = "weak"

    evidence: tuple[RCAEvidenceReference, ...] = ()
    validation: HypothesisValidation | None = None

    limitations: tuple[str, ...] = ()
    rule_ids: tuple[str, ...] = ()

    @field_validator(
        "hypothesis_id",
        "title",
        "statement",
        "mechanism",
    )
    @classmethod
    def required_text(cls, value: str) -> str:
        return _clean_non_blank(value, "hypothesis field")

    @field_validator("limitations")
    @classmethod
    def clean_limitations(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        return tuple(
            _clean_non_blank(value, "limitation")
            for value in values
        )

    @field_validator("rule_ids")
    @classmethod
    def unique_rule_ids(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _validate_unique(values, "rule IDs")

    @model_validator(mode="after")
    def valid_assessment(self) -> RCAHypothesis:
        evidence_ids = [item.evidence_id for item in self.evidence]

        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError(
                "Evidence IDs must be unique within a hypothesis"
            )

        roles = {item.role for item in self.evidence}

        if self.status == "supported" and "supporting" not in roles:
            raise ValueError(
                "A supported hypothesis requires supporting evidence"
            )

        if self.status == "rejected" and "counter" not in roles:
            raise ValueError(
                "A rejected hypothesis requires counter-evidence"
            )

        if self.status == "validated":
            if self.validation is None:
                raise ValueError(
                    "A validated hypothesis requires validation"
                )
            if self.validation.outcome != "confirmed":
                raise ValueError(
                    "A validated hypothesis requires confirmed validation"
                )
            if "supporting" not in roles:
                raise ValueError(
                    "A validated hypothesis requires supporting evidence"
                )

        if self.validation is not None:
            if (
                self.validation.outcome == "confirmed"
                and self.status != "validated"
            ):
                raise ValueError(
                    "Confirmed validation requires status='validated'"
                )

            if (
                self.validation.outcome == "refuted"
                and self.status != "rejected"
            ):
                raise ValueError(
                    "Refuted validation requires status='rejected'"
                )

            if (
                self.validation.outcome == "inconclusive"
                and self.status not in {"candidate", "inconclusive"}
            ):
                raise ValueError(
                    "Inconclusive validation requires candidate "
                    "or inconclusive status"
                )

            hypothesis_evidence_ids = set(evidence_ids)
            validation_evidence_ids = {
                item.evidence_id
                for item in self.validation.evidence
            }

            if not validation_evidence_ids.issubset(
                hypothesis_evidence_ids
            ):
                raise ValueError(
                    "Validation evidence must be traceable from "
                    "hypothesis evidence"
                )

        return self


class RCAReport(DomainModel):
    schema_version: Literal["1.0", "1.1"] = "1.1"

    investigation_id: str
    trigger_type: RCATriggerType
    status: RCAStatus
    scope: InvestigationScope

    hypotheses: tuple[RCAHypothesis, ...] = ()

    conclusion_type: ConclusionType
    conclusion_level: ConclusionLevel
    summary: str

    limitations: tuple[str, ...] = ()
    differential_analysis_ids: tuple[str, ...] = ()

    algorithm_name: str
    algorithm_version: str
    evidence_fingerprint: str
    created_at: datetime

    @field_validator(
        "investigation_id",
        "summary",
        "algorithm_name",
        "algorithm_version",
        "evidence_fingerprint",
    )
    @classmethod
    def required_text(cls, value: str) -> str:
        return _clean_non_blank(value, "report field")

    @field_validator("created_at")
    @classmethod
    def aware_created_at(cls, value: datetime) -> datetime:
        return _require_aware_datetime(value, "created_at")

    @field_validator("limitations")
    @classmethod
    def clean_limitations(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        return tuple(
            _clean_non_blank(value, "limitation")
            for value in values
        )

    @field_validator("differential_analysis_ids")
    @classmethod
    def unique_analysis_ids(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _validate_unique(
            values,
            "differential analysis IDs",
        )

    @model_validator(mode="after")
    def validate_report_contract(self) -> RCAReport:
        ids = [hypothesis.hypothesis_id for hypothesis in self.hypotheses]

        if len(ids) != len(set(ids)):
            raise ValueError("Hypothesis IDs must be unique")

        report_analysis_ids = set(self.differential_analysis_ids)

        referenced_analysis_ids = {
            evidence.differential_analysis_id
            for hypothesis in self.hypotheses
            for evidence in (
                *hypothesis.evidence,
                *(
                    hypothesis.validation.evidence
                    if hypothesis.validation is not None
                    else ()
                ),
            )
            if evidence.differential_analysis_id is not None
        }

        if not referenced_analysis_ids.issubset(report_analysis_ids):
            raise ValueError(
                "Report differential_analysis_ids must include "
                "every analysis referenced by evidence"
            )

        supported = [
            hypothesis
            for hypothesis in self.hypotheses
            if hypothesis.status in {"supported", "validated"}
        ]

        if (
            self.conclusion_type == "probable_cause"
            and not supported
        ):
            raise ValueError(
                "probable_cause requires a supported hypothesis"
            )

        if self.conclusion_type == "multiple_contributing_factors":
            if len(supported) < 2:
                raise ValueError(
                    "multiple_contributing_factors requires at least "
                    "two supported hypotheses"
                )

        if (
            self.conclusion_type == "no_supported_cause"
            and supported
        ):
            raise ValueError(
                "no_supported_cause conflicts with supported hypotheses"
            )

        if (
            self.conclusion_type == "insufficient_evidence"
            and self.conclusion_level != "descriptive"
        ):
            raise ValueError(
                "insufficient_evidence must use descriptive level"
            )

        if self.conclusion_level == "validated_causal":
            causal_methods = {
                "controlled_experiment",
                "rollback_test",
            }

            valid_causal_validation = any(
                hypothesis.status == "validated"
                and hypothesis.validation is not None
                and hypothesis.validation.outcome == "confirmed"
                and hypothesis.validation.method in causal_methods
                for hypothesis in self.hypotheses
            )

            if not valid_causal_validation:
                raise ValueError(
                    "validated_causal requires a confirmed "
                    "controlled_experiment or rollback_test"
                )

        if (
            self.status != "completed"
            and self.conclusion_level == "validated_causal"
        ):
            raise ValueError(
                "An unfinished investigation cannot publish "
                "a validated causal conclusion"
            )

        return self


def compute_evidence_fingerprint(
    evidence: tuple[RCAEvidenceReference, ...],
) -> str:
    canonical = [
        {
            "evidence_id": item.evidence_id,
            "source_type": item.source_type,
            "source_ref": item.source_ref,
            "role": item.role,
            "rationale": item.rationale,
            "relationship_id": item.relationship_id,
            "differential_analysis_id": item.differential_analysis_id,
            "source_event_id": item.source_event_id,
            "source_observation_id": item.source_observation_id,
        }
        for item in sorted(
            evidence,
            key=lambda item: (
                item.evidence_id,
                item.source_type,
                item.source_ref,
            ),
        )
    ]

    payload = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )

    return sha256(payload.encode("utf-8")).hexdigest()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def is_sensitive_key(key: object) -> bool:
    if not isinstance(key, str):
        return False
    normalized = key.lower().replace("-", "_")
    return any(token in normalized for token in SENSITIVE_KEY_TOKENS)


def scrub_secret_text(value: str) -> tuple[str, int]:
    scrubbed = value
    count = 0
    for pattern in _SECRET_VALUE_PATTERNS:
        scrubbed, replacements = pattern.subn(REDACTION_MARKER, scrubbed)
        count += replacements
    return scrubbed, count


def redact_report_payload(
    value: Any,
    *,
    depth: int = 0,
) -> tuple[Any, int]:
    if depth > RCA_REPORT_DEPTH_LIMIT:
        return value, 0

    if isinstance(value, str):
        scrubbed, count = scrub_secret_text(value)
        return scrubbed, count

    if isinstance(value, dict):
        count = 0
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            if is_sensitive_key(key):
                redacted[key] = REDACTION_MARKER
                count += 1
            else:
                cleaned, nested = redact_report_payload(
                    item, depth=depth + 1
                )
                redacted[key] = cleaned
                count += nested
        return redacted, count

    if isinstance(value, list):
        count = 0
        cleaned_items = []
        for item in value:
            cleaned, nested = redact_report_payload(item, depth=depth + 1)
            cleaned_items.append(cleaned)
            count += nested
        return cleaned_items, count

    if isinstance(value, tuple):
        count = 0
        cleaned_items = []
        for item in value:
            cleaned, nested = redact_report_payload(item, depth=depth + 1)
            cleaned_items.append(cleaned)
            count += nested
        return tuple(cleaned_items), count

    return value, 0


def _report_depth(value: Any, depth: int = 1) -> int:
    if isinstance(value, dict):
        if not value:
            return depth
        return max(
            _report_depth(item, depth + 1) for item in value.values()
        )
    if isinstance(value, (list, tuple)):
        if not value:
            return depth
        return max(_report_depth(item, depth + 1) for item in value)
    return depth


def report_limit_violations(report: Any) -> list[str]:
    violations: list[str] = []

    try:
        serialized = json.dumps(
            report, ensure_ascii=False, default=str
        ).encode("utf-8")
    except (TypeError, ValueError):
        serialized = b""
        violations.append("report payload is not serializable")

    if len(serialized) > RCA_REPORT_BYTES_LIMIT:
        violations.append(
            f"report payload exceeds {RCA_REPORT_BYTES_LIMIT} bytes"
        )

    hypotheses = report.get("hypotheses") if isinstance(report, dict) else None
    if isinstance(hypotheses, list):
        if len(hypotheses) > RCA_HYPOTHESES_LIMIT:
            violations.append(
                f"report carries {len(hypotheses)} hypotheses; limit is "
                f"{RCA_HYPOTHESES_LIMIT}"
            )
        for index, hypothesis in enumerate(hypotheses):
            if not isinstance(hypothesis, dict):
                continue
            evidence = hypothesis.get("evidence")
            if isinstance(evidence, list) and len(evidence) > (
                RCA_HYPOTHESIS_EVIDENCE_LIMIT
            ):
                violations.append(
                    f"hypotheses[{index}] carries {len(evidence)} evidence "
                    f"references; limit is {RCA_HYPOTHESIS_EVIDENCE_LIMIT}"
                )
            for field in ("title", "statement", "mechanism"):
                text = hypothesis.get(field)
                if isinstance(text, str) and len(text) > RCA_REPORT_STRING_LIMIT:
                    violations.append(
                        f"hypotheses[{index}].{field} exceeds "
                        f"{RCA_REPORT_STRING_LIMIT} characters"
                    )
            hypothesis_limitations = hypothesis.get("limitations")
            if isinstance(hypothesis_limitations, list):
                for item_index, text in enumerate(hypothesis_limitations):
                    if isinstance(text, str) and len(text) > (
                        RCA_REPORT_STRING_LIMIT
                    ):
                        violations.append(
                            f"hypotheses[{index}].limitations[{item_index}] "
                            f"exceeds {RCA_REPORT_STRING_LIMIT} characters"
                        )

    limitations = report.get("limitations") if isinstance(report, dict) else None
    if isinstance(limitations, list):
        if len(limitations) > RCA_REPORT_LIMITATIONS_LIMIT:
            violations.append(
                f"report carries {len(limitations)} limitations; limit is "
                f"{RCA_REPORT_LIMITATIONS_LIMIT}"
            )
        for index, text in enumerate(limitations):
            if isinstance(text, str) and len(text) > RCA_REPORT_STRING_LIMIT:
                violations.append(
                    f"limitations[{index}] exceeds "
                    f"{RCA_REPORT_STRING_LIMIT} characters"
                )

    if isinstance(report, dict):
        depth = _report_depth(report)
        if depth > RCA_REPORT_DEPTH_LIMIT:
            violations.append(
                f"report nesting depth {depth} exceeds "
                f"{RCA_REPORT_DEPTH_LIMIT}"
            )

    return violations
