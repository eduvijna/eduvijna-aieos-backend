"""TOS-DEV06-I03 — TeachingAssignment application service tests."""

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
    is_learner_assignable_content_type,
)
from aieos.domains.teaching.application.audit import api_mutation_audit_provenance
from aieos.domains.teaching.application.errors import (
    ClassRefNotAssignable,
    ContentNotEligibleForAssignment,
    SchoolContextUnavailable,
)
from aieos.domains.teaching.application.models import CreateTeachingAssignmentCommand
from aieos.domains.teaching.application.school_context import (
    AssignableClassRef,
    SchoolContextClassAuthorityService,
)
from tests.domains.teaching.helpers_dev06_i03 import (
    FIXED_NOW,
    create_assignment,
    create_service,
    event_context,
    seed_published_learner_content,
    seed_teacher_only_content,
)

pytestmark = pytest.mark.tos_dev06_i03


class _Reader:
    def __init__(
        self,
        items: tuple[AssignableClassRef, ...],
        *,
        unavailable: bool = False,
        other_teacher_only: uuid.UUID | None = None,
    ) -> None:
        self._items = items
        self._unavailable = unavailable
        self._other_teacher_only = other_teacher_only

    def list_assignable_classes(self, tenant_id, teacher_principal_id):
        if self._unavailable:
            raise SchoolContextUnavailable("School Context is temporarily unavailable")
        if (
            self._other_teacher_only is not None
            and teacher_principal_id == self._other_teacher_only
        ):
            return ()
        return self._items


class TestLearnerAssignableContentType:
    def test_worksheet_is_assignable(self) -> None:
        assert is_learner_assignable_content_type(WORKSHEET_CONTENT_TYPE)

    def test_quiz_is_assignable(self) -> None:
        assert is_learner_assignable_content_type(QUIZ_CONTENT_TYPE)

    def test_homework_is_assignable(self) -> None:
        assert is_learner_assignable_content_type(HOMEWORK_CONTENT_TYPE)

    def test_answer_key_is_not_assignable(self) -> None:
        assert not is_learner_assignable_content_type(ANSWER_KEY_CONTENT_TYPE)

    def test_lesson_plan_is_not_assignable(self) -> None:
        assert not is_learner_assignable_content_type(LESSON_PLAN_CONTENT_TYPE)

    def test_teacher_notes_is_not_assignable(self) -> None:
        assert not is_learner_assignable_content_type(TEACHER_NOTES_CONTENT_TYPE)

    def test_unknown_type_fails_closed(self) -> None:
        assert not is_learner_assignable_content_type("unknown.kind")


class TestLearnerKindCreatePaths:
    @pytest.mark.parametrize(
        "content_type",
        [WORKSHEET_CONTENT_TYPE, QUIZ_CONTENT_TYPE, HOMEWORK_CONTENT_TYPE],
    )
    def test_learner_kind_create_succeeds(
        self,
        runtime_engine: Engine,
        bootstrap_engine: Engine,
        content_type: str,
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_published_learner_content(
            bootstrap_engine,
            tenant_id=tenant_id,
            content_type=content_type,
        )
        result = create_assignment(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            content_id=content_id,
            content_version_id=version_id,
            idempotency_key=f"i03-learner-{content_type}",
        )
        assert result.lifecycle_state == "ACTIVE"
        assert result.content_id == content_id

    @pytest.mark.parametrize(
        "content_type",
        [
            LESSON_PLAN_CONTENT_TYPE,
            ANSWER_KEY_CONTENT_TYPE,
            TEACHER_NOTES_CONTENT_TYPE,
            "unknown.kind",
        ],
    )
    def test_non_learner_kind_create_rejects(
        self,
        runtime_engine: Engine,
        bootstrap_engine: Engine,
        content_type: str,
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_teacher_only_content(
            bootstrap_engine,
            tenant_id=tenant_id,
            content_type=content_type,
        )
        service = create_service(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        with pytest.raises(ContentNotEligibleForAssignment):
            service.create(
                tenant_id,
                principal_id,
                CreateTeachingAssignmentCommand(
                    content_id=content_id,
                    content_version_id=version_id,
                    class_ref="class-5a",
                ),
                idempotency_key=f"i03-reject-{content_type}",
                event_context=event_context(principal_id),
                audit_provenance=api_mutation_audit_provenance(principal_id),
                now=FIXED_NOW,
            )


class TestSchoolContextClassAuthorityService:
    def test_require_returns_matching_class(self) -> None:
        tenant_id = uuid.uuid7()
        teacher_id = uuid.uuid7()
        service = SchoolContextClassAuthorityService(
            _Reader((AssignableClassRef(class_ref="class-5a", display_label="Grade 5A"),))
        )
        found = service.require_assignable_class_ref(tenant_id, teacher_id, "class-5a")
        assert found.display_label == "Grade 5A"

    def test_require_rejects_unknown_class_ref(self) -> None:
        service = SchoolContextClassAuthorityService(
            _Reader((AssignableClassRef(class_ref="class-5a", display_label="Grade 5A"),))
        )
        with pytest.raises(ClassRefNotAssignable):
            service.require_assignable_class_ref(uuid.uuid7(), uuid.uuid7(), "class-5b")

    def test_class_ref_for_other_teacher_rejects(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        teacher_a = uuid.uuid7()
        teacher_b = uuid.uuid7()
        content_id, version_id = seed_published_learner_content(
            bootstrap_engine, tenant_id=tenant_id, content_type=WORKSHEET_CONTENT_TYPE
        )
        authority = SchoolContextClassAuthorityService(
            _Reader(
                (AssignableClassRef(class_ref="class-5a", display_label="Grade 5A"),),
                other_teacher_only=teacher_b,
            )
        )
        service = create_service(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=teacher_b,
            class_authority=authority,
        )
        with pytest.raises(ClassRefNotAssignable):
            service.create(
                tenant_id,
                teacher_b,
                CreateTeachingAssignmentCommand(
                    content_id=content_id,
                    content_version_id=version_id,
                    class_ref="class-5a",
                ),
                idempotency_key="i03-other-teacher-class",
                event_context=event_context(teacher_b),
                audit_provenance=api_mutation_audit_provenance(teacher_b),
                now=FIXED_NOW,
            )

    def test_school_context_unavailable_rejects_new_create(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_published_learner_content(
            bootstrap_engine, tenant_id=tenant_id, content_type=WORKSHEET_CONTENT_TYPE
        )
        authority = SchoolContextClassAuthorityService(
            _Reader((), unavailable=True)
        )
        service = create_service(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            class_authority=authority,
        )
        with pytest.raises(SchoolContextUnavailable):
            service.create(
                tenant_id,
                principal_id,
                CreateTeachingAssignmentCommand(
                    content_id=content_id,
                    content_version_id=version_id,
                    class_ref="class-5a",
                ),
                idempotency_key="i03-unavail-create",
                event_context=event_context(principal_id),
                audit_provenance=api_mutation_audit_provenance(principal_id),
                now=FIXED_NOW,
            )

    def test_audience_display_label_is_server_derived(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_published_learner_content(
            bootstrap_engine, tenant_id=tenant_id, content_type=WORKSHEET_CONTENT_TYPE
        )
        result = create_assignment(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            content_id=content_id,
            content_version_id=version_id,
            idempotency_key="i03-display-label",
        )
        assert result.audience_display_label == "Grade 5A"
