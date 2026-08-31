"""TOS-DEV06-I03 — TeachingAssignment application service tests."""

from __future__ import annotations

from uuid import uuid4

import pytest

from aieos.domains.education.schema import (
    ANSWER_KEY_CONTENT_TYPE,
    WORKSHEET_CONTENT_TYPE,
    is_learner_assignable_content_type,
)
from aieos.domains.teaching.application.errors import ClassRefNotAssignable
from aieos.domains.teaching.application.school_context import (
    AssignableClassRef,
    SchoolContextClassAuthorityService,
)

pytestmark = pytest.mark.tos_dev06_i03


class _Reader:
    def __init__(self, items: tuple[AssignableClassRef, ...]) -> None:
        self._items = items

    def list_assignable_classes(self, tenant_id, teacher_principal_id):
        return self._items


class TestLearnerAssignableContentType:
    def test_worksheet_is_assignable(self) -> None:
        assert is_learner_assignable_content_type(WORKSHEET_CONTENT_TYPE)

    def test_answer_key_is_not_assignable(self) -> None:
        assert not is_learner_assignable_content_type(ANSWER_KEY_CONTENT_TYPE)

    def test_unknown_type_fails_closed(self) -> None:
        assert not is_learner_assignable_content_type("unknown.kind")


class TestSchoolContextClassAuthorityService:
    def test_require_returns_matching_class(self) -> None:
        tenant_id = uuid4()
        teacher_id = uuid4()
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
            service.require_assignable_class_ref(uuid4(), uuid4(), "class-5b")
