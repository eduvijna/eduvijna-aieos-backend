"""Teaching authorization adapters backed by AuthorizationKernel."""

from __future__ import annotations

from uuid import UUID

from aieos.domains.assessment.application.ports import ASSESSMENT_CLASSROOM_READ
from aieos.domains.teaching.application.errors import (
    TeachingWorkCapabilityForbidden,
)
from aieos.domains.teaching.application.ports import TEACHING_WORK_CREATE
from aieos.platform.security.authorization.decisions import AuthorityDecision
from aieos.platform.security.authorization.kernel import (
    AuthorizationKernel,
    capability_contains_wildcard,
)
from aieos.platform.security.context import AuthorizationUnavailableError

AIEOS_REMEDIATION_CREATE_CAPABILITIES = frozenset(
    {TEACHING_WORK_CREATE, ASSESSMENT_CLASSROOM_READ}
)


class KernelTeachingWorkAuthorization:
    """Exact-capability authorization for Assessment-origin Work creation."""

    def __init__(self, kernel: AuthorizationKernel) -> None:
        self._kernel = kernel

    def authorize(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        capability: str,
    ) -> None:
        if (
            capability_contains_wildcard(capability)
            or capability not in AIEOS_REMEDIATION_CREATE_CAPABILITIES
        ):
            raise TeachingWorkCapabilityForbidden("teaching capability denied")
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
        if decision is not AuthorityDecision.ALLOW:
            raise TeachingWorkCapabilityForbidden("teaching capability denied")
