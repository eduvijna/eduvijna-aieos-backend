"""TOS-DEV08-I02 Cases A/B/C composition + ClassRef authority proofs."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from aieos.domains.education.schema import (
    ANSWER_KEY_CONTENT_TYPE,
    LESSON_PLAN_CONTENT_TYPE,
    QUIZ_CONTENT_TYPE,
    TEACHER_NOTES_CONTENT_TYPE,
)
from aieos.domains.teaching.application.audit import api_mutation_audit_provenance
from aieos.domains.teaching.application.models import (
    TeachingExecutionContentBindingInput,
)
from tests.domains.assessment.helpers_dev08_i02 import (
    RECORD_PATH,
    MutableSchoolContextClassReader,
    build_assessment_client,
    complete_execution_with_binding,
    headers,
    republish_content_to_new_version,
    seed_published_learner_content,
    seed_teacher_only_content,
)
from tests.domains.teaching.helpers_dev06_i03 import create_assignment
from tests.domains.teaching.helpers_dev07_i02 import (
    FIXED_NOW,
    FixedTeachingWorkArtifacts,
    cancel_service,
    event_context,
    seed_teaching_work,
    start_execution,
    work_artifact,
)

pytestmark = pytest.mark.tos_dev08_i02


@pytest.fixture
def tenant_id():
    return uuid.uuid7()


@pytest.fixture
def principal_id():
    return uuid.uuid7()


class TestCaseCStandalone:
    def test_r05_old_non_current_version_fails(
        self, runtime_engine, bootstrap_engine, tenant_id, principal_id
    ) -> None:
        content_id, v1 = seed_published_learner_content(
            bootstrap_engine,
            tenant_id=tenant_id,
            owner_id=principal_id,
            content_type=QUIZ_CONTENT_TYPE,
        )
        republish_content_to_new_version(
            bootstrap_engine,
            tenant_id=tenant_id,
            content_id=content_id,
            parent_version_id=v1,
            owner_id=principal_id,
            content_type=QUIZ_CONTENT_TYPE,
        )
        client = build_assessment_client(runtime_engine, tenant_id, principal_id)
        response = client.post(
            RECORD_PATH,
            headers=headers(tenant_id, idempotency_key="r05"),
            json={
                "class_ref": "class-5a",
                "content_id": str(content_id),
                "content_version_id": str(v1),
                "class_result_level": "DEMONSTRATED",
            },
        )
        assert response.status_code == 400, response.text

    @pytest.mark.parametrize(
        "ctype,key",
        [
            (ANSWER_KEY_CONTENT_TYPE, "r07"),
            (TEACHER_NOTES_CONTENT_TYPE, "r08"),
        ],
    )
    def test_r07_r08_teacher_only_rejected(
        self, runtime_engine, bootstrap_engine, tenant_id, principal_id, ctype, key
    ) -> None:
        content_id, version_id = seed_teacher_only_content(
            bootstrap_engine,
            tenant_id=tenant_id,
            owner_id=principal_id,
            content_type=ctype,
        )
        client = build_assessment_client(runtime_engine, tenant_id, principal_id)
        response = client.post(
            RECORD_PATH,
            headers=headers(tenant_id, idempotency_key=key),
            json={
                "class_ref": "class-5a",
                "content_id": str(content_id),
                "content_version_id": str(version_id),
                "class_result_level": "DEMONSTRATED",
            },
        )
        assert response.status_code == 400, response.text


class TestCaseAExecutionBound:
    def test_r09_completed_v1_after_publish_move(
        self, runtime_engine, bootstrap_engine, tenant_id, principal_id
    ) -> None:
        content_id, v1 = seed_published_learner_content(
            bootstrap_engine,
            tenant_id=tenant_id,
            owner_id=principal_id,
            content_type=QUIZ_CONTENT_TYPE,
        )
        completed, work_id = complete_execution_with_binding(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            content_id=content_id,
            version_id=v1,
            content_type=QUIZ_CONTENT_TYPE,
            key_prefix="r09",
        )
        republish_content_to_new_version(
            bootstrap_engine,
            tenant_id=tenant_id,
            content_id=content_id,
            parent_version_id=v1,
            owner_id=principal_id,
            content_type=QUIZ_CONTENT_TYPE,
        )
        client = build_assessment_client(runtime_engine, tenant_id, principal_id)
        response = client.post(
            RECORD_PATH,
            headers=headers(tenant_id, idempotency_key="r09-rec"),
            json={
                "class_ref": "class-5a",
                "content_id": str(content_id),
                "content_version_id": str(v1),
                "class_result_level": "DEMONSTRATED",
                "execution_id": str(completed.execution_id),
                "work_id": str(work_id.value if hasattr(work_id, "value") else work_id),
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["execution_id"] == str(completed.execution_id)

    def test_r10_in_progress_fails(
        self, runtime_engine, bootstrap_engine, tenant_id, principal_id
    ) -> None:
        content_id, v1 = seed_published_learner_content(
            bootstrap_engine,
            tenant_id=tenant_id,
            owner_id=principal_id,
            content_type=QUIZ_CONTENT_TYPE,
        )
        work_id = seed_teaching_work(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        artifacts = FixedTeachingWorkArtifacts(
            items=(
                work_artifact(
                    content_id=content_id,
                    version_id=v1,
                    content_type=QUIZ_CONTENT_TYPE,
                ),
            )
        )
        started = start_execution(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            idempotency_key="r10-start",
            bindings=(
                TeachingExecutionContentBindingInput(
                    content_id=content_id,
                    content_version_id=v1,
                    artifact_kind=QUIZ_CONTENT_TYPE,
                ),
            ),
            artifacts=artifacts,
        )
        client = build_assessment_client(runtime_engine, tenant_id, principal_id)
        response = client.post(
            RECORD_PATH,
            headers=headers(tenant_id, idempotency_key="r10-rec"),
            json={
                "class_ref": "class-5a",
                "content_id": str(content_id),
                "content_version_id": str(v1),
                "class_result_level": "DEMONSTRATED",
                "execution_id": str(started.execution_id),
            },
        )
        assert response.status_code == 400, response.text

    def test_r11_cancelled_fails(
        self, runtime_engine, bootstrap_engine, tenant_id, principal_id
    ) -> None:
        content_id, v1 = seed_published_learner_content(
            bootstrap_engine,
            tenant_id=tenant_id,
            owner_id=principal_id,
            content_type=QUIZ_CONTENT_TYPE,
        )
        work_id = seed_teaching_work(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        artifacts = FixedTeachingWorkArtifacts(
            items=(
                work_artifact(
                    content_id=content_id,
                    version_id=v1,
                    content_type=QUIZ_CONTENT_TYPE,
                ),
            )
        )
        started = start_execution(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            idempotency_key="r11-start",
            bindings=(
                TeachingExecutionContentBindingInput(
                    content_id=content_id,
                    content_version_id=v1,
                    artifact_kind=QUIZ_CONTENT_TYPE,
                ),
            ),
            artifacts=artifacts,
        )
        cancel_service(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        ).cancel(
            tenant_id,
            principal_id,
            execution_id=started.execution_id,
            expected_aggregate_revision=started.aggregate_revision,
            idempotency_key="r11-cancel",
            event_context=event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        client = build_assessment_client(runtime_engine, tenant_id, principal_id)
        response = client.post(
            RECORD_PATH,
            headers=headers(tenant_id, idempotency_key="r11-rec"),
            json={
                "class_ref": "class-5a",
                "content_id": str(content_id),
                "content_version_id": str(v1),
                "class_result_level": "DEMONSTRATED",
                "execution_id": str(started.execution_id),
            },
        )
        assert response.status_code == 400, response.text

    def test_r12_class_mismatch_fails(
        self, runtime_engine, bootstrap_engine, tenant_id, principal_id
    ) -> None:
        content_id, v1 = seed_published_learner_content(
            bootstrap_engine,
            tenant_id=tenant_id,
            owner_id=principal_id,
            content_type=QUIZ_CONTENT_TYPE,
        )
        completed, _ = complete_execution_with_binding(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            content_id=content_id,
            version_id=v1,
            content_type=QUIZ_CONTENT_TYPE,
            class_ref="class-5a",
            key_prefix="r12",
        )
        client = build_assessment_client(runtime_engine, tenant_id, principal_id)
        response = client.post(
            RECORD_PATH,
            headers=headers(tenant_id, idempotency_key="r12-rec"),
            json={
                "class_ref": "class-5b",
                "content_id": str(content_id),
                "content_version_id": str(v1),
                "class_result_level": "DEMONSTRATED",
                "execution_id": str(completed.execution_id),
            },
        )
        assert response.status_code == 400, response.text

    def test_r13_teacher_mismatch_invisible(
        self, runtime_engine, bootstrap_engine, tenant_id, principal_id
    ) -> None:
        other = uuid.uuid7()
        content_id, v1 = seed_published_learner_content(
            bootstrap_engine,
            tenant_id=tenant_id,
            owner_id=other,
            content_type=QUIZ_CONTENT_TYPE,
        )
        completed, _ = complete_execution_with_binding(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=other,
            content_id=content_id,
            version_id=v1,
            content_type=QUIZ_CONTENT_TYPE,
            key_prefix="r13",
        )
        # Assessment teacher differs; class authority for principal_id still ok
        client = build_assessment_client(runtime_engine, tenant_id, principal_id)
        response = client.post(
            RECORD_PATH,
            headers=headers(tenant_id, idempotency_key="r13-rec"),
            json={
                "class_ref": "class-5a",
                "content_id": str(content_id),
                "content_version_id": str(v1),
                "class_result_level": "DEMONSTRATED",
                "execution_id": str(completed.execution_id),
            },
        )
        assert response.status_code in {400, 403, 404}, response.text

    def test_r14_binding_mismatch_fails(
        self, runtime_engine, bootstrap_engine, tenant_id, principal_id
    ) -> None:
        content_id, v1 = seed_published_learner_content(
            bootstrap_engine,
            tenant_id=tenant_id,
            owner_id=principal_id,
            content_type=QUIZ_CONTENT_TYPE,
        )
        other_content, other_v = seed_published_learner_content(
            bootstrap_engine,
            tenant_id=tenant_id,
            owner_id=principal_id,
            content_type=QUIZ_CONTENT_TYPE,
        )
        completed, _ = complete_execution_with_binding(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            content_id=content_id,
            version_id=v1,
            content_type=QUIZ_CONTENT_TYPE,
            key_prefix="r14",
        )
        client = build_assessment_client(runtime_engine, tenant_id, principal_id)
        response = client.post(
            RECORD_PATH,
            headers=headers(tenant_id, idempotency_key="r14-rec"),
            json={
                "class_ref": "class-5a",
                "content_id": str(other_content),
                "content_version_id": str(other_v),
                "class_result_level": "DEMONSTRATED",
                "execution_id": str(completed.execution_id),
            },
        )
        assert response.status_code == 400, response.text

    def test_r15_teacher_only_binding_rejected(
        self, runtime_engine, bootstrap_engine, tenant_id, principal_id
    ) -> None:
        content_id, v1 = seed_teacher_only_content(
            bootstrap_engine,
            tenant_id=tenant_id,
            owner_id=principal_id,
            content_type=LESSON_PLAN_CONTENT_TYPE,
        )
        work_id = seed_teaching_work(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        artifacts = FixedTeachingWorkArtifacts(
            items=(
                work_artifact(
                    content_id=content_id,
                    version_id=v1,
                    content_type=LESSON_PLAN_CONTENT_TYPE,
                ),
            )
        )
        started = start_execution(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            idempotency_key="r15-start",
            bindings=(
                TeachingExecutionContentBindingInput(
                    content_id=content_id,
                    content_version_id=v1,
                    artifact_kind=LESSON_PLAN_CONTENT_TYPE,
                ),
            ),
            artifacts=artifacts,
        )
        from tests.domains.teaching.helpers_dev07_i02 import complete_service

        completed = complete_service(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        ).complete(
            tenant_id,
            principal_id,
            execution_id=started.execution_id,
            expected_aggregate_revision=started.aggregate_revision,
            idempotency_key="r15-complete",
            event_context=event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        client = build_assessment_client(runtime_engine, tenant_id, principal_id)
        response = client.post(
            RECORD_PATH,
            headers=headers(tenant_id, idempotency_key="r15-rec"),
            json={
                "class_ref": "class-5a",
                "content_id": str(content_id),
                "content_version_id": str(v1),
                "class_result_level": "DEMONSTRATED",
                "execution_id": str(completed.execution_id),
            },
        )
        assert response.status_code == 400, response.text

    def test_r23_work_id_mismatch_fails(
        self, runtime_engine, bootstrap_engine, tenant_id, principal_id
    ) -> None:
        content_id, v1 = seed_published_learner_content(
            bootstrap_engine,
            tenant_id=tenant_id,
            owner_id=principal_id,
            content_type=QUIZ_CONTENT_TYPE,
        )
        completed, _ = complete_execution_with_binding(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            content_id=content_id,
            version_id=v1,
            content_type=QUIZ_CONTENT_TYPE,
            key_prefix="r23",
        )
        other_work = seed_teaching_work(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        client = build_assessment_client(runtime_engine, tenant_id, principal_id)
        response = client.post(
            RECORD_PATH,
            headers=headers(tenant_id, idempotency_key="r23-rec"),
            json={
                "class_ref": "class-5a",
                "content_id": str(content_id),
                "content_version_id": str(v1),
                "class_result_level": "DEMONSTRATED",
                "execution_id": str(completed.execution_id),
                "work_id": str(
                    other_work.value if hasattr(other_work, "value") else other_work
                ),
            },
        )
        assert response.status_code == 400, response.text


class TestCaseBAssignmentBound:
    def test_r16_assignment_after_publish_move(
        self, runtime_engine, bootstrap_engine, tenant_id, principal_id
    ) -> None:
        content_id, v1 = seed_published_learner_content(
            bootstrap_engine,
            tenant_id=tenant_id,
            owner_id=principal_id,
            content_type=QUIZ_CONTENT_TYPE,
        )
        assignment = create_assignment(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            content_id=content_id,
            content_version_id=v1,
            idempotency_key="r16-asn",
        )
        republish_content_to_new_version(
            bootstrap_engine,
            tenant_id=tenant_id,
            content_id=content_id,
            parent_version_id=v1,
            owner_id=principal_id,
            content_type=QUIZ_CONTENT_TYPE,
        )
        client = build_assessment_client(runtime_engine, tenant_id, principal_id)
        response = client.post(
            RECORD_PATH,
            headers=headers(tenant_id, idempotency_key="r16-rec"),
            json={
                "class_ref": "class-5a",
                "content_id": str(content_id),
                "content_version_id": str(v1),
                "class_result_level": "MIXED",
                "assignment_id": str(assignment.assignment_id),
            },
        )
        assert response.status_code == 201, response.text

    def test_r17_assignment_class_mismatch(
        self, runtime_engine, bootstrap_engine, tenant_id, principal_id
    ) -> None:
        content_id, v1 = seed_published_learner_content(
            bootstrap_engine,
            tenant_id=tenant_id,
            owner_id=principal_id,
            content_type=QUIZ_CONTENT_TYPE,
        )
        assignment = create_assignment(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            content_id=content_id,
            content_version_id=v1,
            idempotency_key="r17-asn",
            class_ref="class-5a",
        )
        client = build_assessment_client(runtime_engine, tenant_id, principal_id)
        response = client.post(
            RECORD_PATH,
            headers=headers(tenant_id, idempotency_key="r17-rec"),
            json={
                "class_ref": "class-5b",
                "content_id": str(content_id),
                "content_version_id": str(v1),
                "class_result_level": "DEMONSTRATED",
                "assignment_id": str(assignment.assignment_id),
            },
        )
        assert response.status_code == 400, response.text

    def test_r19_assignment_version_mismatch(
        self, runtime_engine, bootstrap_engine, tenant_id, principal_id
    ) -> None:
        content_id, v1 = seed_published_learner_content(
            bootstrap_engine,
            tenant_id=tenant_id,
            owner_id=principal_id,
            content_type=QUIZ_CONTENT_TYPE,
        )
        assignment = create_assignment(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            content_id=content_id,
            content_version_id=v1,
            idempotency_key="r19-asn",
        )
        v2 = republish_content_to_new_version(
            bootstrap_engine,
            tenant_id=tenant_id,
            content_id=content_id,
            parent_version_id=v1,
            owner_id=principal_id,
            content_type=QUIZ_CONTENT_TYPE,
        )
        client = build_assessment_client(runtime_engine, tenant_id, principal_id)
        response = client.post(
            RECORD_PATH,
            headers=headers(tenant_id, idempotency_key="r19-rec"),
            json={
                "class_ref": "class-5a",
                "content_id": str(content_id),
                "content_version_id": str(v2),
                "class_result_level": "DEMONSTRATED",
                "assignment_id": str(assignment.assignment_id),
            },
        )
        assert response.status_code == 400, response.text

    def test_r20_r21_execution_and_assignment(
        self, runtime_engine, bootstrap_engine, tenant_id, principal_id
    ) -> None:
        content_id, v1 = seed_published_learner_content(
            bootstrap_engine,
            tenant_id=tenant_id,
            owner_id=principal_id,
            content_type=QUIZ_CONTENT_TYPE,
        )
        assignment = create_assignment(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            content_id=content_id,
            content_version_id=v1,
            idempotency_key="r20-asn",
        )
        completed, work_id = complete_execution_with_binding(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            content_id=content_id,
            version_id=v1,
            content_type=QUIZ_CONTENT_TYPE,
            key_prefix="r20",
        )
        client = build_assessment_client(runtime_engine, tenant_id, principal_id)
        ok = client.post(
            RECORD_PATH,
            headers=headers(tenant_id, idempotency_key="r20-rec"),
            json={
                "class_ref": "class-5a",
                "content_id": str(content_id),
                "content_version_id": str(v1),
                "class_result_level": "DEMONSTRATED",
                "execution_id": str(completed.execution_id),
                "assignment_id": str(assignment.assignment_id),
                "work_id": str(work_id.value if hasattr(work_id, "value") else work_id),
            },
        )
        assert ok.status_code == 201, ok.text

        other_content, other_v = seed_published_learner_content(
            bootstrap_engine,
            tenant_id=tenant_id,
            owner_id=principal_id,
            content_type=QUIZ_CONTENT_TYPE,
        )
        other_asn = create_assignment(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            content_id=other_content,
            content_version_id=other_v,
            idempotency_key="r21-asn",
        )
        bad = client.post(
            RECORD_PATH,
            headers=headers(tenant_id, idempotency_key="r21-rec"),
            json={
                "class_ref": "class-5a",
                "content_id": str(content_id),
                "content_version_id": str(v1),
                "class_result_level": "DEMONSTRATED",
                "execution_id": str(completed.execution_id),
                "assignment_id": str(other_asn.assignment_id),
            },
        )
        assert bad.status_code == 400, bad.text


class TestClassRefHistoricalAndUnavailable:
    def test_a04_a06_historical_read_mutation_denied(
        self, runtime_engine, bootstrap_engine, tenant_id, principal_id
    ) -> None:
        content_id, version_id = seed_published_learner_content(
            bootstrap_engine,
            tenant_id=tenant_id,
            owner_id=principal_id,
            content_type=QUIZ_CONTENT_TYPE,
        )
        reader = MutableSchoolContextClassReader(
            tenant_id=tenant_id, teacher_principal_id=principal_id
        )
        client = build_assessment_client(
            runtime_engine,
            tenant_id,
            principal_id,
            school_context_reader=reader,
        )
        created = client.post(
            RECORD_PATH,
            headers=headers(tenant_id, idempotency_key="hist-rec"),
            json={
                "class_ref": "class-5a",
                "content_id": str(content_id),
                "content_version_id": str(version_id),
                "class_result_level": "DEMONSTRATED",
            },
        )
        assert created.status_code == 201, created.text
        assessment_id = created.json()["assessment_id"]
        etag = created.headers["etag"]

        reader.class_refs = []
        got = client.get(
            f"{RECORD_PATH}/{assessment_id}",
            headers=headers(tenant_id),
        )
        assert got.status_code == 200, got.text

        corrected = client.post(
            f"{RECORD_PATH}/{assessment_id}/actions/correct",
            headers=headers(tenant_id, idempotency_key="hist-corr", if_match=etag),
            json={"class_result_level": "MIXED", "class_result_note": None},
        )
        assert corrected.status_code == 403, corrected.text

        voided = client.post(
            f"{RECORD_PATH}/{assessment_id}/actions/void",
            headers=headers(tenant_id, idempotency_key="hist-void", if_match=etag),
        )
        assert voided.status_code == 403, voided.text

    def test_a07_school_context_unavailable(
        self, runtime_engine, bootstrap_engine, tenant_id, principal_id
    ) -> None:
        content_id, version_id = seed_published_learner_content(
            bootstrap_engine,
            tenant_id=tenant_id,
            owner_id=principal_id,
            content_type=QUIZ_CONTENT_TYPE,
        )
        client = build_assessment_client(
            runtime_engine,
            tenant_id,
            principal_id,
            with_school_context=False,
        )
        response = client.post(
            RECORD_PATH,
            headers=headers(tenant_id, idempotency_key="a07"),
            json={
                "class_ref": "class-5a",
                "content_id": str(content_id),
                "content_version_id": str(version_id),
                "class_result_level": "DEMONSTRATED",
            },
        )
        assert response.status_code == 503, response.text
        with bootstrap_engine.connect() as conn:
            count = conn.execute(
                text(
                    """
                    SELECT count(*) FROM assessment.classroom_assessments
                    WHERE tenant_id = :tid
                    """
                ),
                {"tid": tenant_id},
            ).scalar_one()
            assert count == 0


class TestIdempotencyExtended:
    def test_i03_distinct_keys_allow_distinct_assessments(
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
        a = client.post(
            RECORD_PATH, headers=headers(tenant_id, idempotency_key="k1"), json=body
        )
        b = client.post(
            RECORD_PATH, headers=headers(tenant_id, idempotency_key="k2"), json=body
        )
        assert a.status_code == 201 and b.status_code == 201
        assert a.json()["assessment_id"] != b.json()["assessment_id"]

    def test_i04_i07_correct_replay_no_duplicate_audit(
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
            headers=headers(tenant_id, idempotency_key="i04-rec"),
            json={
                "class_ref": "class-5a",
                "content_id": str(content_id),
                "content_version_id": str(version_id),
                "class_result_level": "NOT_YET_DEMONSTRATED",
            },
        )
        assert created.status_code == 201
        assessment_id = created.json()["assessment_id"]
        etag = created.headers["etag"]
        body = {"class_result_level": "MIXED", "class_result_note": "note"}
        first = client.post(
            f"{RECORD_PATH}/{assessment_id}/actions/correct",
            headers=headers(tenant_id, idempotency_key="corr-same", if_match=etag),
            json=body,
        )
        second = client.post(
            f"{RECORD_PATH}/{assessment_id}/actions/correct",
            headers=headers(tenant_id, idempotency_key="corr-same", if_match=etag),
            json=body,
        )
        assert first.status_code == 200 and second.status_code == 200
        assert first.json()["aggregate_revision"] == second.json()["aggregate_revision"] == 1
        conflict = client.post(
            f"{RECORD_PATH}/{assessment_id}/actions/correct",
            headers=headers(tenant_id, idempotency_key="corr-same", if_match=etag),
            json={"class_result_level": "DEMONSTRATED", "class_result_note": None},
        )
        assert conflict.status_code == 409
        with bootstrap_engine.connect() as conn:
            count = conn.execute(
                text(
                    """
                    SELECT count(*) FROM security.audit_records
                    WHERE tenant_id = :tid
                      AND action = 'assessment.classroom.correct'
                      AND primary_resource_id = :aid
                    """
                ),
                {"tid": tenant_id, "aid": assessment_id},
            ).scalar_one()
            assert count == 1

    def test_i06_void_replay(
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
            headers=headers(tenant_id, idempotency_key="i06-rec"),
            json={
                "class_ref": "class-5a",
                "content_id": str(content_id),
                "content_version_id": str(version_id),
                "class_result_level": "DEMONSTRATED",
            },
        )
        assessment_id = created.json()["assessment_id"]
        etag = created.headers["etag"]
        first = client.post(
            f"{RECORD_PATH}/{assessment_id}/actions/void",
            headers=headers(tenant_id, idempotency_key="void-same", if_match=etag),
        )
        second = client.post(
            f"{RECORD_PATH}/{assessment_id}/actions/void",
            headers=headers(tenant_id, idempotency_key="void-same", if_match=etag),
        )
        assert first.status_code == 200 and second.status_code == 200
        assert first.json()["aggregate_revision"] == second.json()["aggregate_revision"]
        with bootstrap_engine.connect() as conn:
            count = conn.execute(
                text(
                    """
                    SELECT count(*) FROM security.audit_records
                    WHERE tenant_id = :tid
                      AND action = 'assessment.classroom.void'
                      AND primary_resource_id = :aid
                    """
                ),
                {"tid": tenant_id, "aid": assessment_id},
            ).scalar_one()
            assert count == 1
