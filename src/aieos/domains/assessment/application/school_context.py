"""Current ClassRef authority consumed by ClassroomAssessment commands.

Assessment does not own Class / Roster / Enrollment master data and does not
own a School Context provider. It reuses the Bootstrap School Context authority
already established for Teaching (TOS-DEV06-I01) through a thin adapter that
translates Teaching application errors into the Assessment error vocabulary.

Bootstrap authority answers exactly one question: "is this ClassRef currently
assignable to this teacher". Assessment consumes that answer as its
assessable-class gate for RECORD / CORRECT / VOID. Current assignability is
NOT permanently equivalent to assessability: a dedicated
"currently assessable class" authority may replace this adapter without
changing the Assessment application services, and historical GET / LIST reads
deliberately do not consult it at all.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from aieos.domains.assessment.application.errors import (
    ClassRefNotAssignable,
    SchoolContextContractError,
    SchoolContextUnavailable,
)
from aieos.domains.teaching.application.errors import (
    ClassRefNotAssignable as TeachingClassRefNotAssignable,
)
from aieos.domains.teaching.application.errors import (
    SchoolContextContractError as TeachingSchoolContextContractError,
)
from aieos.domains.teaching.application.errors import (
    SchoolContextUnavailable as TeachingSchoolContextUnavailable,
)
from aieos.domains.teaching.application.school_context import (
    AssignableClassRef,
    SchoolContextClassAuthority,
)


class AssessmentClassAuthority(Protocol):
    """Assessment-owned current ClassRef authority contract."""

    def require_assessable_class_ref(
        self,
        tenant_id: UUID,
        teacher_principal_id: UUID,
        class_ref: str,
    ) -> AssignableClassRef: ...


class SchoolContextAssessmentClassAuthority:
    """Adapts the Bootstrap School Context authority to Assessment errors."""

    def __init__(self, authority: SchoolContextClassAuthority) -> None:
        self._authority = authority

    def require_assessable_class_ref(
        self,
        tenant_id: UUID,
        teacher_principal_id: UUID,
        class_ref: str,
    ) -> AssignableClassRef:
        try:
            return self._authority.require_assignable_class_ref(
                tenant_id, teacher_principal_id, class_ref
            )
        except TeachingClassRefNotAssignable as exc:
            raise ClassRefNotAssignable(
                "requested ClassRef is not currently assessable for this teacher"
            ) from exc
        except TeachingSchoolContextContractError as exc:
            raise SchoolContextContractError(
                "School Context provider returned an invalid response"
            ) from exc
        except TeachingSchoolContextUnavailable as exc:
            raise SchoolContextUnavailable(
                "School Context is temporarily unavailable"
            ) from exc


__all__ = [
    "AssessmentClassAuthority",
    "AssignableClassRef",
    "SchoolContextAssessmentClassAuthority",
]
