"""PED-I10B6 Asset authorization transactional security-audit ledger extension.

Revision ID: pedi10b6001
Revises: pedi10b2001
Create Date: 2026-08-18

Extends security.audit_records for ADR-AIEOS-036 / ADR-AIEOS-036R1:
exact asset.* actions, NULL primary_resource_revision for Asset rows,
and explicit Content-vs-Asset revision-family CHECKs.

Executes under AIEOS_SECURITY_SCHEMA_OWNER_ROLE, then restores
AIEOS_SCHEMA_OWNER_ROLE so Content migrations stay on the content owner.
Does not create tables, modify asset.*/content.* tables, weaken
immutability, grant UPDATE/DELETE, or add BYPASSRLS.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "pedi10b6001"
down_revision: str | None = "pedi10b2001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA_OWNER_ROLE_ENV = "AIEOS_SCHEMA_OWNER_ROLE"
SECURITY_SCHEMA_OWNER_ROLE_ENV = "AIEOS_SECURITY_SCHEMA_OWNER_ROLE"
_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")

_ASSET_EVIDENCE_BLOCKED = (
    "PED-I10B6 downgrade refused: Asset security audit evidence exists "
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

_ALL_ACTIONS_SQL = _CONTENT_ACTIONS_SQL.rstrip() + "," + _ASSET_ACTIONS_SQL

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
        DROP CONSTRAINT ck_audit_records_primary_revision_matches_after
    """,
    """
    ALTER TABLE security.audit_records
        DROP CONSTRAINT ck_audit_records_primary_rev_nonneg
    """,
    """
    ALTER TABLE security.audit_records
        ALTER COLUMN primary_resource_revision DROP NOT NULL
    """,
    """
    ALTER TABLE security.audit_records
        ADD CONSTRAINT ck_audit_records_primary_rev_nonneg
        CHECK (
            primary_resource_revision IS NULL
            OR primary_resource_revision >= 0
        )
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
        )
    """,
    """
    ALTER TABLE security.audit_records
        DROP CONSTRAINT ck_audit_records_primary_revision_family
    """,
    """
    ALTER TABLE security.audit_records
        DROP CONSTRAINT ck_audit_records_primary_rev_nonneg
    """,
    """
    ALTER TABLE security.audit_records
        ALTER COLUMN primary_resource_revision SET NOT NULL
    """,
    """
    ALTER TABLE security.audit_records
        ADD CONSTRAINT ck_audit_records_primary_rev_nonneg
        CHECK (primary_resource_revision >= 0)
    """,
    """
    ALTER TABLE security.audit_records
        ADD CONSTRAINT ck_audit_records_primary_revision_matches_after
        CHECK (primary_resource_revision = resource_revision_after)
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
{_CONTENT_ACTIONS_SQL}
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
                WHERE action LIKE 'asset.%'
                   OR primary_resource_revision IS NULL
            )
            """
        )
    ).scalar()
    if blocked:
        op.execute(
            "ALTER TABLE security.audit_records ENABLE ROW LEVEL SECURITY"
        )
        op.execute(
            "ALTER TABLE security.audit_records FORCE ROW LEVEL SECURITY"
        )
        raise RuntimeError(_ASSET_EVIDENCE_BLOCKED)
    for statement in DOWNGRADE_RESTORE_STATEMENTS:
        op.execute(statement)
    op.execute("ALTER TABLE security.audit_records ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE security.audit_records FORCE ROW LEVEL SECURITY")
    op.execute(f"SET LOCAL ROLE {content_owner}")
