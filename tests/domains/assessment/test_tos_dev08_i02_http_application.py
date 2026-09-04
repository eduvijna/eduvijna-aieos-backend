"""TOS-DEV08-I02 HTTP + application authority composition tests."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from aieos.domains.education.schema import (
    HOMEWORK_CONTENT_TYPE,
    LESSON_PLAN_CONTENT_TYPE,
    QUIZ_CONTENT_TYPE,
    WORKSHEET_CONTENT_TYPE,
)
from tests.domains.assessment.helpers_dev08_i02 import (
    RECORD_PATH,
    build_assessment_client,
    headers,
    seed_content_head,
    seed_published_learner_content,
    seed_teacher_only_content,
)

pytestmark = pytest.mark.tos_dev08_i02


@pytest.fixture
def tenant_id():
    return uuid.uuid7()


@pytest.fixture
def principal_id():
    return uuid.uuid7()


class TestRecordStandaloneCases:
    def test_r01_standalone_published_quiz(
        self, runtime_engine, bootstrap_engine, tenant_id, principal_id
    ) -> None:
        content_id, version_id = seed_published_learner_content(
            
            bootstrap_engine,
            tenant_id=tenant_id,
            owner_id=principal_id,
            content_type=QUIZ_CONTENT_TYPE,
        )
        client = build_assessment_client(runtime_engine, tenant_id, principal_id)
        response = client.post(
            RECORD_PATH,
            headers=headers(tenant_id, idempotency_key="rec-r01"),
            json={
                "class_ref": "class-5a",
                "content_id": str(content_id),
                "content_version_id": str(version_id),
                "class_result_level": "DEMONSTRATED",
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["lifecycle_state"] == "RECORDED"
        assert body["aggregate_revision"] == 0
        assert body["class_result_level"] == "DEMONSTRATED"
        assert response.headers["etag"] == '"r0"'
        assert "learner_id" not in body

    def test_r02_r03_worksheet_and_homework(
        self, runtime_engine, bootstrap_engine, tenant_id, principal_id
    ) -> None:
        client = build_assessment_client(runtime_engine, tenant_id, principal_id)
        for idx, ctype in enumerate((WORKSHEET_CONTENT_TYPE, HOMEWORK_CONTENT_TYPE)):
            content_id, version_id = seed_published_learner_content(
                
            bootstrap_engine,
                tenant_id=tenant_id,
                owner_id=principal_id,
                content_type=ctype,
            )
            response = client.post(
                RECORD_PATH,
                headers=headers(tenant_id, idempotency_key=f"rec-r0{idx+2}"),
                json={
                    "class_ref": "class-5a",
                    "content_id": str(content_id),
                    "content_version_id": str(version_id),
                    "class_result_level": "MIXED",
                },
            )
            assert response.status_code == 201, response.text

    def test_r04_unpublished_fails(
        self, runtime_engine, bootstrap_engine, tenant_id, principal_id
    ) -> None:
        content_id, version_id = seed_content_head(
            
            bootstrap_engine,
            tenant_id=tenant_id,
            owner_id=principal_id,
            content_type=QUIZ_CONTENT_TYPE,
            published=False,
        )
        client = build_assessment_client(runtime_engine, tenant_id, principal_id)
        response = client.post(
            RECORD_PATH,
            headers=headers(tenant_id, idempotency_key="rec-r04"),
            json={
                "class_ref": "class-5a",
                "content_id": str(content_id),
                "content_version_id": str(version_id),
                "class_result_level": "DEMONSTRATED",
            },
        )
        assert response.status_code == 400, response.text

    def test_r06_lesson_plan_rejected(
        self, runtime_engine, bootstrap_engine, tenant_id, principal_id
    ) -> None:
        content_id, version_id = seed_teacher_only_content(
            
            bootstrap_engine,
            tenant_id=tenant_id,
            owner_id=principal_id,
            content_type=LESSON_PLAN_CONTENT_TYPE,
        )
        client = build_assessment_client(runtime_engine, tenant_id, principal_id)
        response = client.post(
            RECORD_PATH,
            headers=headers(tenant_id, idempotency_key="rec-r06"),
            json={
                "class_ref": "class-5a",
                "content_id": str(content_id),
                "content_version_id": str(version_id),
                "class_result_level": "DEMONSTRATED",
            },
        )
        assert response.status_code == 400, response.text

    def test_r24_learner_fields_rejected_by_schema(
        self, runtime_engine, bootstrap_engine, tenant_id, principal_id
    ) -> None:
        content_id, version_id = seed_published_learner_content(
            
            bootstrap_engine,
            tenant_id=tenant_id,
            owner_id=principal_id,
            content_type=QUIZ_CONTENT_TYPE,
        )
        client = build_assessment_client(runtime_engine, tenant_id, principal_id)
        response = client.post(
            RECORD_PATH,
            headers=headers(tenant_id, idempotency_key="rec-r24"),
            json={
                "class_ref": "class-5a",
                "content_id": str(content_id),
                "content_version_id": str(version_id),
                "class_result_level": "DEMONSTRATED",
                "learner_id": str(uuid.uuid7()),
            },
        )
        assert response.status_code == 422, response.text


class TestIdempotencyAndMutations:
    def test_i01_i02_record_idempotency(
        self, runtime_engine, bootstrap_engine, tenant_id, principal_id
    ) -> None:
        content_id, version_id = seed_published_learner_content(
            
            bootstrap_engine,
            tenant_id=tenant_id,
            owner_id=principal_id,
            content_type=QUIZ_CONTENT_TYPE,
        )
        client = build_assessment_client(runtime_engine, tenant_id, principal_id)
        body = {
            "class_ref": "class-5a",
            "content_id": str(content_id),
            "content_version_id": str(version_id),
            "class_result_level": "DEMONSTRATED",
        }
        first = client.post(
            RECORD_PATH,
            headers=headers(tenant_id, idempotency_key="same-key"),
            json=body,
        )
        second = client.post(
            RECORD_PATH,
            headers=headers(tenant_id, idempotency_key="same-key"),
            json=body,
        )
        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["assessment_id"] == second.json()["assessment_id"]
        conflict = client.post(
            RECORD_PATH,
            headers=headers(tenant_id, idempotency_key="same-key"),
            json={**body, "class_result_level": "MIXED"},
        )
        assert conflict.status_code == 409

    def test_correct_void_get_list_and_concurrency(
        self, runtime_engine, bootstrap_engine, tenant_id, principal_id
    ) -> None:
        content_id, version_id = seed_published_learner_content(
            
            bootstrap_engine,
            tenant_id=tenant_id,
            owner_id=principal_id,
            content_type=QUIZ_CONTENT_TYPE,
        )
        client = build_assessment_client(runtime_engine, tenant_id, principal_id)
        created = client.post(
            RECORD_PATH,
            headers=headers(tenant_id, idempotency_key="mut-base"),
            json={
                "class_ref": "class-5a",
                "content_id": str(content_id),
                "content_version_id": str(version_id),
                "class_result_level": "NOT_YET_DEMONSTRATED",
                "class_result_note": "needs practice",
            },
        )
        assert created.status_code == 201, created.text
        assessment_id = created.json()["assessment_id"]
        etag = created.headers["etag"]

        got = client.get(
            f"{RECORD_PATH}/{assessment_id}",
            headers=headers(tenant_id),
        )
        assert got.status_code == 200
        assert got.headers["etag"] == etag

        listed = client.get(RECORD_PATH, headers=headers(tenant_id))
        assert listed.status_code == 200
        assert any(
            item["assessment_id"] == assessment_id for item in listed.json()["items"]
        )

        corrected = client.post(
            f"{RECORD_PATH}/{assessment_id}/actions/correct",
            headers=headers(
                tenant_id, idempotency_key="corr-1", if_match=etag
            ),
            json={
                "class_result_level": "MIXED",
                "class_result_note": "improved",
            },
        )
        assert corrected.status_code == 200, corrected.text
        assert corrected.json()["aggregate_revision"] == 1
        assert corrected.json()["class_result_level"] == "MIXED"
        assert corrected.json()["content_id"] == str(content_id)

        stale = client.post(
            f"{RECORD_PATH}/{assessment_id}/actions/correct",
            headers=headers(
                tenant_id, idempotency_key="corr-stale", if_match=etag
            ),
            json={"class_result_level": "DEMONSTRATED", "class_result_note": None},
        )
        assert stale.status_code == 412

        voided = client.post(
            f"{RECORD_PATH}/{assessment_id}/actions/void",
            headers=headers(
                tenant_id,
                idempotency_key="void-1",
                if_match=corrected.headers["etag"],
            ),
        )
        assert voided.status_code == 200, voided.text
        assert voided.json()["lifecycle_state"] == "VOIDED"
        assert voided.json()["aggregate_revision"] == 2

        again = client.post(
            f"{RECORD_PATH}/{assessment_id}/actions/void",
            headers=headers(
                tenant_id,
                idempotency_key="void-2",
                if_match=voided.headers["etag"],
            ),
        )
        assert again.status_code == 409


class TestClassRefAuthority:
    def test_a01_unknown_class_ref_denied(
        self, runtime_engine, bootstrap_engine, tenant_id, principal_id
    ) -> None:
        content_id, version_id = seed_published_learner_content(
            
            bootstrap_engine,
            tenant_id=tenant_id,
            owner_id=principal_id,
            content_type=QUIZ_CONTENT_TYPE,
        )
        client = build_assessment_client(runtime_engine, tenant_id, principal_id)
        response = client.post(
            RECORD_PATH,
            headers=headers(tenant_id, idempotency_key="a01"),
            json={
                "class_ref": "class-unknown",
                "content_id": str(content_id),
                "content_version_id": str(version_id),
                "class_result_level": "DEMONSTRATED",
            },
        )
        assert response.status_code == 403, response.text


class TestAuditMigration:
    def test_alembic_head_and_audit_insert(
        self, runtime_engine, bootstrap_engine, tenant_id, principal_id
    ) -> None:
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "tosd080002"
            )
        content_id, version_id = seed_published_learner_content(
            
            bootstrap_engine,
            tenant_id=tenant_id,
            owner_id=principal_id,
            content_type=QUIZ_CONTENT_TYPE,
        )
        client = build_assessment_client(runtime_engine, tenant_id, principal_id)
        response = client.post(
            RECORD_PATH,
            headers=headers(tenant_id, idempotency_key="audit-1"),
            json={
                "class_ref": "class-5a",
                "content_id": str(content_id),
                "content_version_id": str(version_id),
                "class_result_level": "DEMONSTRATED",
            },
        )
        assert response.status_code == 201, response.text
        with bootstrap_engine.connect() as conn:
            count = conn.execute(
                text(
                    """
                    SELECT count(*) FROM security.audit_records
                    WHERE action = 'assessment.classroom.record'
                      AND tenant_id = :tid
                    """
                ),
                {"tid": tenant_id},
            ).scalar_one()
            assert count == 1
