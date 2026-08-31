"""School Context ClassRef current-authority read (TOS-DEV06-I01).

Teaching consumes opaque ClassRef values from an external School Context
port. Teaching does not own Class / Roster / Enrollment master data.
This read is advisory UX assistance only — future TeachingAssignment CREATE
must revalidate current ClassRef authority separately.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from aieos.domains.teaching.application.errors import (
    ClassRefNotAssignable,
    SchoolContextContractError,
    SchoolContextUnavailable,
)


@dataclass(frozen=True, slots=True)
class AssignableClassRef:
    """Opaque assignable class target for Teacher OS Assign UX assistance."""

    class_ref: str
    display_label: str


class SchoolContextClassReader(Protocol):
    """Replaceable School Context port. Returns CURRENTLY assignable classes."""

    def list_assignable_classes(
        self,
        tenant_id: UUID,
        teacher_principal_id: UUID,
    ) -> Sequence[AssignableClassRef]: ...


class ListAssignableSchoolClassesService:
    """Read-only application service. No UoW, events, or mutation audit."""

    def __init__(self, reader: SchoolContextClassReader) -> None:
        self._reader = reader

    def list(
        self,
        tenant_id: UUID,
        teacher_principal_id: UUID,
    ) -> tuple[AssignableClassRef, ...]:
        try:
            raw = self._reader.list_assignable_classes(
                tenant_id, teacher_principal_id
            )
        except SchoolContextUnavailable:
            raise
        except SchoolContextContractError:
            raise
        except Exception as exc:
            raise SchoolContextUnavailable(
                "School Context is temporarily unavailable"
            ) from exc

        return _validate_provider_items(raw)


def _validate_provider_items(
    raw: object,
) -> tuple[AssignableClassRef, ...]:
    if raw is None:
        raise SchoolContextContractError(
            "School Context provider returned an invalid response"
        )
    try:
        items = list(raw)  # type: ignore[arg-type]
    except TypeError as exc:
        raise SchoolContextContractError(
            "School Context provider returned an invalid response"
        ) from exc

    validated: list[AssignableClassRef] = []
    seen: set[str] = set()
    for item in items:
        class_ref, display_label = _extract_fields(item)
        if not class_ref.strip():
            raise SchoolContextContractError(
                "School Context provider returned a blank ClassRef"
            )
        if not display_label.strip():
            raise SchoolContextContractError(
                "School Context provider returned a blank display label"
            )
        if class_ref in seen:
            raise SchoolContextContractError(
                "School Context provider returned a duplicate ClassRef"
            )
        seen.add(class_ref)
        validated.append(
            AssignableClassRef(class_ref=class_ref, display_label=display_label)
        )
    return tuple(validated)


class SchoolContextClassAuthority(Protocol):
    """Current ClassRef authority for TeachingAssignment CREATE."""

    def require_assignable_class_ref(
        self,
        tenant_id: UUID,
        teacher_principal_id: UUID,
        class_ref: str,
    ) -> AssignableClassRef: ...


class SchoolContextClassAuthorityService:
    """Revalidates current ClassRef authority via the School Context port."""

    def __init__(self, reader: SchoolContextClassReader) -> None:
        self._reader = reader

    def require_assignable_class_ref(
        self,
        tenant_id: UUID,
        teacher_principal_id: UUID,
        class_ref: str,
    ) -> AssignableClassRef:
        items = ListAssignableSchoolClassesService(self._reader).list(
            tenant_id, teacher_principal_id
        )
        normalized = class_ref.strip()
        for item in items:
            if item.class_ref == normalized:
                return item
        raise ClassRefNotAssignable(
            "requested ClassRef is not currently assignable for this teacher"
        )


def _extract_fields(item: object) -> tuple[str, str]:
    if isinstance(item, AssignableClassRef):
        return item.class_ref, item.display_label
    class_ref = getattr(item, "class_ref", None)
    display_label = getattr(item, "display_label", None)
    if not isinstance(class_ref, str) or not isinstance(display_label, str):
        raise SchoolContextContractError(
            "School Context provider returned a structurally invalid class item"
        )
    return class_ref, display_label
