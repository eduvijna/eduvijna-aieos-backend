"""TOS-DEV09-I01 — remediation origin PostgreSQL / RLS / orphan / immutability."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from aieos.domains.teaching.domain.class_result_level_snapshot import (
    ClassResultLevelSnapshot,
)
from aieos.domains.teaching.domain.intent_type import IntentType
from aieos.domains.teaching.domain.remediation_origin import (
    create_remediation_teaching_work_with_origin,
)
from aieos.domains.teaching.domain.work import TeachingWork
from aieos.domains.teaching.infrastructure.persistence.uow import (
    SqlAlchemyTeachingUnitOfWorkFactory,
)
from aieos.platform.runtime.readiness import EXPECTED_ALEMBIC_HEAD
from tests.conftest import alembic_config, provision_runtime_grants
from tools.release.common import EXPECTED_MIGRATION_HEAD

pytestmark = pytest.mark.tos_dev09_i01

FIXED_NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
TARGET = date(2026, 9, 5)
REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = REPO_ROOT / "migrations" / "versions"
_DOWNGRADE_BLOCKED = (
    "tosd090001 downgrade refused: remediate_class TeachingWork or "
    "TeachingWorkRemediationOrigin rows exist"
)


def _prepare_work(*, tenant_id: uuid.UUID, teacher_id: uuid.UUID) -> TeachingWork:
    return TeachingWork.create_from_intent(
        tenant_id=tenant_id,
        teacher_principal_id=teacher_id,
        intent_type=IntentType.PREPARE_TOMORROW,
        goal_text="Prepare fractions",
        target_date=TARGET,
        locale="en-IN",
        created_at=FIXED_NOW,
    )


def _remediation_pair(*, tenant_id: uuid.UUID, teacher_id: uuid.UUID):
    return create_remediation_teaching_work_with_origin(
        tenant_id=tenant_id,
        teacher_principal_id=teacher_id,
        goal_text="Remediate fractions for the class",
        target_date=TARGET,
        locale="en-IN",
        created_at=FIXED_NOW,
        source_assessment_id=uuid.uuid7(),
        source_assessment_aggregate_revision=3,
        source_class_result_level_snapshot=ClassResultLevelSnapshot.MIXED,
        source_class_ref="class-5a",
        source_content_id=uuid.uuid7(),
        source_content_version_id=uuid.uuid7(),
    )


class TestMigrationHeadAndShape:
    def test_alembic_head_tosd090001(self, bootstrap_engine: Engine) -> None:
        assert EXPECTED_ALEMBIC_HEAD == "tosd090001"
        assert EXPECTED_MIGRATION_HEAD == "tosd090001"
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "tosd090001"
            )
        versions = sorted(
            p.name for p in MIGRATIONS.glob("*.py") if p.name != "__init__.py"
        )
        assert versions[-1].startswith("tosd090001_")

    def test_intent_type_check_accepts_both(self, bootstrap_engine: Engine) -> None:
        with bootstrap_engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT pg_get_constraintdef(oid)
                    FROM pg_constraint
                    WHERE conname = 'ck_teaching_works_intent_type'
                    """
                )
            ).scalar_one()
        assert "prepare_tomorrow" in row
        assert "remediate_class" in row

    def test_origin_table_rls_forced(self, bootstrap_engine: Engine) -> None:
        with bootstrap_engine.connect() as conn:
            enabled, forced = conn.execute(
                text(
                    """
                    SELECT c.relrowsecurity, c.relforcerowsecurity
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'teaching'
                      AND c.relname = 'work_remediation_origins'
                    """
                )
            ).one()
        assert enabled is True
        assert forced is True
        insp = inspect(bootstrap_engine)
        cols = {
            col["name"]
            for col in insp.get_columns("work_remediation_origins", schema="teaching")
        }
        assert {
            "work_id",
            "tenant_id",
            "source_assessment_id",
            "source_assessment_aggregate_revision",
            "source_class_result_level_snapshot",
            "source_class_ref",
            "source_content_id",
            "source_content_version_id",
            "source_work_id",
            "source_execution_id",
            "source_assignment_id",
            "initiating_teacher_principal_id",
            "created_at",
        } <= cols
        assert "updated_at" not in cols
        assert "class_result_note" not in cols


class TestPersistenceInvariants:
    def test_valid_pair_commits(self, runtime_engine: Engine) -> None:
        tenant_id = uuid.uuid7()
        teacher_id = uuid.uuid7()
        work, origin = _remediation_pair(tenant_id=tenant_id, teacher_id=teacher_id)
        factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
        with factory(tenant_id) as uow:
            uow.works.insert(work)
            uow.remediation_origins.insert(origin)
            uow.commit()
        with factory(tenant_id) as uow:
            loaded_work = uow.works.get(work.work_id)
            loaded_origin = uow.remediation_origins.get(work.work_id)
        assert loaded_work is not None
        assert loaded_work.intent_type is IntentType.REMEDIATE_CLASS
        assert loaded_origin is not None
        assert loaded_origin.source_assessment_aggregate_revision == 3
        assert (
            loaded_origin.source_class_result_level_snapshot
            is ClassResultLevelSnapshot.MIXED
        )

    def test_remediate_class_without_origin_cannot_commit(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        teacher_id = uuid.uuid7()
        work, _origin = _remediation_pair(tenant_id=tenant_id, teacher_id=teacher_id)
        factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
        with pytest.raises(Exception):
            with factory(tenant_id) as uow:
                uow.works.insert(work)
                uow.commit()
        with factory(tenant_id) as uow:
            assert uow.works.get(work.work_id) is None
            assert uow.remediation_origins.get(work.work_id) is None

    def test_origin_for_prepare_tomorrow_cannot_commit(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        teacher_id = uuid.uuid7()
        prepare = _prepare_work(tenant_id=tenant_id, teacher_id=teacher_id)
        _work, origin = _remediation_pair(tenant_id=tenant_id, teacher_id=teacher_id)
        # Rebuild origin targeting the prepare work identity.
        from aieos.domains.teaching.domain.remediation_origin import (
            TeachingWorkRemediationOrigin,
        )

        bad_origin = TeachingWorkRemediationOrigin.create(
            work_id=prepare.work_id,
            tenant_id=tenant_id,
            source_assessment_id=origin.source_assessment_id,
            source_assessment_aggregate_revision=0,
            source_class_result_level_snapshot=ClassResultLevelSnapshot.DEMONSTRATED,
            source_class_ref="class-5a",
            source_content_id=origin.source_content_id,
            source_content_version_id=origin.source_content_version_id,
            initiating_teacher_principal_id=teacher_id,
            created_at=FIXED_NOW,
        )
        factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
        with pytest.raises(Exception):
            with factory(tenant_id) as uow:
                uow.works.insert(prepare)
                uow.remediation_origins.insert(bad_origin)
                uow.commit()

    def test_second_origin_for_same_work_fails(self, runtime_engine: Engine) -> None:
        tenant_id = uuid.uuid7()
        teacher_id = uuid.uuid7()
        work, origin = _remediation_pair(tenant_id=tenant_id, teacher_id=teacher_id)
        factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
        with factory(tenant_id) as uow:
            uow.works.insert(work)
            uow.remediation_origins.insert(origin)
            uow.commit()
        duplicate = create_remediation_teaching_work_with_origin(
            tenant_id=tenant_id,
            teacher_principal_id=teacher_id,
            goal_text="Duplicate origin attempt",
            target_date=TARGET,
            locale="en-IN",
            created_at=FIXED_NOW,
            source_assessment_id=uuid.uuid7(),
            source_assessment_aggregate_revision=1,
            source_class_result_level_snapshot=ClassResultLevelSnapshot.DEMONSTRATED,
            source_class_ref="class-5a",
            source_content_id=uuid.uuid7(),
            source_content_version_id=uuid.uuid7(),
            work_id=work.work_id,
        )[1]
        with pytest.raises(Exception):
            with factory(tenant_id) as uow:
                uow.remediation_origins.insert(duplicate)
                uow.commit()

    def test_update_and_delete_origin_fail(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        teacher_id = uuid.uuid7()
        work, origin = _remediation_pair(tenant_id=tenant_id, teacher_id=teacher_id)
        factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
        with factory(tenant_id) as uow:
            uow.works.insert(work)
            uow.remediation_origins.insert(origin)
            uow.commit()
        # Runtime role has no UPDATE/DELETE grant (fail closed).
        with runtime_engine.begin() as conn:
            conn.execute(
                text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            with pytest.raises(Exception):
                conn.execute(
                    text(
                        """
                        UPDATE teaching.work_remediation_origins
                        SET source_class_ref = 'mutated'
                        WHERE work_id = :wid
                        """
                    ),
                    {"wid": work.work_id.value},
                )
            with pytest.raises(Exception):
                conn.execute(
                    text(
                        """
                        DELETE FROM teaching.work_remediation_origins
                        WHERE work_id = :wid
                        """
                    ),
                    {"wid": work.work_id.value},
                )
        # Schema-owner path proves immutable triggers (not only GRANT posture).
        with bootstrap_engine.connect() as conn:
            trans = conn.begin()
            try:
                conn.execute(
                    text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                    {"tid": str(tenant_id)},
                )
                with pytest.raises(Exception) as update_exc:
                    conn.execute(
                        text(
                            """
                            UPDATE teaching.work_remediation_origins
                            SET source_class_ref = 'mutated'
                            WHERE work_id = :wid
                            """
                        ),
                        {"wid": work.work_id.value},
                    )
                assert "immutable" in str(update_exc.value).lower()
            finally:
                trans.rollback()
        with bootstrap_engine.connect() as conn:
            trans = conn.begin()
            try:
                conn.execute(
                    text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                    {"tid": str(tenant_id)},
                )
                with pytest.raises(Exception) as delete_exc:
                    conn.execute(
                        text(
                            """
                            DELETE FROM teaching.work_remediation_origins
                            WHERE work_id = :wid
                            """
                        ),
                        {"wid": work.work_id.value},
                    )
                assert "immutable" in str(delete_exc.value).lower()
            finally:
                trans.rollback()

    def test_cross_tenant_origin_isolated(self, runtime_engine: Engine) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        teacher_id = uuid.uuid7()
        work, origin = _remediation_pair(tenant_id=tenant_a, teacher_id=teacher_id)
        factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
        with factory(tenant_a) as uow:
            uow.works.insert(work)
            uow.remediation_origins.insert(origin)
            uow.commit()
        with factory(tenant_b) as uow:
            assert uow.remediation_origins.get(work.work_id) is None
            assert uow.works.get(work.work_id) is None

    def test_snapshot_survives_work_refine(self, runtime_engine: Engine) -> None:
        tenant_id = uuid.uuid7()
        teacher_id = uuid.uuid7()
        work, origin = _remediation_pair(tenant_id=tenant_id, teacher_id=teacher_id)
        factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
        with factory(tenant_id) as uow:
            uow.works.insert(work)
            uow.remediation_origins.insert(origin)
            uow.commit()
        refined = work.refine(
            updated_at=FIXED_NOW.replace(hour=13),
            goal_text="Refined remediation goal",
        )
        with factory(tenant_id) as uow:
            assert uow.works.update(refined, expected_revision=work.aggregate_revision)
            uow.commit()
        with factory(tenant_id) as uow:
            loaded_origin = uow.remediation_origins.get(work.work_id)
            loaded_work = uow.works.get(work.work_id)
        assert loaded_origin is not None
        assert (
            loaded_origin.source_class_result_level_snapshot
            is ClassResultLevelSnapshot.MIXED
        )
        assert loaded_origin.source_assessment_aggregate_revision == 3
        assert loaded_work is not None
        assert loaded_work.goal_text == "Refined remediation goal"


class TestIntentTypeImmutability:
    _IMMUTABLE_MSG = "intent_type is immutable"

    def test_prepare_tomorrow_cannot_become_remediate_class_runtime(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        teacher_id = uuid.uuid7()
        work = _prepare_work(tenant_id=tenant_id, teacher_id=teacher_id)
        factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
        with factory(tenant_id) as uow:
            uow.works.insert(work)
            uow.commit()
        with runtime_engine.connect() as conn:
            trans = conn.begin()
            try:
                conn.execute(
                    text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                    {"tid": str(tenant_id)},
                )
                with pytest.raises(Exception) as excinfo:
                    conn.execute(
                        text(
                            """
                            UPDATE teaching.works
                            SET intent_type = 'remediate_class'
                            WHERE work_id = :wid
                            """
                        ),
                        {"wid": work.work_id.value},
                    )
                assert self._IMMUTABLE_MSG in str(excinfo.value)
            finally:
                trans.rollback()
        with factory(tenant_id) as uow:
            loaded = uow.works.get(work.work_id)
            assert loaded is not None
            assert loaded.intent_type is IntentType.PREPARE_TOMORROW
            assert uow.remediation_origins.get(work.work_id) is None

    def test_remediate_class_cannot_become_prepare_tomorrow_runtime(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        teacher_id = uuid.uuid7()
        work, origin = _remediation_pair(tenant_id=tenant_id, teacher_id=teacher_id)
        factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
        with factory(tenant_id) as uow:
            uow.works.insert(work)
            uow.remediation_origins.insert(origin)
            uow.commit()
        with runtime_engine.connect() as conn:
            trans = conn.begin()
            try:
                conn.execute(
                    text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                    {"tid": str(tenant_id)},
                )
                with pytest.raises(Exception) as excinfo:
                    conn.execute(
                        text(
                            """
                            UPDATE teaching.works
                            SET intent_type = 'prepare_tomorrow'
                            WHERE work_id = :wid
                            """
                        ),
                        {"wid": work.work_id.value},
                    )
                assert self._IMMUTABLE_MSG in str(excinfo.value)
            finally:
                trans.rollback()
        with factory(tenant_id) as uow:
            loaded_work = uow.works.get(work.work_id)
            loaded_origin = uow.remediation_origins.get(work.work_id)
        assert loaded_work is not None
        assert loaded_work.intent_type is IntentType.REMEDIATE_CLASS
        assert loaded_origin is not None
        assert loaded_origin.source_assessment_aggregate_revision == 3

    def test_intent_type_mutation_rejected_for_schema_owner(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        teacher_id = uuid.uuid7()
        prepare = _prepare_work(tenant_id=tenant_id, teacher_id=teacher_id)
        rem_work, rem_origin = _remediation_pair(
            tenant_id=tenant_id, teacher_id=teacher_id
        )
        factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
        with factory(tenant_id) as uow:
            uow.works.insert(prepare)
            uow.works.insert(rem_work)
            uow.remediation_origins.insert(rem_origin)
            uow.commit()
        for work_id, target_intent in (
            (prepare.work_id.value, "remediate_class"),
            (rem_work.work_id.value, "prepare_tomorrow"),
        ):
            with bootstrap_engine.connect() as conn:
                trans = conn.begin()
                try:
                    conn.execute(
                        text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                        {"tid": str(tenant_id)},
                    )
                    with pytest.raises(Exception) as excinfo:
                        conn.execute(
                            text(
                                """
                                UPDATE teaching.works
                                SET intent_type = :intent
                                WHERE work_id = :wid
                                """
                            ),
                            {"intent": target_intent, "wid": work_id},
                        )
                    assert self._IMMUTABLE_MSG in str(excinfo.value)
                finally:
                    trans.rollback()
        with factory(tenant_id) as uow:
            assert uow.works.get(prepare.work_id).intent_type is IntentType.PREPARE_TOMORROW
            assert (
                uow.works.get(rem_work.work_id).intent_type is IntentType.REMEDIATE_CLASS
            )
            assert uow.remediation_origins.get(prepare.work_id) is None
            assert uow.remediation_origins.get(rem_work.work_id) is not None

    def test_prepare_work_refine_still_commits(self, runtime_engine: Engine) -> None:
        tenant_id = uuid.uuid7()
        teacher_id = uuid.uuid7()
        work = _prepare_work(tenant_id=tenant_id, teacher_id=teacher_id)
        factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
        with factory(tenant_id) as uow:
            uow.works.insert(work)
            uow.commit()
        refined = work.refine(
            updated_at=FIXED_NOW.replace(hour=14),
            goal_text="Refined prepare goal",
            class_label="5A",
            subject="Mathematics",
            topic="Fractions",
        )
        with factory(tenant_id) as uow:
            assert uow.works.update(refined, expected_revision=work.aggregate_revision)
            uow.commit()
        with factory(tenant_id) as uow:
            loaded = uow.works.get(work.work_id)
        assert loaded is not None
        assert loaded.intent_type is IntentType.PREPARE_TOMORROW
        assert loaded.goal_text == "Refined prepare goal"
        assert loaded.class_label == "5A"
        assert loaded.subject == "Mathematics"
        assert loaded.topic == "Fractions"


class TestDowngrade:
    def _purge_remediation_rows(self, bootstrap_engine: Engine) -> None:
        with bootstrap_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    DROP TRIGGER IF EXISTS
                        teaching_work_remediation_origins_immutable_delete
                        ON teaching.work_remediation_origins
                    """
                )
            )
            conn.execute(
                text(
                    "ALTER TABLE teaching.work_remediation_origins "
                    "DISABLE ROW LEVEL SECURITY"
                )
            )
            conn.execute(
                text("ALTER TABLE teaching.works DISABLE ROW LEVEL SECURITY")
            )
            conn.execute(text("DELETE FROM teaching.work_remediation_origins"))
            conn.execute(
                text(
                    "DELETE FROM teaching.works WHERE intent_type = 'remediate_class'"
                )
            )
            conn.execute(
                text(
                    """
                    CREATE TRIGGER teaching_work_remediation_origins_immutable_delete
                        BEFORE DELETE ON teaching.work_remediation_origins
                        FOR EACH ROW
                        EXECUTE FUNCTION
                            teaching.reject_work_remediation_origin_mutation()
                    """
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
            conn.execute(
                text("ALTER TABLE teaching.works ENABLE ROW LEVEL SECURITY")
            )
            conn.execute(
                text("ALTER TABLE teaching.works FORCE ROW LEVEL SECURITY")
            )

    def _ensure_head(self, postgres18, bootstrap_engine: Engine) -> None:
        cfg = alembic_config(postgres18["migrator_url"])
        command.upgrade(cfg, "tosd090001")
        provision_runtime_grants(bootstrap_engine)

    def test_empty_downgrade_succeeds(self, postgres18, bootstrap_engine: Engine) -> None:
        self._ensure_head(postgres18, bootstrap_engine)
        self._purge_remediation_rows(bootstrap_engine)
        cfg = alembic_config(postgres18["migrator_url"])
        command.downgrade(cfg, "tosd080002")
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "tosd080002"
            )
            exists = conn.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema = 'teaching'
                          AND table_name = 'work_remediation_origins'
                    )
                    """
                )
            ).scalar_one()
        assert exists is False
        command.upgrade(cfg, "tosd090001")
        provision_runtime_grants(bootstrap_engine)

    def test_nonempty_downgrade_refuses(
        self, postgres18, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        self._ensure_head(postgres18, bootstrap_engine)
        self._purge_remediation_rows(bootstrap_engine)
        tenant_id = uuid.uuid7()
        teacher_id = uuid.uuid7()
        work, origin = _remediation_pair(tenant_id=tenant_id, teacher_id=teacher_id)
        factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
        with factory(tenant_id) as uow:
            uow.works.insert(work)
            uow.remediation_origins.insert(origin)
            uow.commit()
        cfg = alembic_config(postgres18["migrator_url"])
        with pytest.raises(Exception) as excinfo:
            command.downgrade(cfg, "tosd080002")
        assert "tosd090001 downgrade refused" in str(excinfo.value)
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "tosd090001"
            )
        self._purge_remediation_rows(bootstrap_engine)
