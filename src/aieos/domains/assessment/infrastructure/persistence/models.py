"""SQLAlchemy 2.0 table mapping for assessment.classroom_assessments.

Persistence representation only; the ClassroomAssessment aggregate remains the
domain authority. Composition references are stored without cross-domain
PostgreSQL foreign keys.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    PrimaryKeyConstraint,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

from aieos.domains.assessment.infrastructure.persistence.metadata import (
    assessment_metadata,
)

classroom_assessments_table = Table(
    "classroom_assessments",
    assessment_metadata,
    Column("assessment_id", UUID(as_uuid=True), nullable=False),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("teacher_principal_id", UUID(as_uuid=True), nullable=False),
    # Opaque School Context ClassRef. NOT a Class SoR foreign key.
    Column("class_ref", Text, nullable=False),
    Column("content_id", UUID(as_uuid=True), nullable=False),
    Column("content_version_id", UUID(as_uuid=True), nullable=False),
    Column("class_result_level", Text, nullable=False),
    Column("class_result_note", Text, nullable=True),
    Column("lifecycle_state", Text, nullable=False),
    Column("work_id", UUID(as_uuid=True), nullable=True),
    Column("execution_id", UUID(as_uuid=True), nullable=True),
    Column("assignment_id", UUID(as_uuid=True), nullable=True),
    Column("aggregate_revision", BigInteger, nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    Column("voided_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint(
        "assessment_id", name="pk_assessment_classroom_assessments"
    ),
    UniqueConstraint(
        "tenant_id",
        "assessment_id",
        name="uq_assessment_classroom_assessments_tenant_assessment",
    ),
    CheckConstraint(
        "aggregate_revision >= 0",
        name="ck_assessment_classroom_assessments_aggregate_revision_nonnegative",
    ),
    CheckConstraint(
        "btrim(class_ref) <> ''",
        name="ck_assessment_classroom_assessments_class_ref_nonempty",
    ),
    CheckConstraint(
        "class_result_level IN ('DEMONSTRATED', 'MIXED', 'NOT_YET_DEMONSTRATED')",
        name="ck_assessment_classroom_assessments_class_result_level",
    ),
    CheckConstraint(
        "class_result_note IS NULL OR char_length(class_result_note) <= 4096",
        name="ck_assessment_classroom_assessments_class_result_note_length",
    ),
    CheckConstraint(
        "lifecycle_state IN ('RECORDED', 'VOIDED')",
        name="ck_assessment_classroom_assessments_lifecycle_state",
    ),
    CheckConstraint(
        "("
        "lifecycle_state = 'RECORDED' AND voided_at IS NULL"
        ") OR ("
        "lifecycle_state = 'VOIDED' AND voided_at IS NOT NULL"
        ")",
        name="ck_assessment_classroom_assessments_lifecycle_timestamps",
    ),
    CheckConstraint(
        "updated_at >= created_at",
        name="ck_assessment_classroom_assessments_updated_after_created",
    ),
    Index(
        "ix_assessment_classroom_assessments_tenant_teacher",
        "tenant_id",
        "teacher_principal_id",
    ),
    Index(
        "ix_assessment_classroom_assessments_tenant_teacher_lifecycle",
        "tenant_id",
        "teacher_principal_id",
        "lifecycle_state",
    ),
    Index(
        "ix_assessment_classroom_assessments_tenant_class_ref",
        "tenant_id",
        "class_ref",
    ),
    Index(
        "ix_assessment_classroom_assessments_tenant_content_version",
        "tenant_id",
        "content_id",
        "content_version_id",
    ),
    Index(
        "ix_assessment_classroom_assessments_tenant_execution",
        "tenant_id",
        "execution_id",
    ),
    Index(
        "ix_assessment_classroom_assessments_tenant_assignment",
        "tenant_id",
        "assignment_id",
    ),
    Index(
        "ix_assessment_classroom_assessments_tenant_work",
        "tenant_id",
        "work_id",
    ),
    schema="assessment",
)
