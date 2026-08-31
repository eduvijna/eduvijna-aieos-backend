"""TOS-DEV06-I01 — School Context ClassRef application service tests."""

from __future__ import annotations

from uuid import uuid4

import pytest

from aieos.domains.teaching.application.errors import (
    SchoolContextContractError,
    SchoolContextUnavailable,
)
from aieos.domains.teaching.application.school_context import (
    AssignableClassRef,
    ListAssignableSchoolClassesService,
)

pytestmark = pytest.mark.tos_dev06_i01


class _RecordingReader:
    def __init__(
        self,
        result: object = (),
        *,
        exc: BaseException | None = None,
    ) -> None:
        self.result = result
        self.exc = exc
        self.calls: list[tuple[object, object]] = []

    def list_assignable_classes(self, tenant_id, teacher_principal_id):
        self.calls.append((tenant_id, teacher_principal_id))
        if self.exc is not None:
            raise self.exc
        return self.result


class TestListAssignableSchoolClassesService:
    def test_passes_exact_tenant_and_teacher_principal(self) -> None:
        tenant_id = uuid4()
        principal_id = uuid4()
        reader = _RecordingReader(
            (
                AssignableClassRef(
                    class_ref="class-5a", display_label="Grade 5A"
                ),
            )
        )
        service = ListAssignableSchoolClassesService(reader)

        items = service.list(tenant_id, principal_id)

        assert reader.calls == [(tenant_id, principal_id)]
        assert items == (
            AssignableClassRef(class_ref="class-5a", display_label="Grade 5A"),
        )

    def test_empty_valid_set_returns_empty_tuple(self) -> None:
        service = ListAssignableSchoolClassesService(_RecordingReader(()))
        assert service.list(uuid4(), uuid4()) == ()

    def test_duplicate_class_ref_fails_closed(self) -> None:
        reader = _RecordingReader(
            (
                AssignableClassRef(class_ref="dup", display_label="A"),
                AssignableClassRef(class_ref="dup", display_label="B"),
            )
        )
        service = ListAssignableSchoolClassesService(reader)
        with pytest.raises(SchoolContextContractError):
            service.list(uuid4(), uuid4())

    def test_blank_class_ref_fails_closed(self) -> None:
        reader = _RecordingReader(
            (AssignableClassRef(class_ref="  ", display_label="Grade 5A"),)
        )
        service = ListAssignableSchoolClassesService(reader)
        with pytest.raises(SchoolContextContractError):
            service.list(uuid4(), uuid4())

    def test_blank_display_label_fails_closed(self) -> None:
        reader = _RecordingReader(
            (AssignableClassRef(class_ref="class-5a", display_label=""),)
        )
        service = ListAssignableSchoolClassesService(reader)
        with pytest.raises(SchoolContextContractError):
            service.list(uuid4(), uuid4())

    def test_malformed_provider_result_fails_closed(self) -> None:
        service = ListAssignableSchoolClassesService(_RecordingReader(None))
        with pytest.raises(SchoolContextContractError):
            service.list(uuid4(), uuid4())

        service = ListAssignableSchoolClassesService(_RecordingReader([object()]))
        with pytest.raises(SchoolContextContractError):
            service.list(uuid4(), uuid4())

    def test_unexpected_provider_exception_becomes_unavailable(self) -> None:
        reader = _RecordingReader(exc=RuntimeError("erp secret boom"))
        service = ListAssignableSchoolClassesService(reader)
        with pytest.raises(SchoolContextUnavailable):
            service.list(uuid4(), uuid4())
