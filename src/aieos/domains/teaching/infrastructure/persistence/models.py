"""SQLAlchemy 2.0 table mapping for teaching.works.

Persistence representation only; the TeachingWork aggregate remains the domain
authority. There is deliberately no teaching_intents table: a Teaching Intent
is the inbound request that produces a Work, not a durable aggregate. There is
also no mission table: Today's Mission is derived on read.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    PrimaryKeyConstraint,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

from aieos.domains.teaching.infrastructure.persistence.metadata import teaching_metadata

works_table = Table(
    "works",
    teaching_metadata,
    Column("work_id", UUID(as_uuid=True), nullable=False),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("teacher_principal_id", UUID(as_uuid=True), nullable=False),
    Column("intent_type", Text, nullable=False),
    Column("goal_text", Text, nullable=False),
    # class_label is contextual teacher-entered text (e.g. "Grade 5B").
    # It is NOT a foreign key into any Class System of Record.
    Column("class_label", Text, nullable=True),
    Column("subject", Text, nullable=True),
    Column("topic", Text, nullable=True),
    Column("target_date", Date, nullable=False),
    Column("locale", Text, nullable=False),
    Column("aggregate_revision", BigInteger, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("archived_at", DateTime(timezone=True), nullable=True),
    PrimaryKeyConstraint("work_id", name="pk_teaching_works"),
    UniqueConstraint("tenant_id", "work_id", name="uq_teaching_works_tenant_work"),
    CheckConstraint(
        "aggregate_revision >= 0",
        name="ck_teaching_works_aggregate_revision_nonnegative",
    ),
    # Extend this list in a new migration whenever IntentType gains a member.
    CheckConstraint(
        "intent_type IN ('prepare_tomorrow', 'remediate_class')",
        name="ck_teaching_works_intent_type",
    ),
    CheckConstraint("btrim(goal_text) <> ''", name="ck_teaching_works_goal_text_nonempty"),
    CheckConstraint("btrim(locale) <> ''", name="ck_teaching_works_locale_nonempty"),
    CheckConstraint(
        "class_label IS NULL OR btrim(class_label) <> ''",
        name="ck_teaching_works_class_label_nonempty",
    ),
    CheckConstraint(
        "subject IS NULL OR btrim(subject) <> ''",
        name="ck_teaching_works_subject_nonempty",
    ),
    CheckConstraint(
        "topic IS NULL OR btrim(topic) <> ''",
        name="ck_teaching_works_topic_nonempty",
    ),
    CheckConstraint(
        "updated_at >= created_at",
        name="ck_teaching_works_updated_after_created",
    ),
    Index("ix_teaching_works_tenant_id", "tenant_id"),
    Index("ix_teaching_works_tenant_teacher", "tenant_id", "teacher_principal_id"),
    Index(
        "ix_teaching_works_tenant_teacher_target_date",
        "tenant_id",
        "teacher_principal_id",
        "target_date",
    ),
    Index("ix_teaching_works_tenant_archived_at", "tenant_id", "archived_at"),
    schema="teaching",
)


assignments_table = Table(
    "assignments",
    teaching_metadata,
    Column("assignment_id", UUID(as_uuid=True), nullable=False),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("teacher_principal_id", UUID(as_uuid=True), nullable=False),
    Column("content_id", UUID(as_uuid=True), nullable=False),
    Column("content_version_id", UUID(as_uuid=True), nullable=False),
    Column("audience_type", Text, nullable=False),
    # Opaque School Context ClassRef. NOT a Class SoR foreign key.
    Column("class_ref", Text, nullable=False),
    Column("audience_display_label", Text, nullable=True),
    Column("source_work_id", UUID(as_uuid=True), nullable=True),
    Column("lifecycle_state", Text, nullable=False),
    Column("assigned_at", DateTime(timezone=True), nullable=False),
    Column("available_from", DateTime(timezone=True), nullable=False),
    Column("due_at", DateTime(timezone=True), nullable=True),
    Column("closed_at", DateTime(timezone=True), nullable=True),
    Column("cancelled_at", DateTime(timezone=True), nullable=True),
    Column("aggregate_revision", BigInteger, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("assignment_id", name="pk_teaching_assignments"),
    UniqueConstraint(
        "tenant_id",
        "assignment_id",
        name="uq_teaching_assignments_tenant_assignment",
    ),
    CheckConstraint(
        "aggregate_revision >= 0",
        name="ck_teaching_assignments_aggregate_revision_nonnegative",
    ),
    CheckConstraint(
        "audience_type = 'class'",
        name="ck_teaching_assignments_audience_type",
    ),
    CheckConstraint(
        "btrim(class_ref) <> ''",
        name="ck_teaching_assignments_class_ref_nonempty",
    ),
    CheckConstraint(
        "audience_display_label IS NULL OR btrim(audience_display_label) <> ''",
        name="ck_teaching_assignments_audience_display_label_nonempty",
    ),
    CheckConstraint(
        "lifecycle_state IN ('ACTIVE', 'CLOSED', 'CANCELLED')",
        name="ck_teaching_assignments_lifecycle_state",
    ),
    CheckConstraint(
        "("
        "lifecycle_state = 'ACTIVE' AND closed_at IS NULL AND cancelled_at IS NULL"
        ") OR ("
        "lifecycle_state = 'CLOSED' AND closed_at IS NOT NULL AND cancelled_at IS NULL"
        ") OR ("
        "lifecycle_state = 'CANCELLED' AND cancelled_at IS NOT NULL AND closed_at IS NULL"
        ")",
        name="ck_teaching_assignments_lifecycle_timestamps",
    ),
    CheckConstraint(
        "updated_at >= created_at",
        name="ck_teaching_assignments_updated_after_created",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "content_id", "content_version_id"],
        [
            "content.content_versions.tenant_id",
            "content.content_versions.content_id",
            "content.content_versions.version_id",
        ],
        name="fk_teaching_assignments_content_version",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "source_work_id"],
        ["teaching.works.tenant_id", "teaching.works.work_id"],
        name="fk_teaching_assignments_source_work",
        ondelete="RESTRICT",
    ),
    Index(
        "ix_teaching_assignments_tenant_teacher",
        "tenant_id",
        "teacher_principal_id",
    ),
    Index(
        "ix_teaching_assignments_tenant_teacher_lifecycle",
        "tenant_id",
        "teacher_principal_id",
        "lifecycle_state",
    ),
    Index("ix_teaching_assignments_tenant_class_ref", "tenant_id", "class_ref"),
    Index(
        "ix_teaching_assignments_tenant_content_version",
        "tenant_id",
        "content_id",
        "content_version_id",
    ),
    schema="teaching",
)


executions_table = Table(
    "executions",
    teaching_metadata,
    Column("execution_id", UUID(as_uuid=True), nullable=False),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("teacher_principal_id", UUID(as_uuid=True), nullable=False),
    Column("work_id", UUID(as_uuid=True), nullable=False),
    # Opaque School Context ClassRef. NOT a Class SoR foreign key.
    Column("class_ref", Text, nullable=False),
    Column("lifecycle_state", Text, nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("cancelled_at", DateTime(timezone=True), nullable=True),
    Column("aggregate_revision", BigInteger, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("execution_id", name="pk_teaching_executions"),
    UniqueConstraint(
        "tenant_id",
        "execution_id",
        name="uq_teaching_executions_tenant_execution",
    ),
    CheckConstraint(
        "aggregate_revision >= 0",
        name="ck_teaching_executions_aggregate_revision_nonnegative",
    ),
    CheckConstraint(
        "btrim(class_ref) <> ''",
        name="ck_teaching_executions_class_ref_nonempty",
    ),
    CheckConstraint(
        "lifecycle_state IN ('IN_PROGRESS', 'COMPLETED', 'CANCELLED')",
        name="ck_teaching_executions_lifecycle_state",
    ),
    CheckConstraint(
        "("
        "lifecycle_state = 'IN_PROGRESS' "
        "AND completed_at IS NULL AND cancelled_at IS NULL"
        ") OR ("
        "lifecycle_state = 'COMPLETED' "
        "AND completed_at IS NOT NULL AND cancelled_at IS NULL"
        ") OR ("
        "lifecycle_state = 'CANCELLED' "
        "AND cancelled_at IS NOT NULL AND completed_at IS NULL"
        ")",
        name="ck_teaching_executions_lifecycle_timestamps",
    ),
    CheckConstraint(
        "updated_at >= created_at",
        name="ck_teaching_executions_updated_after_created",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "work_id"],
        ["teaching.works.tenant_id", "teaching.works.work_id"],
        name="fk_teaching_executions_work",
        ondelete="RESTRICT",
    ),
    Index(
        "ix_teaching_executions_tenant_teacher",
        "tenant_id",
        "teacher_principal_id",
    ),
    Index(
        "ix_teaching_executions_tenant_teacher_lifecycle",
        "tenant_id",
        "teacher_principal_id",
        "lifecycle_state",
    ),
    Index("ix_teaching_executions_tenant_work", "tenant_id", "work_id"),
    Index("ix_teaching_executions_tenant_class_ref", "tenant_id", "class_ref"),
    schema="teaching",
)


execution_content_bindings_table = Table(
    "execution_content_bindings",
    teaching_metadata,
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("execution_id", UUID(as_uuid=True), nullable=False),
    Column("content_id", UUID(as_uuid=True), nullable=False),
    Column("content_version_id", UUID(as_uuid=True), nullable=False),
    Column("artifact_kind", Text, nullable=False),
    PrimaryKeyConstraint(
        "tenant_id",
        "execution_id",
        "content_id",
        "content_version_id",
        name="pk_teaching_execution_content_bindings",
    ),
    CheckConstraint(
        "btrim(artifact_kind) <> ''",
        name="ck_teaching_execution_content_bindings_artifact_kind_nonempty",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "execution_id"],
        ["teaching.executions.tenant_id", "teaching.executions.execution_id"],
        name="fk_teaching_execution_content_bindings_execution",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "content_id", "content_version_id"],
        [
            "content.content_versions.tenant_id",
            "content.content_versions.content_id",
            "content.content_versions.version_id",
        ],
        name="fk_teaching_execution_content_bindings_content_version",
        ondelete="RESTRICT",
    ),
    Index(
        "ix_teaching_execution_content_bindings_tenant_execution",
        "tenant_id",
        "execution_id",
    ),
    schema="teaching",
)


execution_observations_table = Table(
    "execution_observations",
    teaching_metadata,
    Column("observation_id", UUID(as_uuid=True), nullable=False),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("execution_id", UUID(as_uuid=True), nullable=False),
    Column("observation_kind", Text, nullable=False),
    Column("body", Text, nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("revision", BigInteger, nullable=False),
    PrimaryKeyConstraint(
        "observation_id", name="pk_teaching_execution_observations"
    ),
    UniqueConstraint(
        "tenant_id",
        "observation_id",
        name="uq_teaching_execution_observations_tenant_observation",
    ),
    CheckConstraint(
        "observation_kind IN ('PRIVATE_EXECUTION_NOTE', 'CLASS_OBSERVATION')",
        name="ck_teaching_execution_observations_kind",
    ),
    CheckConstraint(
        "btrim(body) <> ''",
        name="ck_teaching_execution_observations_body_nonempty",
    ),
    CheckConstraint(
        "revision >= 0",
        name="ck_teaching_execution_observations_revision_nonnegative",
    ),
    CheckConstraint(
        "updated_at >= recorded_at",
        name="ck_teaching_execution_observations_updated_after_recorded",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "execution_id"],
        ["teaching.executions.tenant_id", "teaching.executions.execution_id"],
        name="fk_teaching_execution_observations_execution",
        ondelete="RESTRICT",
    ),
    Index(
        "ix_teaching_execution_observations_tenant_execution",
        "tenant_id",
        "execution_id",
    ),
    schema="teaching",
)


work_remediation_origins_table = Table(
    "work_remediation_origins",
    teaching_metadata,
    Column("work_id", UUID(as_uuid=True), nullable=False),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("source_assessment_id", UUID(as_uuid=True), nullable=False),
    Column("source_assessment_aggregate_revision", BigInteger, nullable=False),
    Column("source_class_result_level_snapshot", Text, nullable=False),
    # Opaque School Context ClassRef at Improve initiation. NOT a Class SoR FK.
    Column("source_class_ref", Text, nullable=False),
    # Opaque Content / Assessment composition identities — no cross-domain FK.
    Column("source_content_id", UUID(as_uuid=True), nullable=False),
    Column("source_content_version_id", UUID(as_uuid=True), nullable=False),
    Column("source_work_id", UUID(as_uuid=True), nullable=True),
    Column("source_execution_id", UUID(as_uuid=True), nullable=True),
    Column("source_assignment_id", UUID(as_uuid=True), nullable=True),
    Column("initiating_teacher_principal_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("work_id", name="pk_teaching_work_remediation_origins"),
    UniqueConstraint(
        "tenant_id",
        "work_id",
        name="uq_teaching_work_remediation_origins_tenant_work",
    ),
    CheckConstraint(
        "source_assessment_aggregate_revision >= 0",
        name="ck_teaching_work_remediation_origins_assessment_revision_nonnegative",
    ),
    CheckConstraint(
        "source_class_result_level_snapshot IN "
        "('DEMONSTRATED', 'MIXED', 'NOT_YET_DEMONSTRATED')",
        name="ck_teaching_work_remediation_origins_class_result_level_snapshot",
    ),
    CheckConstraint(
        "btrim(source_class_ref) <> ''",
        name="ck_teaching_work_remediation_origins_class_ref_nonempty",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "work_id"],
        ["teaching.works.tenant_id", "teaching.works.work_id"],
        name="fk_teaching_work_remediation_origins_work",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "source_work_id"],
        ["teaching.works.tenant_id", "teaching.works.work_id"],
        name="fk_teaching_work_remediation_origins_source_work",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "source_execution_id"],
        ["teaching.executions.tenant_id", "teaching.executions.execution_id"],
        name="fk_teaching_work_remediation_origins_source_execution",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "source_assignment_id"],
        ["teaching.assignments.tenant_id", "teaching.assignments.assignment_id"],
        name="fk_teaching_work_remediation_origins_source_assignment",
        ondelete="RESTRICT",
    ),
    Index(
        "ix_teaching_work_remediation_origins_tenant_source_assessment",
        "tenant_id",
        "source_assessment_id",
    ),
    schema="teaching",
)
