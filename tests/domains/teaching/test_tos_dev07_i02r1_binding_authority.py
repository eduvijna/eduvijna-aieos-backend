"""TOS-DEV07-I02R1 — learner-facing binding publication authority proofs."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.engine import Engine

from aieos.domains.education.schema import (
    ANSWER_KEY_CONTENT_TYPE,
    HOMEWORK_CONTENT_TYPE,
    LESSON_PLAN_CONTENT_TYPE,
    QUIZ_CONTENT_TYPE,
    TEACHER_NOTES_CONTENT_TYPE,
    WORKSHEET_CONTENT_TYPE,
)
from aieos.domains.teaching.application.errors import (
    ContentVersionMismatch,
    ExecutionContentBindingRejected,
)
from aieos.domains.teaching.application.models import (
    TeachingExecutionContentBindingInput,
)
from aieos.platform.events.constants import EVENT_TEACHING_EXECUTION_STARTED_V1
from aieos.platform.security.audit import SecurityAuditAction
from tests.domains.teaching.helpers_dev06_i03 import seed_content_head
from tests.domains.teaching.helpers_dev07_i02 import (
    FixedTeachingWorkArtifacts,
    count_executions,
    count_idempotency_outcomes,
    fetch_audit,
    fetch_outbox,
    seed_teaching_work,
    start_execution,
    work_artifact,
)

pytestmark = pytest.mark.tos_dev07_i02


def _assert_clean_rejection(
    bootstrap_engine: Engine, *, tenant_id: UUID, principal_id: UUID
) -> None:
    assert count_executions(bootstrap_engine, tenant_id=tenant_id) == 0
    assert (
        fetch_outbox(
            bootstrap_engine,
            tenant_id=tenant_id,
            event_type=EVENT_TEACHING_EXECUTION_STARTED_V1,
        )
        == []
    )
    assert (
        fetch_audit(
            bootstrap_engine,
            tenant_id=tenant_id,
            action=SecurityAuditAction.TEACHING_EXECUTION_START.value,
        )
        == []
    )
    assert (
        count_idempotency_outcomes(
            bootstrap_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        == 0
    )


def _start_with_binding(
    runtime_engine: Engine,
    bootstrap_engine: Engine,
    *,
    content_type: str,
    published: bool,
    artifact_kind: str | None = None,
    idempotency_key: str,
) -> None:
    tenant_id = uuid.uuid7()
    principal_id = uuid.uuid7()
    work_id = seed_teaching_work(
        runtime_engine, tenant_id=tenant_id, principal_id=principal_id
    )
    content_id, version_id = seed_content_head(
        bootstrap_engine,
        tenant_id=tenant_id,
        content_type=content_type,
        published=published,
        owner_id=principal_id,
    )
    kind = artifact_kind or content_type
    artifacts = FixedTeachingWorkArtifacts(
        items=(
            work_artifact(
                content_id=content_id,
                version_id=version_id,
                content_type=content_type,
                artifact_kind=kind,
            ),
        )
    )
    start_execution(
        runtime_engine,
        tenant_id=tenant_id,
        principal_id=principal_id,
        work_id=work_id,
        idempotency_key=idempotency_key,
        bindings=(
            TeachingExecutionContentBindingInput(
                content_id=content_id,
                content_version_id=version_id,
                artifact_kind=kind,
            ),
        ),
        artifacts=artifacts,
    )


class TestBindingPublicationAuthority:
    def test_a_unpublished_worksheet_rejected(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        work_id = seed_teaching_work(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        content_id, version_id = seed_content_head(
            bootstrap_engine,
            tenant_id=tenant_id,
            content_type=WORKSHEET_CONTENT_TYPE,
            published=False,
            owner_id=principal_id,
        )
        artifacts = FixedTeachingWorkArtifacts(
            items=(
                work_artifact(
                    content_id=content_id,
                    version_id=version_id,
                    content_type=WORKSHEET_CONTENT_TYPE,
                ),
            )
        )
        with pytest.raises(ContentVersionMismatch):
            start_execution(
                runtime_engine,
                tenant_id=tenant_id,
                principal_id=principal_id,
                work_id=work_id,
                idempotency_key="i02r1-a",
                bindings=(
                    TeachingExecutionContentBindingInput(
                        content_id=content_id,
                        content_version_id=version_id,
                        artifact_kind=WORKSHEET_CONTENT_TYPE,
                    ),
                ),
                artifacts=artifacts,
            )
        _assert_clean_rejection(
            bootstrap_engine, tenant_id=tenant_id, principal_id=principal_id
        )

    def test_b_approved_unpublished_worksheet_rejected(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        work_id = seed_teaching_work(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        content_id, version_id = seed_content_head(
            bootstrap_engine,
            tenant_id=tenant_id,
            content_type=WORKSHEET_CONTENT_TYPE,
            published=False,
            owner_id=principal_id,
        )
        # seed_content_head always uses stewardship_state APPROVED.
        artifacts = FixedTeachingWorkArtifacts(
            items=(
                work_artifact(
                    content_id=content_id,
                    version_id=version_id,
                    content_type=WORKSHEET_CONTENT_TYPE,
                ),
            )
        )
        with pytest.raises(ContentVersionMismatch):
            start_execution(
                runtime_engine,
                tenant_id=tenant_id,
                principal_id=principal_id,
                work_id=work_id,
                idempotency_key="i02r1-b",
                bindings=(
                    TeachingExecutionContentBindingInput(
                        content_id=content_id,
                        content_version_id=version_id,
                        artifact_kind=WORKSHEET_CONTENT_TYPE,
                    ),
                ),
                artifacts=artifacts,
            )
        _assert_clean_rejection(
            bootstrap_engine, tenant_id=tenant_id, principal_id=principal_id
        )

    def test_c_published_worksheet_allowed(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        _start_with_binding(
            runtime_engine,
            bootstrap_engine,
            content_type=WORKSHEET_CONTENT_TYPE,
            published=True,
            idempotency_key="i02r1-c",
        )

    def test_d_published_quiz_allowed(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        _start_with_binding(
            runtime_engine,
            bootstrap_engine,
            content_type=QUIZ_CONTENT_TYPE,
            published=True,
            idempotency_key="i02r1-d",
        )

    def test_e_published_homework_allowed(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        _start_with_binding(
            runtime_engine,
            bootstrap_engine,
            content_type=HOMEWORK_CONTENT_TYPE,
            published=True,
            idempotency_key="i02r1-e",
        )

    def test_f_teacher_only_lesson_plan_without_publication(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        _start_with_binding(
            runtime_engine,
            bootstrap_engine,
            content_type=LESSON_PLAN_CONTENT_TYPE,
            published=False,
            idempotency_key="i02r1-f",
        )

    def test_g_teacher_only_answer_key_and_notes_without_publication(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        _start_with_binding(
            runtime_engine,
            bootstrap_engine,
            content_type=ANSWER_KEY_CONTENT_TYPE,
            published=False,
            idempotency_key="i02r1-g-ak",
        )
        _start_with_binding(
            runtime_engine,
            bootstrap_engine,
            content_type=TEACHER_NOTES_CONTENT_TYPE,
            published=False,
            idempotency_key="i02r1-g-tn",
        )

    def test_h_unknown_audience_fail_closed(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        work_id = seed_teaching_work(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        content_id, version_id = seed_content_head(
            bootstrap_engine,
            tenant_id=tenant_id,
            content_type="unknown.kind",
            published=True,
            owner_id=principal_id,
        )
        artifacts = FixedTeachingWorkArtifacts(
            items=(
                work_artifact(
                    content_id=content_id,
                    version_id=version_id,
                    content_type="unknown.kind",
                    artifact_kind="unknown.kind",
                ),
            )
        )
        with pytest.raises(ExecutionContentBindingRejected):
            start_execution(
                runtime_engine,
                tenant_id=tenant_id,
                principal_id=principal_id,
                work_id=work_id,
                idempotency_key="i02r1-h",
                bindings=(
                    TeachingExecutionContentBindingInput(
                        content_id=content_id,
                        content_version_id=version_id,
                        artifact_kind="unknown.kind",
                    ),
                ),
                artifacts=artifacts,
            )
        _assert_clean_rejection(
            bootstrap_engine, tenant_id=tenant_id, principal_id=principal_id
        )

    def test_j_mismatched_content_version_rejected(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        work_id = seed_teaching_work(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        content_id, version_id = seed_content_head(
            bootstrap_engine,
            tenant_id=tenant_id,
            content_type=WORKSHEET_CONTENT_TYPE,
            published=True,
            owner_id=principal_id,
        )
        other_content_id, other_version_id = seed_content_head(
            bootstrap_engine,
            tenant_id=tenant_id,
            content_type=WORKSHEET_CONTENT_TYPE,
            published=True,
            owner_id=principal_id,
        )
        artifacts = FixedTeachingWorkArtifacts(
            items=(
                work_artifact(
                    content_id=content_id,
                    version_id=other_version_id,
                    content_type=WORKSHEET_CONTENT_TYPE,
                ),
            )
        )
        with pytest.raises(ExecutionContentBindingRejected):
            start_execution(
                runtime_engine,
                tenant_id=tenant_id,
                principal_id=principal_id,
                work_id=work_id,
                idempotency_key="i02r1-j",
                bindings=(
                    TeachingExecutionContentBindingInput(
                        content_id=content_id,
                        content_version_id=other_version_id,
                        artifact_kind=WORKSHEET_CONTENT_TYPE,
                    ),
                ),
                artifacts=artifacts,
            )
        del other_content_id, version_id
        _assert_clean_rejection(
            bootstrap_engine, tenant_id=tenant_id, principal_id=principal_id
        )
