"""GCI-I07 workflow start and command intents.

Revision ID: gcii070001
Revises: gcii060001
Create Date: 2026-08-14
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "gcii070001"
down_revision: str | None = "gcii060001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE_STATEMENTS: tuple[str, ...] = (
    "CREATE SCHEMA workflow",
    """
    CREATE OR REPLACE FUNCTION workflow.current_tenant_id()
    RETURNS uuid
    LANGUAGE plpgsql
    VOLATILE
    SET search_path = workflow, pg_temp
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
    CREATE TABLE workflow.workflow_start_intents (
        workflow_start_intent_id UUID NOT NULL,
        tenant_id UUID NOT NULL,
        workflow_instance_id UUID NOT NULL,
        workflow_type TEXT NOT NULL,
        workflow_major_version INTEGER NOT NULL,
        temporal_workflow_id TEXT NOT NULL,
        task_queue TEXT NOT NULL,
        business_key TEXT NOT NULL,
        input JSONB NOT NULL,
        status TEXT NOT NULL,
        attempt_count INTEGER NOT NULL,
        available_at TIMESTAMPTZ NOT NULL,
        claimed_by TEXT NULL,
        claimed_until TIMESTAMPTZ NULL,
        delivered_at TIMESTAMPTZ NULL,
        last_error_code TEXT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT pk_workflow_start_intents PRIMARY KEY (workflow_start_intent_id),
        CONSTRAINT uq_workflow_start_intents_instance UNIQUE (workflow_instance_id),
        CONSTRAINT uq_workflow_start_intents_temporal_id UNIQUE (temporal_workflow_id),
        CONSTRAINT uq_workflow_start_intents_business_key UNIQUE (
            tenant_id,
            workflow_type,
            business_key
        ),
        CONSTRAINT ck_workflow_start_intents_major
            CHECK (workflow_major_version > 0),
        CONSTRAINT ck_workflow_start_intents_attempts
            CHECK (attempt_count >= 0),
        CONSTRAINT ck_workflow_start_intents_status
            CHECK (status IN ('PENDING', 'CLAIMED', 'DELIVERED', 'QUARANTINED')),
        CONSTRAINT ck_workflow_start_intents_input_object
            CHECK (jsonb_typeof(input) = 'object'),
        CONSTRAINT ck_workflow_start_intents_workflow_type
            CHECK (
                char_length(workflow_type) BETWEEN 1 AND 128
                AND btrim(workflow_type) <> ''
            ),
        CONSTRAINT ck_workflow_start_intents_temporal_id
            CHECK (
                char_length(temporal_workflow_id) BETWEEN 1 AND 255
                AND btrim(temporal_workflow_id) <> ''
            ),
        CONSTRAINT ck_workflow_start_intents_task_queue
            CHECK (
                char_length(task_queue) BETWEEN 1 AND 128
                AND btrim(task_queue) <> ''
            ),
        CONSTRAINT ck_workflow_start_intents_business_key
            CHECK (
                char_length(business_key) BETWEEN 1 AND 255
                AND btrim(business_key) <> ''
            ),
        CONSTRAINT ck_workflow_start_intents_error_code
            CHECK (
                last_error_code IS NULL
                OR (
                    char_length(last_error_code) BETWEEN 1 AND 64
                    AND btrim(last_error_code) <> ''
                )
            )
    )
    """,
    """
    CREATE INDEX ix_workflow_start_intents_dispatch
        ON workflow.workflow_start_intents (tenant_id, status, available_at)
    """,
    """
    CREATE TABLE workflow.workflow_command_intents (
        workflow_command_intent_id UUID NOT NULL,
        tenant_id UUID NOT NULL,
        workflow_instance_id UUID NOT NULL,
        temporal_workflow_id TEXT NOT NULL,
        command_id UUID NOT NULL,
        command_type TEXT NOT NULL,
        business_key TEXT NOT NULL,
        payload JSONB NOT NULL,
        status TEXT NOT NULL,
        attempt_count INTEGER NOT NULL,
        available_at TIMESTAMPTZ NOT NULL,
        claimed_by TEXT NULL,
        claimed_until TIMESTAMPTZ NULL,
        delivered_at TIMESTAMPTZ NULL,
        last_error_code TEXT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT pk_workflow_command_intents PRIMARY KEY (workflow_command_intent_id),
        CONSTRAINT uq_workflow_command_intents_command_id UNIQUE (command_id),
        CONSTRAINT uq_workflow_command_intents_business_key UNIQUE (
            tenant_id,
            business_key
        ),
        CONSTRAINT fk_workflow_command_intents_instance
            FOREIGN KEY (workflow_instance_id)
            REFERENCES workflow.workflow_start_intents (workflow_instance_id)
            ON DELETE RESTRICT,
        CONSTRAINT ck_workflow_command_intents_attempts
            CHECK (attempt_count >= 0),
        CONSTRAINT ck_workflow_command_intents_status
            CHECK (status IN ('PENDING', 'CLAIMED', 'DELIVERED', 'QUARANTINED')),
        CONSTRAINT ck_workflow_command_intents_payload_object
            CHECK (jsonb_typeof(payload) = 'object'),
        CONSTRAINT ck_workflow_command_intents_command_type
            CHECK (
                char_length(command_type) BETWEEN 1 AND 128
                AND btrim(command_type) <> ''
            ),
        CONSTRAINT ck_workflow_command_intents_temporal_id
            CHECK (
                char_length(temporal_workflow_id) BETWEEN 1 AND 255
                AND btrim(temporal_workflow_id) <> ''
            ),
        CONSTRAINT ck_workflow_command_intents_business_key
            CHECK (
                char_length(business_key) BETWEEN 1 AND 255
                AND btrim(business_key) <> ''
            ),
        CONSTRAINT ck_workflow_command_intents_error_code
            CHECK (
                last_error_code IS NULL
                OR (
                    char_length(last_error_code) BETWEEN 1 AND 64
                    AND btrim(last_error_code) <> ''
                )
            )
    )
    """,
    """
    CREATE INDEX ix_workflow_command_intents_dispatch
        ON workflow.workflow_command_intents (tenant_id, status, available_at)
    """,
    "ALTER TABLE workflow.workflow_start_intents ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE workflow.workflow_start_intents FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY workflow_start_intents_tenant_isolation
        ON workflow.workflow_start_intents
        FOR ALL
        USING (tenant_id = workflow.current_tenant_id())
        WITH CHECK (tenant_id = workflow.current_tenant_id())
    """,
    "ALTER TABLE workflow.workflow_command_intents ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE workflow.workflow_command_intents FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY workflow_command_intents_tenant_isolation
        ON workflow.workflow_command_intents
        FOR ALL
        USING (tenant_id = workflow.current_tenant_id())
        WITH CHECK (tenant_id = workflow.current_tenant_id())
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS workflow CASCADE")
