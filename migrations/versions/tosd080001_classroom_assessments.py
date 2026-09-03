"""TOS-DEV08-I01 ClassroomAssessment PostgreSQL schema.

Creates the assessment schema and assessment.classroom_assessments — durable
class-level Assessment SoR.

Deliberately absent:
  * learner / student / roster / attempt / submission tables
  * cross-domain PostgreSQL REFERENCES to Content or Teaching
  * business uniqueness over teacher/class/content/date
  * Assessment HTTP / application command / outbox / audit / events

Revision ID: tosd080001
Revises: tosd070002
Create Date: 2026-09-03
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "tosd080001"
down_revision: str | None = "tosd070002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DOWNGRADE_BLOCKED = (
    "TOS-DEV08-I01 downgrade refused: ClassroomAssessment evidence exists "
    "and must not be deleted"
)

UPGRADE_STATEMENTS: tuple[str, ...] = (
    "CREATE SCHEMA assessment",
    """
    CREATE TABLE assessment.classroom_assessments (
        assessment_id UUID NOT NULL,
        tenant_id UUID NOT NULL,
        teacher_principal_id UUID NOT NULL,
        class_ref TEXT NOT NULL,
        content_id UUID NOT NULL,
        content_version_id UUID NOT NULL,
        class_result_level TEXT NOT NULL,
        class_result_note TEXT NULL,
        lifecycle_state TEXT NOT NULL,
        work_id UUID NULL,
        execution_id UUID NULL,
        assignment_id UUID NULL,
        aggregate_revision BIGINT NOT NULL,
        recorded_at TIMESTAMPTZ NOT NULL,
        voided_at TIMESTAMPTZ NULL,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT pk_assessment_classroom_assessments
            PRIMARY KEY (assessment_id),
        CONSTRAINT uq_assessment_classroom_assessments_tenant_assessment
            UNIQUE (tenant_id, assessment_id),
        CONSTRAINT ck_assessment_classroom_assessments_aggregate_revision_nonnegative
            CHECK (aggregate_revision >= 0),
        CONSTRAINT ck_assessment_classroom_assessments_class_ref_nonempty
            CHECK (btrim(class_ref) <> ''),
        CONSTRAINT ck_assessment_classroom_assessments_class_result_level
            CHECK (
                class_result_level IN (
                    'DEMONSTRATED',
                    'MIXED',
                    'NOT_YET_DEMONSTRATED'
                )
            ),
        CONSTRAINT ck_assessment_classroom_assessments_class_result_note_length
            CHECK (
                class_result_note IS NULL
                OR char_length(class_result_note) <= 4096
            ),
        CONSTRAINT ck_assessment_classroom_assessments_lifecycle_state
            CHECK (lifecycle_state IN ('RECORDED', 'VOIDED')),
        CONSTRAINT ck_assessment_classroom_assessments_lifecycle_timestamps
            CHECK (
                (
                    lifecycle_state = 'RECORDED'
                    AND voided_at IS NULL
                )
                OR (
                    lifecycle_state = 'VOIDED'
                    AND voided_at IS NOT NULL
                )
            ),
        CONSTRAINT ck_assessment_classroom_assessments_updated_after_created
            CHECK (updated_at >= created_at)
    )
    """,
    """
    CREATE INDEX ix_assessment_classroom_assessments_tenant_teacher
        ON assessment.classroom_assessments (tenant_id, teacher_principal_id)
    """,
    """
    CREATE INDEX ix_assessment_classroom_assessments_tenant_teacher_lifecycle
        ON assessment.classroom_assessments (
            tenant_id, teacher_principal_id, lifecycle_state
        )
    """,
    """
    CREATE INDEX ix_assessment_classroom_assessments_tenant_class_ref
        ON assessment.classroom_assessments (tenant_id, class_ref)
    """,
    """
    CREATE INDEX ix_assessment_classroom_assessments_tenant_content_version
        ON assessment.classroom_assessments (
            tenant_id, content_id, content_version_id
        )
    """,
    """
    CREATE INDEX ix_assessment_classroom_assessments_tenant_execution
        ON assessment.classroom_assessments (tenant_id, execution_id)
    """,
    """
    CREATE INDEX ix_assessment_classroom_assessments_tenant_assignment
        ON assessment.classroom_assessments (tenant_id, assignment_id)
    """,
    """
    CREATE INDEX ix_assessment_classroom_assessments_tenant_work
        ON assessment.classroom_assessments (tenant_id, work_id)
    """,
    """
    CREATE OR REPLACE FUNCTION assessment.current_tenant_id()
    RETURNS uuid
    LANGUAGE plpgsql
    VOLATILE
    SET search_path = assessment, pg_temp
    AS $$
    DECLARE
        raw text;
    BEGIN
        raw := nullif(current_setting('aieos.tenant_id', true), '');
        IF raw IS NULL THEN
            RAISE EXCEPTION 'aieos.tenant_id is not set'
                USING ERRCODE = '42501';
        END IF;
        RETURN raw::uuid;
    END;
    $$
    """,
    "ALTER TABLE assessment.classroom_assessments ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE assessment.classroom_assessments FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY assessment_classroom_assessments_tenant_isolation
        ON assessment.classroom_assessments
        FOR ALL
        USING (tenant_id = assessment.current_tenant_id())
        WITH CHECK (tenant_id = assessment.current_tenant_id())
    """,
)

DOWNGRADE_STATEMENTS: tuple[str, ...] = (
    """
    DROP POLICY IF EXISTS assessment_classroom_assessments_tenant_isolation
        ON assessment.classroom_assessments
    """,
    "DROP TABLE IF EXISTS assessment.classroom_assessments",
    "DROP FUNCTION IF EXISTS assessment.current_tenant_id()",
    "DROP SCHEMA IF EXISTS assessment",
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    table_exists = bind.execute(
        text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'assessment'
                  AND table_name = 'classroom_assessments'
            )
            """
        )
    ).scalar()
    if table_exists:
        op.execute(
            "ALTER TABLE assessment.classroom_assessments DISABLE ROW LEVEL SECURITY"
        )
        blocked = bind.execute(
            text("SELECT EXISTS (SELECT 1 FROM assessment.classroom_assessments)")
        ).scalar()
        if blocked:
            op.execute(
                "ALTER TABLE assessment.classroom_assessments ENABLE ROW LEVEL SECURITY"
            )
            op.execute(
                "ALTER TABLE assessment.classroom_assessments FORCE ROW LEVEL SECURITY"
            )
            raise RuntimeError(_DOWNGRADE_BLOCKED)
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
