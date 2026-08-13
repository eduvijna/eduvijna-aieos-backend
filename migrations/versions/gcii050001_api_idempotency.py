"""GCI-I05 platform API idempotency records.

Revision ID: gcii050001
Revises: gcii020001
Create Date: 2026-08-13
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "gcii050001"
down_revision: str | None = "gcii020001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE_STATEMENTS: tuple[str, ...] = (
    "CREATE SCHEMA api",
    """
    CREATE TABLE api.idempotency_records (
        idempotency_record_id UUID NOT NULL,
        tenant_id UUID NOT NULL,
        actor_principal_id UUID NOT NULL,
        operation TEXT NOT NULL,
        idempotency_key_sha256 CHAR(64) NOT NULL,
        request_fingerprint_sha256 CHAR(64) NOT NULL,
        result_content_id UUID NOT NULL,
        result_version_id UUID NULL,
        result_aggregate_revision BIGINT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        expires_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT pk_idempotency_records PRIMARY KEY (idempotency_record_id),
        CONSTRAINT uq_idempotency_scope UNIQUE (
            tenant_id,
            actor_principal_id,
            operation,
            idempotency_key_sha256
        ),
        CONSTRAINT ck_idempotency_key_sha256
            CHECK (idempotency_key_sha256 ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_idempotency_fingerprint_sha256
            CHECK (request_fingerprint_sha256 ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_idempotency_revision_nonnegative
            CHECK (result_aggregate_revision >= 0),
        CONSTRAINT ck_idempotency_operation_nonempty
            CHECK (btrim(operation) <> ''),
        CONSTRAINT ck_idempotency_expires_after_created
            CHECK (expires_at > created_at)
    )
    """,
    "CREATE INDEX ix_idempotency_records_tenant ON api.idempotency_records (tenant_id)",
    """
    CREATE OR REPLACE FUNCTION api.current_tenant_id()
    RETURNS uuid
    LANGUAGE plpgsql
    VOLATILE
    SET search_path = api, pg_temp
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
    "ALTER TABLE api.idempotency_records ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE api.idempotency_records FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY idempotency_records_tenant_isolation ON api.idempotency_records
        FOR ALL
        USING (tenant_id = api.current_tenant_id())
        WITH CHECK (tenant_id = api.current_tenant_id())
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS api CASCADE")
