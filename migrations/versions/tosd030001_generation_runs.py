"""TOS-DEV03 Lane B AI GenerationRun PostgreSQL schema.

Creates ai.generation_runs execution/provenance SoR only.

Deliberately absent:
  * prompt / raw model output / API key columns
  * worksheet payload storage (ContentVersion remains authoritative)

Revision ID: tosd030001
Revises: tosd020001
Create Date: 2026-08-27
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "tosd030001"
down_revision: str | None = "tosd020001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE_STATEMENTS: tuple[str, ...] = (
    "CREATE SCHEMA ai",
    """
    CREATE TABLE ai.generation_runs (
        generation_run_id UUID NOT NULL,
        tenant_id UUID NOT NULL,
        principal_id UUID NOT NULL,
        work_resource_type TEXT NOT NULL,
        work_resource_id UUID NOT NULL,
        work_resource_revision BIGINT NOT NULL,
        capability_id TEXT NOT NULL,
        provider_id TEXT NOT NULL,
        model_id TEXT NOT NULL,
        status TEXT NOT NULL,
        request_fingerprint_sha256 TEXT NOT NULL,
        idempotency_key_sha256 TEXT NOT NULL,
        provider_response_id TEXT NULL,
        input_tokens INTEGER NULL,
        output_tokens INTEGER NULL,
        total_tokens INTEGER NULL,
        educational_quality_summary JSONB NULL,
        result_content_id UUID NULL,
        result_version_id UUID NULL,
        result_content_revision BIGINT NULL,
        failure_code TEXT NULL,
        aggregate_revision BIGINT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        completed_at TIMESTAMPTZ NULL,
        CONSTRAINT pk_ai_generation_runs PRIMARY KEY (generation_run_id),
        CONSTRAINT uq_ai_generation_runs_tenant_run
            UNIQUE (tenant_id, generation_run_id),
        CONSTRAINT uq_ai_generation_runs_tenant_principal_idempotency
            UNIQUE (tenant_id, principal_id, idempotency_key_sha256),
        CONSTRAINT ck_ai_generation_runs_aggregate_revision_nonnegative
            CHECK (aggregate_revision >= 0),
        CONSTRAINT ck_ai_generation_runs_work_revision_nonnegative
            CHECK (work_resource_revision >= 0),
        CONSTRAINT ck_ai_generation_runs_status
            CHECK (status IN ('RUNNING', 'VALIDATED', 'SUCCEEDED', 'FAILED')),
        CONSTRAINT ck_ai_generation_runs_work_resource_type
            CHECK (work_resource_type = 'teaching.work'),
        CONSTRAINT ck_ai_generation_runs_capability_nonempty
            CHECK (btrim(capability_id) <> ''),
        CONSTRAINT ck_ai_generation_runs_provider_nonempty
            CHECK (btrim(provider_id) <> ''),
        CONSTRAINT ck_ai_generation_runs_model_nonempty
            CHECK (btrim(model_id) <> ''),
        CONSTRAINT ck_ai_generation_runs_fingerprint_sha256
            CHECK (char_length(request_fingerprint_sha256) = 64),
        CONSTRAINT ck_ai_generation_runs_idempotency_sha256
            CHECK (char_length(idempotency_key_sha256) = 64),
        CONSTRAINT ck_ai_generation_runs_updated_after_created
            CHECK (updated_at >= created_at)
    )
    """,
    "CREATE INDEX ix_ai_generation_runs_tenant_id ON ai.generation_runs (tenant_id)",
    """
    CREATE INDEX ix_ai_generation_runs_tenant_principal
        ON ai.generation_runs (tenant_id, principal_id)
    """,
    """
    CREATE INDEX ix_ai_generation_runs_tenant_work
        ON ai.generation_runs (tenant_id, work_resource_id)
    """,
    """
    CREATE INDEX ix_ai_generation_runs_tenant_status
        ON ai.generation_runs (tenant_id, status)
    """,
    """
    CREATE OR REPLACE FUNCTION ai.current_tenant_id()
    RETURNS uuid
    LANGUAGE plpgsql
    VOLATILE
    SET search_path = ai, pg_temp
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
    "ALTER TABLE ai.generation_runs ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE ai.generation_runs FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY ai_generation_runs_tenant_isolation ON ai.generation_runs
        FOR ALL
        USING (tenant_id = ai.current_tenant_id())
        WITH CHECK (tenant_id = ai.current_tenant_id())
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS ai CASCADE")
