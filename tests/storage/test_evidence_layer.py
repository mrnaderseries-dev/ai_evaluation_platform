from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from ai_eval_platform.storage.models import (
    Analysis,
    PersistedEvidenceRecord,
    PersistedRelationship,
    RelationshipEvidence,
)
from ai_eval_platform.storage.repositories import (
    EntityNotFoundError,
    EvidenceRepository,
    RepositoryError,
    RelationshipRepository,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _evidence(evidence_id: str, **overrides) -> PersistedEvidenceRecord:
    values = {
        "evidence_id": evidence_id,
        "source": "signal",
        "source_ref": "sig-1",
        "evidence_kind": "observation",
        "domain": "monitoring",
        "subject_type": "run",
        "subject_id": "run-1",
        "agent_id": "agent-1",
        "observed_at": _utcnow(),
    }
    values.update(overrides)
    return PersistedEvidenceRecord(**values)


def _analysis(session, analysis_id: str = "an-1") -> str:
    session.add(Analysis(analysis_id=analysis_id, analysis_type="test"))
    session.flush()
    return analysis_id


def test_evidence_insert_is_idempotent(session):
    repo = EvidenceRepository(session)

    assert repo.insert_ignore_duplicates([_evidence("ev-1")]) == 1
    assert repo.insert_ignore_duplicates([_evidence("ev-1")]) == 0
    assert repo.get("ev-1") is not None


def test_evidence_subject_time_query_orders_by_observed_at(session):
    repo = EvidenceRepository(session)
    first = _utcnow()
    repo.insert_ignore_duplicates(
        [
            _evidence("ev-b", observed_at=first, subject_id="run-9"),
            _evidence("ev-a", observed_at=first, subject_id="run-9"),
        ]
    )

    rows = repo.list_for_subject("run", "run-9")

    assert [row.evidence_id for row in rows] == ["ev-a", "ev-b"]


def _relationship(
    relationship_id: str, key: str, **overrides
) -> PersistedRelationship:
    values = {
        "relationship_id": relationship_id,
        "analysis_id": "an-1",
        "relationship_key": key,
        "relationship_type": "temporal",
        "basis": "temporal",
        "status": "candidate",
        "algorithm_name": "relationship_analyzer",
        "algorithm_version": "1.0.0",
    }
    values.update(overrides)
    return PersistedRelationship(**values)


def _relationship_evidence(
    relationship_id: str,
    evidence_id: str,
    *,
    role: str = "supporting",
    order: int = 0,
    link_id: str | None = None,
) -> RelationshipEvidence:
    return RelationshipEvidence(
        relationship_evidence_id=link_id or f"link-{relationship_id}-{evidence_id}-{role}",
        relationship_id=relationship_id,
        evidence_id=evidence_id,
        role=role,
        evidence_order=order,
    )


def test_relationship_replay_reuses_analysis_key(session):
    _analysis(session)
    repo = RelationshipRepository(session)
    EvidenceRepository(session).insert_ignore_duplicates([_evidence("ev-1")])
    link = RelationshipEvidence(
        relationship_evidence_id="link-1",
        relationship_id="rel-1",
        evidence_id="ev-1",
        role="supporting",
    )

    persisted, inserted = repo.insert_with_evidence_ignore_duplicates(
        _relationship("rel-1", "temporal:a:b"), [link]
    )

    assert (persisted.relationship_id, inserted) == ("rel-1", True)

    persisted_again, inserted_again = repo.insert_with_evidence_ignore_duplicates(
        _relationship("rel-2", "temporal:a:b"), []
    )

    assert (persisted_again.relationship_id, inserted_again) == ("rel-1", False)


def test_validation_is_explicit_action(session):
    _analysis(session)
    repo = RelationshipRepository(session)
    repo.insert_with_evidence_ignore_duplicates(
        _relationship("rel-v", "k-1"), []
    )

    validated = repo.set_validation_status(
        "rel-v", status="validated", validated_by="reviewer"
    )

    assert validated.status == "validated"
    assert validated.validated_by == "reviewer"
    assert validated.validated_at is not None

    with pytest.raises(ValueError):
        repo.set_validation_status(
            "rel-v", status="candidate", validated_by="reviewer"
        )

    with pytest.raises(EntityNotFoundError):
        repo.set_validation_status(
            "missing", status="validated", validated_by="reviewer"
        )


@pytest.mark.parametrize("status", ["validated", "rejected"])
def test_reviewed_status_cannot_be_requested_on_insert(session, status):
    _analysis(session)
    repo = RelationshipRepository(session)

    with pytest.raises(RepositoryError, match="must be inserted as candidates"):
        repo.insert_with_evidence_ignore_duplicates(
            _relationship("rel-direct", "key-direct", status=status),
            [],
        )

    assert repo.get("rel-direct") is None


def test_validated_relationship_replay_with_identical_links_is_idempotent(session):
    _analysis(session)
    evidence_repo = EvidenceRepository(session)
    evidence_repo.insert_ignore_duplicates(
        [_evidence("ev-a"), _evidence("ev-b")]
    )
    repo = RelationshipRepository(session)
    links = [
        _relationship_evidence("rel-a", "ev-a", order=0),
        _relationship_evidence("rel-a", "ev-b", order=1),
    ]
    repo.insert_with_evidence_ignore_duplicates(
        _relationship("rel-a", "reviewed-key"), links
    )
    repo.set_validation_status(
        "rel-a", status="validated", validated_by="reviewer"
    )

    persisted, inserted = repo.insert_with_evidence_ignore_duplicates(
        _relationship("rel-replay", "reviewed-key"),
        [
            _relationship_evidence("rel-replay", "ev-a", order=0),
            _relationship_evidence("rel-replay", "ev-b", order=1),
        ],
    )

    assert not inserted
    assert persisted.relationship_id == "rel-a"
    assert persisted.status == "validated"
    assert {
        (link.evidence_id, link.role, link.evidence_order)
        for link in repo.list_evidence_links("rel-a")
    } == {("ev-a", "supporting", 0), ("ev-b", "supporting", 1)}


@pytest.mark.parametrize(
    "replayed_evidence_ids",
    [(), ("ev-a", "ev-b")],
    ids=["remove-evidence", "add-evidence"],
)
def test_validated_relationship_rejects_evidence_set_changes(
    session,
    replayed_evidence_ids,
):
    _analysis(session)
    EvidenceRepository(session).insert_ignore_duplicates(
        [_evidence("ev-a"), _evidence("ev-b")]
    )
    repo = RelationshipRepository(session)
    repo.insert_with_evidence_ignore_duplicates(
        _relationship("rel-v", "validated-key"),
        [_relationship_evidence("rel-v", "ev-a")],
    )
    repo.set_validation_status(
        "rel-v", status="validated", validated_by="reviewer"
    )

    replay_links = [
        _relationship_evidence("rel-replay", evidence_id, order=index)
        for index, evidence_id in enumerate(replayed_evidence_ids)
    ]
    with pytest.raises(RepositoryError, match="Cannot change a validated"):
        repo.insert_with_evidence_ignore_duplicates(
            _relationship("rel-replay", "validated-key"), replay_links
        )

    assert repo.get("rel-v").status == "validated"
    assert [link.evidence_id for link in repo.list_evidence_links("rel-v")] == [
        "ev-a"
    ]


def test_rejected_relationship_rejects_changed_evidence_on_replay(session):
    _analysis(session)
    EvidenceRepository(session).insert_ignore_duplicates(
        [_evidence("ev-a"), _evidence("ev-b")]
    )
    repo = RelationshipRepository(session)
    repo.insert_with_evidence_ignore_duplicates(
        _relationship("rel-r", "rejected-key"),
        [_relationship_evidence("rel-r", "ev-a")],
    )
    repo.set_validation_status(
        "rel-r", status="rejected", validated_by="reviewer"
    )

    with pytest.raises(RepositoryError, match="Cannot change a validated"):
        repo.insert_with_evidence_ignore_duplicates(
            _relationship("rel-replay", "rejected-key"),
            [_relationship_evidence("rel-replay", "ev-b")],
        )

    assert repo.get("rel-r").status == "rejected"
    assert [link.evidence_id for link in repo.list_evidence_links("rel-r")] == [
        "ev-a"
    ]


def test_reviewed_relationship_rejects_changed_content_on_replay(session):
    _analysis(session)
    repo = RelationshipRepository(session)
    repo.insert_with_evidence_ignore_duplicates(
        _relationship(
            "rel-content",
            "content-key",
            attributes={"threshold": 1},
        ),
        [],
    )
    repo.set_validation_status(
        "rel-content", status="validated", validated_by="reviewer"
    )

    with pytest.raises(RepositoryError, match="Cannot change a validated"):
        repo.insert_with_evidence_ignore_duplicates(
            _relationship(
                "rel-replay",
                "content-key",
                attributes={"threshold": 2},
            ),
            [],
        )

    assert repo.get("rel-content").attributes == {"threshold": 1}
    assert repo.get("rel-content").status == "validated"


def test_relationship_and_evidence_links_follow_caller_rollback(session):
    _analysis(session)
    EvidenceRepository(session).insert_ignore_duplicates([_evidence("ev-rb")])
    repo = RelationshipRepository(session)
    repo.insert_with_evidence_ignore_duplicates(
        _relationship("rel-rb", "rollback-key"),
        [_relationship_evidence("rel-rb", "ev-rb")],
    )

    session.rollback()

    assert repo.get("rel-rb") is None


def test_counter_evidence_coexists_with_supporting(session):
    _analysis(session)
    EvidenceRepository(session).insert_ignore_duplicates([_evidence("ev-c")])
    repo = RelationshipRepository(session)
    repo.insert_with_evidence_ignore_duplicates(
        _relationship("rel-c", "k-c"),
        [
            RelationshipEvidence(
                relationship_evidence_id="link-s",
                relationship_id="rel-c",
                evidence_id="ev-c",
                role="supporting",
            ),
            RelationshipEvidence(
                relationship_evidence_id="link-c",
                relationship_id="rel-c",
                evidence_id="ev-c",
                role="counter",
            ),
        ],
    )

    links = repo.list_evidence_links("rel-c")

    assert {link.role for link in links} == {"supporting", "counter"}


def test_cited_evidence_is_restrict_protected():
    table = RelationshipEvidence.__table__
    ondelete = None

    for foreign_key in table.c["evidence_id"].foreign_keys:
        if foreign_key.target_fullname == "evidence_records.evidence_id":
            ondelete = foreign_key.ondelete

    assert ondelete == "RESTRICT"


def test_relationship_status_check_rejects_unknown(session):
    _analysis(session, "an-ck")
    session.add(
        PersistedRelationship(
            relationship_id="rel-ck",
            analysis_id="an-ck",
            relationship_key="k-ck",
            relationship_type="temporal",
            basis="temporal",
            status="bogus",
            algorithm_name="relationship_analyzer",
            algorithm_version="1.0.0",
        )
    )

    with pytest.raises(IntegrityError):
        session.flush()
