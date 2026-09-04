"""TOS-DEV07-I02R1 — tosd070001 -> tosd070002 TeachingExecution audit vocabulary."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.engine import Engine

from tests.conftest import alembic_config, provision_runtime_grants
from tests.dbutil import clear_asset_audit_rows_for_schema_downgrade

pytestmark = pytest.mark.tos_dev07_i02r1

FIXED_NOW = datetime(2026, 9, 3, 8, 0, tzinfo=UTC)

_EXECUTION_ACTIONS = (
    "teaching.execution.start",
    "teaching.execution.complete",
    "teaching.execution.cancel",
    "teaching.execution.observation.create",
    "teaching.execution.observation.correct",
)


@pytest.fixture(autouse=True)
def _ensure_alembic_head(postgres18, bootstrap_engine: Engine):
    cfg = alembic_config(postgres18["migrator_url"])
    command.upgrade(cfg, "head")
    provision_runtime_grants(bootstrap_engine)
    yield
    command.upgrade(cfg, "head")
    provision_runtime_grants(bootstrap_engine)


def _insert_audit(
    conn,
    *,
    tenant_id: uuid.UUID,
    action: str,
    resource_type: str,
    before: int | None,
    after: int,
    primary_revision: int | None = None,
) -> None:
    if primary_revision is None:
        primary_revision = after
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
                :resource_type, :resource_id, :primary_revision,
                :before, :after,
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
            "resource_type": resource_type,
            "resource_id": uuid.uuid7(),
            "primary_revision": primary_revision,
            "before": before,
            "after": after,
            "principal": principal,
            "corr": uuid.uuid7(),
            "caus": uuid.uuid7(),
            "occurred_at": FIXED_NOW,
        },
    )


def _count_action(conn, *, tenant_id: uuid.UUID, action: str) -> int:
    return int(
        conn.execute(
            text(
                "SELECT count(*) FROM security.audit_records "
                "WHERE tenant_id = :tid AND action = :action"
            ),
            {"tid": tenant_id, "action": action},
        ).scalar_one()
    )


def _reject_insert(conn, **kwargs) -> None:
    conn.execute(text("SAVEPOINT i02r1_invalid"))
    with pytest.raises(Exception):
        _insert_audit(conn, **kwargs)
    conn.execute(text("ROLLBACK TO SAVEPOINT i02r1_invalid"))


class TestTosd070002UpgradeAndAcceptance:
    def test_upgrade_tosd070001_to_tosd070002(
        self, postgres18, bootstrap_engine: Engine
    ) -> None:
        cfg = alembic_config(postgres18["migrator_url"])
        clear_asset_audit_rows_for_schema_downgrade(bootstrap_engine)
        try:
            command.downgrade(cfg, "tosd070001")
            with bootstrap_engine.connect() as conn:
                assert (
                    conn.execute(
                        text("SELECT version_num FROM alembic_version")
                    ).scalar_one()
                    == "tosd070001"
                )
                assert (
                    conn.execute(
                        text(
                            "SELECT count(*) FROM information_schema.tables "
                            "WHERE table_schema = 'teaching' "
                            "AND table_name = 'executions'"
                        )
                    ).scalar_one()
                    == 1
                )
            command.upgrade(cfg, "tosd070002")
            provision_runtime_grants(bootstrap_engine)
            with bootstrap_engine.connect() as conn:
                assert (
                    conn.execute(
                        text("SELECT version_num FROM alembic_version")
                    ).scalar_one()
                    == "tosd070002"
                )
                assert (
                    conn.execute(
                        text(
                            "SELECT count(*) FROM information_schema.tables "
                            "WHERE table_schema = 'teaching' "
                            "AND table_name = 'executions'"
                        )
                    ).scalar_one()
                    == 1
                )
        finally:
            command.upgrade(cfg, "head")
            provision_runtime_grants(bootstrap_engine)

    def test_valid_execution_and_observation_actions_accepted(
        self, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        with bootstrap_engine.begin() as conn:
            _insert_audit(
                conn,
                tenant_id=tenant_id,
                action="teaching.execution.start",
                resource_type="teaching.execution",
                before=None,
                after=0,
            )
            _insert_audit(
                conn,
                tenant_id=tenant_id,
                action="teaching.execution.complete",
                resource_type="teaching.execution",
                before=0,
                after=1,
            )
            _insert_audit(
                conn,
                tenant_id=tenant_id,
                action="teaching.execution.cancel",
                resource_type="teaching.execution",
                before=3,
                after=4,
            )
            _insert_audit(
                conn,
                tenant_id=tenant_id,
                action="teaching.execution.observation.create",
                resource_type="teaching.execution.observation",
                before=None,
                after=0,
            )
            _insert_audit(
                conn,
                tenant_id=tenant_id,
                action="teaching.execution.observation.correct",
                resource_type="teaching.execution.observation",
                before=1,
                after=2,
            )
        with bootstrap_engine.connect() as conn:
            for action in _EXECUTION_ACTIONS:
                assert _count_action(conn, tenant_id=tenant_id, action=action) == 1


class TestTosd070002Rejection:
    def test_wrong_revision_semantics_and_unknown_actions_rejected(
        self, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        with bootstrap_engine.begin() as conn:
            _reject_insert(
                conn,
                tenant_id=tenant_id,
                action="teaching.execution.start",
                resource_type="teaching.execution",
                before=0,
                after=1,
            )
            _reject_insert(
                conn,
                tenant_id=tenant_id,
                action="teaching.execution.start",
                resource_type="teaching.execution",
                before=None,
                after=1,
            )
            _reject_insert(
                conn,
                tenant_id=tenant_id,
                action="teaching.execution.complete",
                resource_type="teaching.execution",
                before=None,
                after=0,
            )
            _reject_insert(
                conn,
                tenant_id=tenant_id,
                action="teaching.execution.cancel",
                resource_type="teaching.execution",
                before=2,
                after=2,
            )
            _reject_insert(
                conn,
                tenant_id=tenant_id,
                action="teaching.execution.observation.create",
                resource_type="teaching.execution.observation",
                before=0,
                after=1,
            )
            _reject_insert(
                conn,
                tenant_id=tenant_id,
                action="teaching.execution.observation.correct",
                resource_type="teaching.execution.observation",
                before=None,
                after=0,
            )
            _reject_insert(
                conn,
                tenant_id=tenant_id,
                action="teaching.execution.start",
                resource_type="teaching.execution",
                before=None,
                after=0,
                primary_revision=1,
            )
            _reject_insert(
                conn,
                tenant_id=tenant_id,
                action="teaching.execution.foo",
                resource_type="teaching.execution",
                before=None,
                after=0,
            )
            _reject_insert(
                conn,
                tenant_id=tenant_id,
                action="teaching.execution.observation.delete",
                resource_type="teaching.execution.observation",
                before=0,
                after=1,
            )


class TestTosd070002Downgrade:
    def test_downgrade_with_no_execution_evidence_succeeds(
        self, postgres18, bootstrap_engine: Engine
    ) -> None:
        cfg = alembic_config(postgres18["migrator_url"])
        clear_asset_audit_rows_for_schema_downgrade(bootstrap_engine)
        try:
            command.downgrade(cfg, "tosd070001")
            with bootstrap_engine.connect() as conn:
                assert (
                    conn.execute(
                        text("SELECT version_num FROM alembic_version")
                    ).scalar_one()
                    == "tosd070001"
                )
            tenant_id = uuid.uuid7()
            with bootstrap_engine.begin() as conn:
                _insert_audit(
                    conn,
                    tenant_id=tenant_id,
                    action="teaching.assignment.create",
                    resource_type="teaching.assignment",
                    before=None,
                    after=0,
                )
            with bootstrap_engine.connect() as conn:
                assert (
                    _count_action(
                        conn,
                        tenant_id=tenant_id,
                        action="teaching.assignment.create",
                    )
                    == 1
                )
            command.upgrade(cfg, "tosd070002")
            provision_runtime_grants(bootstrap_engine)
            with bootstrap_engine.connect() as conn:
                assert (
                    conn.execute(
                        text("SELECT version_num FROM alembic_version")
                    ).scalar_one()
                    == "tosd070002"
                )
                assert (
                    _count_action(
                        conn,
                        tenant_id=tenant_id,
                        action="teaching.assignment.create",
                    )
                    == 1
                )
        finally:
            command.upgrade(cfg, "head")
            provision_runtime_grants(bootstrap_engine)

    def test_downgrade_with_assignment_only_evidence_succeeds(
        self, postgres18, bootstrap_engine: Engine
    ) -> None:
        cfg = alembic_config(postgres18["migrator_url"])
        clear_asset_audit_rows_for_schema_downgrade(bootstrap_engine)
        tenant_id = uuid.uuid7()
        with bootstrap_engine.begin() as conn:
            _insert_audit(
                conn,
                tenant_id=tenant_id,
                action="teaching.assignment.create",
                resource_type="teaching.assignment",
                before=None,
                after=0,
            )
            _insert_audit(
                conn,
                tenant_id=tenant_id,
                action="teaching.assignment.close",
                resource_type="teaching.assignment",
                before=0,
                after=1,
            )
        try:
            command.downgrade(cfg, "tosd070001")
            with bootstrap_engine.connect() as conn:
                assert (
                    conn.execute(
                        text("SELECT version_num FROM alembic_version")
                    ).scalar_one()
                    == "tosd070001"
                )
                assert (
                    _count_action(
                        conn,
                        tenant_id=tenant_id,
                        action="teaching.assignment.create",
                    )
                    == 1
                )
                assert (
                    _count_action(
                        conn,
                        tenant_id=tenant_id,
                        action="teaching.assignment.close",
                    )
                    == 1
                )
        finally:
            command.upgrade(cfg, "head")
            provision_runtime_grants(bootstrap_engine)

    def test_downgrade_with_execution_evidence_refused_fail_closed(
        self, postgres18, bootstrap_engine: Engine
    ) -> None:
        cfg = alembic_config(postgres18["migrator_url"])
        command.upgrade(cfg, "head")
        provision_runtime_grants(bootstrap_engine)
        tenant_id = uuid.uuid7()
        with bootstrap_engine.begin() as conn:
            _insert_audit(
                conn,
                tenant_id=tenant_id,
                action="teaching.execution.start",
                resource_type="teaching.execution",
                before=None,
                after=0,
            )
        try:
            with pytest.raises(Exception) as exc:
                command.downgrade(cfg, "tosd070001")
            message = str(exc.value)
            cause = exc.value.__cause__
            if cause is not None:
                message = f"{message} {cause}"
            assert "TeachingExecution security audit evidence" in message
            with bootstrap_engine.connect() as conn:
                assert (
                    conn.execute(
                        text("SELECT version_num FROM alembic_version")
                    ).scalar_one()
                    == "tosd080001"
                )
                assert (
                    _count_action(
                        conn,
                        tenant_id=tenant_id,
                        action="teaching.execution.start",
                    )
                    == 1
                )
                force = conn.execute(
                    text(
                        "SELECT c.relrowsecurity, c.relforcerowsecurity "
                        "FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = 'security' AND c.relname = 'audit_records'"
                    )
                ).one()
                assert force.relrowsecurity is True
                assert force.relforcerowsecurity is True
        finally:
            clear_asset_audit_rows_for_schema_downgrade(bootstrap_engine)
            command.upgrade(cfg, "head")
            provision_runtime_grants(bootstrap_engine)

    def test_upgrade_again_after_safe_downgrade(
        self, postgres18, bootstrap_engine: Engine
    ) -> None:
        cfg = alembic_config(postgres18["migrator_url"])
        clear_asset_audit_rows_for_schema_downgrade(bootstrap_engine)
        try:
            command.downgrade(cfg, "tosd070001")
            command.upgrade(cfg, "tosd070002")
            provision_runtime_grants(bootstrap_engine)
            tenant_id = uuid.uuid7()
            with bootstrap_engine.begin() as conn:
                _insert_audit(
                    conn,
                    tenant_id=tenant_id,
                    action="teaching.execution.observation.create",
                    resource_type="teaching.execution.observation",
                    before=None,
                    after=0,
                )
            with bootstrap_engine.connect() as conn:
                assert (
                    conn.execute(
                        text("SELECT version_num FROM alembic_version")
                    ).scalar_one()
                    == "tosd070002"
                )
                assert (
                    _count_action(
                        conn,
                        tenant_id=tenant_id,
                        action="teaching.execution.observation.create",
                    )
                    == 1
                )
        finally:
            command.upgrade(cfg, "head")
            provision_runtime_grants(bootstrap_engine)
