"""Assessment persistence and application ports.

Infrastructure types are not part of these contracts.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from aieos.domains.assessment.domain.classroom_assessment import ClassroomAssessment
from aieos.domains.assessment.domain.identities import AggregateRevision, AssessmentId
from aieos.domains.assessment.domain.lifecycle import AssessmentLifecycleState
from aieos.platform.idempotency.ports import IdempotencyRepository
from aieos.platform.security.audit.models import SecurityMutationAuditRecord

# Exact ADR-AIEOS-031 / ADR-AIEOS-055 Assessment capability vocabulary.
# Protected operations compose: current tenant access + exact capability ALLOW
# + ownership (resource-specific) + current ClassRef (mutations) + Case A/B/C
# (fresh RECORD). Capability ALLOW does not replace ClassRef or ownership.
ASSESSMENT_CLASSROOM_RECORD = "assessment.classroom.record"
ASSESSMENT_CLASSROOM_CORRECT = "assessment.classroom.correct"
ASSESSMENT_CLASSROOM_VOID = "assessment.classroom.void"
ASSESSMENT_CLASSROOM_READ = "assessment.classroom.read"
ASSESSMENT_CLASSROOM_LIST = "assessment.classroom.list"

AIEOS_ASSESSMENT_CAPABILITIES = frozenset(
    {
        ASSESSMENT_CLASSROOM_RECORD,
        ASSESSMENT_CLASSROOM_CORRECT,
        ASSESSMENT_CLASSROOM_VOID,
        ASSESSMENT_CLASSROOM_READ,
        ASSESSMENT_CLASSROOM_LIST,
    }
)


class ClassroomAssessmentAuthorization(Protocol):
    """Technology-neutral current Assessment capability authorization port."""

    def authorize(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        capability: str,
    ) -> None: ...


class ClassroomAssessmentRepository(Protocol):
    """Durable persistence for the teacher-owned ClassroomAssessment aggregate."""

    def insert(self, assessment: ClassroomAssessment) -> None: ...

    def get(self, assessment_id: AssessmentId) -> ClassroomAssessment | None: ...

    def get_for_update(
        self, assessment_id: AssessmentId
    ) -> ClassroomAssessment | None: ...

    def update(
        self,
        assessment: ClassroomAssessment,
        *,
        expected_revision: AggregateRevision,
    ) -> bool: ...

    def list_for_teacher(
        self,
        teacher_principal_id: UUID,
        *,
        class_ref: str | None = None,
        work_id: UUID | None = None,
        execution_id: UUID | None = None,
        assignment_id: UUID | None = None,
        lifecycle_state: AssessmentLifecycleState | str | None = None,
        limit: int = 50,
    ) -> list[ClassroomAssessment]: ...


class SecurityMutationAuditRepository(Protocol):
    def insert(self, record: SecurityMutationAuditRecord) -> None: ...


class AssessmentContentAuthorityPort(Protocol):
    """Case C: race-safe current published Assessment-eligible ContentVersion."""

    def verify_current_published_assessment_content(
        self,
        *,
        content_id: UUID,
        content_version_id: UUID,
    ) -> str: ...


class AssessmentTeachingCompositionPort(Protocol):
    """Read-only Teaching composition for Cases A/B and optional work_id."""

    def load_completed_execution(
        self,
        *,
        execution_id: UUID,
        teacher_principal_id: UUID,
        class_ref: str,
        content_id: UUID,
        content_version_id: UUID,
        work_id: UUID | None,
    ) -> UUID: ...

    def load_class_assignment(
        self,
        *,
        assignment_id: UUID,
        teacher_principal_id: UUID,
        class_ref: str,
        content_id: UUID,
        content_version_id: UUID,
        work_id: UUID | None,
    ) -> UUID | None: ...

    def require_owned_work(
        self,
        *,
        work_id: UUID,
        teacher_principal_id: UUID,
    ) -> None: ...


class AssessmentUnitOfWork(Protocol):
    classroom_assessments: ClassroomAssessmentRepository
    idempotency: IdempotencyRepository
    audit: SecurityMutationAuditRepository
    content_authority: AssessmentContentAuthorityPort
    teaching_composition: AssessmentTeachingCompositionPort

    def __enter__(self) -> AssessmentUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class AssessmentUnitOfWorkFactory(Protocol):
    def __call__(self, execution_tenant_id: UUID) -> AssessmentUnitOfWork: ...
