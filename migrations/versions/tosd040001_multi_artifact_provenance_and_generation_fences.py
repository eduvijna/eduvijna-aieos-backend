"""TOS-DEV04-I02 multi-artifact provenance and generation fences.

Evolves DEV03 persistence for ADR-AIEOS-052:

  * version-aware AI provenance DB validation (strict V1 + V2)
  * V1/V2 ContentVersion uniqueness (run vs run+artifact_kind)
  * GenerationRun Fence A (revision + capability outcome)
  * GenerationRun Fence B (single RUNNING per work + capability)

Does not rewrite historical migrations, backfill V1 artifact_kind, or
introduce result-bridge tables.

Revision ID: tosd040001
Revises: tosd030002
Create Date: 2026-08-29
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import context, op
from sqlalchemy import text

revision: str = "tosd040001"
down_revision: str | None = "tosd030002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CREATE_V2_VALIDATOR = """
CREATE OR REPLACE FUNCTION content.ai_generation_provenance_v2_is_valid(value jsonb)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
SET search_path = content, pg_temp
AS $$
DECLARE
    keys text[];
    expected text[] := ARRAY[
        'artifact_kind',
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
    artifact_kind text;
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
       OR (value->>'schema_version')::numeric <> 2
       OR (value->>'schema_version')::numeric
          <> trunc((value->>'schema_version')::numeric)
       OR (value->>'schema_version') IS DISTINCT FROM '2' THEN
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
    IF jsonb_typeof(value->'artifact_kind') <> 'string' THEN
        RETURN false;
    END IF;
    artifact_kind := value->>'artifact_kind';
    IF artifact_kind IS NULL
       OR artifact_kind !~ '^[a-z][a-z0-9._-]{0,63}$' THEN
        RETURN false;
    END IF;
    RETURN true;
END;
$$
"""

_CREATE_DISPATCHER = """
CREATE OR REPLACE FUNCTION content.ai_generation_provenance_is_valid(value jsonb)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
SET search_path = content, pg_temp
AS $$
BEGIN
    IF value IS NULL OR jsonb_typeof(value) <> 'object' THEN
        RETURN false;
    END IF;
    IF jsonb_typeof(value->'schema_version') <> 'number'
       OR (value->>'schema_version')::numeric
          <> trunc((value->>'schema_version')::numeric) THEN
        RETURN false;
    END IF;
    IF (value->>'schema_version') IS NOT DISTINCT FROM '1' THEN
        RETURN content.ai_generation_provenance_v1_is_valid(value);
    END IF;
    IF (value->>'schema_version') IS NOT DISTINCT FROM '2' THEN
        RETURN content.ai_generation_provenance_v2_is_valid(value);
    END IF;
    RETURN false;
END;
$$
"""


def _assert_upgrade_preconditions(connection) -> None:
    """Fail closed if current rows cannot satisfy the evolved uniqueness fences."""
    if context.is_offline_mode():
        # Offline SQL cannot query live row state; CREATE UNIQUE INDEX still
        # fails closed online if historical duplicates exist.
        return
    connection.execute(text("ALTER TABLE ai.generation_runs DISABLE ROW LEVEL SECURITY"))
    connection.execute(
        text("ALTER TABLE content.content_versions DISABLE ROW LEVEL SECURITY")
    )
    try:
        conflict = connection.execute(
            text(
                """
                SELECT tenant_id::text,
                       work_resource_id::text,
                       work_resource_revision::text,
                       capability_id,
                       count(*) AS n
                  FROM ai.generation_runs
                 WHERE status IN ('RUNNING', 'SUCCEEDED')
                 GROUP BY tenant_id, work_resource_id, work_resource_revision, capability_id
                HAVING count(*) > 1
                 LIMIT 1
                """
            )
        ).mappings().first()
        if conflict is not None:
            raise RuntimeError(
                "tosd040001 refuse upgrade: GenerationRun Fence A would be violated by "
                f"existing rows ({conflict})"
            )

        running_conflict = connection.execute(
            text(
                """
                SELECT tenant_id::text,
                       work_resource_id::text,
                       capability_id,
                       count(*) AS n
                  FROM ai.generation_runs
                 WHERE status = 'RUNNING'
                 GROUP BY tenant_id, work_resource_id, capability_id
                HAVING count(*) > 1
                 LIMIT 1
                """
            )
        ).mappings().first()
        if running_conflict is not None:
            raise RuntimeError(
                "tosd040001 refuse upgrade: GenerationRun Fence B would be violated by "
                f"existing rows ({running_conflict})"
            )

        v1_dup = connection.execute(
            text(
                """
                SELECT tenant_id::text,
                       provenance #>> '{generation_run_ref,resource_id}' AS run_id,
                       count(*) AS n
                  FROM content.content_versions
                 WHERE origin = 'AI'
                   AND provenance IS NOT NULL
                   AND (provenance->>'schema_version') = '1'
                 GROUP BY tenant_id, provenance #>> '{generation_run_ref,resource_id}'
                HAVING count(*) > 1
                 LIMIT 1
                """
            )
        ).mappings().first()
        if v1_dup is not None:
            raise RuntimeError(
                "tosd040001 refuse upgrade: V1 ContentVersion uniqueness would be "
                f"violated by existing rows ({v1_dup})"
            )
    finally:
        connection.execute(
            text("ALTER TABLE ai.generation_runs ENABLE ROW LEVEL SECURITY")
        )
        connection.execute(
            text("ALTER TABLE ai.generation_runs FORCE ROW LEVEL SECURITY")
        )
        connection.execute(
            text("ALTER TABLE content.content_versions ENABLE ROW LEVEL SECURITY")
        )
        connection.execute(
            text("ALTER TABLE content.content_versions FORCE ROW LEVEL SECURITY")
        )


def _assert_downgrade_safe(connection) -> None:
    """Refuse downgrade when V2 / multi-artifact / multi-outcome state exists."""
    if context.is_offline_mode():
        return
    connection.execute(text("ALTER TABLE ai.generation_runs DISABLE ROW LEVEL SECURITY"))
    connection.execute(
        text("ALTER TABLE content.content_versions DISABLE ROW LEVEL SECURITY")
    )
    try:
        v2_count = connection.execute(
            text(
                """
                SELECT count(*)
                  FROM content.content_versions
                 WHERE origin = 'AI'
                   AND provenance IS NOT NULL
                   AND (provenance->>'schema_version') = '2'
                """
            )
        ).scalar_one()
        if int(v2_count) > 0:
            raise RuntimeError(
                "tosd040001 refuse downgrade: AI provenance schema_version=2 "
                "ContentVersions exist; cannot recreate V1-only CHECK safely"
            )

        multi = connection.execute(
            text(
                """
                SELECT tenant_id::text,
                       provenance #>> '{generation_run_ref,resource_id}' AS run_id,
                       count(*) AS n
                  FROM content.content_versions
                 WHERE origin = 'AI'
                   AND provenance IS NOT NULL
                   AND provenance ? 'generation_run_ref'
                 GROUP BY tenant_id, provenance #>> '{generation_run_ref,resource_id}'
                HAVING count(*) > 1
                 LIMIT 1
                """
            )
        ).mappings().first()
        if multi is not None:
            raise RuntimeError(
                "tosd040001 refuse downgrade: multiple AI ContentVersions share a "
                f"generation_run_id ({multi}); old unique index cannot be recreated"
            )

        work_fence = connection.execute(
            text(
                """
                SELECT tenant_id::text,
                       work_resource_id::text,
                       count(*) AS n
                  FROM ai.generation_runs
                 WHERE status IN ('RUNNING', 'SUCCEEDED')
                 GROUP BY tenant_id, work_resource_id
                HAVING count(*) > 1
                 LIMIT 1
                """
            )
        ).mappings().first()
        if work_fence is not None:
            raise RuntimeError(
                "tosd040001 refuse downgrade: multiple RUNNING|SUCCEEDED GenerationRuns "
                f"exist for one work ({work_fence}); old work-only fence cannot be "
                "recreated without discarding capability/revision outcomes"
            )
    finally:
        connection.execute(
            text("ALTER TABLE ai.generation_runs ENABLE ROW LEVEL SECURITY")
        )
        connection.execute(
            text("ALTER TABLE ai.generation_runs FORCE ROW LEVEL SECURITY")
        )
        connection.execute(
            text("ALTER TABLE content.content_versions ENABLE ROW LEVEL SECURITY")
        )
        connection.execute(
            text("ALTER TABLE content.content_versions FORCE ROW LEVEL SECURITY")
        )


def upgrade() -> None:
    connection = op.get_bind()
    _assert_upgrade_preconditions(connection)

    op.execute(_CREATE_V2_VALIDATOR)
    op.execute(_CREATE_DISPATCHER)

    op.execute(
        """
        ALTER TABLE content.content_versions
            DROP CONSTRAINT IF EXISTS ck_content_versions_ai_provenance_v1
        """
    )
    op.execute(
        """
        ALTER TABLE content.content_versions
            ADD CONSTRAINT ck_content_versions_ai_provenance
            CHECK (
                origin <> 'AI'
                OR content.ai_generation_provenance_is_valid(provenance)
            )
        """
    )

    op.execute("DROP INDEX IF EXISTS content.uq_content_versions_ai_generation_run_id")
    op.execute("DROP INDEX IF EXISTS ai.uq_ai_generation_runs_work_active_or_succeeded")

    op.execute(
        """
        CREATE UNIQUE INDEX uq_content_versions_ai_generation_run_id_v1
            ON content.content_versions (
                tenant_id,
                (provenance #>> '{generation_run_ref,resource_id}')
            )
            WHERE origin = 'AI'
              AND provenance IS NOT NULL
              AND (provenance->>'schema_version') = '1'
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_content_versions_ai_generation_run_artifact_v2
            ON content.content_versions (
                tenant_id,
                (provenance #>> '{generation_run_ref,resource_id}'),
                (provenance->>'artifact_kind')
            )
            WHERE origin = 'AI'
              AND provenance IS NOT NULL
              AND (provenance->>'schema_version') = '2'
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_ai_generation_runs_work_revision_capability_outcome
            ON ai.generation_runs (
                tenant_id,
                work_resource_id,
                work_resource_revision,
                capability_id
            )
            WHERE status IN ('RUNNING', 'SUCCEEDED')
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_ai_generation_runs_work_capability_running
            ON ai.generation_runs (
                tenant_id,
                work_resource_id,
                capability_id
            )
            WHERE status = 'RUNNING'
        """
    )


def downgrade() -> None:
    connection = op.get_bind()
    _assert_downgrade_safe(connection)

    op.execute(
        "DROP INDEX IF EXISTS ai.uq_ai_generation_runs_work_capability_running"
    )
    op.execute(
        "DROP INDEX IF EXISTS ai.uq_ai_generation_runs_work_revision_capability_outcome"
    )
    op.execute(
        "DROP INDEX IF EXISTS content.uq_content_versions_ai_generation_run_artifact_v2"
    )
    op.execute(
        "DROP INDEX IF EXISTS content.uq_content_versions_ai_generation_run_id_v1"
    )

    op.execute(
        """
        ALTER TABLE content.content_versions
            DROP CONSTRAINT IF EXISTS ck_content_versions_ai_provenance
        """
    )
    op.execute(
        """
        ALTER TABLE content.content_versions
            ADD CONSTRAINT ck_content_versions_ai_provenance_v1
            CHECK (
                origin <> 'AI'
                OR content.ai_generation_provenance_v1_is_valid(provenance)
            )
        """
    )

    op.execute(
        """
        CREATE UNIQUE INDEX uq_content_versions_ai_generation_run_id
            ON content.content_versions (
                tenant_id,
                (provenance #>> '{generation_run_ref,resource_id}')
            )
            WHERE origin = 'AI'
              AND provenance IS NOT NULL
              AND provenance ? 'generation_run_ref'
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_ai_generation_runs_work_active_or_succeeded
            ON ai.generation_runs (tenant_id, work_resource_id)
            WHERE status IN ('RUNNING', 'SUCCEEDED')
        """
    )

    op.execute(
        "DROP FUNCTION IF EXISTS content.ai_generation_provenance_is_valid(jsonb)"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS content.ai_generation_provenance_v2_is_valid(jsonb)"
    )
