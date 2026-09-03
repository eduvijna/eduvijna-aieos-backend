"""TOS-DEV07-I01 — TeachingExecution PostgreSQL / RLS / concurrency tests."""

from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.domain.version import ContentPayload, canonical_payload_json
from aieos.domains.teaching.application.errors import (
    PersistenceInvariantViolation,
    PersistenceOperationFailed,
)
from aieos.domains.teaching.domain.execution import TeachingExecution
from aieos.domains.teaching.domain.execution_content_binding import ContentBindingSpec
from aieos.domains.teaching.domain.execution_lifecycle import ExecutionLifecycleState
from aieos.domains.teaching.domain.execution_observation import (
    TeachingExecutionObservation,
)
from aieos.domains.teaching.domain.identities import (
    AggregateRevision,
    ObservationRevision,
    WorkId,
)
from aieos.domains.teaching.domain.observation_kind import ObservationKind
from aieos.domains.teaching.domain.work import TeachingWork
from aieos.domains.teaching.infrastructure.persistence.uow import (
    SqlAlchemyTeachingUnitOfWorkFactory,
)
from aieos.platform.runtime.readiness import EXPECTED_ALEMBIC_HEAD
from tests.conftest import alembic_config, provision_runtime_grants
from tools.release.common import EXPECTED_MIGRATION_HEAD

pytestmark = pytest.mark.tos_dev07_i01

FIXED_NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
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
    payload = ContentPayload.from_mapping({"marker": "i01-execution"})
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
        goal_text="Teach fractions",
        target_date=FIXED_NOW.date(),
        locale="en-IN",
        created_at=FIXED_NOW,
    )
    with factory(tenant_id) as uow:
        uow.works.insert(work)
        uow.commit()
    return work.work_id


def _execution(
    *,
    tenant_id: uuid.UUID,
    principal_id: uuid.UUID,
    work_id: WorkId,
    class_ref: str = "class-5a",
    bindings: list[ContentBindingSpec] | None = None,
    started_at: datetime = FIXED_NOW,
) -> TeachingExecution:
    return TeachingExecution.start(
        tenant_id=tenant_id,
        teacher_principal_id=principal_id,
        work_id=work_id,
        class_ref=class_ref,
        started_at=started_at,
        bindings=bindings or (),
    )


class TestMigrationAndSchema:
    def test_alembic_head_tosd070002(self, bootstrap_engine: Engine) -> None:
        assert EXPECTED_ALEMBIC_HEAD == "tosd080001"
        assert EXPECTED_MIGRATION_HEAD == "tosd080001"
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "tosd080001"
            )
        versions = sorted(
            p.name for p in MIGRATIONS.glob("*.py") if p.name != "__init__.py"
        )
        assert versions[-1].startswith("tosd080001_")

    def test_execution_tables_rls_and_policies(
        self, bootstrap_engine: Engine
    ) -> None:
        expected = {
            "executions": "teaching_executions_tenant_isolation",
            "execution_content_bindings": (
                "teaching_execution_content_bindings_tenant_isolation"
            ),
            "execution_observations": (
                "teaching_execution_observations_tenant_isolation"
            ),
        }
        with bootstrap_engine.connect() as conn:
            for table, policy_name in expected.items():
                exists = conn.execute(
                    text(
                        """
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema = 'teaching' AND table_name = :table
                        """
                    ),
                    {"table": table},
                ).scalar_one()
                assert exists == 1
                relrow = conn.execute(
                    text(
                        """
                        SELECT c.relrowsecurity, c.relforcerowsecurity
                        FROM pg_class c
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = 'teaching' AND c.relname = :table
                        """
                    ),
                    {"table": table},
                ).one()
                assert relrow == (True, True)
                policy = conn.execute(
                    text(
                        """
                        SELECT polname FROM pg_policy p
                        JOIN pg_class c ON c.oid = p.polrelid
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = 'teaching' AND c.relname = :table
                        """
                    ),
                    {"table": table},
                ).scalar_one()
                assert policy == policy_name


class TestExecutionPersistence:
    def test_hydration_and_binding_round_trip(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
        work_id = _seed_work(
            factory, tenant_id=tenant_id, principal_id=principal_id
        )
        content_id = _seed_content(bootstrap_engine, tenant_id)
        version_id = _seed_version(
            bootstrap_engine, tenant_id=tenant_id, content_id=content_id
        )
        created = _execution(
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            bindings=[
                ContentBindingSpec(
                    content_id=content_id,
                    content_version_id=version_id,
                    artifact_kind="lesson_plan",
                )
            ],
        )
        with factory(tenant_id) as uow:
            uow.executions.insert(created)
            uow.commit()
        with factory(tenant_id) as uow:
            loaded = uow.executions.get(created.execution_id)
            bindings = uow.executions.list_bindings(created.execution_id)
        assert loaded is not None
        assert loaded.execution_id == created.execution_id
        assert loaded.class_ref == "class-5a"
        assert loaded.lifecycle_state is ExecutionLifecycleState.IN_PROGRESS
        assert len(loaded.bindings) == 1
        assert loaded.bindings[0].content_version_id == version_id
        assert len(bindings) == 1
        assert bindings[0].artifact_kind == "lesson_plan"

    def test_observation_durability_round_trip(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
        work_id = _seed_work(
            factory, tenant_id=tenant_id, principal_id=principal_id
        )
        created = _execution(
            tenant_id=tenant_id, principal_id=principal_id, work_id=work_id
        )
        with factory(tenant_id) as uow:
            uow.executions.insert(created)
            note = created.create_observation(
                observation_kind=ObservationKind.PRIVATE_EXECUTION_NOTE,
                body="worked well",
                recorded_at=FIXED_NOW,
            )
            uow.executions.insert_observation(note)
            uow.commit()
        with factory(tenant_id) as uow:
            loaded = uow.executions.get_observation(note.observation_id)
            listed = uow.executions.list_observations(created.execution_id)
        assert loaded is not None
        assert loaded.body == "worked well"
        assert len(listed) == 1

    def test_tenant_a_cannot_read_or_write_tenant_b(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        principal_id = uuid.uuid7()
        factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
        work_id = _seed_work(
            factory, tenant_id=tenant_a, principal_id=principal_id
        )
        created = _execution(
            tenant_id=tenant_a, principal_id=principal_id, work_id=work_id
        )
        with factory(tenant_a) as uow:
            uow.executions.insert(created)
            note = created.create_observation(
                observation_kind=ObservationKind.CLASS_OBSERVATION,
                body="class note",
                recorded_at=FIXED_NOW,
            )
            uow.executions.insert_observation(note)
            uow.commit()
        with factory(tenant_b) as uow:
            assert uow.executions.get(created.execution_id) is None
            assert uow.executions.get_observation(note.observation_id) is None
            assert uow.executions.list_bindings(created.execution_id) == []
        # Cross-tenant insert must fail closed (RLS WITH CHECK) when using
        # tenant B context with tenant A identifiers.
        with factory(tenant_b) as uow:
            with pytest.raises(PersistenceOperationFailed):
                cross = TeachingExecution.start(
                    tenant_id=tenant_a,
                    teacher_principal_id=principal_id,
                    work_id=work_id,
                    class_ref="class-x",
                    started_at=FIXED_NOW,
                )
                uow.executions.insert(cross)
                uow.commit()

    def test_cross_tenant_execution_update_fails_closed(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        principal_id = uuid.uuid7()
        factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
        work_id = _seed_work(
            factory, tenant_id=tenant_a, principal_id=principal_id
        )
        created = _execution(
            tenant_id=tenant_a, principal_id=principal_id, work_id=work_id
        )
        with factory(tenant_a) as uow:
            uow.executions.insert(created)
            uow.commit()
        completed = created.complete(
            completed_at=FIXED_NOW + timedelta(seconds=1)
        )
        with factory(tenant_b) as uow:
            assert not uow.executions.update(
                completed, expected_revision=created.aggregate_revision
            )
            uow.commit()
        with factory(tenant_a) as uow:
            loaded = uow.executions.get(created.execution_id)
        assert loaded is not None
        assert loaded.lifecycle_state is ExecutionLifecycleState.IN_PROGRESS
        assert loaded.completed_at is None
        assert int(loaded.aggregate_revision) == 0

    def test_cross_tenant_observation_update_fails_closed(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        principal_id = uuid.uuid7()
        factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
        work_id = _seed_work(
            factory, tenant_id=tenant_a, principal_id=principal_id
        )
        created = _execution(
            tenant_id=tenant_a, principal_id=principal_id, work_id=work_id
        )
        with factory(tenant_a) as uow:
            uow.executions.insert(created)
            note = created.create_observation(
                observation_kind=ObservationKind.PRIVATE_EXECUTION_NOTE,
                body="owned by A",
                recorded_at=FIXED_NOW,
            )
            uow.executions.insert_observation(note)
            uow.commit()
        corrected = note.correct(
            body="cross-tenant attempt",
            updated_at=FIXED_NOW + timedelta(seconds=1),
        )
        with factory(tenant_b) as uow:
            with pytest.raises(PersistenceInvariantViolation):
                uow.executions.update_observation(
                    corrected, expected_revision=note.revision
                )
        with factory(tenant_a) as uow:
            loaded = uow.executions.get_observation(note.observation_id)
        assert loaded is not None
        assert loaded.body == "owned by A"
        assert int(loaded.revision) == 0
        assert loaded.execution_id == created.execution_id

    def test_spoofed_in_progress_parent_cannot_mutate_terminal_observation(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
        work_id = _seed_work(
            factory, tenant_id=tenant_id, principal_id=principal_id
        )
        execution_a = _execution(
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            class_ref="class-a",
            started_at=FIXED_NOW,
        )
        with factory(tenant_id) as uow:
            uow.executions.insert(execution_a)
            note = execution_a.create_observation(
                observation_kind=ObservationKind.CLASS_OBSERVATION,
                body="belongs to A",
                recorded_at=FIXED_NOW,
            )
            uow.executions.insert_observation(note)
            current_a = uow.executions.get_for_update(execution_a.execution_id)
            assert current_a is not None
            terminal_a = current_a.complete(
                completed_at=FIXED_NOW + timedelta(seconds=1)
            )
            assert uow.executions.update(
                terminal_a, expected_revision=current_a.aggregate_revision
            )
            uow.commit()

        execution_b = _execution(
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            class_ref="class-b",
            started_at=FIXED_NOW + timedelta(seconds=2),
        )
        with factory(tenant_id) as uow:
            uow.executions.insert(execution_b)
            uow.commit()

        spoofed = TeachingExecutionObservation(
            observation_id=note.observation_id,
            execution_id=execution_b.execution_id,
            observation_kind=note.observation_kind,
            body="mutated via B",
            recorded_at=note.recorded_at,
            updated_at=FIXED_NOW + timedelta(seconds=5),
            revision=note.revision.next(),
        )
        with factory(tenant_id) as uow:
            assert not uow.executions.update_observation(
                spoofed, expected_revision=note.revision
            )
            uow.commit()

        with factory(tenant_id) as uow:
            loaded_note = uow.executions.get_observation(note.observation_id)
            loaded_a = uow.executions.get(execution_a.execution_id)
            loaded_b = uow.executions.get(execution_b.execution_id)
        assert loaded_note is not None
        assert loaded_note.execution_id == execution_a.execution_id
        assert loaded_note.body == "belongs to A"
        assert int(loaded_note.revision) == 0
        assert loaded_a is not None
        assert loaded_a.lifecycle_state is ExecutionLifecycleState.COMPLETED
        assert loaded_b is not None
        assert loaded_b.lifecycle_state is ExecutionLifecycleState.IN_PROGRESS

    def test_binding_cannot_cross_tenant(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        principal_id = uuid.uuid7()
        factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
        work_id = _seed_work(
            factory, tenant_id=tenant_a, principal_id=principal_id
        )
        content_b = _seed_content(bootstrap_engine, tenant_b)
        version_b = _seed_version(
            bootstrap_engine, tenant_id=tenant_b, content_id=content_b
        )
        created = _execution(
            tenant_id=tenant_a,
            principal_id=principal_id,
            work_id=work_id,
            bindings=[
                ContentBindingSpec(
                    content_id=content_b,
                    content_version_id=version_b,
                    artifact_kind="worksheet",
                )
            ],
        )
        with factory(tenant_a) as uow:
            with pytest.raises(PersistenceInvariantViolation):
                uow.executions.insert(created)
                uow.commit()

    def test_aggregate_revision_concurrency(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
        work_id = _seed_work(
            factory, tenant_id=tenant_id, principal_id=principal_id
        )
        created = _execution(
            tenant_id=tenant_id, principal_id=principal_id, work_id=work_id
        )
        with factory(tenant_id) as uow:
            uow.executions.insert(created)
            uow.commit()
        with factory(tenant_id) as uow:
            current = uow.executions.get_for_update(created.execution_id)
            assert current is not None
            completed = current.complete(
                completed_at=FIXED_NOW + timedelta(seconds=1)
            )
            assert uow.executions.update(
                completed, expected_revision=current.aggregate_revision
            )
            uow.commit()
        with factory(tenant_id) as uow:
            stale = created.complete(completed_at=FIXED_NOW + timedelta(seconds=2))
            assert not uow.executions.update(
                stale, expected_revision=AggregateRevision(0)
            )
            uow.commit()

    def test_observation_revision_concurrency(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
        work_id = _seed_work(
            factory, tenant_id=tenant_id, principal_id=principal_id
        )
        created = _execution(
            tenant_id=tenant_id, principal_id=principal_id, work_id=work_id
        )
        with factory(tenant_id) as uow:
            uow.executions.insert(created)
            note = created.create_observation(
                observation_kind=ObservationKind.PRIVATE_EXECUTION_NOTE,
                body="v0",
                recorded_at=FIXED_NOW,
            )
            uow.executions.insert_observation(note)
            uow.commit()
        with factory(tenant_id) as uow:
            current = uow.executions.get_observation(note.observation_id)
            assert current is not None
            corrected = created.correct_observation(
                current,
                body="v1",
                updated_at=FIXED_NOW + timedelta(seconds=1),
            )
            assert uow.executions.update_observation(
                corrected, expected_revision=current.revision
            )
            uow.commit()
        with factory(tenant_id) as uow:
            stale = note.correct(
                body="stale", updated_at=FIXED_NOW + timedelta(seconds=2)
            )
            assert not uow.executions.update_observation(
                stale, expected_revision=ObservationRevision(0)
            )
            uow.commit()

    def test_observation_immutable_after_complete_in_repository(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
        work_id = _seed_work(
            factory, tenant_id=tenant_id, principal_id=principal_id
        )
        created = _execution(
            tenant_id=tenant_id, principal_id=principal_id, work_id=work_id
        )
        with factory(tenant_id) as uow:
            uow.executions.insert(created)
            note = created.create_observation(
                observation_kind=ObservationKind.PRIVATE_EXECUTION_NOTE,
                body="v0",
                recorded_at=FIXED_NOW,
            )
            uow.executions.insert_observation(note)
            uow.commit()
        with factory(tenant_id) as uow:
            current = uow.executions.get_for_update(created.execution_id)
            assert current is not None
            completed = current.complete(
                completed_at=FIXED_NOW + timedelta(seconds=1)
            )
            assert uow.executions.update(
                completed, expected_revision=current.aggregate_revision
            )
            uow.commit()
        with factory(tenant_id) as uow:
            loaded_note = uow.executions.get_observation(note.observation_id)
            assert loaded_note is not None
            corrected = loaded_note.correct(
                body="after complete",
                updated_at=FIXED_NOW + timedelta(seconds=2),
            )
            with pytest.raises(PersistenceInvariantViolation):
                uow.executions.update_observation(
                    corrected, expected_revision=loaded_note.revision
                )

    def test_terminalization_vs_observation_update_fails_closed(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
        work_id = _seed_work(
            factory, tenant_id=tenant_id, principal_id=principal_id
        )
        created = _execution(
            tenant_id=tenant_id, principal_id=principal_id, work_id=work_id
        )
        with factory(tenant_id) as uow:
            uow.executions.insert(created)
            note = created.create_observation(
                observation_kind=ObservationKind.CLASS_OBSERVATION,
                body="v0",
                recorded_at=FIXED_NOW,
            )
            uow.executions.insert_observation(note)
            uow.commit()

        barrier = threading.Barrier(2)
        errors: list[BaseException] = []
        results: dict[str, bool] = {}

        def complete_execution() -> None:
            try:
                with factory(tenant_id) as uow:
                    current = uow.executions.get_for_update(created.execution_id)
                    assert current is not None
                    barrier.wait(timeout=10)
                    completed = current.complete(
                        completed_at=FIXED_NOW + timedelta(seconds=5)
                    )
                    ok = uow.executions.update(
                        completed, expected_revision=current.aggregate_revision
                    )
                    uow.commit()
                    results["complete"] = ok
            except BaseException as exc:  # noqa: BLE001 — capture for assertion
                errors.append(exc)

        def correct_observation() -> None:
            try:
                with factory(tenant_id) as uow:
                    current = uow.executions.get_observation(note.observation_id)
                    assert current is not None
                    barrier.wait(timeout=10)
                    corrected = created.correct_observation(
                        current,
                        body="race",
                        updated_at=FIXED_NOW + timedelta(seconds=6),
                    )
                    ok = uow.executions.update_observation(
                        corrected, expected_revision=current.revision
                    )
                    uow.commit()
                    results["correct"] = ok
            except BaseException as exc:  # noqa: BLE001 — capture for assertion
                errors.append(exc)
                results["correct"] = False

        t1 = threading.Thread(target=complete_execution)
        t2 = threading.Thread(target=correct_observation)
        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)

        # Fail-closed: either observation update raises / returns False after
        # terminalization, or completion lost the CAS race — never both succeed
        # with a post-terminal observation mutation surviving.
        with factory(tenant_id) as uow:
            loaded = uow.executions.get(created.execution_id)
            obs = uow.executions.get_observation(note.observation_id)
        assert loaded is not None
        assert obs is not None
        if loaded.lifecycle_state is ExecutionLifecycleState.COMPLETED:
            assert obs.body == "v0" or any(
                isinstance(e, PersistenceInvariantViolation) for e in errors
            )
            if obs.body == "race":
                pytest.fail(
                    "observation mutated after COMPLETED — fail-closed violated"
                )
        else:
            # Completion lost; observation may have corrected while IN_PROGRESS.
            assert results.get("correct") is True or obs.body in {"v0", "race"}

    def test_same_teacher_work_class_multiple_executions(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
        work_id = _seed_work(
            factory, tenant_id=tenant_id, principal_id=principal_id
        )
        first = _execution(
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            class_ref="class-5a",
            started_at=FIXED_NOW,
        )
        second = _execution(
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            class_ref="class-5a",
            started_at=FIXED_NOW + timedelta(hours=1),
        )
        with factory(tenant_id) as uow:
            uow.executions.insert(first)
            uow.executions.insert(second)
            uow.commit()
        with factory(tenant_id) as uow:
            assert uow.executions.get(first.execution_id) is not None
            assert uow.executions.get(second.execution_id) is not None
            assert first.execution_id != second.execution_id


class TestMigrationUpgradeDowngrade:
    @pytest.fixture(autouse=True)
    def _ensure_alembic_head(self, postgres18, bootstrap_engine: Engine):
        cfg = alembic_config(postgres18["migrator_url"])
        command.upgrade(cfg, "head")
        provision_runtime_grants(bootstrap_engine)
        yield
        command.upgrade(cfg, "head")
        provision_runtime_grants(bootstrap_engine)

    def test_upgrade_tosd060002_to_tosd070001(
        self, postgres18, bootstrap_engine: Engine
    ) -> None:
        cfg = alembic_config(postgres18["migrator_url"])
        command.downgrade(cfg, "tosd060002")
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "tosd060002"
            )
            assert (
                conn.execute(
                    text(
                        """
                        SELECT COUNT(*) FROM information_schema.tables
                        WHERE table_schema = 'teaching'
                          AND table_name = 'executions'
                        """
                    )
                ).scalar_one()
                == 0
            )
        command.upgrade(cfg, "tosd070001")
        provision_runtime_grants(bootstrap_engine)
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "tosd070001"
            )
            assert (
                conn.execute(
                    text(
                        """
                        SELECT COUNT(*) FROM information_schema.tables
                        WHERE table_schema = 'teaching'
                          AND table_name IN (
                            'executions',
                            'execution_content_bindings',
                            'execution_observations'
                          )
                        """
                    )
                ).scalar_one()
                == 3
            )

    def test_downgrade_tosd070001_to_tosd060002(
        self, postgres18, bootstrap_engine: Engine
    ) -> None:
        cfg = alembic_config(postgres18["migrator_url"])
        command.downgrade(cfg, "tosd060002")
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "tosd060002"
            )
            assert (
                conn.execute(
                    text(
                        """
                        SELECT COUNT(*) FROM information_schema.tables
                        WHERE table_schema = 'teaching'
                          AND table_name = 'executions'
                        """
                    )
                ).scalar_one()
                == 0
            )
            # Historical assignments table must remain.
            assert (
                conn.execute(
                    text(
                        """
                        SELECT COUNT(*) FROM information_schema.tables
                        WHERE table_schema = 'teaching'
                          AND table_name = 'assignments'
                        """
                    )
                ).scalar_one()
                == 1
            )
        command.upgrade(cfg, "head")
        provision_runtime_grants(bootstrap_engine)
