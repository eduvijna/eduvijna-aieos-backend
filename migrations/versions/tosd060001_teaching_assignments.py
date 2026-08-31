"""TOS-DEV06-I02/I03 TeachingAssignment schema and Teaching audit extension.

Creates teaching.assignments — teacher-owned classroom assignment intent SoR.
Extends security.audit_records for teaching.assignment.* actions (TOS-DEV06-I03).

Revision ID: tosd060001
Revises: tosd040001
Create Date: 2026-08-31
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "tosd060001"
down_revision: str | None = "tosd040001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA_OWNER_ROLE_ENV = "AIEOS_SCHEMA_OWNER_ROLE"
SECURITY_SCHEMA_OWNER_ROLE_ENV = "AIEOS_SECURITY_SCHEMA_OWNER_ROLE"
_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")

_TEACHING_EVIDENCE_BLOCKED = (
    "TOS-DEV06-I03 downgrade refused: Teaching security audit evidence exists "
    "and must not be deleted or rewritten"
)

_CONTENT_ACTIONS_SQL = """
                    'content.create',
                    'content.version.create',
                    'content.review.submit',
                    'content.review.approve',
                    'content.review.request_changes',
                    'content.review.reject',
                    'content.publish',
                    'content.ai.materialize',
                    'content.migration.import'
"""

_ASSET_ACTIONS_SQL = """
                    'asset.create',
                    'asset.revision.register',
                    'asset.revision.activate',
                    'asset.lifecycle.withdraw',
                    'asset.lifecycle.restore',
                    'asset.lifecycle.delete',
                    'asset.quarantine.set',
                    'asset.quarantine.clear',
                    'asset.safety.pass',
                    'asset.safety.fail'
"""

_TEACHING_ACTIONS_SQL = """
                    'teaching.assignment.create',
                    'teaching.assignment.due_update',
                    'teaching.assignment.close',
                    'teaching.assignment.cancel'
"""

_ALL_ACTIONS_SQL = (
    _CONTENT_ACTIONS_SQL.rstrip()
    + ","
    + _ASSET_ACTIONS_SQL
    + ","
    + _TEACHING_ACTIONS_SQL
)

_CONTENT_INCREMENT_SQL = """
                    'content.version.create',
                    'content.review.submit',
                    'content.review.approve',
                    'content.review.request_changes',
                    'content.review.reject',
                    'content.publish',
                    'content.ai.materialize'
"""

_ASSET_INCREMENT_SQL = """
                    'asset.revision.activate',
                    'asset.lifecycle.withdraw',
                    'asset.lifecycle.restore',
                    'asset.lifecycle.delete',
                    'asset.quarantine.set',
                    'asset.quarantine.clear',
                    'asset.safety.pass',
                    'asset.safety.fail'
"""

_TEACHING_INCREMENT_SQL = """
                    'teaching.assignment.due_update',
                    'teaching.assignment.close',
                    'teaching.assignment.cancel'
"""

_CONTENT_AND_TEACHING_ACTIONS_SQL = (
    _CONTENT_ACTIONS_SQL.rstrip() + "," + _TEACHING_ACTIONS_SQL
)


def _require_role(env_name: str, *, purpose: str) -> str:
    role = os.environ.get(env_name, "").strip()
    if not role:
        raise RuntimeError(
            f"{env_name} must be set to the {purpose}; Alembic will not "
            "silently alter security objects as the migrator or content owner."
        )
    if not _ROLE_NAME.fullmatch(role):
        raise RuntimeError(
            f"{env_name} must be a lowercase unquoted PostgreSQL identifier"
        )
    return role


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

AUDIT_UPGRADE_STATEMENTS: tuple[str, ...] = (
    """
    ALTER TABLE security.audit_records
        DROP CONSTRAINT ck_audit_records_action
    """,
    f"""
    ALTER TABLE security.audit_records
        ADD CONSTRAINT ck_audit_records_action
        CHECK (
            action IN (
{_ALL_ACTIONS_SQL}
            )
        )
    """,
    """
    ALTER TABLE security.audit_records
        DROP CONSTRAINT ck_audit_records_primary_revision_family
    """,
    f"""
    ALTER TABLE security.audit_records
        ADD CONSTRAINT ck_audit_records_primary_revision_family
        CHECK (
            (
                action IN (
{_CONTENT_AND_TEACHING_ACTIONS_SQL}
                )
                AND primary_resource_revision IS NOT NULL
                AND primary_resource_revision = resource_revision_after
            )
            OR (
                action IN (
{_ASSET_ACTIONS_SQL}
                )
                AND primary_resource_revision IS NULL
            )
        )
    """,
    """
    ALTER TABLE security.audit_records
        DROP CONSTRAINT ck_audit_records_revision_semantics
    """,
    f"""
    ALTER TABLE security.audit_records
        ADD CONSTRAINT ck_audit_records_revision_semantics
        CHECK (
            (
                action = 'content.create'
                AND resource_revision_before IS NULL
                AND resource_revision_after = 0
            )
            OR (
                action = 'content.migration.import'
                AND resource_revision_before IS NULL
                AND resource_revision_after = 1
            )
            OR (
                action IN (
{_CONTENT_INCREMENT_SQL}
                )
                AND resource_revision_before IS NOT NULL
                AND resource_revision_after = resource_revision_before + 1
            )
            OR (
                action = 'asset.create'
                AND resource_revision_before IS NULL
                AND resource_revision_after = 0
            )
            OR (
                action = 'asset.revision.register'
                AND resource_revision_before IS NOT NULL
                AND resource_revision_after = resource_revision_before
            )
            OR (
                action IN (
{_ASSET_INCREMENT_SQL}
                )
                AND resource_revision_before IS NOT NULL
                AND resource_revision_after = resource_revision_before + 1
            )
            OR (
                action = 'teaching.assignment.create'
                AND resource_revision_before IS NULL
                AND resource_revision_after = 0
            )
            OR (
                action IN (
{_TEACHING_INCREMENT_SQL}
                )
                AND resource_revision_before IS NOT NULL
                AND resource_revision_after = resource_revision_before + 1
            )
        )
    """,
)

AUDIT_DOWNGRADE_RESTORE_STATEMENTS: tuple[str, ...] = (
    """
    ALTER TABLE security.audit_records
        DROP CONSTRAINT ck_audit_records_revision_semantics
    """,
    f"""
    ALTER TABLE security.audit_records
        ADD CONSTRAINT ck_audit_records_revision_semantics
        CHECK (
            (
                action = 'content.create'
                AND resource_revision_before IS NULL
                AND resource_revision_after = 0
            )
            OR (
                action = 'content.migration.import'
                AND resource_revision_before IS NULL
                AND resource_revision_after = 1
            )
            OR (
                action IN (
{_CONTENT_INCREMENT_SQL}
                )
                AND resource_revision_before IS NOT NULL
                AND resource_revision_after = resource_revision_before + 1
            )
            OR (
                action = 'asset.create'
                AND resource_revision_before IS NULL
                AND resource_revision_after = 0
            )
            OR (
                action = 'asset.revision.register'
                AND resource_revision_before IS NOT NULL
                AND resource_revision_after = resource_revision_before
            )
            OR (
                action IN (
{_ASSET_INCREMENT_SQL}
                )
                AND resource_revision_before IS NOT NULL
                AND resource_revision_after = resource_revision_before + 1
            )
        )
    """,
    """
    ALTER TABLE security.audit_records
        DROP CONSTRAINT ck_audit_records_primary_revision_family
    """,
    f"""
    ALTER TABLE security.audit_records
        ADD CONSTRAINT ck_audit_records_primary_revision_family
        CHECK (
            (
                action IN (
{_CONTENT_ACTIONS_SQL}
                )
                AND primary_resource_revision IS NOT NULL
                AND primary_resource_revision = resource_revision_after
            )
            OR (
                action IN (
{_ASSET_ACTIONS_SQL}
                )
                AND primary_resource_revision IS NULL
            )
        )
    """,
    """
    ALTER TABLE security.audit_records
        DROP CONSTRAINT ck_audit_records_action
    """,
    f"""
    ALTER TABLE security.audit_records
        ADD CONSTRAINT ck_audit_records_action
        CHECK (
            action IN (
{_CONTENT_ACTIONS_SQL.rstrip()}
                    ,
{_ASSET_ACTIONS_SQL}
            )
        )
    """,
)

DOWNGRADE_STATEMENTS: tuple[str, ...] = (
    "DROP POLICY IF EXISTS teaching_assignments_tenant_isolation ON teaching.assignments",
    "DROP TABLE IF EXISTS teaching.assignments",
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)
    schema_owner = _require_role(
        SCHEMA_OWNER_ROLE_ENV, purpose="Generic Content schema-owner role"
    )
    security_owner = _require_role(
        SECURITY_SCHEMA_OWNER_ROLE_ENV,
        purpose="security schema-owner role",
    )
    op.execute(f"SET LOCAL ROLE {security_owner}")
    for statement in AUDIT_UPGRADE_STATEMENTS:
        op.execute(statement)
    op.execute(f"SET LOCAL ROLE {schema_owner}")


def downgrade() -> None:
    schema_owner = _require_role(
        SCHEMA_OWNER_ROLE_ENV, purpose="Generic Content schema-owner role"
    )
    security_owner = _require_role(
        SECURITY_SCHEMA_OWNER_ROLE_ENV,
        purpose="security schema-owner role",
    )
    op.execute(f"SET LOCAL ROLE {security_owner}")
    op.execute("ALTER TABLE security.audit_records DISABLE ROW LEVEL SECURITY")
    blocked = op.get_bind().execute(
        text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM security.audit_records
                WHERE action LIKE 'teaching.%'
            )
            """
        )
    ).scalar()
    if blocked:
        op.execute("ALTER TABLE security.audit_records ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE security.audit_records FORCE ROW LEVEL SECURITY")
        op.execute(f"SET LOCAL ROLE {schema_owner}")
        raise RuntimeError(_TEACHING_EVIDENCE_BLOCKED)
    for statement in AUDIT_DOWNGRADE_RESTORE_STATEMENTS:
        op.execute(statement)
    op.execute("ALTER TABLE security.audit_records ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE security.audit_records FORCE ROW LEVEL SECURITY")
    op.execute(f"SET LOCAL ROLE {schema_owner}")
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
