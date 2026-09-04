"""Shared helpers for GCI-I02 PostgreSQL tests. Not production runtime."""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parents[1]


def set_tenant(conn, tenant_id: uuid.UUID) -> None:
    conn.execute(
        text("SELECT set_config('aieos.tenant_id', :tid, true)"),
        {"tid": str(tenant_id)},
    )


def clear_asset_audit_rows_for_schema_downgrade(engine) -> None:
    """TEST-ONLY isolation for historical Alembic cycle tests.

    Production downgrade paths remain fail-closed and never delete audit
    evidence. The shared pytest PostgreSQL is session-scoped; immutable
    asset.*, teaching.*, and assessment.* rows would otherwise block
    unrelated downgrades.
    """
    with engine.begin() as conn:
        exists = conn.execute(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'security' AND table_name = 'audit_records'"
                ")"
            )
        ).scalar()
        if not exists:
            return
        conn.execute(
            text(
                "ALTER TABLE security.audit_records "
                "DISABLE TRIGGER audit_records_immutable_delete"
            )
        )
        conn.execute(
            text(
                "DELETE FROM security.audit_records "
                "WHERE action LIKE 'asset.%' "
                "OR action LIKE 'teaching.%' "
                "OR action LIKE 'assessment.%'"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE security.audit_records "
                "ENABLE TRIGGER audit_records_immutable_delete"
            )
        )
        assessment_exists = conn.execute(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'assessment' "
                "AND table_name = 'classroom_assessments'"
                ")"
            )
        ).scalar()
        if assessment_exists:
            conn.execute(
                text(
                    "ALTER TABLE assessment.classroom_assessments "
                    "DISABLE ROW LEVEL SECURITY"
                )
            )
            conn.execute(text("DELETE FROM assessment.classroom_assessments"))
            conn.execute(
                text(
                    "ALTER TABLE assessment.classroom_assessments "
                    "ENABLE ROW LEVEL SECURITY"
                )
            )
            conn.execute(
                text(
                    "ALTER TABLE assessment.classroom_assessments "
                    "FORCE ROW LEVEL SECURITY"
                )
            )
        remediation_origins_exist = conn.execute(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'teaching' "
                "AND table_name = 'work_remediation_origins'"
                ")"
            )
        ).scalar()
        if remediation_origins_exist:
            conn.execute(
                text(
                    "ALTER TABLE teaching.work_remediation_origins "
                    "DISABLE TRIGGER "
                    "teaching_work_remediation_origins_immutable_delete"
                )
            )
            conn.execute(
                text(
                    "ALTER TABLE teaching.work_remediation_origins "
                    "DISABLE ROW LEVEL SECURITY"
                )
            )
            conn.execute(text("DELETE FROM teaching.work_remediation_origins"))
            conn.execute(
                text(
                    "ALTER TABLE teaching.work_remediation_origins "
                    "ENABLE TRIGGER "
                    "teaching_work_remediation_origins_immutable_delete"
                )
            )
            conn.execute(
                text(
                    "ALTER TABLE teaching.work_remediation_origins "
                    "ENABLE ROW LEVEL SECURITY"
                )
            )
            conn.execute(
                text(
                    "ALTER TABLE teaching.work_remediation_origins "
                    "FORCE ROW LEVEL SECURITY"
                )
            )
        teaching_works_exist = conn.execute(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'teaching' AND table_name = 'works'"
                ")"
            )
        ).scalar()
        if teaching_works_exist:
            conn.execute(
                text("ALTER TABLE teaching.works DISABLE ROW LEVEL SECURITY")
            )
            conn.execute(
                text(
                    "DELETE FROM teaching.works "
                    "WHERE intent_type = 'remediate_class'"
                )
            )
            conn.execute(text("ALTER TABLE teaching.works ENABLE ROW LEVEL SECURITY"))
            conn.execute(text("ALTER TABLE teaching.works FORCE ROW LEVEL SECURITY"))
