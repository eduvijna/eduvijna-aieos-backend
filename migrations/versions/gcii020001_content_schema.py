"""GCI-I02 Generic Content PostgreSQL schema.

Revision ID: gcii020001
Revises:
Create Date: 2026-08-13
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "gcii020001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE_STATEMENTS: tuple[str, ...] = (
    "CREATE SCHEMA content",
    """
    CREATE TABLE content.contents (
        content_id UUID NOT NULL,
        tenant_id UUID NOT NULL,
        owner_principal_id UUID NOT NULL,
        content_type TEXT NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        locale TEXT NOT NULL,
        stewardship_state TEXT NOT NULL,
        current_version_id UUID NULL,
        published_version_id UUID NULL,
        aggregate_revision BIGINT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        created_by_principal_id UUID NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        archived_at TIMESTAMPTZ NULL,
        CONSTRAINT pk_contents PRIMARY KEY (content_id),
        CONSTRAINT uq_contents_tenant_content UNIQUE (tenant_id, content_id),
        CONSTRAINT ck_contents_aggregate_revision_nonnegative
            CHECK (aggregate_revision >= 0),
        CONSTRAINT ck_contents_content_type_nonempty
            CHECK (btrim(content_type) <> ''),
        CONSTRAINT ck_contents_title_nonempty
            CHECK (btrim(title) <> ''),
        CONSTRAINT ck_contents_locale_nonempty
            CHECK (btrim(locale) <> ''),
        CONSTRAINT ck_contents_stewardship_state
            CHECK (stewardship_state IN ('DRAFT', 'GENERATED', 'IN_REVIEW', 'APPROVED', 'ARCHIVED')),
        CONSTRAINT ck_contents_archive_iff_archived_at
            CHECK (
                (stewardship_state = 'ARCHIVED' AND archived_at IS NOT NULL)
                OR (stewardship_state <> 'ARCHIVED' AND archived_at IS NULL)
            ),
        CONSTRAINT ck_contents_archived_withdraws_publication
            CHECK (stewardship_state <> 'ARCHIVED' OR published_version_id IS NULL)
    )
    """,
    "CREATE INDEX ix_contents_tenant_id ON content.contents (tenant_id)",
    "CREATE INDEX ix_contents_tenant_content_type ON content.contents (tenant_id, content_type)",
    "CREATE INDEX ix_contents_tenant_stewardship_state ON content.contents (tenant_id, stewardship_state)",
    "CREATE INDEX ix_contents_tenant_owner_principal ON content.contents (tenant_id, owner_principal_id)",
    """
    CREATE TABLE content.content_versions (
        version_id UUID NOT NULL,
        tenant_id UUID NOT NULL,
        content_id UUID NOT NULL,
        version_number BIGINT NOT NULL,
        parent_version_id UUID NULL,
        schema_id TEXT NOT NULL,
        schema_version INTEGER NOT NULL,
        payload JSONB NOT NULL,
        payload_sha256 VARCHAR(64) NOT NULL,
        origin TEXT NOT NULL,
        provenance JSONB NULL,
        created_at TIMESTAMPTZ NOT NULL,
        created_by_principal_id UUID NOT NULL,
        CONSTRAINT pk_content_versions PRIMARY KEY (version_id),
        CONSTRAINT uq_content_versions_tenant_content_number
            UNIQUE (tenant_id, content_id, version_number),
        CONSTRAINT uq_content_versions_tenant_content_version
            UNIQUE (tenant_id, content_id, version_id),
        CONSTRAINT ck_content_versions_version_number_positive
            CHECK (version_number > 0),
        CONSTRAINT ck_content_versions_schema_version_positive
            CHECK (schema_version > 0),
        CONSTRAINT ck_content_versions_payload_object
            CHECK (jsonb_typeof(payload) = 'object'),
        CONSTRAINT ck_content_versions_payload_sha256
            CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_content_versions_origin
            CHECK (origin IN ('HUMAN', 'AI', 'IMPORT', 'SYSTEM')),
        CONSTRAINT ck_content_versions_first_version_lineage
            CHECK (
                (version_number = 1 AND parent_version_id IS NULL)
                OR (version_number > 1 AND parent_version_id IS NOT NULL)
            ),
        CONSTRAINT ck_content_versions_ai_provenance_required
            CHECK (origin <> 'AI' OR provenance IS NOT NULL),
        CONSTRAINT ck_content_versions_provenance_object
            CHECK (provenance IS NULL OR jsonb_typeof(provenance) = 'object'),
        CONSTRAINT fk_content_versions_contents
            FOREIGN KEY (tenant_id, content_id)
            REFERENCES content.contents (tenant_id, content_id)
            ON DELETE RESTRICT,
        CONSTRAINT fk_content_versions_parent
            FOREIGN KEY (tenant_id, content_id, parent_version_id)
            REFERENCES content.content_versions (tenant_id, content_id, version_id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX ix_content_versions_parent
        ON content.content_versions (tenant_id, content_id, parent_version_id)
    """,
    """
    ALTER TABLE content.contents
        ADD CONSTRAINT fk_contents_current_version
        FOREIGN KEY (tenant_id, content_id, current_version_id)
        REFERENCES content.content_versions (tenant_id, content_id, version_id)
        ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED
    """,
    """
    ALTER TABLE content.contents
        ADD CONSTRAINT fk_contents_published_version
        FOREIGN KEY (tenant_id, content_id, published_version_id)
        REFERENCES content.content_versions (tenant_id, content_id, version_id)
        ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED
    """,
    """
    CREATE OR REPLACE FUNCTION content.current_tenant_id()
    RETURNS uuid
    LANGUAGE plpgsql
    VOLATILE
    SET search_path = content, pg_temp
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
    CREATE OR REPLACE FUNCTION content.reject_content_version_mutation()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = content, pg_temp
    AS $$
    BEGIN
        RAISE EXCEPTION 'content.content_versions is immutable'
            USING ERRCODE = '27000';
    END;
    $$
    """,
    """
    CREATE TRIGGER content_versions_immutable_update
        BEFORE UPDATE ON content.content_versions
        FOR EACH ROW
        EXECUTE FUNCTION content.reject_content_version_mutation()
    """,
    """
    CREATE TRIGGER content_versions_immutable_delete
        BEFORE DELETE ON content.content_versions
        FOR EACH ROW
        EXECUTE FUNCTION content.reject_content_version_mutation()
    """,
    "ALTER TABLE content.contents ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE content.contents FORCE ROW LEVEL SECURITY",
    "ALTER TABLE content.content_versions ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE content.content_versions FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY contents_tenant_isolation ON content.contents
        FOR ALL
        USING (tenant_id = content.current_tenant_id())
        WITH CHECK (tenant_id = content.current_tenant_id())
    """,
    """
    CREATE POLICY content_versions_tenant_isolation ON content.content_versions
        FOR ALL
        USING (tenant_id = content.current_tenant_id())
        WITH CHECK (tenant_id = content.current_tenant_id())
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS content CASCADE")
