"""PED-I09 security authority SoR tables with tenant RLS.

Revision ID: pedi090001
Revises: saii020001
Create Date: 2026-08-16

Adds ADR-AIEOS-031 current-authority tables under the existing security schema.
Does not recreate security.current_tenant_id() (owned by saii020001).

Executes under AIEOS_SECURITY_SCHEMA_OWNER_ROLE, then restores
AIEOS_SCHEMA_OWNER_ROLE so Content migrations stay on the content owner.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence

from alembic import op

revision: str = "pedi090001"
down_revision: str | None = "saii020001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA_OWNER_ROLE_ENV = "AIEOS_SCHEMA_OWNER_ROLE"
SECURITY_SCHEMA_OWNER_ROLE_ENV = "AIEOS_SECURITY_SCHEMA_OWNER_ROLE"
_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")


def _require_role(env_name: str, *, purpose: str) -> str:
    role = os.environ.get(env_name, "").strip()
    if not role:
        raise RuntimeError(
            f"{env_name} must be set to the {purpose}; Alembic will not "
            "silently create security objects as the migrator or content owner."
        )
    if not _ROLE_NAME.fullmatch(role):
        raise RuntimeError(
            f"{env_name} must be a lowercase unquoted PostgreSQL identifier"
        )
    return role


UPGRADE_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE security.principals (
        principal_id UUID NOT NULL,
        status TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT pk_security_principals PRIMARY KEY (principal_id),
        CONSTRAINT ck_security_principals_status
            CHECK (status IN ('ACTIVE', 'SUSPENDED', 'DISABLED'))
    )
    """,
    """
    CREATE TABLE security.tenants (
        tenant_id UUID NOT NULL,
        status TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT pk_security_tenants PRIMARY KEY (tenant_id),
        CONSTRAINT ck_security_tenants_status
            CHECK (status IN ('ACTIVE', 'SUSPENDED', 'DISABLED'))
    )
    """,
    """
    CREATE TABLE security.tenant_memberships (
        tenant_id UUID NOT NULL,
        principal_id UUID NOT NULL,
        status TEXT NOT NULL,
        expires_at TIMESTAMPTZ NULL,
        revoked_at TIMESTAMPTZ NULL,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT pk_security_tenant_memberships
            PRIMARY KEY (tenant_id, principal_id),
        CONSTRAINT fk_security_tenant_memberships_tenant
            FOREIGN KEY (tenant_id) REFERENCES security.tenants (tenant_id)
            ON DELETE RESTRICT,
        CONSTRAINT fk_security_tenant_memberships_principal
            FOREIGN KEY (principal_id) REFERENCES security.principals (principal_id)
            ON DELETE RESTRICT,
        CONSTRAINT ck_security_tenant_memberships_status
            CHECK (status IN ('ACTIVE', 'SUSPENDED', 'REVOKED'))
    )
    """,
    """
    CREATE TABLE security.capability_grants (
        tenant_id UUID NOT NULL,
        principal_id UUID NOT NULL,
        capability TEXT NOT NULL,
        status TEXT NOT NULL,
        expires_at TIMESTAMPTZ NULL,
        revoked_at TIMESTAMPTZ NULL,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT pk_security_capability_grants
            PRIMARY KEY (tenant_id, principal_id, capability),
        CONSTRAINT fk_security_capability_grants_membership
            FOREIGN KEY (tenant_id, principal_id)
            REFERENCES security.tenant_memberships (tenant_id, principal_id)
            ON DELETE RESTRICT,
        CONSTRAINT ck_security_capability_grants_status
            CHECK (status IN ('ACTIVE', 'REVOKED')),
        CONSTRAINT ck_security_capability_grants_capability_nonempty
            CHECK (btrim(capability) <> '')
    )
    """,
    "CREATE INDEX ix_security_tenant_memberships_principal "
    "ON security.tenant_memberships (principal_id)",
    "CREATE INDEX ix_security_capability_grants_principal "
    "ON security.capability_grants (principal_id)",
    "ALTER TABLE security.tenants ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE security.tenants FORCE ROW LEVEL SECURITY",
    "ALTER TABLE security.tenant_memberships ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE security.tenant_memberships FORCE ROW LEVEL SECURITY",
    "ALTER TABLE security.capability_grants ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE security.capability_grants FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY tenants_tenant_isolation ON security.tenants
        FOR ALL
        USING (tenant_id = security.current_tenant_id())
        WITH CHECK (tenant_id = security.current_tenant_id())
    """,
    """
    CREATE POLICY tenant_memberships_tenant_isolation
        ON security.tenant_memberships
        FOR ALL
        USING (tenant_id = security.current_tenant_id())
        WITH CHECK (tenant_id = security.current_tenant_id())
    """,
    """
    CREATE POLICY capability_grants_tenant_isolation
        ON security.capability_grants
        FOR ALL
        USING (tenant_id = security.current_tenant_id())
        WITH CHECK (tenant_id = security.current_tenant_id())
    """,
    "REVOKE ALL ON TABLE security.principals FROM PUBLIC",
    "REVOKE ALL ON TABLE security.tenants FROM PUBLIC",
    "REVOKE ALL ON TABLE security.tenant_memberships FROM PUBLIC",
    "REVOKE ALL ON TABLE security.capability_grants FROM PUBLIC",
)

DOWNGRADE_STATEMENTS: tuple[str, ...] = (
    "DROP TABLE IF EXISTS security.capability_grants",
    "DROP TABLE IF EXISTS security.tenant_memberships",
    "DROP TABLE IF EXISTS security.tenants",
    "DROP TABLE IF EXISTS security.principals",
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
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
    op.execute(f"SET LOCAL ROLE {content_owner}")
