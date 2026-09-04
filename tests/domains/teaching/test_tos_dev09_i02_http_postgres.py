"""TOS-DEV09-I02 dedicated remediation HTTP + PostgreSQL acceptance."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import text

from aieos.domains.assessment.application.errors import AssessmentCapabilityForbidden
from aieos.domains.education.schema import QUIZ_CONTENT_TYPE
from aieos.domains.teaching.application.errors import (
    TeachingWorkCapabilityForbidden,
)
from aieos.domains.teaching.domain.class_result_level_snapshot import (
    ClassResultLevelSnapshot,
)
from aieos.domains.teaching.domain.identities import WorkId
from aieos.domains.teaching.domain.intent_type import IntentType
from aieos.domains.teaching.domain.remediation_origin import (
    create_remediation_teaching_work_with_origin,
)
from aieos.domains.teaching.infrastructure.persistence.uow import (
    SqlAlchemyTeachingUnitOfWorkFactory,
)
from tests.domains.assessment.helpers_dev08_i02 import (
    RECORD_PATH,
    MutableSchoolContextClassReader,
    build_assessment_client,
    headers,
    seed_published_learner_content,
)
from tests.dbutil import clear_asset_audit_rows_for_schema_downgrade

pytestmark = pytest.mark.tos_dev09_i02

REMEDIATION_PATH = "/api/v1/teaching/works/from-classroom-assessment"
FIXED_TARGET = "2026-09-08"


@pytest.fixture(autouse=True)
def _purge_remediation_state_after_test(bootstrap_engine):
    yield
    clear_asset_audit_rows_for_schema_downgrade(bootstrap_engine)


def _record(
    runtime_engine,
    bootstrap_engine,
    *,
    tenant_id,
    principal_id,
    class_result_level="MIXED",
    class_result_note=None,
    client=None,
):
    content_id, version_id = seed_published_learner_content(
        bootstrap_engine,
        tenant_id=tenant_id,
        owner_id=principal_id,
        content_type=QUIZ_CONTENT_TYPE,
    )
    client = client or build_assessment_client(
        runtime_engine, tenant_id, principal_id
    )
    body = {
        "class_ref": "class-5a",
        "content_id": str(content_id),
        "content_version_id": str(version_id),
        "class_result_level": class_result_level,
    }
    if class_result_note is not None:
        body["class_result_note"] = class_result_note
    response = client.post(
        RECORD_PATH,
        headers=headers(tenant_id, idempotency_key=f"record-{uuid.uuid7()}"),
        json=body,
    )
    assert response.status_code == 201, response.text
    return client, response.json(), content_id, version_id


def _payload(assessment, **changes):
    body = {
        "assessment_id": assessment["assessment_id"],
        "expected_assessment_aggregate_revision": assessment["aggregate_revision"],
        "goal_text": "Re-teach fractions with concrete examples",
        "target_date": FIXED_TARGET,
        "locale": "en-IN",
        "subject": "Mathematics",
        "topic": "Fractions",
    }
    body.update(changes)
    return body


def _post(client, tenant_id, assessment, *, key=None, payload=None):
    return client.post(
        REMEDIATION_PATH,
        headers=headers(
            tenant_id, idempotency_key=key or f"remediate-{uuid.uuid7()}"
        ),
        json=payload or _payload(assessment),
    )


def test_success_persists_exact_origin_owner_audit_and_contract(
    runtime_engine, bootstrap_engine
) -> None:
    tenant_id, teacher_id = uuid.uuid7(), uuid.uuid7()
    client, assessment, content_id, version_id = _record(
        runtime_engine,
        bootstrap_engine,
        tenant_id=tenant_id,
        principal_id=teacher_id,
        class_result_level="NOT_YET_DEMONSTRATED",
    )
    response = _post(client, tenant_id, assessment, key="success-contract")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["intent_type"] == "remediate_class"
    assert body["class_label"] == "Grade 5A"
    assert body["aggregate_revision"] == 0
    assert response.headers["etag"] == '"r0"'
    assert response.headers["location"] == f"/api/v1/teaching/works/{body['work_id']}"

    factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
    with factory(tenant_id) as uow:
        work = uow.works.get(WorkId(uuid.UUID(body["work_id"])))
        origin = uow.remediation_origins.get(WorkId(uuid.UUID(body["work_id"])))
    assert work is not None and work.teacher_principal_id == teacher_id
    assert work.intent_type is IntentType.REMEDIATE_CLASS
    assert origin is not None
    assert origin.source_assessment_id == uuid.UUID(assessment["assessment_id"])
    assert origin.source_assessment_aggregate_revision == assessment["aggregate_revision"]
    assert origin.source_class_result_level_snapshot.value == "NOT_YET_DEMONSTRATED"
    assert origin.source_class_ref == "class-5a"
    assert origin.source_content_id == content_id
    assert origin.source_content_version_id == version_id
    assert origin.initiating_teacher_principal_id == teacher_id
    with bootstrap_engine.connect() as conn:
        assert conn.execute(
            text(
                """
                SELECT count(*) FROM security.audit_records
                WHERE tenant_id = :tenant_id
                  AND action = 'teaching.work.remediation.create'
                  AND primary_resource_id = :work_id
                  AND primary_resource_revision = 0
                  AND resource_revision_before IS NULL
                  AND resource_revision_after = 0
                """
            ),
            {"tenant_id": tenant_id, "work_id": uuid.UUID(body["work_id"])},
        ).scalar_one() == 1


def test_stale_revision_and_voided_assessment_are_rejected(
    runtime_engine, bootstrap_engine
) -> None:
    tenant_id, teacher_id = uuid.uuid7(), uuid.uuid7()
    client, assessment, _content_id, _version_id = _record(
        runtime_engine,
        bootstrap_engine,
        tenant_id=tenant_id,
        principal_id=teacher_id,
    )
    stale = _post(
        client,
        tenant_id,
        assessment,
        payload=_payload(
            assessment,
            expected_assessment_aggregate_revision=assessment["aggregate_revision"] + 1,
        ),
    )
    assert stale.status_code == 412, stale.text

    voided = client.post(
        f"{RECORD_PATH}/{assessment['assessment_id']}/actions/void",
        headers=headers(
            tenant_id, idempotency_key="void-source", if_match='"r0"'
        ),
    )
    assert voided.status_code == 200, voided.text
    rejected = _post(
        client,
        tenant_id,
        voided.json(),
        payload=_payload(
            assessment,
            expected_assessment_aggregate_revision=voided.json()["aggregate_revision"],
        ),
    )
    assert rejected.status_code == 409, rejected.text


def test_foreign_teacher_assessment_is_concealed(
    runtime_engine, bootstrap_engine
) -> None:
    tenant_id, teacher_a, teacher_b = uuid.uuid7(), uuid.uuid7(), uuid.uuid7()
    _client_a, assessment, _content_id, _version_id = _record(
        runtime_engine,
        bootstrap_engine,
        tenant_id=tenant_id,
        principal_id=teacher_a,
    )
    client_b = build_assessment_client(runtime_engine, tenant_id, teacher_b)
    response = _post(client_b, tenant_id, assessment)
    assert response.status_code == 404, response.text
    assert response.json()["code"] == "classroom_assessment_not_found"


def test_current_class_ref_rechecked_on_create_and_replay(
    runtime_engine, bootstrap_engine
) -> None:
    tenant_id, teacher_id = uuid.uuid7(), uuid.uuid7()
    reader = MutableSchoolContextClassReader(
        tenant_id=tenant_id, teacher_principal_id=teacher_id
    )
    client = build_assessment_client(
        runtime_engine, tenant_id, teacher_id, school_context_reader=reader
    )
    client, assessment, _content_id, _version_id = _record(
        runtime_engine,
        bootstrap_engine,
        tenant_id=tenant_id,
        principal_id=teacher_id,
        client=client,
    )
    reader.class_refs.remove("class-5a")
    denied = _post(client, tenant_id, assessment, key="revoked-before")
    assert denied.status_code == 403, denied.text

    reader.class_refs.append("class-5a")
    created = _post(client, tenant_id, assessment, key="replay-revocation")
    assert created.status_code == 201, created.text
    reader.class_refs.remove("class-5a")
    replay = _post(client, tenant_id, assessment, key="replay-revocation")
    assert replay.status_code == 403, replay.text


def test_school_context_unavailable_and_missing_composition_return_503(
    runtime_engine, bootstrap_engine
) -> None:
    tenant_id, teacher_id = uuid.uuid7(), uuid.uuid7()
    _client, assessment, _content_id, _version_id = _record(
        runtime_engine,
        bootstrap_engine,
        tenant_id=tenant_id,
        principal_id=teacher_id,
    )
    reader = MutableSchoolContextClassReader(
        tenant_id=tenant_id, teacher_principal_id=teacher_id
    )
    reader.raise_unavailable = True
    unavailable_client = build_assessment_client(
        runtime_engine, tenant_id, teacher_id, school_context_reader=reader
    )
    assert _post(unavailable_client, tenant_id, assessment).status_code == 503
    missing_client = build_assessment_client(
        runtime_engine, tenant_id, teacher_id, with_school_context=False
    )
    assert _post(missing_client, tenant_id, assessment).status_code == 503


def test_request_rejects_note_and_origin_never_copies_assessment_note(
    runtime_engine, bootstrap_engine
) -> None:
    tenant_id, teacher_id = uuid.uuid7(), uuid.uuid7()
    secret_note = f"private-note-{uuid.uuid7()}"
    client, assessment, _content_id, _version_id = _record(
        runtime_engine,
        bootstrap_engine,
        tenant_id=tenant_id,
        principal_id=teacher_id,
        class_result_note=secret_note,
    )
    invalid = _post(
        client,
        tenant_id,
        assessment,
        payload=_payload(assessment, class_result_note="client supplied"),
    )
    assert invalid.status_code == 422, invalid.text
    created = _post(client, tenant_id, assessment, key="note-not-copied")
    assert created.status_code == 201, created.text
    work_id = WorkId(uuid.UUID(created.json()["work_id"]))
    with SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)(tenant_id) as uow:
        origin = uow.remediation_origins.get(work_id)
    assert origin is not None
    assert not hasattr(origin, "class_result_note")
    with bootstrap_engine.connect() as conn:
        raw = conn.execute(
            text(
                """
                SELECT row_to_json(o)::text
                FROM teaching.work_remediation_origins o
                WHERE work_id = :work_id
                """
            ),
            {"work_id": work_id.value},
        ).scalar_one()
        audit_raw = conn.execute(
            text(
                """
                SELECT row_to_json(a)::text
                FROM security.audit_records a
                WHERE primary_resource_id = :work_id
                  AND action = 'teaching.work.remediation.create'
                """
            ),
            {"work_id": work_id.value},
        ).scalar_one()
    assert secret_note not in raw
    assert secret_note not in audit_raw


def test_generic_work_create_cannot_create_remediation(
    runtime_engine, bootstrap_engine
) -> None:
    tenant_id, teacher_id = uuid.uuid7(), uuid.uuid7()
    client = build_assessment_client(runtime_engine, tenant_id, teacher_id)
    response = client.post(
        "/api/v1/teaching/works",
        headers=headers(tenant_id, idempotency_key="generic-remediation"),
        json={
            "intent_type": "remediate_class",
            "goal_text": "bypass dedicated path",
            "target_date": FIXED_TARGET,
            "locale": "en-IN",
        },
    )
    assert response.status_code == 422, response.text


def test_dedicated_idempotency_replays_once_and_conflicts_on_payload_change(
    runtime_engine, bootstrap_engine
) -> None:
    tenant_id, teacher_id = uuid.uuid7(), uuid.uuid7()
    client, assessment, _content_id, _version_id = _record(
        runtime_engine,
        bootstrap_engine,
        tenant_id=tenant_id,
        principal_id=teacher_id,
    )
    first = _post(client, tenant_id, assessment, key="dedicated-idempotency")
    replay = _post(client, tenant_id, assessment, key="dedicated-idempotency")
    assert first.status_code == replay.status_code == 201
    assert first.json()["work_id"] == replay.json()["work_id"]
    changed = _post(
        client,
        tenant_id,
        assessment,
        key="dedicated-idempotency",
        payload=_payload(assessment, goal_text="different remediation"),
    )
    assert changed.status_code == 409, changed.text
    with bootstrap_engine.connect() as conn:
        assert conn.execute(
            text(
                """
                SELECT count(*) FROM security.audit_records
                WHERE primary_resource_id = :work_id
                  AND action = 'teaching.work.remediation.create'
                """
            ),
            {"work_id": uuid.UUID(first.json()["work_id"])},
        ).scalar_one() == 1


class _CapabilityAuthorization:
    def __init__(self, denied: str) -> None:
        self.denied = denied

    def authorize(self, *, tenant_id, principal_id, capability) -> None:
        if capability != self.denied:
            return
        if capability == "teaching.work.create":
            raise TeachingWorkCapabilityForbidden("denied")
        raise AssessmentCapabilityForbidden("denied")


@pytest.mark.parametrize(
    ("capability", "expected_code"),
    (
        ("teaching.work.create", "teaching_work_capability_forbidden"),
        ("assessment.classroom.read", "assessment_capability_forbidden"),
    ),
)
def test_capability_denial_applies_to_create_and_replay(
    runtime_engine, bootstrap_engine, capability, expected_code
) -> None:
    tenant_id, teacher_id = uuid.uuid7(), uuid.uuid7()
    allow_client, assessment, _content_id, _version_id = _record(
        runtime_engine,
        bootstrap_engine,
        tenant_id=tenant_id,
        principal_id=teacher_id,
    )
    created = _post(allow_client, tenant_id, assessment, key=f"cap-{capability}")
    assert created.status_code == 201
    deny_client = build_assessment_client(
        runtime_engine,
        tenant_id,
        teacher_id,
        teaching_authorization=_CapabilityAuthorization(capability),
    )
    new_key = _post(deny_client, tenant_id, assessment, key=f"new-{capability}")
    replay = _post(deny_client, tenant_id, assessment, key=f"cap-{capability}")
    assert new_key.status_code == replay.status_code == 403
    assert new_key.json()["code"] == replay.json()["code"] == expected_code


def test_cross_tenant_rls_hides_work_and_origin(runtime_engine, bootstrap_engine) -> None:
    tenant_a, tenant_b, teacher_id = uuid.uuid7(), uuid.uuid7(), uuid.uuid7()
    client, assessment, _content_id, _version_id = _record(
        runtime_engine,
        bootstrap_engine,
        tenant_id=tenant_a,
        principal_id=teacher_id,
    )
    created = _post(client, tenant_a, assessment)
    assert created.status_code == 201
    work_id = WorkId(uuid.UUID(created.json()["work_id"]))
    factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
    with factory(tenant_b) as uow:
        assert uow.works.get(work_id) is None
        assert uow.remediation_origins.get(work_id) is None


def test_intent_immutable_and_orphan_pair_still_enforced(
    runtime_engine, bootstrap_engine
) -> None:
    tenant_id, teacher_id = uuid.uuid7(), uuid.uuid7()
    work, origin = create_remediation_teaching_work_with_origin(
        tenant_id=tenant_id,
        teacher_principal_id=teacher_id,
        goal_text="Remediate fractions",
        target_date=date(2026, 9, 8),
        locale="en-IN",
        created_at=datetime(2026, 9, 4, tzinfo=UTC),
        source_assessment_id=uuid.uuid7(),
        source_assessment_aggregate_revision=0,
        source_class_result_level_snapshot=ClassResultLevelSnapshot.MIXED,
        source_class_ref="class-5a",
        source_content_id=uuid.uuid7(),
        source_content_version_id=uuid.uuid7(),
    )
    factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
    with pytest.raises(Exception):
        with factory(tenant_id) as uow:
            uow.works.insert(work)
            uow.commit()
    with factory(tenant_id) as uow:
        uow.works.insert(work)
        uow.remediation_origins.insert(origin)
        uow.commit()
    with bootstrap_engine.connect() as conn:
        transaction = conn.begin()
        try:
            conn.execute(
                text("SELECT set_config('aieos.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )
            with pytest.raises(Exception) as exc:
                conn.execute(
                    text(
                        """
                        UPDATE teaching.works SET intent_type = 'prepare_tomorrow'
                        WHERE work_id = :work_id
                        """
                    ),
                    {"work_id": work.work_id.value},
                )
            assert "intent_type is immutable" in str(exc.value)
        finally:
            transaction.rollback()


def test_correct_after_create_does_not_mutate_origin(
    runtime_engine, bootstrap_engine
) -> None:
    tenant_id, teacher_id = uuid.uuid7(), uuid.uuid7()
    client, assessment, _content_id, _version_id = _record(
        runtime_engine,
        bootstrap_engine,
        tenant_id=tenant_id,
        principal_id=teacher_id,
        class_result_level="NOT_YET_DEMONSTRATED",
    )
    created = _post(client, tenant_id, assessment)
    assert created.status_code == 201
    corrected = client.post(
        f"{RECORD_PATH}/{assessment['assessment_id']}/actions/correct",
        headers=headers(
            tenant_id, idempotency_key="correct-after-create", if_match='"r0"'
        ),
        json={"class_result_level": "DEMONSTRATED", "class_result_note": "changed"},
    )
    assert corrected.status_code == 200, corrected.text
    with SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)(tenant_id) as uow:
        origin = uow.remediation_origins.get(
            WorkId(uuid.UUID(created.json()["work_id"]))
        )
    assert origin is not None
    assert origin.source_assessment_aggregate_revision == 0
    assert (
        origin.source_class_result_level_snapshot
        is ClassResultLevelSnapshot.NOT_YET_DEMONSTRATED
    )
