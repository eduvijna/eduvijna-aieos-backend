"""TOS-DEV06-I03R1 — tosd060001 -> tosd060002 migration upgrade tests."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.domain.version import ContentPayload, canonical_payload_json
from aieos.domains.education.schema import WORKSHEET_CONTENT_TYPE
from aieos.platform.runtime.readiness import EXPECTED_ALEMBIC_HEAD
from aieos.platform.security.audit.models import SecurityMutationAuditRecord
from aieos.platform.security.audit.persistence.models import audit_records_table
from tests.conftest import alembic_config, provision_runtime_grants
from tests.dbutil import clear_asset_audit_rows_for_schema_downgrade
from tools.release.common import EXPECTED_MIGRATION_HEAD

pytestmark = pytest.mark.tos_dev06_i03

REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = REPO_ROOT / "migrations" / "versions"
FIXED_NOW = datetime(2026, 8, 31, 14, 0, tzinfo=UTC)


def _insert_teaching_create_audit(conn, *, tenant_id: uuid.UUID) -> None:
    assignment_id = uuid.uuid7()
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
                :audit_record_id, :tenant_id, 'teaching.assignment.create',
                'teaching.assignment', :assignment_id, 0,
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
            "assignment_id": assignment_id,
            "principal": principal,
            "corr": uuid.uuid7(),
            "caus": uuid.uuid7(),
            "occurred_at": FIXED_NOW,
        },
    )


def _seed_assignment_row(conn, *, tenant_id: uuid.UUID) -> uuid.UUID:
    assignment_id = uuid.uuid7()
    content_id = uuid.uuid7()
    version_id = uuid.uuid7()
    teacher = uuid.uuid7()
    conn.execute(
        text(
            """
            INSERT INTO content.contents (
                content_id, tenant_id, owner_principal_id, content_type, title,
                description, locale, stewardship_state, current_version_id,
                published_version_id, aggregate_revision, created_at,
                created_by_principal_id, updated_at, archived_at
            ) VALUES (
                :content_id, :tenant_id, :owner, :content_type, 'Worksheet',
                'Description', 'en-IN', 'APPROVED', :version_id,
                :version_id, 1, :now, :owner, :now, NULL
            )
            """
        ),
        {
            "content_id": content_id,
            "tenant_id": tenant_id,
            "owner": teacher,
            "content_type": WORKSHEET_CONTENT_TYPE,
            "version_id": version_id,
            "now": FIXED_NOW,
        },
    )
    payload = ContentPayload.from_mapping({"marker": "i03r1-migration"})
    conn.execute(
        text(
            """
            INSERT INTO content.content_versions (
                version_id, tenant_id, content_id, version_number, parent_version_id,
                schema_id, schema_version, payload, payload_sha256, origin,
                provenance, created_at, created_by_principal_id
            ) VALUES (
                :vid, :tid, :cid, 1, NULL,
                'education.worksheet', 1, CAST(:payload AS jsonb),
                :sha, 'HUMAN',
                CAST(:prov AS jsonb), :now, :actor
            )
            """
        ),
        {
            "vid": version_id,
            "tid": tenant_id,
            "cid": content_id,
            "payload": canonical_payload_json(payload.body),
            "sha": payload.sha256.value,
            "prov": json.dumps({}),
            "now": FIXED_NOW,
            "actor": teacher,
        },
    )
    conn.execute(
        text(
            """
            INSERT INTO teaching.assignments (
                assignment_id, tenant_id, teacher_principal_id,
                content_id, content_version_id, audience_type, class_ref,
                audience_display_label, source_work_id, lifecycle_state,
                assigned_at, available_from, due_at, closed_at, cancelled_at,
                aggregate_revision, created_at, updated_at
            ) VALUES (
                :aid, :tid, :teacher, :cid, :vid, 'class', 'class-5a',
                'Grade 5A', NULL, 'ACTIVE',
                :now, :now, NULL, NULL, NULL,
                0, :now, :now
            )
            """
        ),
        {
            "aid": assignment_id,
            "tid": tenant_id,
            "teacher": teacher,
            "cid": content_id,
            "vid": version_id,
            "now": FIXED_NOW,
        },
    )
    return assignment_id


class TestTosd060002Migration:
    def test_head_constants_and_chain(self) -> None:
        assert EXPECTED_ALEMBIC_HEAD == "tosd060002"
        assert EXPECTED_MIGRATION_HEAD == "tosd060002"
        text_002 = (MIGRATIONS / "tosd060002_teaching_assignment_audit.py").read_text(
            encoding="utf-8"
        )
        assert 'revision: str = "tosd060002"' in text_002
        assert 'down_revision: str | None = "tosd060001"' in text_002
        text_001 = (MIGRATIONS / "tosd060001_teaching_assignments.py").read_text(
            encoding="utf-8"
        )
        assert "teaching.assignment.create" not in text_001
        assert "AUDIT_UPGRADE_STATEMENTS" not in text_001

    def test_upgrade_from_existing_tosd060001_schema(
        self, postgres18, bootstrap_engine: Engine
    ) -> None:
        cfg = alembic_config(postgres18["migrator_url"])
        clear_asset_audit_rows_for_schema_downgrade(bootstrap_engine)
        command.downgrade(cfg, "tosd060001")
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "tosd060001"
            )
            tenant_id = uuid.uuid7()
            with conn.begin():
                _seed_assignment_row(conn, tenant_id=tenant_id)
        command.upgrade(cfg, "tosd060002")
        provision_runtime_grants(bootstrap_engine)
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "tosd060002"
            )
            assignments = conn.execute(
                text("SELECT count(*) FROM teaching.assignments")
            ).scalar_one()
            assert int(assignments) == 1
            with conn.begin():
                _insert_teaching_create_audit(conn, tenant_id=tenant_id)
            with conn.begin():
                conn.execute(text("SAVEPOINT teaching_invalid"))
                with pytest.raises(Exception):
                    conn.execute(
                        text(
                            """
                            INSERT INTO security.audit_records (
                                audit_record_id, tenant_id, action,
                                primary_resource_type, primary_resource_id,
                                primary_resource_revision,
                                resource_revision_before, resource_revision_after,
                                related_resource_refs,
                                initiating_principal_id, effective_actor_id,
                                executing_principal_id,
                                delegation_id, execution_channel,
                                correlation_id, causation_id, trace_id, occurred_at
                            ) VALUES (
                                :audit_record_id, :tenant_id, 'teaching.assignment.due_update',
                                'teaching.assignment', :assignment_id, 2,
                                0, 0,
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
                            "assignment_id": uuid.uuid7(),
                            "principal": uuid.uuid7(),
                            "corr": uuid.uuid7(),
                            "caus": uuid.uuid7(),
                            "occurred_at": FIXED_NOW,
                        },
                    )
                conn.execute(text("ROLLBACK TO SAVEPOINT teaching_invalid"))

    def test_fresh_database_reaches_same_contract(self, bootstrap_engine: Engine) -> None:
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "tosd060002"
            )
        action_constraint = next(
            c
            for c in audit_records_table.constraints
            if c.name == "ck_audit_records_action"
        )
        sql = str(action_constraint.sqltext)
        assert "teaching.assignment.create" in sql
        assert "content.create" in sql

    def test_downgrade_fail_closed_when_teaching_evidence_exists(
        self, postgres18, bootstrap_engine: Engine
    ) -> None:
        cfg = alembic_config(postgres18["migrator_url"])
        tenant_id = uuid.uuid7()
        with bootstrap_engine.connect() as conn:
            with conn.begin():
                _insert_teaching_create_audit(conn, tenant_id=tenant_id)
        with pytest.raises(Exception) as exc:
            command.downgrade(cfg, "tosd060001")
        message = str(exc.value)
        cause = exc.value.__cause__
        if cause is not None:
            message = f"{message} {cause}"
        assert "Teaching security audit evidence" in message
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "tosd060002"
            )
