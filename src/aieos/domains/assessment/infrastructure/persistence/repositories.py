"""SQLAlchemy Core Assessment repositories. They never commit or rollback."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.engine import Connection

from aieos.domains.assessment.application.errors import PersistenceInvariantViolation
from aieos.domains.assessment.domain.classroom_assessment import ClassroomAssessment
from aieos.domains.assessment.domain.identities import AggregateRevision, AssessmentId
from aieos.domains.assessment.infrastructure.persistence.errors import (
    reraise_as_application_error,
)
from aieos.domains.assessment.infrastructure.persistence.models import (
    classroom_assessments_table,
)


def classroom_assessment_from_row(row) -> ClassroomAssessment:
    try:
        return ClassroomAssessment(
            assessment_id=AssessmentId(row["assessment_id"]),
            tenant_id=row["tenant_id"],
            teacher_principal_id=row["teacher_principal_id"],
            class_ref=row["class_ref"],
            content_id=row["content_id"],
            content_version_id=row["content_version_id"],
            class_result_level=row["class_result_level"],
            class_result_note=row["class_result_note"],
            lifecycle_state=row["lifecycle_state"],
            work_id=row["work_id"],
            execution_id=row["execution_id"],
            assignment_id=row["assignment_id"],
            aggregate_revision=AggregateRevision(int(row["aggregate_revision"])),
            recorded_at=row["recorded_at"],
            voided_at=row["voided_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
    except Exception as exc:
        raise PersistenceInvariantViolation(
            "stored ClassroomAssessment row violates the aggregate contract"
        ) from exc


class SqlAlchemyClassroomAssessmentRepository:
    def __init__(self, connection: Connection, execution_tenant_id: UUID) -> None:
        self._connection = connection
        self._execution_tenant_id = execution_tenant_id

    def insert(self, assessment: ClassroomAssessment) -> None:
        try:
            self._connection.execute(
                classroom_assessments_table.insert().values(
                    assessment_id=assessment.assessment_id.value,
                    tenant_id=assessment.tenant_id,
                    teacher_principal_id=assessment.teacher_principal_id,
                    class_ref=assessment.class_ref,
                    content_id=assessment.content_id,
                    content_version_id=assessment.content_version_id,
                    class_result_level=assessment.class_result_level.value,
                    class_result_note=assessment.class_result_note,
                    lifecycle_state=assessment.lifecycle_state.value,
                    work_id=assessment.work_id,
                    execution_id=assessment.execution_id,
                    assignment_id=assessment.assignment_id,
                    aggregate_revision=int(assessment.aggregate_revision),
                    recorded_at=assessment.recorded_at,
                    voided_at=assessment.voided_at,
                    created_at=assessment.created_at,
                    updated_at=assessment.updated_at,
                )
            )
        except Exception as exc:
            reraise_as_application_error(exc)

    def get(self, assessment_id: AssessmentId) -> ClassroomAssessment | None:
        try:
            row = (
                self._connection.execute(
                    select(classroom_assessments_table).where(
                        classroom_assessments_table.c.assessment_id
                        == assessment_id.value,
                        classroom_assessments_table.c.tenant_id
                        == self._execution_tenant_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
        except Exception as exc:
            reraise_as_application_error(exc)
        if row is None:
            return None
        return classroom_assessment_from_row(row)

    def get_for_update(
        self, assessment_id: AssessmentId
    ) -> ClassroomAssessment | None:
        try:
            row = (
                self._connection.execute(
                    select(classroom_assessments_table)
                    .where(
                        classroom_assessments_table.c.assessment_id
                        == assessment_id.value,
                        classroom_assessments_table.c.tenant_id
                        == self._execution_tenant_id,
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
        except Exception as exc:
            reraise_as_application_error(exc)
        if row is None:
            return None
        return classroom_assessment_from_row(row)

    def update(
        self,
        assessment: ClassroomAssessment,
        *,
        expected_revision: AggregateRevision,
    ) -> bool:
        """Compare-and-set mutable assessment state. False means a lost race.

        Immutable RECORD fields are never rewritten.
        """
        try:
            result = self._connection.execute(
                update(classroom_assessments_table)
                .where(
                    classroom_assessments_table.c.assessment_id
                    == assessment.assessment_id.value,
                    classroom_assessments_table.c.tenant_id
                    == self._execution_tenant_id,
                    classroom_assessments_table.c.aggregate_revision
                    == int(expected_revision),
                )
                .values(
                    class_result_level=assessment.class_result_level.value,
                    class_result_note=assessment.class_result_note,
                    lifecycle_state=assessment.lifecycle_state.value,
                    voided_at=assessment.voided_at,
                    aggregate_revision=int(assessment.aggregate_revision),
                    updated_at=assessment.updated_at,
                )
            )
        except Exception as exc:
            reraise_as_application_error(exc)
        return result.rowcount == 1
