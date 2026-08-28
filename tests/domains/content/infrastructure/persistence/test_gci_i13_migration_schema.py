"""GCI-I13 migration_import_records schema, RLS, privileges, and IMPORT CHECK."""

from __future__ import annotations

import io
import json
import uuid
from contextlib import redirect_stdout
from datetime import UTC, datetime

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.domain.migration_provenance import (
    MigrationImportProvenanceV1,
    migration_import_provenance_as_json,
)
from tests.conftest import (
    SCHEMA_OWNER_ROLE,
    alembic_config,
    provision_runtime_grants,
)
from tests.dbutil import REPO_ROOT, clear_asset_audit_rows_for_schema_downgrade, set_tenant

pytestmark = pytest.mark.gci_i13

FIXED_NOW = datetime(2026, 8, 14, 22, 30, tzinfo=UTC)
DIGEST = "c" * 64
EXPECTED_COLUMNS = {
    "tenant_id",
    "source_system",
    "source_resource_type",
    "source_resource_id",
    "source_version",
    "source_digest_sha256",
    "mapping_id",
    "mapping_version",
    "first_migration_batch_id",
    "last_migration_batch_id",
    "outcome",
    "target_content_id",
    "target_version_id",
    "attempt_count",
    "first_attempt_at",
    "last_attempt_at",
    "completed_at",
    "failure_code",
}


def _canonical_provenance() -> dict:
    return migration_import_provenance_as_json(
        MigrationImportProvenanceV1(
            migration_batch_id=uuid.uuid7(),
            source_system="legacy.edu",
            source_resource_type="lesson",
            source_resource_id="42",
            source_version="v1",
            source_digest_sha256=DIGEST,
            mapping_id="edu.lesson.v1",
            mapping_version=1,
        )
    )


def _seed_content(bootstrap_engine: Engine, tenant_id: uuid.UUID) -> uuid.UUID:
    content_id = uuid.uuid7()
    owner = uuid.uuid7()
    with bootstrap_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO content.contents (
                    content_id, tenant_id, owner_principal_id, content_type, title,
                    description, locale, stewardship_state, current_version_id,
                    published_version_id, aggregate_revision, created_at,
                    created_by_principal_id, updated_at, archived_at
                ) VALUES (
                    :content_id, :tenant_id, :owner, 'test.generic', 'Title',
                    'Description', 'en-IN', 'DRAFT', NULL,
                    NULL, 0, :now, :owner, :now, NULL
                )
                """
            ),
            {
                "content_id": content_id,
                "tenant_id": tenant_id,
                "owner": owner,
                "now": FIXED_NOW,
            },
        )
    return content_id


def _insert_import_version(
    bootstrap_engine: Engine,
    *,
    tenant_id: uuid.UUID,
    content_id: uuid.UUID,
    provenance: dict | None,
) -> None:
    with bootstrap_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO content.content_versions (
                    version_id, tenant_id, content_id, version_number, parent_version_id,
                    schema_id, schema_version, payload, payload_sha256, origin,
                    provenance, created_at, created_by_principal_id
                ) VALUES (
                    :vid, :tid, :cid, 1, NULL,
                    'test.generic', 1, '{"marker":"v1"}'::jsonb,
                    repeat('a', 64), 'IMPORT',
                    CAST(:prov AS jsonb), :now, :actor
                )
                """
            ),
            {
                "vid": uuid.uuid7(),
                "tid": tenant_id,
                "cid": content_id,
                "prov": None if provenance is None else json.dumps(provenance),
                "now": FIXED_NOW,
                "actor": uuid.uuid7(),
            },
        )


def _insert_migration_row(
    engine: Engine,
    *,
    tenant_id: uuid.UUID,
    outcome: str = "FAILED",
    source_resource_id: str = "row-1",
    set_tenant_ctx: bool = True,
    **overrides,
) -> None:
    values = {
        "tenant_id": tenant_id,
        "source_system": "legacy.edu",
        "source_resource_type": "lesson",
        "source_resource_id": source_resource_id,
        "source_version": "1",
        "source_digest_sha256": DIGEST,
        "mapping_id": "edu.lesson.v1",
        "mapping_version": 1,
        "first_migration_batch_id": uuid.uuid7(),
        "last_migration_batch_id": uuid.uuid7(),
        "outcome": outcome,
        "target_content_id": None,
        "target_version_id": None,
        "attempt_count": 1,
        "first_attempt_at": FIXED_NOW,
        "last_attempt_at": FIXED_NOW,
        "completed_at": None,
        "failure_code": "schema_validation_failed",
    }
    if outcome == "IMPORTED":
        values["target_content_id"] = uuid.uuid7()
        values["target_version_id"] = uuid.uuid7()
        values["completed_at"] = FIXED_NOW
        values["failure_code"] = None
    values.update(overrides)
    with engine.begin() as conn:
        if set_tenant_ctx:
            set_tenant(conn, tenant_id)
        conn.execute(
            text(
                """
                INSERT INTO content.migration_import_records (
                    tenant_id, source_system, source_resource_type, source_resource_id,
                    source_version, source_digest_sha256, mapping_id, mapping_version,
                    first_migration_batch_id, last_migration_batch_id, outcome,
                    target_content_id, target_version_id, attempt_count,
                    first_attempt_at, last_attempt_at, completed_at, failure_code
                ) VALUES (
                    :tenant_id, :source_system, :source_resource_type, :source_resource_id,
                    :source_version, :source_digest_sha256, :mapping_id, :mapping_version,
                    :first_migration_batch_id, :last_migration_batch_id, :outcome,
                    :target_content_id, :target_version_id, :attempt_count,
                    :first_attempt_at, :last_attempt_at, :completed_at, :failure_code
                )
                """
            ),
            values,
        )


class TestMigrationTableShape:
    def test_exact_columns_and_pk_no_target_fk(self, bootstrap_engine) -> None:
        with bootstrap_engine.connect() as conn:
            cols = {
                row[0]
                for row in conn.execute(
                    text(
                        """
                        SELECT column_name FROM information_schema.columns
                        WHERE table_schema = 'content'
                          AND table_name = 'migration_import_records'
                        """
                    )
                )
            }
            assert cols == EXPECTED_COLUMNS
            pk = [
                row[0]
                for row in conn.execute(
                    text(
                        """
                        SELECT a.attname
                        FROM pg_index i
                        JOIN pg_attribute a ON a.attrelid = i.indrelid
                          AND a.attnum = ANY(i.indkey)
                        JOIN pg_class c ON c.oid = i.indrelid
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = 'content'
                          AND c.relname = 'migration_import_records'
                          AND i.indisprimary
                        ORDER BY array_position(i.indkey, a.attnum)
                        """
                    )
                )
            ]
            assert pk == [
                "tenant_id",
                "source_system",
                "source_resource_type",
                "source_resource_id",
            ]
            fks = conn.execute(
                text(
                    """
                    SELECT count(*) FROM information_schema.table_constraints
                    WHERE table_schema = 'content'
                      AND table_name = 'migration_import_records'
                      AND constraint_type = 'FOREIGN KEY'
                    """
                )
            ).scalar_one()
            assert int(fks) == 0
            force = conn.execute(
                text(
                    """
                    SELECT c.relforcerowsecurity
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'content'
                      AND c.relname = 'migration_import_records'
                    """
                )
            ).scalar_one()
            assert force is True


class TestImportProvenanceDb:
    def test_canonical_accepted_and_arbitrary_rejected(self, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id)
        _insert_import_version(
            bootstrap_engine,
            tenant_id=tenant_id,
            content_id=content_id,
            provenance=_canonical_provenance(),
        )
        bad_id = _seed_content(bootstrap_engine, tenant_id)
        with pytest.raises(Exception):
            _insert_import_version(
                bootstrap_engine,
                tenant_id=tenant_id,
                content_id=bad_id,
                provenance={"kind": "migration_import", "api_key": "SECRET"},
            )


class TestOutcomeAndImmutability:
    def test_outcome_checks_and_transitions(self, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        with pytest.raises(Exception):
            _insert_migration_row(
                bootstrap_engine,
                tenant_id=tenant_id,
                outcome="FAILED",
                source_resource_id="bad-failed",
                failure_code=None,
            )
        with pytest.raises(Exception):
            _insert_migration_row(
                bootstrap_engine,
                tenant_id=tenant_id,
                outcome="IMPORTED",
                source_resource_id="bad-imported",
                target_content_id=None,
                target_version_id=None,
                completed_at=None,
                failure_code=None,
            )
        _insert_migration_row(
            bootstrap_engine, tenant_id=tenant_id, source_resource_id="ok-failed"
        )
        target_c = uuid.uuid7()
        target_v = uuid.uuid7()
        with bootstrap_engine.begin() as conn:
            set_tenant(conn, tenant_id)
            conn.execute(
                text(
                    """
                    UPDATE content.migration_import_records
                    SET outcome = 'IMPORTED',
                        target_content_id = :c,
                        target_version_id = :v,
                        completed_at = :now,
                        failure_code = NULL,
                        attempt_count = 2
                    WHERE source_resource_id = 'ok-failed'
                    """
                ),
                {"c": target_c, "v": target_v, "now": FIXED_NOW},
            )
        with bootstrap_engine.begin() as conn:
            set_tenant(conn, tenant_id)
            with pytest.raises(Exception):
                conn.execute(
                    text(
                        """
                        UPDATE content.migration_import_records
                        SET outcome = 'FAILED', failure_code = 'x'
                        WHERE source_resource_id = 'ok-failed'
                        """
                    )
                )
        with bootstrap_engine.begin() as conn:
            set_tenant(conn, tenant_id)
            with pytest.raises(Exception):
                conn.execute(
                    text(
                        """
                        UPDATE content.migration_import_records
                        SET source_digest_sha256 = :d
                        WHERE source_resource_id = 'ok-failed'
                        """
                    ),
                    {"d": "d" * 64},
                )
        with bootstrap_engine.begin() as conn:
            set_tenant(conn, tenant_id)
            with pytest.raises(Exception):
                conn.execute(
                    text(
                        "DELETE FROM content.migration_import_records "
                        "WHERE source_resource_id = 'ok-failed'"
                    )
                )


class TestRlsAndPrivileges:
    def test_rls_fail_closed_and_cross_tenant(
        self, migration_runtime_engine, bootstrap_engine
    ) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        _insert_migration_row(
            bootstrap_engine, tenant_id=tenant_a, source_resource_id="rls-a"
        )
        with migration_runtime_engine.connect() as conn:
            with pytest.raises(Exception, match="aieos.tenant_id is not set"):
                conn.execute(
                    text("SELECT count(*) FROM content.migration_import_records")
                ).scalar_one()
        with migration_runtime_engine.connect() as conn:
            set_tenant(conn, tenant_b)
            count = conn.execute(
                text("SELECT count(*) FROM content.migration_import_records")
            ).scalar_one()
            assert int(count) == 0
        with migration_runtime_engine.connect() as conn:
            set_tenant(conn, tenant_a)
            count = conn.execute(
                text("SELECT count(*) FROM content.migration_import_records")
            ).scalar_one()
            assert int(count) == 1

    def test_migration_workload_least_privilege(
        self, migration_runtime_engine, postgres18
    ) -> None:
        with migration_runtime_engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT rolsuper, rolbypassrls FROM pg_roles
                    WHERE rolname = :role
                    """
                ),
                {"role": postgres18["migration_runtime_user"]},
            ).one()
            assert row.rolsuper is False
            assert row.rolbypassrls is False
            with pytest.raises(Exception):
                conn.execute(
                    text(
                        """
                        INSERT INTO content.review_decisions (
                            review_decision_id, tenant_id, content_id, version_id,
                            decision, reason_code, comment, reviewer_principal_id,
                            effective_actor_id, delegation_id, decided_at, correlation_id
                        ) VALUES (
                            :id, :tid, :cid, :vid, 'APPROVE', NULL, NULL, :p, :p,
                            NULL, :now, :corr
                        )
                        """
                    ),
                    {
                        "id": uuid.uuid7(),
                        "tid": uuid.uuid7(),
                        "cid": uuid.uuid7(),
                        "vid": uuid.uuid7(),
                        "p": uuid.uuid7(),
                        "now": FIXED_NOW,
                        "corr": uuid.uuid7(),
                    },
                )

    def test_runtime_cannot_write_migration_records(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        with runtime_engine.begin() as conn:
            set_tenant(conn, tenant_id)
            with pytest.raises(Exception):
                conn.execute(
                    text(
                        """
                        INSERT INTO content.migration_import_records (
                            tenant_id, source_system, source_resource_type,
                            source_resource_id, source_version, source_digest_sha256,
                            mapping_id, mapping_version, first_migration_batch_id,
                            last_migration_batch_id, outcome, target_content_id,
                            target_version_id, attempt_count, first_attempt_at,
                            last_attempt_at, completed_at, failure_code
                        ) VALUES (
                            :tid, 'legacy.edu', 'lesson', 'runtime-write', '1', :d,
                            'edu.lesson.v1', 1, :b, :b, 'FAILED', NULL, NULL, 1,
                            :now, :now, NULL, 'schema_validation_failed'
                        )
                        """
                    ),
                    {
                        "tid": tenant_id,
                        "d": DIGEST,
                        "b": uuid.uuid7(),
                        "now": FIXED_NOW,
                    },
                )


class TestMigrationCycle:
    def test_upgrade_downgrade_to_i11_and_head(
        self, postgres18, bootstrap_engine
    ) -> None:
        cfg = alembic_config(postgres18["migrator_url"])
        clear_asset_audit_rows_for_schema_downgrade(bootstrap_engine)
        command.downgrade(cfg, "gcii110001")
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "gcii110001"
            )
            exists = conn.execute(
                text(
                    """
                    SELECT count(*) FROM pg_tables
                    WHERE schemaname = 'content'
                      AND tablename = 'migration_import_records'
                    """
                )
            ).scalar_one()
        assert int(exists) == 0
        command.upgrade(cfg, "head")
        provision_runtime_grants(bootstrap_engine)
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "tosd030002"
            )

    def test_offline_sql_assumes_owner_before_i13_ddl(self) -> None:
        cfg = alembic_config("postgresql+psycopg://offline-check/unused")
        output = io.StringIO()
        with redirect_stdout(output):
            command.upgrade(cfg, "gcii130001", sql=True)
        sql = output.getvalue()
        assert f"SET LOCAL ROLE {SCHEMA_OWNER_ROLE}" in sql
        assert sql.index(f"SET LOCAL ROLE {SCHEMA_OWNER_ROLE}") < sql.index(
            "migration_import_provenance_v1_is_valid"
        )
        assert (
            REPO_ROOT / "migrations" / "versions" / "gcii130001_migration_import.py"
        ).is_file()
