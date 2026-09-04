"""TOS-DEV08-I02 — tosd080001 -> tosd080002 Assessment audit vocabulary."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.engine import Engine

from tests.conftest import alembic_config, provision_runtime_grants
from tests.dbutil import clear_asset_audit_rows_for_schema_downgrade

pytestmark = pytest.mark.tos_dev08_i02

FIXED_NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _ensure_alembic_head(postgres18, bootstrap_engine: Engine):
    cfg = alembic_config(postgres18["migrator_url"])
    command.upgrade(cfg, "head")
    provision_runtime_grants(bootstrap_engine)
    yield
    command.upgrade(cfg, "head")
    provision_runtime_grants(bootstrap_engine)


def _insert_assessment_audit(conn, *, tenant_id: uuid.UUID, action: str) -> None:
    principal = uuid.uuid7()
    conn.execute(
        text(
            """
            INSERT INTO security.audit_records (
                audit_record_id, tenant_id, action,
                primary_resource_type, primary_resource_id, primary_resource_revision,
                resource_revision_before, resource_revision_after,
                related_resource_refs,
                initiating_principal_id, effective_actor_id, executing_principal_id,
                delegation_id, execution_channel,
                correlation_id, causation_id, trace_id, occurred_at
            ) VALUES (
                :audit_record_id, :tenant_id, :action,
                'assessment.classroom', :resource_id, 0,
                NULL, 0,
                CAST('[]' AS jsonb),
                :principal, :principal, :principal,
                NULL, 'API',
                :corr, :caus, NULL, :occurred_at
            )
            """
        ),
        {
            "audit_record_id": uuid.uuid7(),
            "tenant_id": tenant_id,
            "action": action,
            "resource_id": uuid.uuid7(),
            "principal": principal,
            "corr": uuid.uuid7(),
            "caus": uuid.uuid7(),
            "occurred_at": FIXED_NOW,
        },
    )


class TestAssessmentAuditMigration:
    def test_empty_downgrade_to_tosd080001_then_reupgrade(
        self, postgres18, bootstrap_engine: Engine
    ) -> None:
        cfg = alembic_config(postgres18["migrator_url"])
        clear_asset_audit_rows_for_schema_downgrade(bootstrap_engine)
        command.downgrade(cfg, "tosd080001")
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "tosd080001"
            )
        command.upgrade(cfg, "tosd080002")
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "tosd080002"
            )

    def test_downgrade_with_assessment_evidence_refused(
        self, postgres18, bootstrap_engine: Engine
    ) -> None:
        cfg = alembic_config(postgres18["migrator_url"])
        command.upgrade(cfg, "head")
        provision_runtime_grants(bootstrap_engine)
        tenant_id = uuid.uuid7()
        with bootstrap_engine.begin() as conn:
            _insert_assessment_audit(
                conn,
                tenant_id=tenant_id,
                action="assessment.classroom.record",
            )
        try:
            with pytest.raises(Exception) as exc:
                command.downgrade(cfg, "tosd080001")
            message = str(exc.value)
            cause = exc.value.__cause__
            if cause is not None:
                message = f"{message} {cause}"
            assert "ClassroomAssessment security audit evidence" in message
            with bootstrap_engine.connect() as conn:
                assert (
                    conn.execute(
                        text("SELECT version_num FROM alembic_version")
                    ).scalar_one()
                    == "tosd080002"
                )
                count = conn.execute(
                    text(
                        """
                        SELECT count(*) FROM security.audit_records
                        WHERE tenant_id = :tid
                          AND action = 'assessment.classroom.record'
                        """
                    ),
                    {"tid": tenant_id},
                ).scalar_one()
                assert count == 1
        finally:
            clear_asset_audit_rows_for_schema_downgrade(bootstrap_engine)
            command.upgrade(cfg, "head")
            provision_runtime_grants(bootstrap_engine)
