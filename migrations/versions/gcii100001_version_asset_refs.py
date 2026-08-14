"""GCI-I10 immutable ContentVersion → Asset ResourceRef associations.

Revision ID: gcii100001
Revises: gcii090001
Create Date: 2026-08-14
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "gcii100001"
down_revision: str | None = "gcii090001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE content.version_asset_refs (
        tenant_id UUID NOT NULL,
        content_id UUID NOT NULL,
        version_id UUID NOT NULL,
        asset_resource_type TEXT NOT NULL,
        asset_resource_id UUID NOT NULL,
        asset_resource_revision BIGINT NULL,
        role TEXT NOT NULL,
        ordinal INTEGER NOT NULL,
        required BOOLEAN NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT pk_version_asset_refs PRIMARY KEY (
            tenant_id,
            content_id,
            version_id,
            role,
            ordinal
        ),
        CONSTRAINT fk_version_asset_refs_version
            FOREIGN KEY (tenant_id, content_id, version_id)
            REFERENCES content.content_versions (tenant_id, content_id, version_id)
            ON DELETE RESTRICT,
        CONSTRAINT ck_version_asset_refs_resource_type
            CHECK (
                asset_resource_type ~ '^[a-z][a-z0-9._-]{0,63}$'
            ),
        CONSTRAINT ck_version_asset_refs_role
            CHECK (
                role ~ '^[a-z][a-z0-9._-]{0,63}$'
            ),
        CONSTRAINT ck_version_asset_refs_ordinal
            CHECK (ordinal >= 0),
        CONSTRAINT ck_version_asset_refs_revision
            CHECK (
                asset_resource_revision IS NULL
                OR asset_resource_revision >= 0
            )
    )
    """,
    """
    CREATE INDEX ix_version_asset_refs_tenant_id
        ON content.version_asset_refs (tenant_id)
    """,
    """
    CREATE INDEX ix_version_asset_refs_version
        ON content.version_asset_refs (tenant_id, content_id, version_id)
    """,
    """
    CREATE OR REPLACE FUNCTION content.reject_version_asset_ref_mutation()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = content, pg_temp
    AS $$
    BEGIN
        RAISE EXCEPTION 'content.version_asset_refs is immutable'
            USING ERRCODE = '27000';
    END;
    $$
    """,
    """
    CREATE TRIGGER version_asset_refs_immutable_update
        BEFORE UPDATE ON content.version_asset_refs
        FOR EACH ROW
        EXECUTE FUNCTION content.reject_version_asset_ref_mutation()
    """,
    """
    CREATE TRIGGER version_asset_refs_immutable_delete
        BEFORE DELETE ON content.version_asset_refs
        FOR EACH ROW
        EXECUTE FUNCTION content.reject_version_asset_ref_mutation()
    """,
    "ALTER TABLE content.version_asset_refs ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE content.version_asset_refs FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY version_asset_refs_tenant_isolation ON content.version_asset_refs
        FOR ALL
        USING (tenant_id = content.current_tenant_id())
        WITH CHECK (tenant_id = content.current_tenant_id())
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS content.version_asset_refs")
    op.execute("DROP FUNCTION IF EXISTS content.reject_version_asset_ref_mutation()")
