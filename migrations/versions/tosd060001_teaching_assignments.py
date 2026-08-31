"""TOS-DEV06-I02 TeachingAssignment PostgreSQL schema.

Creates teaching.assignments — teacher-owned classroom assignment intent SoR.

Deliberately absent:
  * Class / Roster / Enrollment master tables
  * business uniqueness over teacher/content/class/due
  * assignment HTTP / application command / outbox / audit execution

Revision ID: tosd060001
Revises: tosd040001
Create Date: 2026-08-31
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "tosd060001"
down_revision: str | None = "tosd040001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE teaching.assignments (
        assignment_id UUID NOT NULL,
        tenant_id UUID NOT NULL,
        teacher_principal_id UUID NOT NULL,
        content_id UUID NOT NULL,
        content_version_id UUID NOT NULL,
        audience_type TEXT NOT NULL,
        class_ref TEXT NOT NULL,
        audience_display_label TEXT NULL,
        source_work_id UUID NULL,
        lifecycle_state TEXT NOT NULL,
        assigned_at TIMESTAMPTZ NOT NULL,
        available_from TIMESTAMPTZ NOT NULL,
        due_at TIMESTAMPTZ NULL,
        closed_at TIMESTAMPTZ NULL,
        cancelled_at TIMESTAMPTZ NULL,
        aggregate_revision BIGINT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT pk_teaching_assignments PRIMARY KEY (assignment_id),
        CONSTRAINT uq_teaching_assignments_tenant_assignment
            UNIQUE (tenant_id, assignment_id),
        CONSTRAINT ck_teaching_assignments_aggregate_revision_nonnegative
            CHECK (aggregate_revision >= 0),
        CONSTRAINT ck_teaching_assignments_audience_type
            CHECK (audience_type = 'class'),
        CONSTRAINT ck_teaching_assignments_class_ref_nonempty
            CHECK (btrim(class_ref) <> ''),
        CONSTRAINT ck_teaching_assignments_audience_display_label_nonempty
            CHECK (
                audience_display_label IS NULL
                OR btrim(audience_display_label) <> ''
            ),
        CONSTRAINT ck_teaching_assignments_lifecycle_state
            CHECK (lifecycle_state IN ('ACTIVE', 'CLOSED', 'CANCELLED')),
        CONSTRAINT ck_teaching_assignments_lifecycle_timestamps
            CHECK (
                (
                    lifecycle_state = 'ACTIVE'
                    AND closed_at IS NULL
                    AND cancelled_at IS NULL
                )
                OR (
                    lifecycle_state = 'CLOSED'
                    AND closed_at IS NOT NULL
                    AND cancelled_at IS NULL
                )
                OR (
                    lifecycle_state = 'CANCELLED'
                    AND cancelled_at IS NOT NULL
                    AND closed_at IS NULL
                )
            ),
        CONSTRAINT ck_teaching_assignments_updated_after_created
            CHECK (updated_at >= created_at),
        CONSTRAINT fk_teaching_assignments_content_version
            FOREIGN KEY (tenant_id, content_id, content_version_id)
            REFERENCES content.content_versions (tenant_id, content_id, version_id)
            ON DELETE RESTRICT,
        CONSTRAINT fk_teaching_assignments_source_work
            FOREIGN KEY (tenant_id, source_work_id)
            REFERENCES teaching.works (tenant_id, work_id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX ix_teaching_assignments_tenant_teacher
        ON teaching.assignments (tenant_id, teacher_principal_id)
    """,
    """
    CREATE INDEX ix_teaching_assignments_tenant_teacher_lifecycle
        ON teaching.assignments (tenant_id, teacher_principal_id, lifecycle_state)
    """,
    """
    CREATE INDEX ix_teaching_assignments_tenant_class_ref
        ON teaching.assignments (tenant_id, class_ref)
    """,
    """
    CREATE INDEX ix_teaching_assignments_tenant_content_version
        ON teaching.assignments (tenant_id, content_id, content_version_id)
    """,
    "ALTER TABLE teaching.assignments ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE teaching.assignments FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY teaching_assignments_tenant_isolation ON teaching.assignments
        FOR ALL
        USING (tenant_id = teaching.current_tenant_id())
        WITH CHECK (tenant_id = teaching.current_tenant_id())
    """,
)

DOWNGRADE_STATEMENTS: tuple[str, ...] = (
    "DROP POLICY IF EXISTS teaching_assignments_tenant_isolation ON teaching.assignments",
    "DROP TABLE IF EXISTS teaching.assignments",
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
