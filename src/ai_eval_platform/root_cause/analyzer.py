
from __future__ import annotations

from hashlib import sha256
import json
from math import isfinite
from typing import Iterable

from .domain import (
    RCA_ARTIFACT_COUNT_LIMIT,
    ConclusionLevel,
    ConclusionType,
    InvestigationScope,
    RCAEvidenceReference,
    RCAHypothesis,
    RCAReport,
    RCAStatus,
    RCATriggerType,
    compute_evidence_fingerprint,
    utc_now,
)
from .evidence import (
    RCAArtifact,
    reference_for,
    sanitize_artifact_for_analysis,
)


class RCAAnalyzer:
    algorithm_name = "hybrid_rca"
    algorithm_version = "1.2.0"

    #: Maximum age skew tolerated when comparing artifact timestamps during
    #: context enrichment. Artifacts without timestamps are still eligible as
    #: context (uncertainty disclosed), but never treated as temporally close.
    context_window_seconds = 300.0

    #: Minimum per-group sample size for a differential comparison to support
    #: (not merely suggest) a regression hypothesis.
    min_group_samples = 5

    #: Sources whose only permitted role is contextual: they may generate a
    #: context-only hypothesis and attach to other hypotheses via
    #: _enrich_with_context, and are never causal support or counter-evidence.
    #: validation_artifact never generates a hypothesis; its presence is
    #: disclosed as a report limitation instead.
    _CONTEXT_TYPES = frozenset(
        {
            "event",
            "trace",
            "metric",
            "log",
            "validation_artifact",
            "observation",
            "statistical_analysis",
            "analysis_result",
        }
    )

    _SYMPTOM_SIGNALS = frozenset(
        {
            "latency_anomaly",
            "confidence_drop",
            "tool_usage_drift",
            "error_pattern",
            "route_change",
            "quality_regression",
            "data_drift",
            "behavior_drift",
        }
    )

    _FAILED_DECISIONS = frozenset(
        {"fail", "failed", "block", "blocked"}
    )

    _PASSED_DECISIONS = frozenset({"pass", "passed"})

    def analyze(
        self,
        *,
        investigation_id: str,
        trigger_type: RCATriggerType,
        scope: InvestigationScope,
        artifacts: Iterable[RCAArtifact],
        status: RCAStatus = "completed",
        limitations: tuple[str, ...] = (),
    ) -> RCAReport:
        supplied = tuple(
            sorted(
                artifacts,
                key=lambda item: (
                    item.source_type,
                    item.artifact_id,
                    item.source_ref,
                ),
            )
        )

        if len(supplied) > RCA_ARTIFACT_COUNT_LIMIT:
            raise ValueError(
                "RCA analysis accepts at most "
                f"{RCA_ARTIFACT_COUNT_LIMIT} artifacts per "
                f"investigation; received {len(supplied)}."
            )

        artifact_ids = [item.artifact_id for item in supplied]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError(
                "RCA artifacts must have unique artifact_id values"
            )

        report_limitations = list(self._clean_texts(limitations))
        eligible: list[RCAArtifact] = []

        for artifact in supplied:
            sanitized, sanitize_notes = sanitize_artifact_for_analysis(artifact)

            if sanitize_notes:
                report_limitations.extend(sanitize_notes)

            if sanitized is None:
                continue

            artifact = sanitized

            in_scope, scope_notes = self._scope_check(artifact, scope)

            if not in_scope:
                continue

            if scope_notes:
                artifact = artifact.model_copy(
                    update={
                        "limitations": self._clean_texts(
                            (*artifact.limitations, *scope_notes)
                        )
                    }
                )
                # Scope uncertainty must be visible at report level too, not
                # only on the individual artifact's hypothesis.
                report_limitations.extend(scope_notes)

            eligible.append(artifact)

        excluded_count = len(supplied) - len(eligible)

        if excluded_count:
            report_limitations.append(
                f"Excluded {excluded_count} artifact(s) because of explicit scope conflicts."
            )

        if not eligible:
            report_limitations.append(
                "No eligible in-scope evidence artifacts were available."
            )

        eligible_by_id = {
            artifact.artifact_id: artifact for artifact in eligible
        }

        eligible_lineage = {
            value
            for artifact in eligible
            for value in (artifact.artifact_id, artifact.source_ref)
        }

        unresolved_relationship_ids: set[str] = set()

        for artifact in eligible:
            if artifact.source_type != "relationship":
                continue

            evidence_ids = artifact.attributes.get("evidence_ids", ())
            if not isinstance(evidence_ids, (list, tuple, set)):
                evidence_ids = ()

            missing_ids = [
                str(evidence_id)
                for evidence_id in evidence_ids
                if str(evidence_id) not in eligible_lineage
            ]

            if missing_ids:
                unresolved_relationship_ids.add(artifact.artifact_id)
                report_limitations.append(
                    f"Relationship {artifact.source_ref} references "
                    f"{len(missing_ids)} evidence item(s) unavailable "
                    "in the investigation scope."
                )

        hypotheses: list[RCAHypothesis] = []

        for artifact in eligible:
            hypothesis = self._hypothesis_from_artifact(
                artifact,
                report_limitations,
            )

            if hypothesis is not None:
                if (
                    artifact.source_type == "relationship"
                    and artifact.artifact_id
                    in unresolved_relationship_ids
                ):
                    hypothesis = hypothesis.model_copy(
                        update={
                            "limitations": self._clean_texts(
                                (
                                    *hypothesis.limitations,
                                    "The relationship could not be fully resolved against eligible source evidence.",
                                )
                            )
                        }
                    )

                hypotheses.append(hypothesis)

        context_artifacts = [
            artifact
            for artifact in eligible
            if self._is_context_artifact(artifact)
        ]

        enriched_hypotheses = [
            self._enrich_with_context(
                hypothesis=hypothesis,
                context_artifacts=context_artifacts,
                artifact_by_id=eligible_by_id,
            )
            for hypothesis in hypotheses
        ]

        assessed_hypotheses = [
            self._assess_counter_evidence(hypothesis)
            for hypothesis in enriched_hypotheses
        ]

        # Re-assess after merging: a merge can bring counter references into
        # a hypothesis that was supported on its constituent evidence alone.
        source_keys = self._source_component_keys(
            assessed_hypotheses, artifact_by_id=eligible_by_id
        )

        merged_hypotheses = [
            self._assess_counter_evidence(hypothesis)
            for hypothesis in self._merge_hypotheses(
                assessed_hypotheses,
                source_keys=source_keys,
            )
        ]

        component_owners: dict[str, set[str]] = {}
        supporting_components: dict[str, frozenset[str]] = {}

        for hypothesis in merged_hypotheses:
            supporting: set[str] = set()
            for reference in hypothesis.evidence:
                component = source_keys.get(
                    reference.evidence_id,
                    self._evidence_source_key(reference),
                )
                component_owners.setdefault(component, set()).add(
                    hypothesis.hypothesis_id
                )
                if reference.role == "supporting":
                    supporting.add(component)
            supporting_components[hypothesis.hypothesis_id] = frozenset(
                supporting
            )

        shared_components = [
            component
            for component, owners in component_owners.items()
            if len(owners) > 1
        ]

        if shared_components:
            report_limitations.append(
                f"{len(shared_components)} underlying source(s) appear as "
                "evidence under more than one hypothesis; duplicate "
                "representations of one source do not count as independent "
                "support."
            )

        ranked_hypotheses = tuple(
            sorted(
                merged_hypotheses,
                key=lambda hypothesis: (
                    self._rank(hypothesis),
                    not any(
                        reference.role == "supporting"
                        for reference in hypothesis.evidence
                    ),
                    hypothesis.hypothesis_id,
                ),
            )
        )

        conclusion_type, conclusion_level, summary = (
            self._conclusion(ranked_hypotheses, supporting_components)
        )

        all_references = tuple(
            reference
            for hypothesis in ranked_hypotheses
            for reference in hypothesis.evidence
        )

        differential_analysis_ids = tuple(
            sorted(
                {
                    reference.differential_analysis_id
                    for reference in all_references
                    if reference.differential_analysis_id is not None
                }
            )
        )

        report_limitations = list(
            self._clean_texts(report_limitations)
        )

        return RCAReport(
            schema_version="1.1",
            investigation_id=investigation_id,
            trigger_type=trigger_type,
            status=status,
            scope=scope,
            hypotheses=ranked_hypotheses,
            conclusion_type=conclusion_type,
            conclusion_level=conclusion_level,
            summary=summary,
            limitations=tuple(report_limitations),
            differential_analysis_ids=differential_analysis_ids,
            algorithm_name=self.algorithm_name,
            algorithm_version=self.algorithm_version,
            evidence_fingerprint=compute_evidence_fingerprint(
                all_references
            ),
            created_at=utc_now(),
        )

    @staticmethod
    def _clean_texts(values: Iterable[str]) -> tuple[str, ...]:
        result: list[str] = []
        seen: set[str] = set()

        for value in values:
            normalized = str(value).strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                result.append(normalized)

        return tuple(result)

    @staticmethod
    def _scope_check(
        artifact: RCAArtifact,
        scope: InvestigationScope,
    ) -> tuple[bool, tuple[str, ...]]:
        pipeline_run_id = artifact.pipeline_run_id
        if pipeline_run_id is None:
            attribute_pipeline_run = artifact.attributes.get("pipeline_run_id")
            if attribute_pipeline_run is not None:
                pipeline_run_id = str(attribute_pipeline_run)

        environment = artifact.environment
        if environment is None:
            attribute_environment = artifact.attributes.get("environment")
            if attribute_environment is not None:
                environment = str(attribute_environment)

        dimensions = (
            ("domain", scope.domain, artifact.domain),
            ("subject_type", scope.subject_type, artifact.subject_type),
            ("subject_id", scope.subject_id, artifact.subject_id),
            ("agent_id", scope.agent_id, artifact.agent_id),
            ("system_id", scope.system_id, artifact.system_id),
            (
                "system_version",
                scope.system_version,
                artifact.system_version,
            ),
            ("run_id", scope.run_id, artifact.run_id),
            (
                "evaluation_run_id",
                scope.evaluation_run_id,
                artifact.evaluation_run_id,
            ),
            (
                "pipeline_run_id",
                scope.pipeline_run_id,
                pipeline_run_id,
            ),
            (
                "environment",
                scope.environment,
                environment,
            ),
        )

        for _, expected, actual in dimensions:
            if (
                expected is not None
                and actual is not None
                and expected != actual
            ):
                return False, ()

        # Differential comparisons carry their own run lineage; a candidate
        # run that matches neither the investigation run nor the evaluation
        # run is out of scope even when the artifact omits the top-level
        # run field (the adapter stores it under evaluation_run_id only).
        if artifact.source_type == "differential_analysis":
            candidate_run = artifact.attributes.get("candidate_evaluation_run_id")
            baseline_run = artifact.attributes.get("baseline_evaluation_run_id")
            run_scope = scope.evaluation_run_id or scope.run_id
            if (
                run_scope is not None
                and candidate_run is not None
                and candidate_run != run_scope
                and baseline_run != run_scope
            ):
                return False, ()

        # CI/CD results carry commit/deployment lineage in attributes; an
        # explicit commit or deployment mismatch excludes, a match is
        # neutral (never proof), and absence stays uncertain (noted below).
        if artifact.source_type == "ci_cd_result":
            scope_commit = scope.commit_sha
            artifact_commit = artifact.attributes.get(
                "commit_sha", artifact.attributes.get("commit")
            )
            if (
                scope_commit is not None
                and artifact_commit is not None
                and str(artifact_commit) != str(scope_commit)
            ):
                return False, ()

        # Dataset lineage rides in attributes for providers that record it;
        # an explicit mismatch excludes, absence stays uncertain (noted below).
        if scope.dataset_version is not None:
            artifact_dataset = artifact.attributes.get("dataset_version")
            if artifact_dataset is not None and str(artifact_dataset) != str(
                scope.dataset_version
            ):
                return False, ()

        if scope.observed_after is not None:
            if artifact.observed_at is not None:
                if artifact.observed_at < scope.observed_after:
                    return False, ()

        if scope.observed_before is not None:
            if artifact.observed_at is not None:
                if artifact.observed_at > scope.observed_before:
                    return False, ()

        notes: list[str] = []

        for name, expected, actual in dimensions:
            if expected is not None and actual is None:
                notes.append(
                    f"Artifact has no {name}; this scope dimension could not be verified."
                )

        # Missing high-risk lineage on evidence that claims a comparison or
        # check outcome stays uncertain and weakens support downstream.
        if artifact.source_type == "differential_analysis":
            if artifact.attributes.get("candidate_evaluation_run_id") is None:
                notes.append(
                    "Differential artifact has no candidate run lineage; "
                    "staleness could not be verified."
                )
        if artifact.source_type == "ci_cd_result":
            if (
                artifact.attributes.get("commit_sha") is None
                and artifact.attributes.get("commit") is None
                and artifact.attributes.get("build_id") is None
                and artifact.attributes.get("pipeline_id") is None
                and artifact.attributes.get("deployment_id") is None
            ):
                notes.append(
                    "CI/CD artifact has no commit/build/deployment lineage; "
                    "scope matching is uncertain."
                )

        if (
            scope.dataset_version is not None
            and artifact.attributes.get("dataset_version") is None
        ):
            notes.append(
                "Artifact has no dataset_version; this scope dimension could not be verified."
            )

        if (
            scope.observed_after is not None
            or scope.observed_before is not None
        ) and artifact.observed_at is None:
            notes.append(
                "Artifact has no timestamp; the investigation time window could not be verified."
            )

        return True, tuple(notes)

    def _hypothesis_from_artifact(
        self,
        artifact: RCAArtifact,
        report_limitations: list[str],
    ) -> RCAHypothesis | None:
        """Route one artifact; every recognized type has an explicit outcome.

        Primary generators (signal, differential_analysis, relationship,
        event, ci_cd_result) build explanatory hypotheses; statistical_
        analysis, analysis_result, trace, metric, log and observation build
        context-only hypotheses and are also eligible for attachment via
        _enrich_with_context. validation_artifact never builds a hypothesis
        and is disclosed as a report limitation. Unrecognized types are
        disclosed as skipped so no recognized artifact disappears silently.
        """
        if artifact.source_type == "signal":
            return self._signal_hypothesis(artifact)

        if artifact.source_type == "differential_analysis":
            return self._differential_hypothesis(artifact, report_limitations)

        if artifact.source_type == "relationship":
            return self._relationship_hypothesis(artifact)

        if artifact.source_type == "event":
            return self._event_hypothesis(artifact)

        if artifact.source_type == "ci_cd_result":
            return self._cicd_hypothesis(artifact, report_limitations)

        if artifact.source_type == "statistical_analysis":
            return self._statistical_hypothesis(artifact, report_limitations)

        if artifact.source_type == "analysis_result":
            return self._analysis_result_hypothesis(artifact, report_limitations)

        if artifact.source_type == "validation_artifact":
            report_limitations.append(
                f"Validation artifact {artifact.source_ref} was supplied, "
                "but this analyzer does not independently verify validation outcomes."
            )
            return None

        if artifact.source_type in {
            "trace",
            "metric",
            "log",
            "observation",
        }:
            return self._context_hypothesis(artifact)

        report_limitations.append(
            f"Skipped artifact {artifact.artifact_id}: "
            f"unsupported source_type {artifact.source_type!r}."
        )
        return None

    def _signal_hypothesis(
        self,
        artifact: RCAArtifact,
    ) -> RCAHypothesis:
        attributes = artifact.attributes
        signal_type = str(
            attributes.get("signal_type", "unknown_signal")
        )
        origin = str(attributes.get("origin", "unknown"))
        payload = attributes.get("payload")

        if not isinstance(payload, dict):
            payload = {}

        metric = str(
            payload.get("metric")
            or payload.get("metric_name")
            or payload.get("drift_metric")
            or signal_type
        )

        supporting_symptom = (
            signal_type in self._SYMPTOM_SIGNALS
        )

        role = "supporting" if supporting_symptom else "context"

        rationale = (
            f"The {origin}-origin signal records the investigated symptom; "
            "it does not independently identify the upstream cause."
            if supporting_symptom
            else "The signal is retained as contextual evidence because its type does not establish a direct relationship to the investigated symptom."
        )

        reference = reference_for(
            artifact,
            role=role,
            rationale=rationale,
        )

        return self._make_hypothesis(
            artifact=artifact,
            rule_id=f"signal.symptom.{signal_type}",
            title=f"Investigate {metric} condition",
            statement=(
                f"The observed {signal_type} condition may be relevant "
                "to the investigated behavior."
            ),
            mechanism=(
                "The signal identifies an observed condition. "
                "Additional evidence is needed to establish its mechanism "
                "or upstream cause."
            ),
            references=(reference,),
            status="candidate",
            strength="weak",
            limitations=(
                *artifact.limitations,
                "A signal label and its origin do not establish causality.",
            ),
        )

    def _differential_hypothesis(
        self,
        artifact: RCAArtifact,
        report_limitations: list[str],
    ) -> RCAHypothesis | None:
        attributes = artifact.attributes
        metric = str(
            attributes.get("metric_name")
            or attributes.get("metric")
            or "unspecified_metric"
        )

        result_status = str(
            attributes.get("status", "")
        ).strip().lower()

        if result_status not in {
            "regressed",
            "improved",
            "no_clear_difference",
            "insufficient_evidence",
        }:
            return None

        regression = result_status == "regressed"
        improvement = result_status == "improved"

        n_baseline = self._sample_size(
            attributes.get(
                "baseline_sample_size",
                attributes.get("n_baseline"),
            )
        )
        n_candidate = self._sample_size(
            attributes.get(
                "candidate_sample_size",
                attributes.get("n_candidate"),
            )
        )

        enough_samples = (
            n_baseline is not None
            and n_candidate is not None
            and min(n_baseline, n_candidate) >= self.min_group_samples
        )

        significant = (
            attributes.get("statistically_significant") is True
        )

        p_value = self._finite_number(
            attributes.get(
                "adjusted_p_value",
                attributes.get(
                    "decision_p_value",
                    attributes.get("p_value"),
                ),
            )
        )

        if p_value is not None and not 0.0 <= p_value <= 1.0:
            p_value = None

        # Trust the upstream statistical decision (which already applies the
        # configured multiple-comparison policy, e.g. Holm). The persisted
        # flag is authoritative; a finite p-value is only a sanity bound,
        # never an independent re-test at a hard-coded alpha.
        if significant and p_value is None and (
            attributes.get("adjusted_p_value") is not None
            or attributes.get("decision_p_value") is not None
            or attributes.get("p_value") is not None
        ):
            significant = False
            report_limitations.append(
                f"Differential artifact {artifact.source_ref} claims "
                "significance with a non-finite or out-of-range p-value; "
                "treated as not significant."
            )

        practical = attributes.get("practical_significance_met")

        effect_size = self._finite_number(
            attributes.get(
                "effect_size",
                attributes.get("rank_biserial"),
            )
        )

        confidence_interval = attributes.get(
            "confidence_interval",
            attributes.get("effect_size_confidence_interval"),
        )

        confidence_interval_available = (
            isinstance(confidence_interval, (list, tuple))
            and len(confidence_interval) == 2
            and all(
                self._finite_number(value) is not None
                for value in confidence_interval
            )
        )

        # The upstream contract only defines "higher_is_better" and
        # "lower_is_better"; an unrecognized direction makes the effect
        # uninterpretable, so support is withheld and the defect disclosed.
        direction_value = attributes.get("metric_direction")
        direction_known = direction_value is None or str(
            direction_value
        ) in {"higher_is_better", "lower_is_better"}

        statistical_support = (
            regression
            and enough_samples
            and significant
            and practical is True
            and direction_known
        )

        role = (
            "supporting"
            if regression
            else "counter"
            if improvement or result_status == "no_clear_difference"
            else "context"
        )

        reference = reference_for(
            artifact,
            role=role,
            rationale=(
                f"Differential analysis reports {result_status} for {metric}. "
                "This is evidence about the comparison, not proof that "
                "a specific change caused the result."
            ),
        )

        if regression:
            statement = (
                f"The candidate evaluation is associated with a regression "
                f"in {metric}."
            )
        elif improvement:
            statement = (
                f"The candidate evaluation shows an improvement in {metric}; "
                "this result weighs against a regression hypothesis for "
                "this metric."
            )
        elif result_status == "no_clear_difference":
            statement = (
                f"The comparison did not establish a clear difference in "
                f"{metric}."
            )
        else:
            statement = (
                f"The available comparison evidence is insufficient to "
                f"determine a reliable change in {metric}."
            )

        if statistical_support:
            hypothesis_status = "supported"
            strength = (
                "strong"
                if (
                    effect_size is not None
                    and abs(effect_size) >= 0.5
                    and confidence_interval_available
                )
                else "moderate"
            )
        else:
            hypothesis_status = "candidate"
            strength = "weak"

        hypothesis_limitations = list(artifact.limitations)

        if not enough_samples:
            hypothesis_limitations.append(
                "Sample sizes are missing or below the configured minimum of five observations per group."
            )

        if not significant:
            hypothesis_limitations.append(
                "The available evidence does not establish statistical significance under the supplied decision rule."
            )

        if practical is not True:
            hypothesis_limitations.append(
                "Practical significance was not explicitly established."
            )

        if not direction_known:
            hypothesis_limitations.append(
                f"Unrecognized metric direction {direction_value!r}; the "
                "effect direction could not be verified against the "
                "upstream metric direction contract."
            )

        if effect_size is None:
            hypothesis_limitations.append(
                "A finite effect-size estimate was not available."
            )

        if not confidence_interval_available:
            hypothesis_limitations.append(
                "A valid effect-size confidence interval was not available."
            )

        if p_value is not None:
            hypothesis_limitations.append(
                "A p-value does not establish causality and must be interpreted with the study design and multiple-testing policy."
            )

        if not regression:
            hypothesis_limitations.append(
                "This result is not positive evidence that the candidate version caused a regression."
            )

        return self._make_hypothesis(
            artifact=artifact,
            rule_id=f"statistics.differential.{metric}.{result_status}",
            title=f"Differential result for {metric}: {result_status}",
            statement=statement,
            mechanism=(
                "A version, configuration, data, routing, or infrastructure "
                "change may explain the observed difference. The specific "
                "mechanism requires corroborating evidence or validation."
            ),
            references=(reference,),
            status=hypothesis_status,
            strength=strength,
            limitations=tuple(hypothesis_limitations),
        )

    def _relationship_hypothesis(
        self,
        artifact: RCAArtifact,
    ) -> RCAHypothesis | None:
        attributes = artifact.attributes

        relationship_status = str(
            attributes.get("status", "candidate")
        ).strip().lower()

        if relationship_status == "rejected":
            return None

        relationship_type = str(
            attributes.get("relationship_type", "unspecified")
        )

        evidence_ids = attributes.get("evidence_ids", ())
        if not isinstance(evidence_ids, (list, tuple, set)):
            evidence_ids = ()

        reference = reference_for(
            artifact,
            role="context",
            rationale=(
                "The relationship analyzer discovered a relationship "
                "candidate. Its existence does not establish direction "
                "or causality."
            ),
        )

        return self._make_hypothesis(
            artifact=artifact,
            rule_id=f"relationship.{relationship_type}",
            title=f"Investigate {relationship_type} relationship",
            statement=(
                f"The discovered {relationship_type} relationship may "
                "connect evidence relevant to this investigation."
            ),
            mechanism=(
                "Inspect the linked source evidence, temporal ordering, "
                "scope, confounders, and reproducibility before assessing "
                "whether this relationship supports a causal mechanism."
            ),
            references=(reference,),
            status="candidate",
            strength="weak",
            limitations=(
                *artifact.limitations,
                (
                    f"The relationship references {len(evidence_ids)} "
                    "source evidence item(s); the analyzer does not infer "
                    "causality from the relationship alone."
                ),
            ),
        )

    def _event_hypothesis(
        self,
        artifact: RCAArtifact,
    ) -> RCAHypothesis | None:
        attributes = artifact.attributes

        event_type = str(attributes.get("event_type", "")).lower()
        evidence_kind = str(
            attributes.get("evidence_kind", "")
        ).lower()
        source_kind = str(
            attributes.get("source_kind", "")
        ).lower()

        combined_kind = " ".join(
            (event_type, evidence_kind, source_kind)
        )

        is_ci_cd = any(
            token in combined_kind
            for token in (
                "ci_cd",
                "cicd",
                "pipeline_gate",
                "deployment_gate",
                "quality_gate",
            )
        )

        if not is_ci_cd:
            return self._context_hypothesis(artifact)

        decision = str(
            attributes.get(
                "decision",
                attributes.get("status", "unknown"),
            )
        ).strip().lower()

        if decision not in (
            self._FAILED_DECISIONS
            | self._PASSED_DECISIONS
            | {"review"}
        ):
            return self._context_hypothesis(artifact)

        failed = decision in self._FAILED_DECISIONS
        passed = decision in self._PASSED_DECISIONS

        role = (
            "supporting"
            if failed
            else "counter"
            if passed
            else "context"
        )

        reference = reference_for(
            artifact,
            role=role,
            rationale=(
                f"The CI/CD decision is {decision}. It records a policy "
                "outcome and does not independently identify the "
                "technical cause."
            ),
        )

        return self._make_hypothesis(
            artifact=artifact,
            rule_id=f"ci_cd.gate.{decision}",
            title=f"Investigate CI/CD gate decision: {decision}",
            statement=(
                "A configured quality gate failed; the specific failed "
                "criterion must be identified from linked evidence."
                if failed
                else "The CI/CD decision does not indicate a blocking failure."
                if passed
                else "The CI/CD decision requires review."
            ),
            mechanism=(
                "Inspect failed test names, metric comparisons, acceptance "
                "thresholds, policy version, commit, and linked evaluation "
                "artifacts to identify the reason for the gate decision."
            ),
            references=(reference,),
            status="candidate",
            strength="weak",
            limitations=(
                *artifact.limitations,
                "A CI/CD outcome is not an independent causal test.",
            ),
        )

    def _cicd_hypothesis(
        self,
        artifact: RCAArtifact,
        report_limitations: list[str],
    ) -> RCAHypothesis | None:
        """Provider-neutral ci_cd_result: check outcome, never causal proof."""
        attributes = artifact.attributes
        decision = str(
            attributes.get("decision", attributes.get("status", "unknown"))
        ).strip().lower()
        commit = attributes.get("commit_sha") or attributes.get("commit")
        build = (
            attributes.get("build_id")
            or attributes.get("pipeline_id")
            or attributes.get("deployment_id")
        )
        scope_bits = [
            key
            for key in ("system_id", "system_version", "environment")
            if artifact.__dict__.get(key) is not None
            or attributes.get(key) is not None
        ]
        missing_bits = [
            label
            for label, present in (
                ("commit/version metadata", commit is not None or build is not None),
                ("scope metadata", bool(scope_bits)),
            )
            if not present
        ]
        if decision not in (
            self._FAILED_DECISIONS | self._PASSED_DECISIONS | {"review"}
        ):
            report_limitations.append(
                f"CI/CD artifact {artifact.source_ref} has unrecognized "
                f"decision {decision!r}; treated as context only."
            )
            return self._context_hypothesis(artifact)
        failed = decision in self._FAILED_DECISIONS
        passed = decision in self._PASSED_DECISIONS
        role = "supporting" if failed else "counter" if passed else "context"
        failed_tests = (
            attributes.get("failed_tests")
            or attributes.get("failed_test_names")
            or ()
        )
        test_summary = ""
        if isinstance(failed_tests, (list, tuple)) and failed_tests:
            names = ", ".join(str(name) for name in list(failed_tests)[:10])
            test_summary = f" Reported failures include: {names}."
        reference = reference_for(
            artifact,
            role=role,
            rationale=(
                f"The CI/CD decision is {decision}. It records a check "
                "outcome and does not independently identify the cause."
            ),
        )
        limitations: list[str] = [
            *artifact.limitations,
            "A CI/CD outcome is not an independent causal test.",
        ]
        if failed:
            limitations.append(
                "A failed check evidences that check failing, not automatic "
                "proof of a production root cause."
            )
        if passed:
            limitations.append(
                "Passing checks are limited evidence; they do not prove a "
                "deployment is safe."
            )
        if missing_bits:
            limitations.append(
                "CI/CD evidence is missing "
                + " and ".join(missing_bits)
                + "; scope matching is therefore uncertain."
            )
        if (
            attributes.get("rollback_outcome") is not None
            or attributes.get("reproduction_outcome") is not None
        ):
            limitations.append(
                "A referenced rollback/reproduction outcome is not validation "
                "evidence unless a validation artifact satisfying the domain "
                "contract is supplied."
            )
        return self._make_hypothesis(
            artifact=artifact,
            rule_id=f"ci_cd.result.{decision}",
            title=f"Investigate CI/CD result: {decision}",
            statement=(
                "A CI/CD check failed; identify the failed criterion from "
                "linked evidence." + test_summary
                if failed
                else "CI/CD checks passed; weighs only weakly against a cause "
                "in the checked scope."
                if passed
                else "The CI/CD result requires review."
            ),
            mechanism=(
                "Inspect failed test names, metric comparisons, thresholds, "
                "policy version, commit, environment, and linked evaluation "
                "artifacts."
            ),
            references=(reference,),
            status="candidate",
            strength="weak",
            limitations=tuple(limitations),
        )

    def _statistical_hypothesis(
        self,
        artifact: RCAArtifact,
        report_limitations: list[str],
    ) -> RCAHypothesis | None:
        """Raw statistical primitives carry no comparison decision, so context only."""
        report_limitations.append(
            f"Statistical artifact {artifact.source_ref} is a raw primitive "
            "without a comparison decision; treated as context only, not as "
            "support for a change."
        )
        return self._context_hypothesis(artifact)

    def _analysis_result_hypothesis(
        self,
        artifact: RCAArtifact,
        report_limitations: list[str],
    ) -> RCAHypothesis | None:
        """Generic analysis summaries describe observations, not causes."""
        report_limitations.append(
            f"Analysis artifact {artifact.source_ref} is a descriptive "
            "summary; treated as context only."
        )
        return self._context_hypothesis(artifact)

    def _is_context_artifact(self, artifact: RCAArtifact) -> bool:
        return artifact.source_type in self._CONTEXT_TYPES

    def _context_hypothesis(
        self,
        artifact: RCAArtifact,
    ) -> RCAHypothesis | None:
        if artifact.source_type not in self._CONTEXT_TYPES:
            return None

        reference = reference_for(
            artifact,
            role="context",
            rationale=(
                "This artifact records potentially relevant context. "
                "It is not treated as causal support or counter-evidence "
                "without a specific rule linking it to the hypothesis."
            ),
        )

        return self._make_hypothesis(
            artifact=artifact,
            rule_id=f"context.{artifact.source_type}",
            title=f"Context: {artifact.source_type}",
            statement=(
                f"The {artifact.source_type} artifact may help explain "
                "the circumstances surrounding the investigated behavior."
            ),
            mechanism=(
                "Interpret this artifact alongside the relevant run, "
                "version, timestamps, metric definitions, and independent "
                "evidence."
            ),
            references=(reference,),
            status="candidate",
            strength="weak",
            limitations=(
                *artifact.limitations,
                "Context alone does not establish a cause.",
            ),
        )

    def _enrich_with_context(
        self,
        *,
        hypothesis: RCAHypothesis,
        context_artifacts: list[RCAArtifact],
        artifact_by_id: dict[str, RCAArtifact],
    ) -> RCAHypothesis:
        hypothesis_ids = {
            reference.evidence_id for reference in hypothesis.evidence
        }

        anchors = [
            artifact_by_id[evidence_id]
            for evidence_id in hypothesis_ids
            if evidence_id in artifact_by_id
        ]

        anchor_runs = {
            artifact.run_id
            for artifact in anchors
            if artifact.run_id is not None
        }

        anchor_eval_runs = {
            artifact.evaluation_run_id
            for artifact in anchors
            if artifact.evaluation_run_id is not None
        }

        anchor_agents = {
            artifact.agent_id
            for artifact in anchors
            if artifact.agent_id is not None
        }

        anchor_systems = {
            (artifact.system_id, artifact.system_version)
            for artifact in anchors
            if artifact.system_id is not None
        }
        anchor_environments = {
            str(value)
            for artifact in anchors
            for value in (
                artifact.environment,
                artifact.attributes.get("environment"),
            )
            if value is not None
        }

        # Scope-aware enrichment: context must share a run anchor AND must
        # not carry an explicit agent/system conflict with the hypothesis
        # anchors. Time proximity alone never attaches. Missing dimensions
        # are allowed (uncertainty) but disclosed on the attached reference.
        anchor_times = [
            artifact.observed_at
            for artifact in anchors
            if artifact.observed_at is not None
        ]

        if not anchor_runs and not anchor_eval_runs:
            return hypothesis

        candidates: list[tuple[float, RCAArtifact]] = []

        for context in context_artifacts:
            if context.artifact_id in hypothesis_ids:
                continue

            same_run = (
                context.run_id is not None
                and context.run_id in anchor_runs
            )

            same_eval_run = (
                context.evaluation_run_id is not None
                and context.evaluation_run_id in anchor_eval_runs
            )

            if not same_run and not same_eval_run:
                continue

            if (
                context.agent_id is not None
                and anchor_agents
                and context.agent_id not in anchor_agents
            ):
                continue

            if (
                context.system_id is not None
                and anchor_systems
                and (context.system_id, context.system_version)
                not in anchor_systems
                and context.system_id
                not in {system for system, _ in anchor_systems}
            ):
                continue
            context_environment: str | None = context.environment
            if context_environment is None:
                attribute_context_environment = context.attributes.get(
                    "environment"
                )
                if attribute_context_environment is not None:
                    context_environment = str(attribute_context_environment)
            if (
                context_environment is not None
                and anchor_environments
                and context_environment not in anchor_environments
            ):
                continue

            if context.observed_at is not None and anchor_times:
                distance = min(
                    abs(
                        (
                            context.observed_at - anchor_time
                        ).total_seconds()
                    )
                    for anchor_time in anchor_times
                )

                if distance > self.context_window_seconds:
                    continue
            else:
                distance = self.context_window_seconds + 1.0

            candidates.append((distance, context))

        additions: list[RCAEvidenceReference] = []

        for _, context in sorted(
            candidates,
            key=lambda pair: (
                pair[0],
                pair[1].artifact_id,
            ),
        )[:5]:
            rationale = (
                "This artifact shares a compatible run or "
                "evaluation-run scope and is retained as context. "
                "It is not causal proof."
            )
            if context.observed_at is None or not anchor_times:
                rationale += (
                    " Timestamp comparison was unavailable; temporal "
                    "closeness is unverified."
                )
            additions.append(
                reference_for(
                    context,
                    role="context",
                    rationale=rationale,
                )
            )

        if not additions:
            return hypothesis

        references_by_id = {
            reference.evidence_id: reference
            for reference in (*hypothesis.evidence, *additions)
        }

        return hypothesis.model_copy(
            update={
                "evidence": tuple(
                    references_by_id[key]
                    for key in sorted(references_by_id)
                )
            }
        )

    def _merge_hypotheses(
        self,
        hypotheses: list[RCAHypothesis],
        source_keys: dict[str, str] | None = None,
    ) -> tuple[RCAHypothesis, ...]:
        merged: dict[str, RCAHypothesis] = {}
        # When constituents disagree, the merge adopts the status closest to
        # a resolved claim (validated/supported beat contested beat
        # unestablished); _assess_counter_evidence runs again after merging
        # so unioned counter references still demote a supported result.
        status_rank = {
            "validated": 0,
            "supported": 1,
            "inconclusive": 2,
            "candidate": 3,
            "rejected": 4,
        }

        for hypothesis in hypotheses:
            prior = merged.get(hypothesis.hypothesis_id)

            if prior is None:
                merged[hypothesis.hypothesis_id] = hypothesis
                continue

            role_rank = {"supporting": 0, "counter": 1, "context": 2}

            evidence_by_id: dict[str, RCAEvidenceReference] = {}
            role_conflicts: set[str] = set()
            for reference in (*prior.evidence, *hypothesis.evidence):
                existing = evidence_by_id.get(reference.evidence_id)
                if existing is None:
                    evidence_by_id[reference.evidence_id] = reference
                    continue
                if existing.role == reference.role:
                    continue
                role_conflicts.add(reference.evidence_id)
                if role_rank[reference.role] < role_rank[existing.role]:
                    evidence_by_id[reference.evidence_id] = reference

            # Source-level dependence (only within a merge group, i.e. one
            # rule over near-identical evidence sets): references sharing one
            # underlying source are two views of one analysis, not
            # independent corroboration. Keep the strongest role
            # (supporting > counter > context) and disclose the overlap.
            best_by_source: dict[str, RCAEvidenceReference] = {}
            shared_sources: set[str] = set()
            for reference in evidence_by_id.values():
                source_key = self._evidence_source_key(reference)
                prior_best = best_by_source.get(source_key)
                if prior_best is None:
                    best_by_source[source_key] = reference
                    continue
                shared_sources.add(source_key)
                if role_rank[reference.role] < role_rank[prior_best.role]:
                    best_by_source[source_key] = reference

            deduped_evidence = tuple(
                best_by_source[key] for key in sorted(best_by_source)
            )
            extra_limitations: list[str] = []
            if shared_sources:
                extra_limitations.append(
                    "Evidence from the same underlying source "
                    f"({len(shared_sources)} shared source(s)) was grouped "
                    "into a single vote; duplicate representations do not "
                    "count as independent corroboration."
                )

            if role_conflicts:
                extra_limitations.append(
                    "Constituents of the same claim attached conflicting "
                    f"roles to {len(role_conflicts)} shared evidence "
                    "reference(s); the merge kept the strongest role and "
                    "disclosed the conflict."
                )

            if source_keys is not None:
                raw_source_keys = {
                    self._evidence_source_key(reference)
                    for reference in deduped_evidence
                }
                counted_source_keys = {
                    source_keys.get(
                        reference.evidence_id,
                        self._evidence_source_key(reference),
                    )
                    for reference in deduped_evidence
                }
                if len(counted_source_keys) < len(raw_source_keys):
                    extra_limitations.append(
                        "Distinct evidence references in this claim share "
                        "an underlying source or dataset and are counted as "
                        "one independent source; independence beyond the "
                        "recorded lineage was not established."
                    )

            winning = min(
                (prior, hypothesis),
                key=lambda item: status_rank[item.status],
            )
            winning_validation = winning.validation
            merged_status = winning.status
            merged_strength = self._recompute_strength(
                deduped_evidence, merged_status, source_keys
            )

            if prior.statement != hypothesis.statement:
                merged_status = prior.status
                merged_strength = prior.strength
                winning_validation = prior.validation
                extra_limitations.append(
                    "Constituents of the same rule asserted materially "
                    "different claims; the merged claim retains its original "
                    "assessment and does not adopt the stronger status."
                )

            merged_mechanism = prior.mechanism
            if prior.mechanism != hypothesis.mechanism:
                extra_limitations.append(
                    "Constituents of the same claim asserted different "
                    "mechanisms; the merged claim retains the lexicographically "
                    "first mechanism and the conflicting mechanism text is not "
                    "represented."
                )
                merged_mechanism = min(prior.mechanism, hypothesis.mechanism)

            constituent_validations = tuple(
                item.validation
                for item in (prior, hypothesis)
                if item.validation is not None
            )
            validation_conflict = (
                len(constituent_validations) == 2
                and constituent_validations[0].outcome
                != constituent_validations[1].outcome
            )

            if validation_conflict:
                merged_status = "inconclusive"
                merged_strength = "weak"
                winning_validation = None
                outcomes = sorted(
                    {item.outcome for item in constituent_validations}
                )
                extra_limitations.append(
                    "Constituents of the same claim carried conflicting "
                    f"validation outcomes ({', '.join(outcomes)}); the "
                    "merged claim cannot adopt either validation record and "
                    "is retained as inconclusive."
                )
            elif winning_validation is None and constituent_validations:
                unadopted_outcomes = sorted(
                    {item.outcome for item in constituent_validations}
                )
                extra_limitations.append(
                    "A constituent validation record "
                    f"({', '.join(unadopted_outcomes)}) was not adopted by "
                    "the merged claim's assessment; the record is not "
                    "represented on the merged hypothesis."
                )
                if any(
                    item.outcome == "refuted"
                    for item in constituent_validations
                ):
                    merged_status = "inconclusive"
                    merged_strength = "weak"

            merged_roles = {
                reference.role for reference in deduped_evidence
            }
            validation_evidence_ids = (
                {
                    reference.evidence_id
                    for reference in winning_validation.evidence
                }
                if winning_validation is not None
                else set()
            )
            merged_evidence_ids = {
                reference.evidence_id for reference in deduped_evidence
            }

            if merged_status == "validated":
                validation_justified = (
                    winning_validation is not None
                    and winning_validation.outcome == "confirmed"
                    and "supporting" in merged_roles
                    and validation_evidence_ids.issubset(merged_evidence_ids)
                )
                if not validation_justified:
                    merged_status = (
                        "supported" if "supporting" in merged_roles
                        else "candidate"
                    )
                    merged_strength = "weak"
                    winning_validation = None
                    extra_limitations.append(
                        "A merged constituent claimed validated status, but "
                        "the merged evidence does not carry confirmed "
                        "validation traceable to it; the claim was demoted."
                    )

            if merged_status == "supported" and "supporting" not in merged_roles:
                merged_status = "candidate"
                merged_strength = "weak"
                extra_limitations.append(
                    "A merged constituent claimed supported status, but no "
                    "supporting reference survived the merge; the claim was "
                    "demoted to candidate."
                )

            if merged_status == "rejected" and "counter" not in merged_roles:
                merged_status = "candidate"
                merged_strength = "weak"
                extra_limitations.append(
                    "A merged constituent claimed rejected status, but no "
                    "counter reference survived the merge; the claim was "
                    "demoted to candidate."
                )

            if winning_validation is not None:
                expected_status = {
                    "confirmed": "validated",
                    "refuted": "rejected",
                }.get(winning_validation.outcome)
                validation_consistent = (
                    (
                        expected_status is None
                        and merged_status in {"candidate", "inconclusive"}
                    )
                    or merged_status == expected_status
                )
                if not validation_consistent:
                    winning_validation = None
                    extra_limitations.append(
                        "Merged status and validation outcome were "
                        "inconsistent under the domain contract; the "
                        "validation record was detached from the claim."
                    )

            merged[hypothesis.hypothesis_id] = prior.model_copy(
                update={
                    "evidence": deduped_evidence,
                    "status": merged_status,
                    "strength": merged_strength,
                    "validation": winning_validation,
                    "mechanism": merged_mechanism,
                    "limitations": self._clean_texts(
                        (
                            *prior.limitations,
                            *hypothesis.limitations,
                            *extra_limitations,
                        )
                    ),
                    "rule_ids": self._clean_texts(
                        (
                            *prior.rule_ids,
                            *hypothesis.rule_ids,
                        )
                    ),
                }
            )

        return tuple(merged.values())

    @staticmethod
    def _assess_counter_evidence(
        hypothesis: RCAHypothesis,
    ) -> RCAHypothesis:
        """Apply explicit counter-evidence semantics (analyzer-side).

        Status meanings enforced here (validation-gated transitions stay in
        the domain contract, which alone can mint validated/rejected):
        - candidate: default; no supporting association established.
        - supported: supporting association with no disqualifying counter.
        - inconclusive: supporting and counter evidence coexist — the
          hypothesis is contested, not resolved. Supporting associations
          keep their roles; the conclusion no longer counts them.
        Counter-evidence never promotes, never validates, and never
        deletes supporting references. Scope-mismatched counter-evidence
        cannot reach this stage (excluded in _scope_check), and missing
        evidence is never treated as counter-evidence (only explicit
        counter-role references count).
        """
        roles = {reference.role for reference in hypothesis.evidence}
        if hypothesis.status != "supported" or "counter" not in roles:
            return hypothesis
        counter_count = sum(
            1 for reference in hypothesis.evidence if reference.role == "counter"
        )
        supporting_count = sum(
            1
            for reference in hypothesis.evidence
            if reference.role == "supporting"
        )
        return hypothesis.model_copy(
            update={
                "status": "inconclusive",
                "strength": "weak",
                "limitations": RCAAnalyzer._clean_texts(
                    (
                        *hypothesis.limitations,
                        f"Counter-evidence is present ({counter_count} "
                        f"counter reference(s) against {supporting_count} "
                        "supporting reference(s)); the hypothesis is "
                        "contested and is not counted as a supported "
                        "association. Contested hypotheses require "
                        "independent corroboration or validation.",
                    )
                ),
            }
        )

    @staticmethod
    def _evidence_source_key(reference: RCAEvidenceReference) -> str:
        if reference.source_event_id is not None:
            return f"event:{reference.source_event_id}"
        if reference.source_observation_id is not None:
            return f"observation:{reference.source_observation_id}"
        if reference.differential_analysis_id is not None:
            return f"differential_analysis:{reference.differential_analysis_id}"
        if reference.relationship_id is not None:
            return f"relationship:{reference.relationship_id}"
        return f"{reference.source_type}:{reference.source_ref}"

    @staticmethod
    def _source_component_keys(
        hypotheses: Iterable[RCAHypothesis],
        artifact_by_id: dict[str, RCAArtifact] | None = None,
    ) -> dict[str, str]:
        keys: dict[str, str] = {}
        for hypothesis in hypotheses:
            for reference in hypothesis.evidence:
                if reference.evidence_id not in keys:
                    artifact = (
                        artifact_by_id.get(reference.evidence_id)
                        if artifact_by_id is not None
                        else None
                    )
                    keys[reference.evidence_id] = RCAAnalyzer._component_key(
                        reference, artifact
                    )
        return keys

    @staticmethod
    def _component_key(
        reference: RCAEvidenceReference,
        artifact: RCAArtifact | None,
    ) -> str:
        if (
            artifact is not None
            and reference.source_event_id is None
            and reference.source_observation_id is None
            and reference.source_type == "differential_analysis"
        ):
            attributes = artifact.attributes
            baseline = attributes.get("baseline_evaluation_run_id")
            candidate = attributes.get("candidate_evaluation_run_id")
            if baseline is not None and candidate is not None:
                return f"differential_dataset:{baseline}:{candidate}"
        return RCAAnalyzer._evidence_source_key(reference)

    @staticmethod
    def _recompute_strength(
        references: tuple[RCAEvidenceReference, ...],
        merged_status: str,
        source_keys: dict[str, str] | None = None,
    ) -> str:
        if merged_status == "candidate":
            return "weak"
        supporting = [r for r in references if r.role == "supporting"]
        if not supporting:
            return "weak"
        distinct: set[str] = set()
        for reference in supporting:
            if source_keys is not None and reference.evidence_id in source_keys:
                distinct.add(source_keys[reference.evidence_id])
            else:
                distinct.add(RCAAnalyzer._evidence_source_key(reference))
        if len(distinct) >= 2:
            return "strong"
        return "moderate"

    @staticmethod
    def _sample_size(value: object) -> int | None:
        if isinstance(value, bool):
            return None

        if isinstance(value, int) and value >= 0:
            return value

        return None

    @staticmethod
    def _finite_number(value: object) -> float | None:
        if isinstance(value, bool):
            return None

        if not isinstance(value, (int, float)):
            return None

        number = float(value)

        if not isfinite(number):
            return None

        return number

    @staticmethod
    def _rank(hypothesis: RCAHypothesis) -> int:
        return {
            "validated": 0,
            "supported": 1,
            "candidate": 2,
            "inconclusive": 3,
            "rejected": 4,
        }[hypothesis.status]

    @staticmethod
    def _claim_scope_key(artifact: RCAArtifact) -> str:
        attributes = artifact.attributes

        def attribute(name: str) -> str | None:
            value = attributes.get(name)
            if value is None:
                return None
            return str(value)

        dimensions = [
            artifact.domain,
            artifact.subject_type,
            artifact.subject_id,
            artifact.agent_id,
            artifact.system_id,
            artifact.system_version,
            artifact.run_id,
            artifact.evaluation_run_id,
            artifact.pipeline_run_id or attribute("pipeline_run_id"),
            artifact.environment or attribute("environment"),
            attribute("commit_sha") or attribute("commit"),
            attribute("dataset_version"),
            attribute("baseline_evaluation_run_id"),
            attribute("candidate_evaluation_run_id"),
            attribute("window_start"),
            attribute("window_end"),
            attribute("relationship_id"),
            attribute("relationship_key"),
        ]

        return json.dumps(dimensions, ensure_ascii=False, default=str)

    @staticmethod
    def _make_hypothesis(
        *,
        artifact: RCAArtifact,
        rule_id: str,
        title: str,
        statement: str,
        mechanism: str,
        references: tuple[RCAEvidenceReference, ...],
        status: str,
        strength: str,
        limitations: tuple[str, ...],
    ) -> RCAHypothesis:
        unique_references = {
            reference.evidence_id: reference
            for reference in references
        }

        ordered_references = tuple(
            unique_references[key]
            for key in sorted(unique_references)
        )

        hypothesis_key = sha256(
            "\x1f".join(
                (
                    title,
                    statement,
                    RCAAnalyzer._claim_scope_key(artifact),
                )
            ).encode("utf-8")
        ).hexdigest()[:20]

        return RCAHypothesis(
            hypothesis_id=f"hyp_{hypothesis_key}",
            title=title,
            statement=statement,
            mechanism=mechanism,
            status=status,
            strength=strength,
            evidence=ordered_references,
            limitations=RCAAnalyzer._clean_texts(limitations),
            rule_ids=(rule_id,),
        )

    @staticmethod
    def _conclusion(
        hypotheses: tuple[RCAHypothesis, ...],
        supporting_components: dict[str, frozenset[str]],
    ) -> tuple[ConclusionType, ConclusionLevel, str]:
        supported = [
            hypothesis
            for hypothesis in hypotheses
            if hypothesis.status in {"supported", "validated"}
        ]

        independent_support: set[str] = set()
        for hypothesis in supported:
            independent_support.update(
                supporting_components.get(hypothesis.hypothesis_id, frozenset())
            )

        validated = [
            hypothesis
            for hypothesis in supported
            if hypothesis.status == "validated"
            and hypothesis.validation is not None
            and hypothesis.validation.outcome == "confirmed"
            and hypothesis.validation.method
            in {"controlled_experiment", "rollback_test"}
        ]

        if validated:
            if len(validated) >= 2:
                return (
                    "multiple_contributing_factors",
                    "validated_causal",
                    (
                        f"{len(hypotheses)} hypotheses were assessed; "
                        f"{len(validated)} have confirmed causal validation "
                        "under a controlled experiment or rollback test."
                    ),
                )

            return (
                "probable_cause",
                "validated_causal",
                (
                    f"{len(hypotheses)} hypotheses were assessed. "
                    "A causal hypothesis has explicit validation evidence "
                    "from a controlled experiment or rollback test."
                ),
            )

        if len(supported) >= 2 and len(independent_support) >= 2:
            return (
                "multiple_contributing_factors",
                "associational",
                (
                    f"{len(hypotheses)} hypotheses were assessed; "
                    f"{len(independent_support)} independent supporting "
                    "source(s) have supporting evidence for associations. "
                    "Causality has not been established."
                ),
            )

        if len(independent_support) >= 1:
            return (
                "probable_cause",
                "associational",
                (
                    f"{len(hypotheses)} hypotheses were assessed. "
                    "One independent supporting source has supporting "
                    "evidence for an association. It is not a validated "
                    "causal conclusion."
                ),
            )

        if hypotheses:
            return (
                "no_supported_cause",
                "descriptive",
                (
                    f"{len(hypotheses)} candidate hypothesis/hypotheses "
                    "were retained, but none met the current rules for "
                    "a supported association."
                ),
            )

        return (
            "insufficient_evidence",
            "descriptive",
            "No eligible hypothesis could be generated from the supplied evidence.",
        )
