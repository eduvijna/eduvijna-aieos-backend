"""GCI-I09 immutable Publication history and idempotency outcome extension.

Revision ID: gcii090001
Revises: gcii080001
Create Date: 2026-08-14
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "gcii090001"
down_revision: str | None = "gcii080001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE content.publications (
        publication_id UUID NOT NULL,
        tenant_id UUID NOT NULL,
        content_id UUID NOT NULL,
        version_id UUID NOT NULL,
        approval_decision_id UUID NOT NULL,
        published_by_principal_id UUID NOT NULL,
        effective_actor_id UUID NOT NULL,
        published_at TIMESTAMPTZ NOT NULL,
        correlation_id UUID NOT NULL,
        CONSTRAINT pk_publications PRIMARY KEY (publication_id),
        CONSTRAINT uq_publications_tenant_content_version UNIQUE (
            tenant_id,
            content_id,
            version_id
        ),
        CONSTRAINT fk_publications_version
            FOREIGN KEY (tenant_id, content_id, version_id)
            REFERENCES content.content_versions (tenant_id, content_id, version_id)
            ON DELETE RESTRICT,
        CONSTRAINT fk_publications_approval_decision
            FOREIGN KEY (approval_decision_id)
            REFERENCES content.review_decisions (review_decision_id)
            ON DELETE RESTRICT
    )
    """,
    "CREATE INDEX ix_publications_tenant_id ON content.publications (tenant_id)",
    "CREATE INDEX ix_publications_content_id ON content.publications (tenant_id, content_id)",
    """
    CREATE OR REPLACE FUNCTION content.reject_publication_mutation()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = content, pg_temp
    AS $$
    BEGIN
        RAISE EXCEPTION 'content.publications is immutable'
            USING ERRCODE = '27000';
    END;
    $$
    """,
    """
    CREATE TRIGGER publications_immutable_update
        BEFORE UPDATE ON content.publications
        FOR EACH ROW
        EXECUTE FUNCTION content.reject_publication_mutation()
    """,
    """
    CREATE TRIGGER publications_immutable_delete
        BEFORE DELETE ON content.publications
        FOR EACH ROW
        EXECUTE FUNCTION content.reject_publication_mutation()
    """,
    "ALTER TABLE content.publications ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE content.publications FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY publications_tenant_isolation ON content.publications
        FOR ALL
        USING (tenant_id = content.current_tenant_id())
        WITH CHECK (tenant_id = content.current_tenant_id())
    """,
    """
    ALTER TABLE api.idempotency_records
        ADD COLUMN result_publication_id UUID NULL
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute(
        "ALTER TABLE api.idempotency_records DROP COLUMN IF EXISTS result_publication_id"
    )
    op.execute("DROP TABLE IF EXISTS content.publications")
    op.execute("DROP FUNCTION IF EXISTS content.reject_publication_mutation()")
