"""TOS-DEV09-I02 remediation TeachingWork security-audit vocabulary.

Revision ID: tosd090002
Revises: tosd090001
Create Date: 2026-09-04
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "tosd090002"
down_revision: str | None = "tosd090001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA_OWNER_ROLE_ENV = "AIEOS_SCHEMA_OWNER_ROLE"
SECURITY_SCHEMA_OWNER_ROLE_ENV = "AIEOS_SECURITY_SCHEMA_OWNER_ROLE"
_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")
_DOWNGRADE_BLOCKED = (
    "TOS-DEV09-I02 downgrade refused: remediation TeachingWork security audit "
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
_TEACHING_BASE_ACTIONS_SQL = """
                    'teaching.assignment.create',
                    'teaching.assignment.due_update',
                    'teaching.assignment.close',
                    'teaching.assignment.cancel',
                    'teaching.execution.start',
                    'teaching.execution.complete',
                    'teaching.execution.cancel',
                    'teaching.execution.observation.create',
                    'teaching.execution.observation.correct'
"""
_REMEDIATION_ACTION_SQL = """
                    'teaching.work.remediation.create'
"""
_ASSESSMENT_ACTIONS_SQL = """
                    'assessment.classroom.record',
                    'assessment.classroom.correct',
                    'assessment.classroom.void'
"""
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
_TEACHING_BASE_CREATE_SQL = """
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
_ASSESSMENT_CREATE_SQL = """
                    'assessment.classroom.record'
"""
_ASSESSMENT_INCREMENT_SQL = """
                    'assessment.classroom.correct',
                    'assessment.classroom.void'
"""


def _joined(*parts: str) -> str:
    return ",\n".join(part.strip("\n") for part in parts)


_BASE_ACTIONS_SQL = _joined(
    _CONTENT_ACTIONS_SQL,
    _ASSET_ACTIONS_SQL,
    _TEACHING_BASE_ACTIONS_SQL,
    _ASSESSMENT_ACTIONS_SQL,
)
_UPGRADE_ACTIONS_SQL = _joined(_BASE_ACTIONS_SQL, _REMEDIATION_ACTION_SQL)
_BASE_PRIMARY_ACTIONS_SQL = _joined(
    _CONTENT_ACTIONS_SQL, _TEACHING_BASE_ACTIONS_SQL, _ASSESSMENT_ACTIONS_SQL
)
_UPGRADE_PRIMARY_ACTIONS_SQL = _joined(
    _BASE_PRIMARY_ACTIONS_SQL, _REMEDIATION_ACTION_SQL
)
_UPGRADE_TEACHING_CREATE_SQL = _joined(
    _TEACHING_BASE_CREATE_SQL, _REMEDIATION_ACTION_SQL
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


def _constraint_statements(
    *,
    actions_sql: str,
    primary_actions_sql: str,
    teaching_create_sql: str,
) -> tuple[str, ...]:
    return (
        "ALTER TABLE security.audit_records DROP CONSTRAINT ck_audit_records_action",
        f"""
        ALTER TABLE security.audit_records
            ADD CONSTRAINT ck_audit_records_action
            CHECK (action IN (
{actions_sql}
            ))
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
{primary_actions_sql}
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
                (action = 'content.create'
                    AND resource_revision_before IS NULL
                    AND resource_revision_after = 0)
                OR (action = 'content.migration.import'
                    AND resource_revision_before IS NULL
                    AND resource_revision_after = 1)
                OR (action IN ({_CONTENT_INCREMENT_SQL})
                    AND resource_revision_before IS NOT NULL
                    AND resource_revision_after = resource_revision_before + 1)
                OR (action = 'asset.create'
                    AND resource_revision_before IS NULL
                    AND resource_revision_after = 0)
                OR (action = 'asset.revision.register'
                    AND resource_revision_before IS NOT NULL
                    AND resource_revision_after = resource_revision_before)
                OR (action IN ({_ASSET_INCREMENT_SQL})
                    AND resource_revision_before IS NOT NULL
                    AND resource_revision_after = resource_revision_before + 1)
                OR (action IN ({teaching_create_sql})
                    AND resource_revision_before IS NULL
                    AND resource_revision_after = 0)
                OR (action IN ({_TEACHING_INCREMENT_SQL})
                    AND resource_revision_before IS NOT NULL
                    AND resource_revision_after = resource_revision_before + 1)
                OR (action IN ({_ASSESSMENT_CREATE_SQL})
                    AND resource_revision_before IS NULL
                    AND resource_revision_after = 0)
                OR (action IN ({_ASSESSMENT_INCREMENT_SQL})
                    AND resource_revision_before IS NOT NULL
                    AND resource_revision_after = resource_revision_before + 1)
            )
        """,
    )


UPGRADE_STATEMENTS = _constraint_statements(
    actions_sql=_UPGRADE_ACTIONS_SQL,
    primary_actions_sql=_UPGRADE_PRIMARY_ACTIONS_SQL,
    teaching_create_sql=_UPGRADE_TEACHING_CREATE_SQL,
)
DOWNGRADE_RESTORE_STATEMENTS = _constraint_statements(
    actions_sql=_BASE_ACTIONS_SQL,
    primary_actions_sql=_BASE_PRIMARY_ACTIONS_SQL,
    teaching_create_sql=_TEACHING_BASE_CREATE_SQL,
)


def upgrade() -> None:
    content_owner = _require_role(
        SCHEMA_OWNER_ROLE_ENV, purpose="Generic Content schema-owner role"
    )
    security_owner = _require_role(
        SECURITY_SCHEMA_OWNER_ROLE_ENV, purpose="security schema-owner role"
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
        SECURITY_SCHEMA_OWNER_ROLE_ENV, purpose="security schema-owner role"
    )
    op.execute(f"SET LOCAL ROLE {security_owner}")
    op.execute("ALTER TABLE security.audit_records DISABLE ROW LEVEL SECURITY")
    blocked = bool(
        op.get_bind()
        .execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM security.audit_records
                    WHERE action = 'teaching.work.remediation.create'
                )
                """
            )
        )
        .scalar()
    )
    if blocked:
        op.execute("ALTER TABLE security.audit_records ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE security.audit_records FORCE ROW LEVEL SECURITY")
        op.execute(f"SET LOCAL ROLE {content_owner}")
        raise RuntimeError(_DOWNGRADE_BLOCKED)
    for statement in DOWNGRADE_RESTORE_STATEMENTS:
        op.execute(statement)
    op.execute("ALTER TABLE security.audit_records ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE security.audit_records FORCE ROW LEVEL SECURITY")
    op.execute(f"SET LOCAL ROLE {content_owner}")
