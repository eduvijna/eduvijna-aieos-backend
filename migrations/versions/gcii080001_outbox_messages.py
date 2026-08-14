"""GCI-I08 transactional outbox for Content CloudEvents.

Revision ID: gcii080001
Revises: gcii070001
Create Date: 2026-08-14
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "gcii080001"
down_revision: str | None = "gcii070001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE_STATEMENTS: tuple[str, ...] = (
    "CREATE SCHEMA integration",
    """
    CREATE OR REPLACE FUNCTION integration.current_tenant_id()
    RETURNS uuid
    LANGUAGE plpgsql
    VOLATILE
    SET search_path = integration, pg_temp
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
    CREATE TABLE integration.outbox_messages (
        event_id UUID NOT NULL,
        tenant_id UUID NOT NULL,
        event_type TEXT NOT NULL,
        subject TEXT NOT NULL,
        aggregate_type TEXT NOT NULL,
        aggregate_id UUID NOT NULL,
        aggregate_revision BIGINT NOT NULL,
        envelope JSONB NOT NULL,
        status TEXT NOT NULL,
        attempt_count INTEGER NOT NULL,
        available_at TIMESTAMPTZ NOT NULL,
        claimed_by TEXT NULL,
        claimed_until TIMESTAMPTZ NULL,
        published_at TIMESTAMPTZ NULL,
        broker_stream TEXT NULL,
        broker_sequence BIGINT NULL,
        last_error_code TEXT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT pk_outbox_messages PRIMARY KEY (event_id),
        CONSTRAINT uq_outbox_messages_business_event UNIQUE (
            tenant_id,
            aggregate_type,
            aggregate_id,
            aggregate_revision,
            event_type
        ),
        CONSTRAINT ck_outbox_messages_revision
            CHECK (aggregate_revision >= 0),
        CONSTRAINT ck_outbox_messages_attempts
            CHECK (attempt_count >= 0),
        CONSTRAINT ck_outbox_messages_status
            CHECK (status IN ('PENDING', 'CLAIMED', 'PUBLISHED', 'QUARANTINED')),
        CONSTRAINT ck_outbox_messages_envelope_object
            CHECK (jsonb_typeof(envelope) = 'object'),
        CONSTRAINT ck_outbox_messages_event_type
            CHECK (
                char_length(event_type) BETWEEN 1 AND 255
                AND btrim(event_type) <> ''
            ),
        CONSTRAINT ck_outbox_messages_subject
            CHECK (
                char_length(subject) BETWEEN 1 AND 255
                AND btrim(subject) <> ''
            ),
        CONSTRAINT ck_outbox_messages_aggregate_type
            CHECK (
                char_length(aggregate_type) BETWEEN 1 AND 64
                AND btrim(aggregate_type) <> ''
            ),
        CONSTRAINT ck_outbox_messages_error_code
            CHECK (
                last_error_code IS NULL
                OR (
                    char_length(last_error_code) BETWEEN 1 AND 64
                    AND btrim(last_error_code) <> ''
                )
            ),
        CONSTRAINT ck_outbox_messages_published_at
            CHECK (
                (status = 'PUBLISHED' AND published_at IS NOT NULL)
                OR (status <> 'PUBLISHED' AND published_at IS NULL)
            )
    )
    """,
    """
    CREATE INDEX ix_outbox_messages_dispatch
        ON integration.outbox_messages (tenant_id, status, available_at)
    """,
    """
    CREATE OR REPLACE FUNCTION integration.reject_outbox_immutable_fact_mutation()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = integration, pg_temp
    AS $$
    BEGIN
        IF NEW.event_id IS DISTINCT FROM OLD.event_id
           OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
           OR NEW.event_type IS DISTINCT FROM OLD.event_type
           OR NEW.subject IS DISTINCT FROM OLD.subject
           OR NEW.aggregate_type IS DISTINCT FROM OLD.aggregate_type
           OR NEW.aggregate_id IS DISTINCT FROM OLD.aggregate_id
           OR NEW.aggregate_revision IS DISTINCT FROM OLD.aggregate_revision
           OR NEW.envelope IS DISTINCT FROM OLD.envelope
           OR NEW.created_at IS DISTINCT FROM OLD.created_at
        THEN
            RAISE EXCEPTION 'integration.outbox_messages event facts are immutable'
                USING ERRCODE = '27000';
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    """
    CREATE TRIGGER outbox_messages_immutable_facts
        BEFORE UPDATE ON integration.outbox_messages
        FOR EACH ROW
        EXECUTE FUNCTION integration.reject_outbox_immutable_fact_mutation()
    """,
    """
    CREATE OR REPLACE FUNCTION integration.reject_outbox_delete()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = integration, pg_temp
    AS $$
    BEGIN
        RAISE EXCEPTION 'integration.outbox_messages deletes are not allowed'
            USING ERRCODE = '27000';
    END;
    $$
    """,
    """
    CREATE TRIGGER outbox_messages_no_delete
        BEFORE DELETE ON integration.outbox_messages
        FOR EACH ROW
        EXECUTE FUNCTION integration.reject_outbox_delete()
    """,
    "ALTER TABLE integration.outbox_messages ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE integration.outbox_messages FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY outbox_messages_tenant_isolation
        ON integration.outbox_messages
        FOR ALL
        USING (tenant_id = integration.current_tenant_id())
        WITH CHECK (tenant_id = integration.current_tenant_id())
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS integration CASCADE")
