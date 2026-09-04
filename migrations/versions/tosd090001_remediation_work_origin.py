"""TOS-DEV09-I01 Remediation TeachingWork origin persistence.

Widens teaching.works intent_type CHECK for remediate_class and creates
immutable teaching.work_remediation_origins with RLS, immutability triggers,
and DEFERRABLE commit-time Work/origin pair enforcement.

Deliberately absent:
  * Improve HTTP/application command
  * Assessment eligibility / ClassRef composition
  * Improve audit / idempotency operations
  * Improve event-plane / workflow-plane expansion
  * learner / mastery / Memory fields
  * Assessment-note or Observation-body Teaching copies

Revision ID: tosd090001
Revises: tosd080002
Create Date: 2026-09-04
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "tosd090001"
down_revision: str | None = "tosd080002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DOWNGRADE_BLOCKED = (
    "tosd090001 downgrade refused: remediate_class TeachingWork or "
    "TeachingWorkRemediationOrigin rows exist"
)

UPGRADE_STATEMENTS: tuple[str, ...] = (
    """
    ALTER TABLE teaching.works
        DROP CONSTRAINT ck_teaching_works_intent_type
    """,
    """
    ALTER TABLE teaching.works
        ADD CONSTRAINT ck_teaching_works_intent_type
            CHECK (intent_type IN ('prepare_tomorrow', 'remediate_class'))
    """,
    """
    CREATE TABLE teaching.work_remediation_origins (
        work_id UUID NOT NULL,
        tenant_id UUID NOT NULL,
        source_assessment_id UUID NOT NULL,
        source_assessment_aggregate_revision BIGINT NOT NULL,
        source_class_result_level_snapshot TEXT NOT NULL,
        source_class_ref TEXT NOT NULL,
        source_content_id UUID NOT NULL,
        source_content_version_id UUID NOT NULL,
        source_work_id UUID NULL,
        source_execution_id UUID NULL,
        source_assignment_id UUID NULL,
        initiating_teacher_principal_id UUID NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT pk_teaching_work_remediation_origins PRIMARY KEY (work_id),
        CONSTRAINT uq_teaching_work_remediation_origins_tenant_work
            UNIQUE (tenant_id, work_id),
        CONSTRAINT ck_teaching_work_remediation_origins_assessment_revision_nonnegative
            CHECK (source_assessment_aggregate_revision >= 0),
        CONSTRAINT ck_teaching_work_remediation_origins_class_result_level_snapshot
            CHECK (
                source_class_result_level_snapshot IN (
                    'DEMONSTRATED',
                    'MIXED',
                    'NOT_YET_DEMONSTRATED'
                )
            ),
        CONSTRAINT ck_teaching_work_remediation_origins_class_ref_nonempty
            CHECK (btrim(source_class_ref) <> ''),
        CONSTRAINT fk_teaching_work_remediation_origins_work
            FOREIGN KEY (tenant_id, work_id)
            REFERENCES teaching.works (tenant_id, work_id)
            ON DELETE RESTRICT,
        CONSTRAINT fk_teaching_work_remediation_origins_source_work
            FOREIGN KEY (tenant_id, source_work_id)
            REFERENCES teaching.works (tenant_id, work_id)
            ON DELETE RESTRICT,
        CONSTRAINT fk_teaching_work_remediation_origins_source_execution
            FOREIGN KEY (tenant_id, source_execution_id)
            REFERENCES teaching.executions (tenant_id, execution_id)
            ON DELETE RESTRICT,
        CONSTRAINT fk_teaching_work_remediation_origins_source_assignment
            FOREIGN KEY (tenant_id, source_assignment_id)
            REFERENCES teaching.assignments (tenant_id, assignment_id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX ix_teaching_work_remediation_origins_tenant_source_assessment
        ON teaching.work_remediation_origins (tenant_id, source_assessment_id)
    """,
    "ALTER TABLE teaching.work_remediation_origins ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE teaching.work_remediation_origins FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY teaching_work_remediation_origins_tenant_isolation
        ON teaching.work_remediation_origins
        FOR ALL
        USING (tenant_id = teaching.current_tenant_id())
        WITH CHECK (tenant_id = teaching.current_tenant_id())
    """,
    """
    CREATE OR REPLACE FUNCTION teaching.reject_work_remediation_origin_mutation()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = teaching, pg_temp
    AS $$
    BEGIN
        RAISE EXCEPTION 'teaching.work_remediation_origins is immutable'
            USING ERRCODE = '27000';
    END;
    $$
    """,
    """
    CREATE TRIGGER teaching_work_remediation_origins_immutable_update
        BEFORE UPDATE ON teaching.work_remediation_origins
        FOR EACH ROW
        EXECUTE FUNCTION teaching.reject_work_remediation_origin_mutation()
    """,
    """
    CREATE TRIGGER teaching_work_remediation_origins_immutable_delete
        BEFORE DELETE ON teaching.work_remediation_origins
        FOR EACH ROW
        EXECUTE FUNCTION teaching.reject_work_remediation_origin_mutation()
    """,
    """
    CREATE OR REPLACE FUNCTION teaching.enforce_remediation_work_has_origin()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = teaching, pg_temp
    AS $$
    BEGIN
        IF NEW.intent_type = 'remediate_class' THEN
            IF NOT EXISTS (
                SELECT 1
                FROM teaching.work_remediation_origins AS origin
                WHERE origin.work_id = NEW.work_id
                  AND origin.tenant_id = NEW.tenant_id
            ) THEN
                RAISE EXCEPTION
                    'remediate_class TeachingWork requires TeachingWorkRemediationOrigin'
                    USING ERRCODE = '23514';
            END IF;
        END IF;
        RETURN NULL;
    END;
    $$
    """,
    """
    CREATE CONSTRAINT TRIGGER trg_teaching_works_remediation_origin_required
        AFTER INSERT ON teaching.works
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION teaching.enforce_remediation_work_has_origin()
    """,
    """
    CREATE OR REPLACE FUNCTION teaching.enforce_origin_work_is_remediate_class()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = teaching, pg_temp
    AS $$
    DECLARE
        work_intent text;
    BEGIN
        SELECT works.intent_type
          INTO work_intent
          FROM teaching.works AS works
         WHERE works.work_id = NEW.work_id
           AND works.tenant_id = NEW.tenant_id;
        IF work_intent IS DISTINCT FROM 'remediate_class' THEN
            RAISE EXCEPTION
                'TeachingWorkRemediationOrigin requires remediate_class TeachingWork'
                USING ERRCODE = '23514';
        END IF;
        RETURN NULL;
    END;
    $$
    """,
    """
    CREATE CONSTRAINT TRIGGER trg_teaching_origin_requires_remediate_class
        AFTER INSERT ON teaching.work_remediation_origins
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION teaching.enforce_origin_work_is_remediate_class()
    """,
)

DOWNGRADE_STATEMENTS: tuple[str, ...] = (
    """
    DROP TRIGGER IF EXISTS trg_teaching_origin_requires_remediate_class
        ON teaching.work_remediation_origins
    """,
    "DROP FUNCTION IF EXISTS teaching.enforce_origin_work_is_remediate_class()",
    """
    DROP TRIGGER IF EXISTS trg_teaching_works_remediation_origin_required
        ON teaching.works
    """,
    "DROP FUNCTION IF EXISTS teaching.enforce_remediation_work_has_origin()",
    """
    DROP TRIGGER IF EXISTS teaching_work_remediation_origins_immutable_delete
        ON teaching.work_remediation_origins
    """,
    """
    DROP TRIGGER IF EXISTS teaching_work_remediation_origins_immutable_update
        ON teaching.work_remediation_origins
    """,
    "DROP FUNCTION IF EXISTS teaching.reject_work_remediation_origin_mutation()",
    """
    DROP POLICY IF EXISTS teaching_work_remediation_origins_tenant_isolation
        ON teaching.work_remediation_origins
    """,
    "DROP TABLE IF EXISTS teaching.work_remediation_origins",
    """
    ALTER TABLE teaching.works
        DROP CONSTRAINT ck_teaching_works_intent_type
    """,
    """
    ALTER TABLE teaching.works
        ADD CONSTRAINT ck_teaching_works_intent_type
            CHECK (intent_type IN ('prepare_tomorrow'))
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    origins_exist = bind.execute(
        text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'teaching'
                  AND table_name = 'work_remediation_origins'
            )
            """
        )
    ).scalar()
    works_rls_disabled = False
    origins_rls_disabled = False
    try:
        if origins_exist:
            op.execute(
                "ALTER TABLE teaching.work_remediation_origins "
                "DISABLE ROW LEVEL SECURITY"
            )
            origins_rls_disabled = True
        op.execute("ALTER TABLE teaching.works DISABLE ROW LEVEL SECURITY")
        works_rls_disabled = True

        blocked_origins = False
        if origins_exist:
            blocked_origins = bool(
                bind.execute(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM teaching.work_remediation_origins)"
                    )
                ).scalar()
            )
        blocked_works = bool(
            bind.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM teaching.works
                        WHERE intent_type = 'remediate_class'
                    )
                    """
                )
            ).scalar()
        )
        if blocked_origins or blocked_works:
            raise RuntimeError(_DOWNGRADE_BLOCKED)
    except Exception:
        if origins_rls_disabled:
            op.execute(
                "ALTER TABLE teaching.work_remediation_origins "
                "ENABLE ROW LEVEL SECURITY"
            )
            op.execute(
                "ALTER TABLE teaching.work_remediation_origins "
                "FORCE ROW LEVEL SECURITY"
            )
        if works_rls_disabled:
            op.execute("ALTER TABLE teaching.works ENABLE ROW LEVEL SECURITY")
            op.execute("ALTER TABLE teaching.works FORCE ROW LEVEL SECURITY")
        raise

    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
    # teaching.works RLS must be restored after temporary disable for the check.
    op.execute("ALTER TABLE teaching.works ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE teaching.works FORCE ROW LEVEL SECURITY")
