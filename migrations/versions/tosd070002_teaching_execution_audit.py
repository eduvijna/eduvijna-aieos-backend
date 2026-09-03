"""TOS-DEV07-I02R1 TeachingExecution security audit constraint extension.

Revision ID: tosd070002
Revises: tosd070001
Create Date: 2026-09-03

Extends security.audit_records CHECK constraints for teaching.execution.* and
teaching.execution.observation.* actions. Does not alter teaching.executions,
execution_content_bindings, execution_observations, or Content/Asset schema.

Executes under AIEOS_SECURITY_SCHEMA_OWNER_ROLE, then restores
AIEOS_SCHEMA_OWNER_ROLE. Downgrade fails closed when TeachingExecution audit
evidence exists; TeachingAssignment-only evidence must not block downgrade.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "tosd070002"
down_revision: str | None = "tosd070001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA_OWNER_ROLE_ENV = "AIEOS_SCHEMA_OWNER_ROLE"
SECURITY_SCHEMA_OWNER_ROLE_ENV = "AIEOS_SECURITY_SCHEMA_OWNER_ROLE"
_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")

_EXECUTION_EVIDENCE_BLOCKED = (
    "TOS-DEV07-I02R1 downgrade refused: TeachingExecution security audit "
    "evidence exists and must not be deleted or rewritten"
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

_TEACHING_ASSIGNMENT_ACTIONS_SQL = """
                    'teaching.assignment.create',
                    'teaching.assignment.due_update',
                    'teaching.assignment.close',
                    'teaching.assignment.cancel'
"""

_TEACHING_EXECUTION_ACTIONS_SQL = """
                    'teaching.execution.start',
                    'teaching.execution.complete',
                    'teaching.execution.cancel',
                    'teaching.execution.observation.create',
                    'teaching.execution.observation.correct'
"""

_TEACHING_ACTIONS_SQL = (
    _TEACHING_ASSIGNMENT_ACTIONS_SQL.rstrip()
    + ","
    + _TEACHING_EXECUTION_ACTIONS_SQL
)

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

_TEACHING_CREATE_SQL = """
                    'teaching.assignment.create',
                    'teaching.execution.start',
                    'teaching.execution.observation.create'
"""

_TEACHING_INCREMENT_SQL = """
                    'teaching.assignment.due_update',
                    'teaching.assignment.close',
                    'teaching.assignment.cancel',
                    'teaching.execution.complete',
                    'teaching.execution.cancel',
                    'teaching.execution.observation.correct'
"""

_TEACHING_ASSIGNMENT_INCREMENT_SQL = """
                    'teaching.assignment.due_update',
                    'teaching.assignment.close',
                    'teaching.assignment.cancel'
"""

_CONTENT_AND_TEACHING_ACTIONS_SQL = (
    _CONTENT_ACTIONS_SQL.rstrip() + "," + _TEACHING_ACTIONS_SQL
)

_CONTENT_AND_ASSIGNMENT_ACTIONS_SQL = (
    _CONTENT_ACTIONS_SQL.rstrip() + "," + _TEACHING_ASSIGNMENT_ACTIONS_SQL
)

DOWNGRADE_RESTORE_ACTIONS_SQL = (
    _CONTENT_ACTIONS_SQL.rstrip()
    + ","
    + _ASSET_ACTIONS_SQL
    + ","
    + _TEACHING_ASSIGNMENT_ACTIONS_SQL
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
                action IN (
{_TEACHING_CREATE_SQL}
                )
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

DOWNGRADE_RESTORE_STATEMENTS: tuple[str, ...] = (
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
{_TEACHING_ASSIGNMENT_INCREMENT_SQL}
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
{_CONTENT_AND_ASSIGNMENT_ACTIONS_SQL}
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
{DOWNGRADE_RESTORE_ACTIONS_SQL}
            )
        )
    """,
)


def upgrade() -> None:
    content_owner = _require_role(
        SCHEMA_OWNER_ROLE_ENV, purpose="Generic Content schema-owner role"
    )
    security_owner = _require_role(
        SECURITY_SCHEMA_OWNER_ROLE_ENV,
        purpose="security schema-owner role",
    )
    op.execute(f"SET LOCAL ROLE {security_owner}")
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)
    op.execute(f"SET LOCAL ROLE {content_owner}")


def downgrade() -> None:
    content_owner = _require_role(
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
                WHERE action IN (
                    'teaching.execution.start',
                    'teaching.execution.complete',
                    'teaching.execution.cancel',
                    'teaching.execution.observation.create',
                    'teaching.execution.observation.correct'
                )
            )
            """
        )
    ).scalar()
    if blocked:
        op.execute("ALTER TABLE security.audit_records ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE security.audit_records FORCE ROW LEVEL SECURITY")
        op.execute(f"SET LOCAL ROLE {content_owner}")
        raise RuntimeError(_EXECUTION_EVIDENCE_BLOCKED)
    for statement in DOWNGRADE_RESTORE_STATEMENTS:
        op.execute(statement)
    op.execute("ALTER TABLE security.audit_records ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE security.audit_records FORCE ROW LEVEL SECURITY")
    op.execute(f"SET LOCAL ROLE {content_owner}")
