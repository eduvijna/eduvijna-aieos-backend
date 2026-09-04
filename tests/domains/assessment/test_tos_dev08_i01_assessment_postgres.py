"""TOS-DEV08-I01 — ClassroomAssessment PostgreSQL / RLS / concurrency tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from aieos.domains.assessment.application.errors import PersistenceOperationFailed
from aieos.domains.assessment.domain.classroom_assessment import ClassroomAssessment
from aieos.domains.assessment.domain.identities import AggregateRevision
from aieos.domains.assessment.domain.lifecycle import AssessmentLifecycleState
from aieos.domains.assessment.domain.result import ClassResultLevel
from aieos.domains.assessment.infrastructure.persistence.models import (
    classroom_assessments_table,
)
from aieos.domains.assessment.infrastructure.persistence.repositories import (
    SqlAlchemyClassroomAssessmentRepository,
)
from aieos.domains.assessment.infrastructure.persistence.uow import (
    SqlAlchemyAssessmentUnitOfWorkFactory,
)
from aieos.platform.runtime.readiness import EXPECTED_ALEMBIC_HEAD
from tests.conftest import alembic_config, provision_runtime_grants
from tools.release.common import EXPECTED_MIGRATION_HEAD


@pytest.fixture(autouse=True)
def _clear_assessment_rows_after_test(bootstrap_engine: Engine) -> None:
    yield
    with bootstrap_engine.begin() as conn:
        exists = conn.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'assessment'
                      AND table_name = 'classroom_assessments'
                )
                """
            )
        ).scalar()
        if not exists:
            return
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

pytestmark = pytest.mark.tos_dev08_i01

FIXED_NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = REPO_ROOT / "migrations" / "versions"
EXPECTED_COLUMNS = {
    "assessment_id",
    "tenant_id",
    "teacher_principal_id",
    "class_ref",
    "content_id",
    "content_version_id",
    "class_result_level",
    "class_result_note",
    "lifecycle_state",
    "work_id",
    "execution_id",
    "assignment_id",
    "aggregate_revision",
    "recorded_at",
    "voided_at",
    "created_at",
    "updated_at",
}


def _assessment(**overrides) -> ClassroomAssessment:
    values = {
        "tenant_id": uuid.uuid7(),
        "teacher_principal_id": uuid.uuid7(),
        "class_ref": "class-5a",
        "content_id": uuid.uuid7(),
        "content_version_id": uuid.uuid7(),
        "class_result_level": ClassResultLevel.DEMONSTRATED,
        "recorded_at": FIXED_NOW,
    }
    values.update(overrides)
    return ClassroomAssessment.record(**values)


class TestP01P03MigrationAndShape:
    def test_p01_alembic_head_tosd080001(self, bootstrap_engine: Engine) -> None:
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

    def test_p02_assessment_schema_exists(self, bootstrap_engine: Engine) -> None:
        insp = inspect(bootstrap_engine)
        assert "assessment" in insp.get_schema_names()

    def test_p03_classroom_assessments_exact_shape(
        self, bootstrap_engine: Engine
    ) -> None:
        insp = inspect(bootstrap_engine)
        columns = {
            col["name"]
            for col in insp.get_columns("classroom_assessments", schema="assessment")
        }
        assert columns == EXPECTED_COLUMNS
        assert insp.get_table_names(schema="assessment") == ["classroom_assessments"]
        model_cols = {col.name for col in classroom_assessments_table.columns}
        assert model_cols == EXPECTED_COLUMNS


class TestP04TenantFunction:
    def test_current_tenant_id_fails_closed_without_context(
        self, runtime_engine: Engine
    ) -> None:
        with runtime_engine.connect() as conn:
            with pytest.raises(Exception) as excinfo:
                conn.execute(text("SELECT assessment.current_tenant_id()"))
            assert "aieos.tenant_id" in str(excinfo.value)


class TestP05P08Rls:
    def test_p05_same_tenant_insert_read(self, runtime_engine: Engine) -> None:
        tenant_id = uuid.uuid7()
        factory = SqlAlchemyAssessmentUnitOfWorkFactory(runtime_engine)
        created = _assessment(tenant_id=tenant_id)
        with factory(tenant_id) as uow:
            uow.classroom_assessments.insert(created)
            uow.commit()
        with factory(tenant_id) as uow:
            loaded = uow.classroom_assessments.get(created.assessment_id)
        assert loaded is not None
        assert loaded.assessment_id == created.assessment_id
        assert loaded.class_result_level is ClassResultLevel.DEMONSTRATED

    def test_p06_cross_tenant_read_hidden(self, runtime_engine: Engine) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        factory = SqlAlchemyAssessmentUnitOfWorkFactory(runtime_engine)
        created = _assessment(tenant_id=tenant_a)
        with factory(tenant_a) as uow:
            uow.classroom_assessments.insert(created)
            uow.commit()
        with factory(tenant_b) as uow:
            assert uow.classroom_assessments.get(created.assessment_id) is None

    def test_p07_cross_tenant_update_blocked(self, runtime_engine: Engine) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        factory = SqlAlchemyAssessmentUnitOfWorkFactory(runtime_engine)
        created = _assessment(tenant_id=tenant_a)
        with factory(tenant_a) as uow:
            uow.classroom_assessments.insert(created)
            uow.commit()
        corrected = created.correct(
            class_result_level=ClassResultLevel.MIXED,
            class_result_note="spoof",
            updated_at=FIXED_NOW + timedelta(seconds=1),
        )
        with factory(tenant_b) as uow:
            assert not uow.classroom_assessments.update(
                corrected, expected_revision=created.aggregate_revision
            )
            uow.commit()
        with factory(tenant_a) as uow:
            loaded = uow.classroom_assessments.get(created.assessment_id)
        assert loaded is not None
        assert loaded.class_result_level is ClassResultLevel.DEMONSTRATED
        assert int(loaded.aggregate_revision) == 0

    def test_p08_cross_tenant_insert_blocked(self, runtime_engine: Engine) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        factory = SqlAlchemyAssessmentUnitOfWorkFactory(runtime_engine)
        with factory(tenant_b) as uow:
            with pytest.raises(PersistenceOperationFailed):
                uow.classroom_assessments.insert(_assessment(tenant_id=tenant_a))
                uow.commit()


class TestP09P13Repository:
    def test_p09_insert_get_round_trip(self, runtime_engine: Engine) -> None:
        tenant_id = uuid.uuid7()
        factory = SqlAlchemyAssessmentUnitOfWorkFactory(runtime_engine)
        created = _assessment(
            tenant_id=tenant_id,
            class_result_note="class-level only",
            work_id=uuid.uuid7(),
            execution_id=uuid.uuid7(),
            assignment_id=uuid.uuid7(),
        )
        with factory(tenant_id) as uow:
            uow.classroom_assessments.insert(created)
            uow.commit()
        with factory(tenant_id) as uow:
            loaded = uow.classroom_assessments.get(created.assessment_id)
        assert loaded is not None
        assert loaded.class_result_note == "class-level only"
        assert loaded.work_id == created.work_id
        assert loaded.execution_id == created.execution_id
        assert loaded.assignment_id == created.assignment_id
        assert loaded.content_version_id == created.content_version_id

    def test_p10_correction_cas_succeeds(self, runtime_engine: Engine) -> None:
        tenant_id = uuid.uuid7()
        factory = SqlAlchemyAssessmentUnitOfWorkFactory(runtime_engine)
        created = _assessment(tenant_id=tenant_id)
        with factory(tenant_id) as uow:
            uow.classroom_assessments.insert(created)
            uow.commit()
        with factory(tenant_id) as uow:
            locked = uow.classroom_assessments.get_for_update(created.assessment_id)
            assert locked is not None
            corrected = locked.correct(
                class_result_level=ClassResultLevel.MIXED,
                class_result_note="corrected",
                updated_at=FIXED_NOW + timedelta(seconds=1),
            )
            assert uow.classroom_assessments.update(
                corrected, expected_revision=locked.aggregate_revision
            )
            uow.commit()
        with factory(tenant_id) as uow:
            loaded = uow.classroom_assessments.get(created.assessment_id)
        assert loaded is not None
        assert loaded.class_result_level is ClassResultLevel.MIXED
        assert int(loaded.aggregate_revision) == 1

    def test_p11_stale_cas_lost_race(self, runtime_engine: Engine) -> None:
        tenant_id = uuid.uuid7()
        factory = SqlAlchemyAssessmentUnitOfWorkFactory(runtime_engine)
        created = _assessment(tenant_id=tenant_id)
        with factory(tenant_id) as uow:
            uow.classroom_assessments.insert(created)
            uow.commit()
        with factory(tenant_id) as uow:
            locked = uow.classroom_assessments.get_for_update(created.assessment_id)
            assert locked is not None
            corrected = locked.correct(
                class_result_level=ClassResultLevel.MIXED,
                class_result_note="first",
                updated_at=FIXED_NOW + timedelta(seconds=1),
            )
            assert uow.classroom_assessments.update(
                corrected, expected_revision=locked.aggregate_revision
            )
            uow.commit()
        stale = created.correct(
            class_result_level=ClassResultLevel.NOT_YET_DEMONSTRATED,
            class_result_note="stale",
            updated_at=FIXED_NOW + timedelta(seconds=2),
        )
        with factory(tenant_id) as uow:
            assert not uow.classroom_assessments.update(
                stale, expected_revision=AggregateRevision(0)
            )
            uow.commit()
        with factory(tenant_id) as uow:
            loaded = uow.classroom_assessments.get(created.assessment_id)
        assert loaded is not None
        assert loaded.class_result_level is ClassResultLevel.MIXED
        assert loaded.class_result_note == "first"
        assert int(loaded.aggregate_revision) == 1

    def test_p12_void_persists_terminal_state(self, runtime_engine: Engine) -> None:
        tenant_id = uuid.uuid7()
        factory = SqlAlchemyAssessmentUnitOfWorkFactory(runtime_engine)
        created = _assessment(tenant_id=tenant_id)
        with factory(tenant_id) as uow:
            uow.classroom_assessments.insert(created)
            uow.commit()
        with factory(tenant_id) as uow:
            locked = uow.classroom_assessments.get_for_update(created.assessment_id)
            assert locked is not None
            voided = locked.void(voided_at=FIXED_NOW + timedelta(seconds=1))
            assert uow.classroom_assessments.update(
                voided, expected_revision=locked.aggregate_revision
            )
            uow.commit()
        with factory(tenant_id) as uow:
            loaded = uow.classroom_assessments.get(created.assessment_id)
        assert loaded is not None
        assert loaded.lifecycle_state is AssessmentLifecycleState.VOIDED
        assert loaded.voided_at is not None
        assert int(loaded.aggregate_revision) == 1

    def test_p13_immutable_composition_fields_unchanged(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        factory = SqlAlchemyAssessmentUnitOfWorkFactory(runtime_engine)
        created = _assessment(
            tenant_id=tenant_id,
            work_id=uuid.uuid7(),
            execution_id=uuid.uuid7(),
            assignment_id=uuid.uuid7(),
        )
        with factory(tenant_id) as uow:
            uow.classroom_assessments.insert(created)
            uow.commit()
        with factory(tenant_id) as uow:
            locked = uow.classroom_assessments.get_for_update(created.assessment_id)
            assert locked is not None
            corrected = locked.correct(
                class_result_level=ClassResultLevel.MIXED,
                class_result_note="note",
                updated_at=FIXED_NOW + timedelta(seconds=1),
            )
            uow.classroom_assessments.update(
                corrected, expected_revision=locked.aggregate_revision
            )
            uow.commit()
        with factory(tenant_id) as uow:
            loaded = uow.classroom_assessments.get(created.assessment_id)
        assert loaded is not None
        assert loaded.content_id == created.content_id
        assert loaded.content_version_id == created.content_version_id
        assert loaded.class_ref == created.class_ref
        assert loaded.teacher_principal_id == created.teacher_principal_id
        assert loaded.work_id == created.work_id
        assert loaded.execution_id == created.execution_id
        assert loaded.assignment_id == created.assignment_id
        assert loaded.recorded_at == created.recorded_at
        assert loaded.created_at == created.created_at


class TestP14NoDelete:
    def test_repository_has_no_delete(self) -> None:
        assert not hasattr(SqlAlchemyClassroomAssessmentRepository, "delete")


class TestP15NoCrossDomainFk:
    def test_no_foreign_keys(self, bootstrap_engine: Engine) -> None:
        with bootstrap_engine.connect() as conn:
            fks = conn.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM information_schema.table_constraints
                    WHERE table_schema = 'assessment'
                      AND table_name = 'classroom_assessments'
                      AND constraint_type = 'FOREIGN KEY'
                    """
                )
            ).scalar_one()
        assert int(fks) == 0
        assert classroom_assessments_table.foreign_key_constraints == set()


class TestP16P17NoBusinessUniqueness:
    def test_same_class_content_two_assessment_ids(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id = uuid.uuid7()
        version_id = uuid.uuid7()
        factory = SqlAlchemyAssessmentUnitOfWorkFactory(runtime_engine)
        first = _assessment(
            tenant_id=tenant_id,
            teacher_principal_id=principal_id,
            content_id=content_id,
            content_version_id=version_id,
            class_ref="class-5a",
        )
        second = _assessment(
            tenant_id=tenant_id,
            teacher_principal_id=principal_id,
            content_id=content_id,
            content_version_id=version_id,
            class_ref="class-5a",
            recorded_at=FIXED_NOW + timedelta(seconds=1),
        )
        assert first.assessment_id != second.assessment_id
        with factory(tenant_id) as uow:
            uow.classroom_assessments.insert(first)
            uow.classroom_assessments.insert(second)
            uow.commit()
        with factory(tenant_id) as uow:
            assert uow.classroom_assessments.get(first.assessment_id) is not None
            assert uow.classroom_assessments.get(second.assessment_id) is not None


class TestP18P19Downgrade:
    def test_p18_empty_downgrade_succeeds(
        self, postgres18, bootstrap_engine: Engine
    ) -> None:
        cfg = alembic_config(postgres18["migrator_url"])
        with bootstrap_engine.begin() as conn:
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
        command.downgrade(cfg, "tosd070002")
        try:
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
                            """
                            SELECT COUNT(*) FROM information_schema.schemata
                            WHERE schema_name = 'assessment'
                            """
                        )
                    ).scalar_one()
                    == 0
                )
        finally:
            command.upgrade(cfg, "head")
            provision_runtime_grants(bootstrap_engine)

    def test_p19_nonempty_downgrade_refuses(
        self, postgres18, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        cfg = alembic_config(postgres18["migrator_url"])
        tenant_id = uuid.uuid7()
        factory = SqlAlchemyAssessmentUnitOfWorkFactory(runtime_engine)
        created = _assessment(tenant_id=tenant_id)
        with factory(tenant_id) as uow:
            uow.classroom_assessments.insert(created)
            uow.commit()
        try:
            with pytest.raises(Exception) as exc:
                command.downgrade(cfg, "tosd070002")
            message = str(exc.value)
            cause = exc.value.__cause__
            if cause is not None:
                message = f"{message} {cause}"
            assert "ClassroomAssessment evidence exists" in message
            with bootstrap_engine.connect() as conn:
                assert (
                    conn.execute(
                        text("SELECT version_num FROM alembic_version")
                    ).scalar_one()
                    == "tosd080001"
                )
            with factory(tenant_id) as uow:
                loaded = uow.classroom_assessments.get(created.assessment_id)
            assert loaded is not None
        finally:
            with bootstrap_engine.begin() as conn:
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
            command.upgrade(cfg, "head")
            provision_runtime_grants(bootstrap_engine)


class TestP20RuntimeCannotDelete:
    def test_runtime_role_cannot_delete(self, runtime_engine: Engine) -> None:
        tenant_id = uuid.uuid7()
        factory = SqlAlchemyAssessmentUnitOfWorkFactory(runtime_engine)
        created = _assessment(tenant_id=tenant_id)
        with factory(tenant_id) as uow:
            uow.classroom_assessments.insert(created)
            uow.commit()
        with runtime_engine.connect() as conn:
            conn.execute(
                text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            with pytest.raises(Exception) as excinfo:
                conn.execute(
                    text(
                        "DELETE FROM assessment.classroom_assessments "
                        "WHERE assessment_id = :aid"
                    ),
                    {"aid": created.assessment_id.value},
                )
            assert "permission denied" in str(excinfo.value).lower()
            conn.rollback()
        with factory(tenant_id) as uow:
            assert uow.classroom_assessments.get(created.assessment_id) is not None
