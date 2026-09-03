"""Assessment persistence ports. Infrastructure types are not part of this contract."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from aieos.domains.assessment.domain.classroom_assessment import ClassroomAssessment
from aieos.domains.assessment.domain.identities import AggregateRevision, AssessmentId


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


class AssessmentUnitOfWork(Protocol):
    classroom_assessments: ClassroomAssessmentRepository

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
