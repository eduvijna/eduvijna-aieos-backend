"""GCI-I06 Generic Content review decisions.

Revision ID: gcii060001
Revises: gcii050001
Create Date: 2026-08-14
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "gcii060001"
down_revision: str | None = "gcii050001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE content.review_decisions (
        review_decision_id UUID NOT NULL,
        tenant_id UUID NOT NULL,
        content_id UUID NOT NULL,
        version_id UUID NOT NULL,
        decision TEXT NOT NULL,
        reason_code TEXT NULL,
        comment TEXT NULL,
        reviewer_principal_id UUID NOT NULL,
        effective_actor_id UUID NOT NULL,
        delegation_id UUID NULL,
        decided_at TIMESTAMPTZ NOT NULL,
        correlation_id UUID NOT NULL,
        CONSTRAINT pk_review_decisions PRIMARY KEY (review_decision_id),
        CONSTRAINT uq_review_decisions_tenant_content_version UNIQUE (
            tenant_id,
            content_id,
            version_id
        ),
        CONSTRAINT ck_review_decisions_decision
            CHECK (decision IN ('APPROVE', 'REQUEST_CHANGES', 'REJECT')),
        CONSTRAINT ck_review_decisions_reason_code
            CHECK (
                reason_code IS NULL
                OR (
                    char_length(reason_code) <= 64
                    AND reason_code ~ '^[a-z0-9][a-z0-9._-]*$'
                )
            ),
        CONSTRAINT ck_review_decisions_comment
            CHECK (
                comment IS NULL
                OR (btrim(comment) <> '' AND char_length(comment) <= 4000)
            ),
        CONSTRAINT fk_review_decisions_version
            FOREIGN KEY (tenant_id, content_id, version_id)
            REFERENCES content.content_versions (tenant_id, content_id, version_id)
            ON DELETE RESTRICT
    )
    """,
    "CREATE INDEX ix_review_decisions_tenant_id ON content.review_decisions (tenant_id)",
    """
    CREATE OR REPLACE FUNCTION content.reject_review_decision_mutation()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = content, pg_temp
    AS $$
    BEGIN
        RAISE EXCEPTION 'content.review_decisions is immutable'
            USING ERRCODE = '27000';
    END;
    $$
    """,
    """
    CREATE TRIGGER review_decisions_immutable_update
        BEFORE UPDATE ON content.review_decisions
        FOR EACH ROW
        EXECUTE FUNCTION content.reject_review_decision_mutation()
    """,
    """
    CREATE TRIGGER review_decisions_immutable_delete
        BEFORE DELETE ON content.review_decisions
        FOR EACH ROW
        EXECUTE FUNCTION content.reject_review_decision_mutation()
    """,
    "ALTER TABLE content.review_decisions ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE content.review_decisions FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY review_decisions_tenant_isolation ON content.review_decisions
        FOR ALL
        USING (tenant_id = content.current_tenant_id())
        WITH CHECK (tenant_id = content.current_tenant_id())
    """,
    """
    ALTER TABLE api.idempotency_records
        ADD COLUMN result_review_decision_id UUID NULL
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute(
        "ALTER TABLE api.idempotency_records DROP COLUMN IF EXISTS result_review_decision_id"
    )
    op.execute("DROP TABLE IF EXISTS content.review_decisions")
    op.execute("DROP FUNCTION IF EXISTS content.reject_review_decision_mutation()")
