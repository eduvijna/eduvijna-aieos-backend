"""GCI-I11 AI generation provenance V1 DB defense-in-depth.

Revision ID: gcii110001
Revises: gcii100001
Create Date: 2026-08-14
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "gcii110001"
down_revision: str | None = "gcii100001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE_STATEMENTS: tuple[str, ...] = (
    """
    CREATE OR REPLACE FUNCTION content.resource_ref_json_is_valid(value jsonb)
    RETURNS boolean
    LANGUAGE plpgsql
    IMMUTABLE
    SET search_path = content, pg_temp
    AS $$
    DECLARE
        keys text[];
        resource_type text;
        resource_id text;
        resource_revision jsonb;
    BEGIN
        IF value IS NULL OR jsonb_typeof(value) <> 'object' THEN
            RETURN false;
        END IF;
        SELECT array_agg(k ORDER BY k)
          INTO keys
          FROM jsonb_object_keys(value) AS k;
        IF keys IS DISTINCT FROM ARRAY['resource_id', 'resource_revision', 'resource_type'] THEN
            RETURN false;
        END IF;
        IF jsonb_typeof(value->'resource_type') <> 'string' THEN
            RETURN false;
        END IF;
        resource_type := value->>'resource_type';
        IF resource_type IS NULL
           OR resource_type !~ '^[a-z][a-z0-9._-]{0,63}$' THEN
            RETURN false;
        END IF;
        IF jsonb_typeof(value->'resource_id') <> 'string' THEN
            RETURN false;
        END IF;
        resource_id := value->>'resource_id';
        BEGIN
            PERFORM resource_id::uuid;
        EXCEPTION
            WHEN invalid_text_representation THEN
                RETURN false;
        END;
        resource_revision := value->'resource_revision';
        IF resource_revision IS NULL
           OR jsonb_typeof(resource_revision) = 'null' THEN
            RETURN true;
        END IF;
        IF jsonb_typeof(resource_revision) <> 'number' THEN
            RETURN false;
        END IF;
        IF (resource_revision::text)::numeric <> trunc((resource_revision::text)::numeric)
           OR (resource_revision::text)::numeric < 0
           OR (resource_revision::text)::numeric <> ((resource_revision::text)::numeric)::bigint THEN
            RETURN false;
        END IF;
        RETURN true;
    END;
    $$
    """,
    """
    CREATE OR REPLACE FUNCTION content.ai_generation_provenance_v1_is_valid(value jsonb)
    RETURNS boolean
    LANGUAGE plpgsql
    IMMUTABLE
    SET search_path = content, pg_temp
    AS $$
    DECLARE
        keys text[];
        expected text[] := ARRAY[
            'capability_id',
            'correlation_id',
            'evaluation_refs',
            'generation_run_ref',
            'kind',
            'model_id',
            'policy_refs',
            'prompt_execution_ref',
            'provider_id',
            'schema_version',
            'source_refs'
        ];
        item jsonb;
        model_id text;
        provider_id text;
        capability_id text;
        correlation_id text;
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
        IF value->>'kind' IS DISTINCT FROM 'ai_generation' THEN
            RETURN false;
        END IF;
        IF jsonb_typeof(value->'schema_version') <> 'number'
           OR (value->>'schema_version')::numeric <> 1 THEN
            RETURN false;
        END IF;
        IF NOT content.resource_ref_json_is_valid(value->'generation_run_ref') THEN
            RETURN false;
        END IF;
        IF value->'prompt_execution_ref' IS NULL
           OR jsonb_typeof(value->'prompt_execution_ref') = 'null' THEN
            NULL;
        ELSIF NOT content.resource_ref_json_is_valid(value->'prompt_execution_ref') THEN
            RETURN false;
        END IF;
        IF jsonb_typeof(value->'provider_id') <> 'string' THEN
            RETURN false;
        END IF;
        provider_id := value->>'provider_id';
        IF provider_id IS NULL OR provider_id !~ '^[a-z][a-z0-9._-]{0,63}$' THEN
            RETURN false;
        END IF;
        IF jsonb_typeof(value->'model_id') <> 'string' THEN
            RETURN false;
        END IF;
        model_id := value->>'model_id';
        IF model_id IS NULL
           OR length(model_id) = 0
           OR octet_length(model_id) > 255
           OR model_id ~ '[[:cntrl:]]' THEN
            RETURN false;
        END IF;
        IF jsonb_typeof(value->'capability_id') <> 'string' THEN
            RETURN false;
        END IF;
        capability_id := value->>'capability_id';
        IF capability_id IS NULL
           OR capability_id !~ '^[a-z][a-z0-9._-]{0,63}$' THEN
            RETURN false;
        END IF;
        IF jsonb_typeof(value->'source_refs') <> 'array' THEN
            RETURN false;
        END IF;
        FOR item IN SELECT * FROM jsonb_array_elements(value->'source_refs')
        LOOP
            IF NOT content.resource_ref_json_is_valid(item) THEN
                RETURN false;
            END IF;
        END LOOP;
        IF jsonb_typeof(value->'policy_refs') <> 'array' THEN
            RETURN false;
        END IF;
        FOR item IN SELECT * FROM jsonb_array_elements(value->'policy_refs')
        LOOP
            IF NOT content.resource_ref_json_is_valid(item) THEN
                RETURN false;
            END IF;
        END LOOP;
        IF jsonb_typeof(value->'evaluation_refs') <> 'array' THEN
            RETURN false;
        END IF;
        FOR item IN SELECT * FROM jsonb_array_elements(value->'evaluation_refs')
        LOOP
            IF NOT content.resource_ref_json_is_valid(item) THEN
                RETURN false;
            END IF;
        END LOOP;
        IF jsonb_typeof(value->'correlation_id') <> 'string' THEN
            RETURN false;
        END IF;
        correlation_id := value->>'correlation_id';
        BEGIN
            PERFORM correlation_id::uuid;
        EXCEPTION
            WHEN invalid_text_representation THEN
                RETURN false;
        END;
        RETURN true;
    END;
    $$
    """,
    """
    ALTER TABLE content.content_versions
        ADD CONSTRAINT ck_content_versions_ai_provenance_v1
        CHECK (
            origin <> 'AI'
            OR content.ai_generation_provenance_v1_is_valid(provenance)
        )
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute(
        "ALTER TABLE content.content_versions "
        "DROP CONSTRAINT IF EXISTS ck_content_versions_ai_provenance_v1"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS content.ai_generation_provenance_v1_is_valid(jsonb)"
    )
    op.execute("DROP FUNCTION IF EXISTS content.resource_ref_json_is_valid(jsonb)")
