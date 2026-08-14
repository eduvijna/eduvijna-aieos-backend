"""GCI-I13 migration import records and IMPORT provenance defense-in-depth.

Revision ID: gcii130001
Revises: gcii110001
Create Date: 2026-08-14
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "gcii130001"
down_revision: str | None = "gcii110001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE_STATEMENTS: tuple[str, ...] = (
    """
    CREATE OR REPLACE FUNCTION content.migration_import_provenance_v1_is_valid(value jsonb)
    RETURNS boolean
    LANGUAGE plpgsql
    IMMUTABLE
    SET search_path = content, pg_temp
    AS $$
    DECLARE
        keys text[];
        expected text[] := ARRAY[
            'kind',
            'mapping_id',
            'mapping_version',
            'migration_batch_id',
            'schema_version',
            'source_digest_sha256',
            'source_resource_id',
            'source_resource_type',
            'source_system',
            'source_version'
        ];
        migration_batch_id text;
        source_system text;
        source_resource_type text;
        source_resource_id text;
        source_version text;
        source_digest text;
        mapping_id text;
        mapping_version jsonb;
    BEGIN
        IF value IS NULL OR jsonb_typeof(value) <> 'object' THEN
            RETURN false;
        END IF;
        SELECT array_agg(k ORDER BY k)
          INTO keys
          FROM jsonb_object_keys(value) AS k;
        IF keys IS DISTINCT FROM expected THEN
            RETURN false;
        END IF;
        IF value->>'kind' IS DISTINCT FROM 'migration_import' THEN
            RETURN false;
        END IF;
        IF jsonb_typeof(value->'schema_version') <> 'number'
           OR (value->>'schema_version')::numeric <> 1
           OR (value->>'schema_version')::numeric
              <> trunc((value->>'schema_version')::numeric)
           OR (value->>'schema_version') IS DISTINCT FROM '1' THEN
            RETURN false;
        END IF;
        IF jsonb_typeof(value->'migration_batch_id') <> 'string' THEN
            RETURN false;
        END IF;
        migration_batch_id := value->>'migration_batch_id';
        BEGIN
            PERFORM migration_batch_id::uuid;
        EXCEPTION
            WHEN invalid_text_representation THEN
                RETURN false;
        END;
        IF jsonb_typeof(value->'source_system') <> 'string' THEN
            RETURN false;
        END IF;
        source_system := value->>'source_system';
        IF source_system IS NULL
           OR source_system !~ '^[a-z][a-z0-9._-]{0,63}$' THEN
            RETURN false;
        END IF;
        IF jsonb_typeof(value->'source_resource_type') <> 'string' THEN
            RETURN false;
        END IF;
        source_resource_type := value->>'source_resource_type';
        IF source_resource_type IS NULL
           OR source_resource_type !~ '^[a-z][a-z0-9._-]{0,63}$' THEN
            RETURN false;
        END IF;
        IF jsonb_typeof(value->'source_resource_id') <> 'string' THEN
            RETURN false;
        END IF;
        source_resource_id := value->>'source_resource_id';
        IF source_resource_id IS NULL
           OR length(source_resource_id) = 0
           OR length(source_resource_id) > 255
           OR source_resource_id <> btrim(source_resource_id)
           OR source_resource_id ~ '[[:cntrl:]]' THEN
            RETURN false;
        END IF;
        IF value->'source_version' IS NULL
           OR jsonb_typeof(value->'source_version') = 'null' THEN
            NULL;
        ELSIF jsonb_typeof(value->'source_version') <> 'string' THEN
            RETURN false;
        ELSE
            source_version := value->>'source_version';
            IF source_version IS NULL
               OR length(source_version) = 0
               OR length(source_version) > 255
               OR source_version <> btrim(source_version)
               OR source_version ~ '[[:cntrl:]]' THEN
                RETURN false;
            END IF;
        END IF;
        IF jsonb_typeof(value->'source_digest_sha256') <> 'string' THEN
            RETURN false;
        END IF;
        source_digest := value->>'source_digest_sha256';
        IF source_digest IS NULL OR source_digest !~ '^[0-9a-f]{64}$' THEN
            RETURN false;
        END IF;
        IF jsonb_typeof(value->'mapping_id') <> 'string' THEN
            RETURN false;
        END IF;
        mapping_id := value->>'mapping_id';
        IF mapping_id IS NULL
           OR mapping_id !~ '^[a-z][a-z0-9._-]{0,63}$' THEN
            RETURN false;
        END IF;
        mapping_version := value->'mapping_version';
        IF jsonb_typeof(mapping_version) <> 'number'
           OR (mapping_version::text)::numeric
              <> trunc((mapping_version::text)::numeric)
           OR (mapping_version::text)::numeric < 1
           OR (value->>'mapping_version') ~ '\\.' THEN
            RETURN false;
        END IF;
        RETURN true;
    END;
    $$
    """,
    """
    ALTER TABLE content.content_versions
        ADD CONSTRAINT ck_content_versions_migration_import_provenance_v1
        CHECK (
            origin <> 'IMPORT'
            OR content.migration_import_provenance_v1_is_valid(provenance)
        )
    """,
    """
    CREATE TABLE content.migration_import_records (
        tenant_id UUID NOT NULL,
        source_system TEXT NOT NULL,
        source_resource_type TEXT NOT NULL,
        source_resource_id TEXT NOT NULL,
        source_version TEXT NULL,
        source_digest_sha256 CHAR(64) NOT NULL,
        mapping_id TEXT NOT NULL,
        mapping_version INTEGER NOT NULL,
        first_migration_batch_id UUID NOT NULL,
        last_migration_batch_id UUID NOT NULL,
        outcome TEXT NOT NULL,
        target_content_id UUID NULL,
        target_version_id UUID NULL,
        attempt_count INTEGER NOT NULL,
        first_attempt_at TIMESTAMPTZ NOT NULL,
        last_attempt_at TIMESTAMPTZ NOT NULL,
        completed_at TIMESTAMPTZ NULL,
        failure_code TEXT NULL,
        CONSTRAINT pk_migration_import_records PRIMARY KEY (
            tenant_id,
            source_system,
            source_resource_type,
            source_resource_id
        ),
        CONSTRAINT ck_migration_import_records_source_system
            CHECK (source_system ~ '^[a-z][a-z0-9._-]{0,63}$'),
        CONSTRAINT ck_migration_import_records_source_resource_type
            CHECK (source_resource_type ~ '^[a-z][a-z0-9._-]{0,63}$'),
        CONSTRAINT ck_migration_import_records_source_resource_id
            CHECK (
                length(source_resource_id) BETWEEN 1 AND 255
                AND source_resource_id = btrim(source_resource_id)
                AND source_resource_id !~ '[[:cntrl:]]'
            ),
        CONSTRAINT ck_migration_import_records_source_version
            CHECK (
                source_version IS NULL
                OR (
                    length(source_version) BETWEEN 1 AND 255
                    AND source_version = btrim(source_version)
                    AND source_version !~ '[[:cntrl:]]'
                )
            ),
        CONSTRAINT ck_migration_import_records_source_digest
            CHECK (source_digest_sha256 ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_migration_import_records_mapping_id
            CHECK (mapping_id ~ '^[a-z][a-z0-9._-]{0,63}$'),
        CONSTRAINT ck_migration_import_records_mapping_version
            CHECK (mapping_version >= 1),
        CONSTRAINT ck_migration_import_records_attempt_count
            CHECK (attempt_count >= 1),
        CONSTRAINT ck_migration_import_records_failure_code
            CHECK (
                failure_code IS NULL
                OR (
                    char_length(failure_code) <= 64
                    AND failure_code ~ '^[a-z][a-z0-9._-]*$'
                )
            ),
        CONSTRAINT ck_migration_import_records_outcome
            CHECK (
                (
                    outcome = 'IMPORTED'
                    AND target_content_id IS NOT NULL
                    AND target_version_id IS NOT NULL
                    AND completed_at IS NOT NULL
                    AND failure_code IS NULL
                )
                OR (
                    outcome = 'FAILED'
                    AND target_content_id IS NULL
                    AND target_version_id IS NULL
                    AND failure_code IS NOT NULL
                )
            )
    )
    """,
    """
    CREATE INDEX ix_migration_import_records_tenant_id
        ON content.migration_import_records (tenant_id)
    """,
    """
    CREATE OR REPLACE FUNCTION content.guard_migration_import_record_update()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = content, pg_temp
    AS $$
    BEGIN
        IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
           OR NEW.source_system IS DISTINCT FROM OLD.source_system
           OR NEW.source_resource_type IS DISTINCT FROM OLD.source_resource_type
           OR NEW.source_resource_id IS DISTINCT FROM OLD.source_resource_id
           OR NEW.source_version IS DISTINCT FROM OLD.source_version
           OR NEW.source_digest_sha256 IS DISTINCT FROM OLD.source_digest_sha256
           OR NEW.mapping_id IS DISTINCT FROM OLD.mapping_id
           OR NEW.mapping_version IS DISTINCT FROM OLD.mapping_version
           OR NEW.first_migration_batch_id IS DISTINCT FROM OLD.first_migration_batch_id
           OR NEW.first_attempt_at IS DISTINCT FROM OLD.first_attempt_at THEN
            RAISE EXCEPTION 'content.migration_import_records source evidence is immutable'
                USING ERRCODE = '27000';
        END IF;
        IF OLD.outcome = 'IMPORTED' THEN
            IF NEW.outcome IS DISTINCT FROM 'IMPORTED'
               OR NEW.target_content_id IS DISTINCT FROM OLD.target_content_id
               OR NEW.target_version_id IS DISTINCT FROM OLD.target_version_id
               OR NEW.completed_at IS DISTINCT FROM OLD.completed_at
               OR NEW.failure_code IS DISTINCT FROM OLD.failure_code
               OR NEW.attempt_count IS DISTINCT FROM OLD.attempt_count
               OR NEW.last_migration_batch_id IS DISTINCT FROM OLD.last_migration_batch_id
               OR NEW.last_attempt_at IS DISTINCT FROM OLD.last_attempt_at THEN
                RAISE EXCEPTION
                    'content.migration_import_records IMPORTED evidence is terminal'
                    USING ERRCODE = '27000';
            END IF;
        END IF;
        IF OLD.outcome = 'FAILED' AND NEW.outcome = 'IMPORTED' THEN
            IF NEW.target_content_id IS NULL
               OR NEW.target_version_id IS NULL
               OR NEW.completed_at IS NULL
               OR NEW.failure_code IS NOT NULL THEN
                RAISE EXCEPTION
                    'content.migration_import_records FAILED to IMPORTED transition invalid'
                    USING ERRCODE = '27000';
            END IF;
        ELSIF OLD.outcome = 'FAILED' AND NEW.outcome = 'FAILED' THEN
            IF NEW.target_content_id IS NOT NULL
               OR NEW.target_version_id IS NOT NULL THEN
                RAISE EXCEPTION
                    'content.migration_import_records FAILED retry must not set targets'
                    USING ERRCODE = '27000';
            END IF;
        ELSIF OLD.outcome IS DISTINCT FROM NEW.outcome THEN
            RAISE EXCEPTION
                'content.migration_import_records outcome transition is not allowed'
                USING ERRCODE = '27000';
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    """
    CREATE OR REPLACE FUNCTION content.reject_migration_import_record_delete()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = content, pg_temp
    AS $$
    BEGIN
        RAISE EXCEPTION 'content.migration_import_records deletes are not allowed'
            USING ERRCODE = '27000';
    END;
    $$
    """,
    """
    CREATE TRIGGER migration_import_records_guard_update
        BEFORE UPDATE ON content.migration_import_records
        FOR EACH ROW
        EXECUTE FUNCTION content.guard_migration_import_record_update()
    """,
    """
    CREATE TRIGGER migration_import_records_reject_delete
        BEFORE DELETE ON content.migration_import_records
        FOR EACH ROW
        EXECUTE FUNCTION content.reject_migration_import_record_delete()
    """,
    "ALTER TABLE content.migration_import_records ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE content.migration_import_records FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY migration_import_records_tenant_isolation
        ON content.migration_import_records
        FOR ALL
        USING (tenant_id = content.current_tenant_id())
        WITH CHECK (tenant_id = content.current_tenant_id())
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS content.migration_import_records")
    op.execute(
        "DROP FUNCTION IF EXISTS content.guard_migration_import_record_update()"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS content.reject_migration_import_record_delete()"
    )
    op.execute(
        "ALTER TABLE content.content_versions "
        "DROP CONSTRAINT IF EXISTS ck_content_versions_migration_import_provenance_v1"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS content.migration_import_provenance_v1_is_valid(jsonb)"
    )
