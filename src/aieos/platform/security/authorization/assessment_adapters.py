"""Assessment authorization port adapters backed by AuthorizationKernel.

Assessment capability vocabulary is owned by
``aieos.domains.assessment.application.ports`` — this module composes the
injected catalog from those canonical constants and does not redefine them.
"""

from __future__ import annotations

from uuid import UUID

from aieos.domains.assessment.application.errors import AssessmentCapabilityForbidden
from aieos.domains.assessment.application.ports import (
    ASSESSMENT_CLASSROOM_CORRECT,
    ASSESSMENT_CLASSROOM_LIST,
    ASSESSMENT_CLASSROOM_READ,
    ASSESSMENT_CLASSROOM_RECORD,
    ASSESSMENT_CLASSROOM_VOID,
)
from aieos.platform.security.authorization.decisions import AuthorityDecision
from aieos.platform.security.authorization.kernel import (
    AuthorizationKernel,
    capability_contains_wildcard,
)
from aieos.platform.security.context import AuthorizationUnavailableError

# Code-governed Assessment capability catalog (composition only; not a DB catalog).
AIEOS_ASSESSMENT_CAPABILITIES: frozenset[str] = frozenset(
    {
        ASSESSMENT_CLASSROOM_RECORD,
        ASSESSMENT_CLASSROOM_CORRECT,
        ASSESSMENT_CLASSROOM_VOID,
        ASSESSMENT_CLASSROOM_READ,
        ASSESSMENT_CLASSROOM_LIST,
    }
)


class KernelClassroomAssessmentAuthorization:
    """AuthorizationKernel adapter for ClassroomAssessment protected operations."""

    def __init__(self, kernel: AuthorizationKernel) -> None:
        self._kernel = kernel

    def authorize(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        capability: str,
    ) -> None:
        if capability_contains_wildcard(capability) or capability not in (
            AIEOS_ASSESSMENT_CAPABILITIES
        ):
            raise AssessmentCapabilityForbidden("assessment capability denied")
        try:
            decision = self._kernel.decide_capability(
                principal_id=principal_id,
                tenant_id=tenant_id,
                capability=capability,
            )
        except AuthorizationUnavailableError:
            raise
        except Exception as exc:
            raise AuthorizationUnavailableError(
                "authorization unavailable"
            ) from exc
        if decision is AuthorityDecision.ALLOW:
            return
        raise AssessmentCapabilityForbidden("assessment capability denied")
