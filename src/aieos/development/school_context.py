"""NON_PRODUCTION School Context ClassRef adapter (TOS-DEV06-I01).

Deterministic synthetic assignable classes for Teacher OS development only.
No network access. No real student/teacher data. Not production authority.
Must never be imported by production runtime entrypoints.
"""

from __future__ import annotations

from uuid import UUID

from aieos.domains.teaching.application.school_context import (
    AssignableClassRef,
    SchoolContextClassAuthorityService,
)

_SYNTHETIC_CLASSES: tuple[AssignableClassRef, ...] = (
    AssignableClassRef(class_ref="class-5a", display_label="Grade 5A"),
    AssignableClassRef(class_ref="class-5b", display_label="Grade 5B"),
)


class DevelopmentSchoolContextClassReader:
    """Tenant- and principal-scoped NON_PRODUCTION ClassRef reader.

    Synthetic authority is bound to the exact configured tenant + teacher
    principal. Not ERP/SIS authority.
    """

    def __init__(self, *, tenant_id: UUID, teacher_principal_id: UUID) -> None:
        self._tenant_id = tenant_id
        self._teacher_principal_id = teacher_principal_id
        self.call_count = 0
        self.calls: list[tuple[UUID, UUID]] = []

    def list_assignable_classes(
        self,
        tenant_id: UUID,
        teacher_principal_id: UUID,
    ) -> tuple[AssignableClassRef, ...]:
        self.call_count += 1
        self.calls.append((tenant_id, teacher_principal_id))
        if (
            tenant_id != self._tenant_id
            or teacher_principal_id != self._teacher_principal_id
        ):
            return ()
        return _SYNTHETIC_CLASSES


def development_class_authority(
    *,
    tenant_id: UUID,
    teacher_principal_id: UUID,
) -> SchoolContextClassAuthorityService:
    """NON_PRODUCTION ClassRef authority for TeachingAssignment CREATE."""
    return SchoolContextClassAuthorityService(
        DevelopmentSchoolContextClassReader(
            tenant_id=tenant_id,
            teacher_principal_id=teacher_principal_id,
        )
    )
