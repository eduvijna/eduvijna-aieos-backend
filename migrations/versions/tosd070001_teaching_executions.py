"""TOS-DEV07-I01 TeachingExecution PostgreSQL schema.

Creates teaching.executions, teaching.execution_content_bindings, and
teaching.execution_observations — durable classroom execution SoR.

Deliberately absent:
  * PreparationKit aggregate and kit lifecycle fields
  * Class / Roster / Enrollment / timetable / period master tables
  * business uniqueness over teacher/work/class/date
  * execution HTTP / application command / outbox / audit / events
  * learner-specific observation fields

Revision ID: tosd070001
Revises: tosd060002
Create Date: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "tosd070001"
down_revision: str | None = "tosd060002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE teaching.executions (
        execution_id UUID NOT NULL,
        tenant_id UUID NOT NULL,
        teacher_principal_id UUID NOT NULL,
        work_id UUID NOT NULL,
        class_ref TEXT NOT NULL,
        lifecycle_state TEXT NOT NULL,
        started_at TIMESTAMPTZ NOT NULL,
        completed_at TIMESTAMPTZ NULL,
        cancelled_at TIMESTAMPTZ NULL,
        aggregate_revision BIGINT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT pk_teaching_executions PRIMARY KEY (execution_id),
        CONSTRAINT uq_teaching_executions_tenant_execution
            UNIQUE (tenant_id, execution_id),
        CONSTRAINT ck_teaching_executions_aggregate_revision_nonnegative
            CHECK (aggregate_revision >= 0),
        CONSTRAINT ck_teaching_executions_class_ref_nonempty
            CHECK (btrim(class_ref) <> ''),
        CONSTRAINT ck_teaching_executions_lifecycle_state
            CHECK (
                lifecycle_state IN ('IN_PROGRESS', 'COMPLETED', 'CANCELLED')
            ),
        CONSTRAINT ck_teaching_executions_lifecycle_timestamps
            CHECK (
                (
                    lifecycle_state = 'IN_PROGRESS'
                    AND completed_at IS NULL
                    AND cancelled_at IS NULL
                )
                OR (
                    lifecycle_state = 'COMPLETED'
                    AND completed_at IS NOT NULL
                    AND cancelled_at IS NULL
                )
                OR (
                    lifecycle_state = 'CANCELLED'
                    AND cancelled_at IS NOT NULL
                    AND completed_at IS NULL
                )
            ),
        CONSTRAINT ck_teaching_executions_updated_after_created
            CHECK (updated_at >= created_at),
        CONSTRAINT fk_teaching_executions_work
            FOREIGN KEY (tenant_id, work_id)
            REFERENCES teaching.works (tenant_id, work_id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX ix_teaching_executions_tenant_teacher
        ON teaching.executions (tenant_id, teacher_principal_id)
    """,
    """
    CREATE INDEX ix_teaching_executions_tenant_teacher_lifecycle
        ON teaching.executions (
            tenant_id, teacher_principal_id, lifecycle_state
        )
    """,
    """
    CREATE INDEX ix_teaching_executions_tenant_work
        ON teaching.executions (tenant_id, work_id)
    """,
    """
    CREATE INDEX ix_teaching_executions_tenant_class_ref
        ON teaching.executions (tenant_id, class_ref)
    """,
    "ALTER TABLE teaching.executions ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE teaching.executions FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY teaching_executions_tenant_isolation ON teaching.executions
        FOR ALL
        USING (tenant_id = teaching.current_tenant_id())
        WITH CHECK (tenant_id = teaching.current_tenant_id())
    """,
    """
    CREATE TABLE teaching.execution_content_bindings (
        tenant_id UUID NOT NULL,
        execution_id UUID NOT NULL,
        content_id UUID NOT NULL,
        content_version_id UUID NOT NULL,
        artifact_kind TEXT NOT NULL,
        CONSTRAINT pk_teaching_execution_content_bindings
            PRIMARY KEY (
                tenant_id, execution_id, content_id, content_version_id
            ),
        CONSTRAINT ck_teaching_execution_content_bindings_artifact_kind_nonempty
            CHECK (btrim(artifact_kind) <> ''),
        CONSTRAINT fk_teaching_execution_content_bindings_execution
            FOREIGN KEY (tenant_id, execution_id)
            REFERENCES teaching.executions (tenant_id, execution_id)
            ON DELETE RESTRICT,
        CONSTRAINT fk_teaching_execution_content_bindings_content_version
            FOREIGN KEY (tenant_id, content_id, content_version_id)
            REFERENCES content.content_versions (
                tenant_id, content_id, version_id
            )
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX ix_teaching_execution_content_bindings_tenant_execution
        ON teaching.execution_content_bindings (tenant_id, execution_id)
    """,
    "ALTER TABLE teaching.execution_content_bindings ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE teaching.execution_content_bindings FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY teaching_execution_content_bindings_tenant_isolation
        ON teaching.execution_content_bindings
        FOR ALL
        USING (tenant_id = teaching.current_tenant_id())
        WITH CHECK (tenant_id = teaching.current_tenant_id())
    """,
    """
    CREATE TABLE teaching.execution_observations (
        observation_id UUID NOT NULL,
        tenant_id UUID NOT NULL,
        execution_id UUID NOT NULL,
        observation_kind TEXT NOT NULL,
        body TEXT NOT NULL,
        recorded_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        revision BIGINT NOT NULL,
        CONSTRAINT pk_teaching_execution_observations
            PRIMARY KEY (observation_id),
        CONSTRAINT uq_teaching_execution_observations_tenant_observation
            UNIQUE (tenant_id, observation_id),
        CONSTRAINT ck_teaching_execution_observations_kind
            CHECK (
                observation_kind IN (
                    'PRIVATE_EXECUTION_NOTE',
                    'CLASS_OBSERVATION'
                )
            ),
        CONSTRAINT ck_teaching_execution_observations_body_nonempty
            CHECK (btrim(body) <> ''),
        CONSTRAINT ck_teaching_execution_observations_revision_nonnegative
            CHECK (revision >= 0),
        CONSTRAINT ck_teaching_execution_observations_updated_after_recorded
            CHECK (updated_at >= recorded_at),
        CONSTRAINT fk_teaching_execution_observations_execution
            FOREIGN KEY (tenant_id, execution_id)
            REFERENCES teaching.executions (tenant_id, execution_id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX ix_teaching_execution_observations_tenant_execution
        ON teaching.execution_observations (tenant_id, execution_id)
    """,
    "ALTER TABLE teaching.execution_observations ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE teaching.execution_observations FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY teaching_execution_observations_tenant_isolation
        ON teaching.execution_observations
        FOR ALL
        USING (tenant_id = teaching.current_tenant_id())
        WITH CHECK (tenant_id = teaching.current_tenant_id())
    """,
)

DOWNGRADE_STATEMENTS: tuple[str, ...] = (
    """
    DROP POLICY IF EXISTS teaching_execution_observations_tenant_isolation
        ON teaching.execution_observations
    """,
    "DROP TABLE IF EXISTS teaching.execution_observations",
    """
    DROP POLICY IF EXISTS teaching_execution_content_bindings_tenant_isolation
        ON teaching.execution_content_bindings
    """,
    "DROP TABLE IF EXISTS teaching.execution_content_bindings",
    """
    DROP POLICY IF EXISTS teaching_executions_tenant_isolation
        ON teaching.executions
    """,
    "DROP TABLE IF EXISTS teaching.executions",
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
