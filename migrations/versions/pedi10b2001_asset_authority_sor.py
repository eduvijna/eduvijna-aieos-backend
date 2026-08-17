"""PED-I10B2 Asset-owned PostgreSQL System of Record.

Revision ID: pedi10b2001
Revises: pedi090001
Create Date: 2026-08-17

Creates schema asset and the four authoritative persistence tables under
AIEOS_ASSET_SCHEMA_OWNER_ROLE, then restores AIEOS_SCHEMA_OWNER_ROLE so
Content migrations stay on the content owner.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence

from alembic import op

revision: str = "pedi10b2001"
down_revision: str | None = "pedi090001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA_OWNER_ROLE_ENV = "AIEOS_SCHEMA_OWNER_ROLE"
ASSET_SCHEMA_OWNER_ROLE_ENV = "AIEOS_ASSET_SCHEMA_OWNER_ROLE"
_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")


def _require_role(env_name: str, *, purpose: str) -> str:
    role = os.environ.get(env_name, "").strip()
    if not role:
        raise RuntimeError(
            f"{env_name} must be set to the {purpose}; Alembic will not "
            "silently create asset objects as the migrator or content owner."
        )
    if not _ROLE_NAME.fullmatch(role):
        raise RuntimeError(
            f"{env_name} must be a lowercase unquoted PostgreSQL identifier"
        )
    return role


UPGRADE_STATEMENTS: tuple[str, ...] = (
    "CREATE SCHEMA asset",
    "REVOKE ALL ON SCHEMA asset FROM PUBLIC",
    """
    CREATE TABLE asset.assets (
        tenant_id UUID NOT NULL,
        asset_id UUID NOT NULL,
        resource_type TEXT NOT NULL,
        lifecycle TEXT NOT NULL,
        quarantine_state TEXT NOT NULL,
        current_revision BIGINT NULL,
        aggregate_revision BIGINT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        created_by_principal_id UUID NOT NULL,
        CONSTRAINT pk_assets PRIMARY KEY (asset_id),
        CONSTRAINT uq_assets_tenant_asset UNIQUE (tenant_id, asset_id),
        CONSTRAINT uq_assets_tenant_asset_resource_type
            UNIQUE (tenant_id, asset_id, resource_type),
        CONSTRAINT ck_assets_resource_type
            CHECK (resource_type IN (
                'asset.image', 'asset.document', 'asset.audio', 'asset.video'
            )),
        CONSTRAINT ck_assets_lifecycle
            CHECK (lifecycle IN ('active', 'withdrawn', 'deleted')),
        CONSTRAINT ck_assets_quarantine_state
            CHECK (quarantine_state IN ('clear', 'quarantined')),
        CONSTRAINT ck_assets_current_revision_positive
            CHECK (current_revision IS NULL OR current_revision > 0),
        CONSTRAINT ck_assets_aggregate_revision_nonnegative
            CHECK (aggregate_revision >= 0)
    )
    """,
    "CREATE INDEX ix_assets_tenant_id ON asset.assets (tenant_id)",
    """
    CREATE INDEX ix_assets_tenant_resource_type
        ON asset.assets (tenant_id, resource_type)
    """,
    """
    CREATE INDEX ix_assets_tenant_lifecycle
        ON asset.assets (tenant_id, lifecycle)
    """,
    """
    CREATE TABLE asset.asset_revisions (
        asset_revision_id UUID NOT NULL,
        tenant_id UUID NOT NULL,
        asset_id UUID NOT NULL,
        revision_number BIGINT NOT NULL,
        resource_type TEXT NOT NULL,
        storage_key TEXT NOT NULL,
        media_type TEXT NOT NULL,
        byte_size BIGINT NOT NULL,
        sha256 VARCHAR(64) NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        created_by_principal_id UUID NOT NULL,
        CONSTRAINT pk_asset_revisions PRIMARY KEY (asset_revision_id),
        CONSTRAINT uq_asset_revisions_tenant_asset_number
            UNIQUE (tenant_id, asset_id, revision_number),
        CONSTRAINT uq_asset_revisions_tenant_asset_id_number
            UNIQUE (tenant_id, asset_id, asset_revision_id, revision_number),
        CONSTRAINT ck_asset_revisions_revision_number_positive
            CHECK (revision_number > 0),
        CONSTRAINT ck_asset_revisions_resource_type
            CHECK (resource_type IN (
                'asset.image', 'asset.document', 'asset.audio', 'asset.video'
            )),
        CONSTRAINT ck_asset_revisions_byte_size_nonnegative
            CHECK (byte_size >= 0),
        CONSTRAINT ck_asset_revisions_sha256
            CHECK (sha256 ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_asset_revisions_media_type_nonempty
            CHECK (btrim(media_type) <> ''),
        CONSTRAINT ck_asset_revisions_storage_key_nonempty
            CHECK (btrim(storage_key) <> ''),
        CONSTRAINT fk_asset_revisions_asset_resource
            FOREIGN KEY (tenant_id, asset_id, resource_type)
            REFERENCES asset.assets (tenant_id, asset_id, resource_type)
            ON DELETE RESTRICT
    )
    """,
    """
    ALTER TABLE asset.assets
        ADD CONSTRAINT fk_assets_current_revision
        FOREIGN KEY (tenant_id, asset_id, current_revision)
        REFERENCES asset.asset_revisions (tenant_id, asset_id, revision_number)
        ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED
    """,
    """
    CREATE TABLE asset.asset_revision_states (
        asset_revision_id UUID NOT NULL,
        tenant_id UUID NOT NULL,
        asset_id UUID NOT NULL,
        revision_number BIGINT NOT NULL,
        safety_state TEXT NOT NULL,
        bytes_purged BOOLEAN NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT pk_asset_revision_states PRIMARY KEY (asset_revision_id),
        CONSTRAINT ck_asset_revision_states_revision_number_positive
            CHECK (revision_number > 0),
        CONSTRAINT ck_asset_revision_states_safety_state
            CHECK (safety_state IN ('pending', 'passed', 'failed')),
        CONSTRAINT fk_asset_revision_states_revision
            FOREIGN KEY (
                tenant_id, asset_id, asset_revision_id, revision_number
            )
            REFERENCES asset.asset_revisions (
                tenant_id, asset_id, asset_revision_id, revision_number
            )
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX ix_asset_revision_states_tenant_asset_number
        ON asset.asset_revision_states (tenant_id, asset_id, revision_number)
    """,
    """
    CREATE TABLE asset.deletion_evidence (
        asset_revision_id UUID NOT NULL,
        tenant_id UUID NOT NULL,
        asset_id UUID NOT NULL,
        revision_number BIGINT NOT NULL,
        purged_at TIMESTAMPTZ NOT NULL,
        purged_by_principal_id UUID NOT NULL,
        CONSTRAINT pk_deletion_evidence PRIMARY KEY (asset_revision_id),
        CONSTRAINT ck_deletion_evidence_revision_number_positive
            CHECK (revision_number > 0),
        CONSTRAINT fk_deletion_evidence_revision
            FOREIGN KEY (
                tenant_id, asset_id, asset_revision_id, revision_number
            )
            REFERENCES asset.asset_revisions (
                tenant_id, asset_id, asset_revision_id, revision_number
            )
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX ix_deletion_evidence_tenant_asset_number
        ON asset.deletion_evidence (tenant_id, asset_id, revision_number)
    """,
    """
    CREATE OR REPLACE FUNCTION asset.current_tenant_id()
    RETURNS uuid
    LANGUAGE plpgsql
    VOLATILE
    SET search_path = asset, pg_temp
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
    """
    CREATE OR REPLACE FUNCTION asset.reject_immutable_row_mutation()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = asset, pg_temp
    AS $$
    BEGIN
        RAISE EXCEPTION
            USING MESSAGE = 'asset.' || TG_TABLE_NAME || ' is immutable',
                ERRCODE = '27000';
    END;
    $$
    """,
    """
    CREATE TRIGGER asset_revisions_immutable_update
        BEFORE UPDATE ON asset.asset_revisions
        FOR EACH ROW
        EXECUTE FUNCTION asset.reject_immutable_row_mutation()
    """,
    """
    CREATE TRIGGER asset_revisions_immutable_delete
        BEFORE DELETE ON asset.asset_revisions
        FOR EACH ROW
        EXECUTE FUNCTION asset.reject_immutable_row_mutation()
    """,
    """
    CREATE TRIGGER deletion_evidence_immutable_update
        BEFORE UPDATE ON asset.deletion_evidence
        FOR EACH ROW
        EXECUTE FUNCTION asset.reject_immutable_row_mutation()
    """,
    """
    CREATE TRIGGER deletion_evidence_immutable_delete
        BEFORE DELETE ON asset.deletion_evidence
        FOR EACH ROW
        EXECUTE FUNCTION asset.reject_immutable_row_mutation()
    """,
    "ALTER TABLE asset.assets ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE asset.assets FORCE ROW LEVEL SECURITY",
    "ALTER TABLE asset.asset_revisions ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE asset.asset_revisions FORCE ROW LEVEL SECURITY",
    "ALTER TABLE asset.asset_revision_states ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE asset.asset_revision_states FORCE ROW LEVEL SECURITY",
    "ALTER TABLE asset.deletion_evidence ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE asset.deletion_evidence FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY assets_tenant_isolation ON asset.assets
        FOR ALL
        USING (tenant_id = asset.current_tenant_id())
        WITH CHECK (tenant_id = asset.current_tenant_id())
    """,
    """
    CREATE POLICY asset_revisions_tenant_isolation ON asset.asset_revisions
        FOR ALL
        USING (tenant_id = asset.current_tenant_id())
        WITH CHECK (tenant_id = asset.current_tenant_id())
    """,
    """
    CREATE POLICY asset_revision_states_tenant_isolation
        ON asset.asset_revision_states
        FOR ALL
        USING (tenant_id = asset.current_tenant_id())
        WITH CHECK (tenant_id = asset.current_tenant_id())
    """,
    """
    CREATE POLICY deletion_evidence_tenant_isolation ON asset.deletion_evidence
        FOR ALL
        USING (tenant_id = asset.current_tenant_id())
        WITH CHECK (tenant_id = asset.current_tenant_id())
    """,
    "REVOKE ALL ON TABLE asset.assets FROM PUBLIC",
    "REVOKE ALL ON TABLE asset.asset_revisions FROM PUBLIC",
    "REVOKE ALL ON TABLE asset.asset_revision_states FROM PUBLIC",
    "REVOKE ALL ON TABLE asset.deletion_evidence FROM PUBLIC",
    "REVOKE ALL ON FUNCTION asset.current_tenant_id() FROM PUBLIC",
    "REVOKE ALL ON FUNCTION asset.reject_immutable_row_mutation() FROM PUBLIC",
)

DOWNGRADE_STATEMENTS: tuple[str, ...] = (
    "DROP SCHEMA IF EXISTS asset CASCADE",
)


def upgrade() -> None:
    content_owner = _require_role(
        SCHEMA_OWNER_ROLE_ENV, purpose="Generic Content schema-owner role"
    )
    asset_owner = _require_role(
        ASSET_SCHEMA_OWNER_ROLE_ENV,
        purpose="Asset schema-owner role",
    )
    op.execute(f"SET LOCAL ROLE {asset_owner}")
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)
    op.execute(f"SET LOCAL ROLE {content_owner}")


def downgrade() -> None:
    content_owner = _require_role(
        SCHEMA_OWNER_ROLE_ENV, purpose="Generic Content schema-owner role"
    )
    asset_owner = _require_role(
        ASSET_SCHEMA_OWNER_ROLE_ENV,
        purpose="Asset schema-owner role",
    )
    op.execute(f"SET LOCAL ROLE {asset_owner}")
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
    op.execute(f"SET LOCAL ROLE {content_owner}")
