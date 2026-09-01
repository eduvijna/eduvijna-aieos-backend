"""TOS-DEV06-I02 — TeachingAssignment PostgreSQL / RLS / repository tests."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from aieos.domains.content.domain.version import ContentPayload, canonical_payload_json
from aieos.domains.teaching.application.errors import PersistenceInvariantViolation
from aieos.domains.teaching.domain.assignment import TeachingAssignment
from aieos.domains.teaching.domain.assignment_lifecycle import AssignmentLifecycleState
from aieos.domains.teaching.domain.identities import AggregateRevision, WorkId
from aieos.domains.teaching.domain.work import TeachingWork
from aieos.domains.teaching.infrastructure.persistence.uow import (
    SqlAlchemyTeachingUnitOfWorkFactory,
)
from aieos.platform.runtime.readiness import EXPECTED_ALEMBIC_HEAD
from tools.release.common import EXPECTED_MIGRATION_HEAD

pytestmark = pytest.mark.tos_dev06_i02

FIXED_NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = REPO_ROOT / "migrations" / "versions"


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


def _seed_version(
    bootstrap_engine: Engine,
    *,
    tenant_id: uuid.UUID,
    content_id: uuid.UUID,
    version_number: int = 1,
) -> uuid.UUID:
    vid = uuid.uuid7()
    payload = ContentPayload.from_mapping({"marker": "i02-assignment"})
    with bootstrap_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO content.content_versions (
                    version_id, tenant_id, content_id, version_number, parent_version_id,
                    schema_id, schema_version, payload, payload_sha256, origin,
                    provenance, created_at, created_by_principal_id
                ) VALUES (
                    :vid, :tid, :cid, :vnum, NULL,
                    'test.generic', 1, CAST(:payload AS jsonb),
                    :sha, 'HUMAN',
                    CAST(:prov AS jsonb), :now, :actor
                )
                """
            ),
            {
                "vid": vid,
                "tid": tenant_id,
                "cid": content_id,
                "vnum": version_number,
                "payload": canonical_payload_json(payload.body),
                "sha": payload.sha256.value,
                "prov": json.dumps({}),
                "now": FIXED_NOW,
                "actor": uuid.uuid7(),
            },
        )
    return vid


def _seed_work(
    factory: SqlAlchemyTeachingUnitOfWorkFactory,
    *,
    tenant_id: uuid.UUID,
    principal_id: uuid.UUID,
) -> WorkId:
    work = TeachingWork.create_from_intent(
        tenant_id=tenant_id,
        teacher_principal_id=principal_id,
        intent_type="prepare_tomorrow",
        goal_text="Prepare fractions",
        target_date=FIXED_NOW.date(),
        locale="en-IN",
        created_at=FIXED_NOW,
    )
    with factory(tenant_id) as uow:
        uow.works.insert(work)
        uow.commit()
    return work.work_id


def _assignment(
    *,
    tenant_id: uuid.UUID,
    principal_id: uuid.UUID,
    content_id: uuid.UUID,
    version_id: uuid.UUID,
    class_ref: str = "class-5a",
    source_work_id: WorkId | None = None,
    assigned_at: datetime = FIXED_NOW,
) -> TeachingAssignment:
    return TeachingAssignment.create(
        tenant_id=tenant_id,
        teacher_principal_id=principal_id,
        content_id=content_id,
        content_version_id=version_id,
        class_ref=class_ref,
        assigned_at=assigned_at,
        audience_display_label="Grade 5A",
        source_work_id=source_work_id,
    )


class TestMigrationAndSchema:
    def test_alembic_head_tosd060001(self, bootstrap_engine: Engine) -> None:
        assert EXPECTED_ALEMBIC_HEAD == "tosd060002"
        assert EXPECTED_MIGRATION_HEAD == "tosd060002"
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "tosd060002"
            )
        versions = sorted(
            p.name for p in MIGRATIONS.glob("*.py") if p.name != "__init__.py"
        )
        assert versions[-1].startswith("tosd060002_")

    def test_assignments_table_rls_and_policy(self, bootstrap_engine: Engine) -> None:
        with bootstrap_engine.connect() as conn:
            exists = conn.execute(
                text(
                    """
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'teaching' AND table_name = 'assignments'
                    """
                )
            ).scalar_one()
            assert exists == 1
            relrow = conn.execute(
                text(
                    """
                    SELECT c.relrowsecurity, c.relforcerowsecurity
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'teaching' AND c.relname = 'assignments'
                    """
                )
            ).one()
            assert relrow == (True, True)
            policy = conn.execute(
                text(
                    """
                    SELECT polname FROM pg_policy p
                    JOIN pg_class c ON c.oid = p.polrelid
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'teaching' AND c.relname = 'assignments'
                    """
                )
            ).scalar_one()
            assert policy == "teaching_assignments_tenant_isolation"


class TestAssignmentRepositoryPersistence:
    def test_insert_read_round_trip(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id)
        version_id = _seed_version(
            bootstrap_engine, tenant_id=tenant_id, content_id=content_id
        )
        factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
        created = _assignment(
            tenant_id=tenant_id,
            principal_id=principal_id,
            content_id=content_id,
            version_id=version_id,
        )
        with factory(tenant_id) as uow:
            uow.assignments.insert(created)
            uow.commit()
        with factory(tenant_id) as uow:
            loaded = uow.assignments.get(created.assignment_id)
        assert loaded is not None
        assert loaded.assignment_id == created.assignment_id
        assert loaded.class_ref == "class-5a"
        assert loaded.content_version_id == version_id
        assert loaded.lifecycle_state is AssignmentLifecycleState.ACTIVE

    def test_wrong_tenant_cannot_see(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_a)
        version_id = _seed_version(
            bootstrap_engine, tenant_id=tenant_a, content_id=content_id
        )
        factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
        created = _assignment(
            tenant_id=tenant_a,
            principal_id=principal_id,
            content_id=content_id,
            version_id=version_id,
        )
        with factory(tenant_a) as uow:
            uow.assignments.insert(created)
            uow.commit()
        with factory(tenant_b) as uow:
            assert uow.assignments.get(created.assignment_id) is None

    def test_get_for_update_and_cas(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id)
        version_id = _seed_version(
            bootstrap_engine, tenant_id=tenant_id, content_id=content_id
        )
        factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
        created = _assignment(
            tenant_id=tenant_id,
            principal_id=principal_id,
            content_id=content_id,
            version_id=version_id,
        )
        with factory(tenant_id) as uow:
            uow.assignments.insert(created)
            uow.commit()

        with factory(tenant_id) as uow:
            locked = uow.assignments.get_for_update(created.assignment_id)
            assert locked is not None
            expected = locked.aggregate_revision
            updated = locked.update_due_at(
                due_at=FIXED_NOW + timedelta(days=3),
                updated_at=FIXED_NOW + timedelta(seconds=1),
            )
            assert uow.assignments.update(updated, expected_revision=expected) is True
            uow.commit()

        with factory(tenant_id) as uow:
            locked = uow.assignments.get_for_update(created.assignment_id)
            assert locked is not None
            stale = locked.close(closed_at=FIXED_NOW + timedelta(seconds=2))
            assert (
                uow.assignments.update(
                    stale, expected_revision=AggregateRevision(0)
                )
                is False
            )

    def test_content_version_fk(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        other_tenant = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id)
        version_id = _seed_version(
            bootstrap_engine, tenant_id=tenant_id, content_id=content_id
        )
        other_content = _seed_content(bootstrap_engine, other_tenant)
        other_version = _seed_version(
            bootstrap_engine, tenant_id=other_tenant, content_id=other_content
        )
        factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)

        ok = _assignment(
            tenant_id=tenant_id,
            principal_id=principal_id,
            content_id=content_id,
            version_id=version_id,
        )
        with factory(tenant_id) as uow:
            uow.assignments.insert(ok)
            uow.commit()

        missing = _assignment(
            tenant_id=tenant_id,
            principal_id=principal_id,
            content_id=content_id,
            version_id=uuid.uuid7(),
        )
        with pytest.raises(PersistenceInvariantViolation):
            with factory(tenant_id) as uow:
                uow.assignments.insert(missing)
                uow.commit()

        mismatched = _assignment(
            tenant_id=tenant_id,
            principal_id=principal_id,
            content_id=content_id,
            version_id=other_version,
        )
        with pytest.raises(PersistenceInvariantViolation):
            with factory(tenant_id) as uow:
                uow.assignments.insert(mismatched)
                uow.commit()

        cross = _assignment(
            tenant_id=tenant_id,
            principal_id=principal_id,
            content_id=other_content,
            version_id=other_version,
        )
        with pytest.raises(PersistenceInvariantViolation):
            with factory(tenant_id) as uow:
                uow.assignments.insert(cross)
                uow.commit()

    def test_source_work_fk_optional(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        other_tenant = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id)
        version_id = _seed_version(
            bootstrap_engine, tenant_id=tenant_id, content_id=content_id
        )
        factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
        work_id = _seed_work(
            factory, tenant_id=tenant_id, principal_id=principal_id
        )
        other_work = _seed_work(
            factory, tenant_id=other_tenant, principal_id=uuid.uuid7()
        )

        with_null = _assignment(
            tenant_id=tenant_id,
            principal_id=principal_id,
            content_id=content_id,
            version_id=version_id,
            source_work_id=None,
        )
        with_work = _assignment(
            tenant_id=tenant_id,
            principal_id=principal_id,
            content_id=content_id,
            version_id=version_id,
            source_work_id=work_id,
            class_ref="class-5b",
        )
        with factory(tenant_id) as uow:
            uow.assignments.insert(with_null)
            uow.assignments.insert(with_work)
            uow.commit()

        bad = _assignment(
            tenant_id=tenant_id,
            principal_id=principal_id,
            content_id=content_id,
            version_id=version_id,
            source_work_id=other_work,
            class_ref="class-5c",
        )
        with pytest.raises(PersistenceInvariantViolation):
            with factory(tenant_id) as uow:
                uow.assignments.insert(bad)
                uow.commit()

    def test_duplicate_business_fields_allowed(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id)
        version_id = _seed_version(
            bootstrap_engine, tenant_id=tenant_id, content_id=content_id
        )
        factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
        first = _assignment(
            tenant_id=tenant_id,
            principal_id=principal_id,
            content_id=content_id,
            version_id=version_id,
        )
        second = _assignment(
            tenant_id=tenant_id,
            principal_id=principal_id,
            content_id=content_id,
            version_id=version_id,
        )
        assert first.assignment_id != second.assignment_id
        with factory(tenant_id) as uow:
            uow.assignments.insert(first)
            uow.assignments.insert(second)
            uow.commit()

    def test_lifecycle_constraint_rejects_impossible_row(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id)
        version_id = _seed_version(
            bootstrap_engine, tenant_id=tenant_id, content_id=content_id
        )
        with runtime_engine.begin() as conn:
            conn.execute(
                text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            with pytest.raises(Exception):
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
                            :aid, :tid, :pid, :cid, :vid, 'class', 'class-5a',
                            NULL, NULL, 'ACTIVE',
                            :now, :now, NULL, :now, NULL,
                            0, :now, :now
                        )
                        """
                    ),
                    {
                        "aid": uuid.uuid7(),
                        "tid": tenant_id,
                        "pid": principal_id,
                        "cid": content_id,
                        "vid": version_id,
                        "now": FIXED_NOW,
                    },
                )

    def test_no_delete_repository_path(self) -> None:
        from aieos.domains.teaching.infrastructure.persistence.repositories import (
            SqlAlchemyTeachingAssignmentRepository,
        )

        assert not hasattr(SqlAlchemyTeachingAssignmentRepository, "delete")
        assert not hasattr(SqlAlchemyTeachingAssignmentRepository, "remove")
