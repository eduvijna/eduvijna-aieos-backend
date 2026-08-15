"""SAI-I02 PostgreSQL security audit ledger with RLS and immutability.

Revision ID: saii020001
Revises: gcii130001
Create Date: 2026-08-15

Executes under AIEOS_SECURITY_SCHEMA_OWNER_ROLE, then restores
AIEOS_SCHEMA_OWNER_ROLE so prior/future Content migrations stay on the
content owner.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence

from alembic import op

revision: str = "saii020001"
down_revision: str | None = "gcii130001"
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
    CREATE SCHEMA security
    """,
    """
    REVOKE ALL ON SCHEMA security FROM PUBLIC
    """,
    """
    CREATE OR REPLACE FUNCTION security.current_tenant_id()
    RETURNS uuid
    LANGUAGE plpgsql
    STABLE
    SET search_path = security, pg_temp
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
    CREATE OR REPLACE FUNCTION security.related_resource_refs_are_valid(
        value jsonb,
        primary_resource_type text,
        primary_resource_id uuid,
        primary_resource_revision bigint
    )
    RETURNS boolean
    LANGUAGE plpgsql
    IMMUTABLE
    SET search_path = security, pg_temp
    AS $$
    DECLARE
        item jsonb;
        idx int;
        n int;
        keys text[];
        resource_type text;
        resource_id text;
        resource_id_uuid uuid;
        resource_revision jsonb;
        revision_value bigint;
        seen text[];
        fingerprint text;
    BEGIN
        IF value IS NULL OR jsonb_typeof(value) <> 'array' THEN
            RETURN false;
        END IF;
        n := jsonb_array_length(value);
        IF n IS NULL OR n > 16 THEN
            RETURN false;
        END IF;
        seen := ARRAY[]::text[];
        IF n = 0 THEN
            RETURN true;
        END IF;
        FOR idx IN 0 .. (n - 1) LOOP
            item := value -> idx;
            IF item IS NULL OR jsonb_typeof(item) <> 'object' THEN
                RETURN false;
            END IF;
            SELECT array_agg(k ORDER BY k)
              INTO keys
              FROM jsonb_object_keys(item) AS k;
            IF keys IS DISTINCT FROM
               ARRAY['resource_id', 'resource_revision', 'resource_type'] THEN
                RETURN false;
            END IF;
            IF jsonb_typeof(item->'resource_type') <> 'string' THEN
                RETURN false;
            END IF;
            resource_type := item->>'resource_type';
            IF resource_type IS NULL
               OR resource_type !~ '^[a-z][a-z0-9._-]{0,63}$' THEN
                RETURN false;
            END IF;
            IF jsonb_typeof(item->'resource_id') <> 'string' THEN
                RETURN false;
            END IF;
            resource_id := item->>'resource_id';
            BEGIN
                resource_id_uuid := resource_id::uuid;
            EXCEPTION
                WHEN invalid_text_representation THEN
                    RETURN false;
            END;
            resource_revision := item->'resource_revision';
            IF resource_revision IS NULL
               OR jsonb_typeof(resource_revision) = 'null' THEN
                revision_value := NULL;
            ELSE
                IF jsonb_typeof(resource_revision) <> 'number' THEN
                    RETURN false;
                END IF;
                -- Strict canonical integer text only (reject 1.0, 1e2, etc.).
                IF (resource_revision::text) !~ '^[0-9]+$' THEN
                    RETURN false;
                END IF;
                BEGIN
                    revision_value := (resource_revision::text)::bigint;
                EXCEPTION
                    WHEN numeric_value_out_of_range
                        OR data_exception
                        OR invalid_text_representation THEN
                        RETURN false;
                END;
                IF revision_value < 0 THEN
                    RETURN false;
                END IF;
            END IF;
            IF resource_type = primary_resource_type
               AND resource_id_uuid = primary_resource_id
               AND revision_value IS NOT DISTINCT FROM primary_resource_revision THEN
                RETURN false;
            END IF;
            fingerprint := resource_type
                || '|'
                || resource_id_uuid::text
                || '|'
                || COALESCE(revision_value::text, 'null');
            IF fingerprint = ANY (seen) THEN
                RETURN false;
            END IF;
            seen := array_append(seen, fingerprint);
        END LOOP;
        RETURN true;
    END;
    $$
    """,
    """
    CREATE TABLE security.audit_records (
        audit_record_id UUID NOT NULL,
        tenant_id UUID NOT NULL,
        action TEXT NOT NULL,
        primary_resource_type TEXT NOT NULL,
        primary_resource_id UUID NOT NULL,
        primary_resource_revision BIGINT NOT NULL,
        resource_revision_before BIGINT NULL,
        resource_revision_after BIGINT NOT NULL,
        related_resource_refs JSONB NOT NULL,
        initiating_principal_id UUID NOT NULL,
        effective_actor_id UUID NOT NULL,
        executing_principal_id UUID NOT NULL,
        delegation_id UUID NULL,
        execution_channel TEXT NOT NULL,
        correlation_id UUID NOT NULL,
        causation_id UUID NOT NULL,
        trace_id TEXT NULL,
        occurred_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT pk_audit_records PRIMARY KEY (audit_record_id),
        CONSTRAINT ck_audit_records_id_uuidv7
            CHECK ((get_byte(uuid_send(audit_record_id), 6) >> 4) = 7),
        CONSTRAINT ck_audit_records_action
            CHECK (
                action IN (
                    'content.create',
                    'content.version.create',
                    'content.review.submit',
                    'content.review.approve',
                    'content.review.request_changes',
                    'content.review.reject',
                    'content.publish',
                    'content.ai.materialize',
                    'content.migration.import'
                )
            ),
        CONSTRAINT ck_audit_records_execution_channel
            CHECK (
                execution_channel IN (
                    'API',
                    'WORKFLOW_ACTIVITY',
                    'AI_MATERIALIZATION',
                    'MIGRATION',
                    'SYSTEM'
                )
            ),
        CONSTRAINT ck_audit_records_primary_resource_type
            CHECK (
                primary_resource_type ~ '^[a-z][a-z0-9._-]{0,63}$'
            ),
        CONSTRAINT ck_audit_records_before_nonneg
            CHECK (
                resource_revision_before IS NULL
                OR resource_revision_before >= 0
            ),
        CONSTRAINT ck_audit_records_after_nonneg
            CHECK (resource_revision_after >= 0),
        CONSTRAINT ck_audit_records_primary_rev_nonneg
            CHECK (primary_resource_revision >= 0),
        CONSTRAINT ck_audit_records_primary_revision_matches_after
            CHECK (primary_resource_revision = resource_revision_after),
        CONSTRAINT ck_audit_records_revision_semantics
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
                        'content.version.create',
                        'content.review.submit',
                        'content.review.approve',
                        'content.review.request_changes',
                        'content.review.reject',
                        'content.publish',
                        'content.ai.materialize'
                    )
                    AND resource_revision_before IS NOT NULL
                    AND resource_revision_after = resource_revision_before + 1
                )
            ),
        CONSTRAINT ck_audit_records_related_refs_valid
            CHECK (
                security.related_resource_refs_are_valid(
                    related_resource_refs,
                    primary_resource_type,
                    primary_resource_id,
                    primary_resource_revision
                )
            ),
        CONSTRAINT ck_audit_records_trace_id
            CHECK (
                trace_id IS NULL
                OR (
                    trace_id ~ '^[0-9a-f]{32}$'
                    AND trace_id <> repeat('0', 32)
                )
            )
    )
    """,
    """
    CREATE OR REPLACE FUNCTION security.reject_audit_record_mutation()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = security, pg_temp
    AS $$
    BEGIN
        RAISE EXCEPTION 'security.audit_records is immutable'
            USING ERRCODE = '27000';
    END;
    $$
    """,
    """
    CREATE TRIGGER audit_records_immutable_update
        BEFORE UPDATE ON security.audit_records
        FOR EACH ROW
        EXECUTE FUNCTION security.reject_audit_record_mutation()
    """,
    """
    CREATE TRIGGER audit_records_immutable_delete
        BEFORE DELETE ON security.audit_records
        FOR EACH ROW
        EXECUTE FUNCTION security.reject_audit_record_mutation()
    """,
    "ALTER TABLE security.audit_records ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE security.audit_records FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY audit_records_tenant_insert ON security.audit_records
        FOR INSERT
        WITH CHECK (tenant_id = security.current_tenant_id())
    """,
    """
    REVOKE ALL ON TABLE security.audit_records FROM PUBLIC
    """,
    """
    REVOKE ALL ON FUNCTION security.current_tenant_id() FROM PUBLIC
    """,
    """
    REVOKE ALL ON FUNCTION security.related_resource_refs_are_valid(
        jsonb, text, uuid, bigint
    ) FROM PUBLIC
    """,
    """
    REVOKE ALL ON FUNCTION security.reject_audit_record_mutation() FROM PUBLIC
    """,
)

DOWNGRADE_STATEMENTS: tuple[str, ...] = (
    "DROP TABLE IF EXISTS security.audit_records",
    "DROP FUNCTION IF EXISTS security.reject_audit_record_mutation()",
    """
    DROP FUNCTION IF EXISTS security.related_resource_refs_are_valid(
        jsonb, text, uuid, bigint
    )
    """,
    "DROP FUNCTION IF EXISTS security.current_tenant_id()",
    "DROP SCHEMA IF EXISTS security",
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
