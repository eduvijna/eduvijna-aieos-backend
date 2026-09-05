"""PostgreSQL acceptance for tosd090002 remediation audit vocabulary."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.engine import Engine

from tests.conftest import alembic_config, provision_runtime_grants
from tests.dbutil import clear_asset_audit_rows_for_schema_downgrade

pytestmark = pytest.mark.tos_dev09_i02


@pytest.fixture(autouse=True)
def _head(postgres18, bootstrap_engine: Engine):
    cfg = alembic_config(postgres18["migrator_url"])
    command.upgrade(cfg, "head")
    provision_runtime_grants(bootstrap_engine)
    yield
    command.upgrade(cfg, "head")
    provision_runtime_grants(bootstrap_engine)


def _insert(conn, *, tenant_id: uuid.UUID) -> None:
    principal = uuid.uuid7()
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
                executing_principal_id, delegation_id, execution_channel,
                correlation_id, causation_id, trace_id, occurred_at
            ) VALUES (
                :audit_id, :tenant_id, 'teaching.work.remediation.create',
                'teaching.work', :resource_id, 0,
                NULL, 0, CAST('[]' AS jsonb),
                :principal, :principal, :principal, NULL, 'API',
                :correlation_id, :causation_id, NULL, :occurred_at
            )
            """
        ),
        {
            "audit_id": uuid.uuid7(),
            "tenant_id": tenant_id,
            "resource_id": uuid.uuid7(),
            "principal": principal,
            "correlation_id": uuid.uuid7(),
            "causation_id": uuid.uuid7(),
            "occurred_at": datetime(2026, 9, 4, 12, tzinfo=UTC),
        },
    )


def _insert_action(
    conn,
    *,
    action: str,
    primary_type: str,
    before: int | None,
    after: int,
    primary_revision: int | None,
    tenant_id: uuid.UUID | None = None,
) -> None:
    principal = uuid.uuid7()
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
                executing_principal_id, delegation_id, execution_channel,
                correlation_id, causation_id, trace_id, occurred_at
            ) VALUES (
                :audit_id, :tenant_id, :action, :primary_type, :resource_id,
                :primary_revision, :before, :after, CAST('[]' AS jsonb),
                :principal, :principal, :principal, NULL, 'API',
                :correlation_id, :causation_id, NULL, :occurred_at
            )
            """
        ),
        {
            "audit_id": uuid.uuid7(),
            "tenant_id": tenant_id or uuid.uuid7(),
            "action": action,
            "primary_type": primary_type,
            "resource_id": uuid.uuid7(),
            "primary_revision": primary_revision,
            "before": before,
            "after": after,
            "principal": principal,
            "correlation_id": uuid.uuid7(),
            "causation_id": uuid.uuid7(),
            "occurred_at": datetime(2026, 9, 4, 12, tzinfo=UTC),
        },
    )


def test_current_database_head_is_tosd090002(bootstrap_engine: Engine) -> None:
    with bootstrap_engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "tosd090002"
        )


def test_valid_remediation_create_audit_row_inserts(
    bootstrap_engine: Engine,
) -> None:
    tenant_id = uuid.uuid7()
    with bootstrap_engine.begin() as conn:
        _insert(conn, tenant_id=tenant_id)
        row = conn.execute(
            text(
                """
                SELECT action, primary_resource_type, primary_resource_revision,
                       resource_revision_before, resource_revision_after
                FROM security.audit_records
                WHERE tenant_id = :tenant_id
                  AND action = 'teaching.work.remediation.create'
                """
            ),
            {"tenant_id": tenant_id},
        ).one()
    assert tuple(row) == (
        "teaching.work.remediation.create",
        "teaching.work",
        0,
        None,
        0,
    )


@pytest.mark.parametrize(
    ("before", "after", "primary_revision"),
    ((0, 0, 0), (None, 1, 1), (None, 0, None)),
    ids=("before-must-be-null", "after-must-be-zero", "primary-revision-required"),
)
def test_invalid_remediation_revision_semantics_rejected(
    bootstrap_engine: Engine,
    before: int | None,
    after: int,
    primary_revision: int | None,
) -> None:
    with pytest.raises(Exception):
        with bootstrap_engine.begin() as conn:
            _insert_action(
                conn,
                action="teaching.work.remediation.create",
                primary_type="teaching.work",
                before=before,
                after=after,
                primary_revision=primary_revision,
            )


@pytest.mark.parametrize(
    ("action", "primary_type", "before", "after", "primary_revision"),
    (
        ("teaching.assignment.create", "teaching.assignment", None, 0, 0),
        ("assessment.classroom.record", "assessment.classroom", None, 0, 0),
        ("assessment.classroom.correct", "assessment.classroom", 0, 1, 1),
    ),
)
def test_previous_teaching_and_assessment_actions_remain_accepted(
    bootstrap_engine: Engine,
    action: str,
    primary_type: str,
    before: int | None,
    after: int,
    primary_revision: int,
) -> None:
    with bootstrap_engine.begin() as conn:
        _insert_action(
            conn,
            action=action,
            primary_type=primary_type,
            before=before,
            after=after,
            primary_revision=primary_revision,
        )


def test_content_and_asset_constraint_families_are_preserved(
    bootstrap_engine: Engine,
) -> None:
    with bootstrap_engine.connect() as conn:
        definitions = "\n".join(
            conn.execute(
                text(
                    """
                    SELECT pg_get_constraintdef(oid)
                    FROM pg_constraint
                    WHERE conrelid = 'security.audit_records'::regclass
                      AND conname IN (
                        'ck_audit_records_action',
                        'ck_audit_records_primary_revision_family',
                        'ck_audit_records_revision_semantics'
                      )
                    ORDER BY conname
                    """
                )
            ).scalars()
        )
    assert "content.create" in definitions
    assert "asset.create" in definitions
    assert "resource_revision_before IS NULL" in definitions
    assert "resource_revision_after = 0" in definitions


def test_empty_downgrade_restores_assessment_vocabulary(
    postgres18, bootstrap_engine: Engine
) -> None:
    cfg = alembic_config(postgres18["migrator_url"])
    clear_asset_audit_rows_for_schema_downgrade(bootstrap_engine)
    command.downgrade(cfg, "tosd090001")
    with bootstrap_engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "tosd090001"
        )
    command.upgrade(cfg, "tosd090002")


def test_evidence_downgrade_refused_and_evidence_rls_preserved(
    postgres18, bootstrap_engine: Engine
) -> None:
    cfg = alembic_config(postgres18["migrator_url"])
    tenant_id = uuid.uuid7()
    with bootstrap_engine.begin() as conn:
        _insert(conn, tenant_id=tenant_id)
    try:
        with pytest.raises(Exception) as exc:
            command.downgrade(cfg, "tosd090001")
        message = f"{exc.value} {exc.value.__cause__ or ''}"
        assert (
            "TOS-DEV09-I02 downgrade refused: remediation TeachingWork security "
            "audit evidence exists and must not be deleted or rewritten"
        ) in message
        with bootstrap_engine.connect() as conn:
            assert conn.execute(
                text(
                    """
                    SELECT count(*) FROM security.audit_records
                    WHERE tenant_id = :tenant_id
                      AND action = 'teaching.work.remediation.create'
                    """
                ),
                {"tenant_id": tenant_id},
            ).scalar_one() == 1
            assert conn.execute(
                text(
                    """
                    SELECT relrowsecurity AND relforcerowsecurity
                    FROM pg_class
                    WHERE oid = 'security.audit_records'::regclass
                    """
                )
            ).scalar_one() is True
    finally:
        clear_asset_audit_rows_for_schema_downgrade(bootstrap_engine)
        command.upgrade(cfg, "head")
