"""TOS-DEV02 Lane B Teaching Work PostgreSQL schema.

Creates the durable teacher-owned Teaching Work container only.

Deliberately absent, and required to stay absent:
  * teaching_intents — a Teaching Intent is the request that creates a Work.
    It is not a durable aggregate and has no System of Record table.
  * any mission table — Today's Mission is a derived read projection.

Revision ID: tosd020001
Revises: adra045001
Create Date: 2026-08-27
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "tosd020001"
down_revision: str | None = "adra045001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE_STATEMENTS: tuple[str, ...] = (
    "CREATE SCHEMA teaching",
    # class_label is contextual free text captured from the teacher
    # (for example 'Grade 5B'). It is NOT a foreign key into any Class
    # System of Record and must never be treated as one.
    #
    # ck_teaching_works_intent_type is intentionally narrow: widening it is
    # the required migration step whenever IntentType gains a member.
    """
    CREATE TABLE teaching.works (
        work_id UUID NOT NULL,
        tenant_id UUID NOT NULL,
        teacher_principal_id UUID NOT NULL,
        intent_type TEXT NOT NULL,
        goal_text TEXT NOT NULL,
        class_label TEXT NULL,
        subject TEXT NULL,
        topic TEXT NULL,
        target_date DATE NOT NULL,
        locale TEXT NOT NULL,
        aggregate_revision BIGINT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        archived_at TIMESTAMPTZ NULL,
        CONSTRAINT pk_teaching_works PRIMARY KEY (work_id),
        CONSTRAINT uq_teaching_works_tenant_work UNIQUE (tenant_id, work_id),
        CONSTRAINT ck_teaching_works_aggregate_revision_nonnegative
            CHECK (aggregate_revision >= 0),
        CONSTRAINT ck_teaching_works_intent_type
            CHECK (intent_type IN ('prepare_tomorrow')),
        CONSTRAINT ck_teaching_works_goal_text_nonempty
            CHECK (btrim(goal_text) <> ''),
        CONSTRAINT ck_teaching_works_locale_nonempty
            CHECK (btrim(locale) <> ''),
        CONSTRAINT ck_teaching_works_class_label_nonempty
            CHECK (class_label IS NULL OR btrim(class_label) <> ''),
        CONSTRAINT ck_teaching_works_subject_nonempty
            CHECK (subject IS NULL OR btrim(subject) <> ''),
        CONSTRAINT ck_teaching_works_topic_nonempty
            CHECK (topic IS NULL OR btrim(topic) <> ''),
        CONSTRAINT ck_teaching_works_updated_after_created
            CHECK (updated_at >= created_at)
    )
    """,
    "CREATE INDEX ix_teaching_works_tenant_id ON teaching.works (tenant_id)",
    """
    CREATE INDEX ix_teaching_works_tenant_teacher
        ON teaching.works (tenant_id, teacher_principal_id)
    """,
    """
    CREATE INDEX ix_teaching_works_tenant_teacher_target_date
        ON teaching.works (tenant_id, teacher_principal_id, target_date)
    """,
    """
    CREATE INDEX ix_teaching_works_tenant_archived_at
        ON teaching.works (tenant_id, archived_at)
    """,
    """
    CREATE OR REPLACE FUNCTION teaching.current_tenant_id()
    RETURNS uuid
    LANGUAGE plpgsql
    VOLATILE
    SET search_path = teaching, pg_temp
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
    "ALTER TABLE teaching.works ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE teaching.works FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY teaching_works_tenant_isolation ON teaching.works
        FOR ALL
        USING (tenant_id = teaching.current_tenant_id())
        WITH CHECK (tenant_id = teaching.current_tenant_id())
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS teaching CASCADE")
