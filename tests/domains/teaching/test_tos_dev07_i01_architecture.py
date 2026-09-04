"""TOS-DEV07-I01 — architecture guards for TeachingExecution persistence."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

from aieos.domains.teaching.infrastructure.persistence.models import (
    execution_content_bindings_table,
    execution_observations_table,
    executions_table,
)
from aieos.platform.runtime.readiness import EXPECTED_ALEMBIC_HEAD
from tools.release.common import EXPECTED_MIGRATION_HEAD, EXPECTED_OPENAPI_SHA256

pytestmark = pytest.mark.tos_dev07_i01

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src" / "aieos"
TEACHING_ROOT = SRC_ROOT / "domains" / "teaching"
MIGRATIONS = REPO_ROOT / "migrations" / "versions"
MIGRATION = MIGRATIONS / "tosd070001_teaching_executions.py"
OPENAPI = REPO_ROOT / "contracts" / "openapi" / "aieos-v1.json"
DOMAIN_FILES = (
    TEACHING_ROOT / "domain" / "execution.py",
    TEACHING_ROOT / "domain" / "execution_content_binding.py",
    TEACHING_ROOT / "domain" / "execution_observation.py",
    TEACHING_ROOT / "domain" / "execution_lifecycle.py",
    TEACHING_ROOT / "domain" / "observation_kind.py",
)
IMPL_GLOBS = (
    "domain/*.py",
    "application/*.py",
    "infrastructure/persistence/*.py",
)


def _sql_literals(path: Path) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return "\n".join(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )


def _impl_sources() -> list[Path]:
    paths: list[Path] = []
    for pattern in IMPL_GLOBS:
        paths.extend(TEACHING_ROOT.glob(pattern))
    return paths


class TestI01ArchitectureGuards:
    def test_migration_head_and_chain(self) -> None:
        assert EXPECTED_ALEMBIC_HEAD == "tosd080002"
        assert EXPECTED_MIGRATION_HEAD == "tosd080002"
        text = MIGRATION.read_text(encoding="utf-8")
        assert 'revision: str = "tosd070001"' in text
        assert 'down_revision: str | None = "tosd060002"' in text
        assert "DROP SCHEMA" not in text
        assert "teaching.executions" in text
        assert "teaching.execution_content_bindings" in text
        assert "teaching.execution_observations" in text

    def test_no_forbidden_sor_or_kit_tables(self) -> None:
        sql = _sql_literals(MIGRATION).lower()
        for needle in (
            "create table teaching.classes",
            "create table teaching.roster",
            "create table teaching.enrollment",
            "create table teaching.preparation_kit",
            "external_period_ref",
            "timetable_id",
            "period_id",
            "learner_id",
            "student_id",
            "execution_assignment",
        ):
            assert needle not in sql
        # Column-level kit fields must not appear in DDL statements.
        ddl = "\n".join(
            line
            for line in sql.splitlines()
            if line.strip().startswith(
                ("create table", "alter table", "constraint", "column")
            )
            or " kit_" in line
            or line.strip().startswith("kit_")
        )
        for needle in ("kit_id", "kit_revision", "kit_status"):
            assert needle not in ddl
            assert f" {needle} " not in f" {sql} "

    def test_no_business_uniqueness_over_teacher_work_class(self) -> None:
        sql = _sql_literals(MIGRATION).lower()
        assert "unique (tenant_id, teacher_principal_id, work_id" not in sql
        assert "unique (teacher_principal_id, work_id, class_ref" not in sql
        assert "unique (tenant_id, work_id, class_ref" not in sql

    def test_no_forbidden_lifecycle_states_in_migration(self) -> None:
        sql = _sql_literals(MIGRATION)
        for state in (
            "PLANNED",
            "SCHEDULED",
            "DELIVERED",
            "ASSESSED",
            "GRADED",
            "MASTERED",
            "ASSIGNED",
        ):
            assert state not in sql

    def test_executions_table_foreign_key_metadata(self) -> None:
        fks = {fk.name: fk for fk in executions_table.foreign_key_constraints}
        assert set(fks) == {"fk_teaching_executions_work"}
        work_fk = fks["fk_teaching_executions_work"]
        assert tuple(work_fk.column_keys) == ("tenant_id", "work_id")
        assert work_fk.ondelete == "RESTRICT"

        binding_fks = {
            fk.name: fk
            for fk in execution_content_bindings_table.foreign_key_constraints
        }
        assert set(binding_fks) == {
            "fk_teaching_execution_content_bindings_execution",
            "fk_teaching_execution_content_bindings_content_version",
        }
        content_fk = binding_fks[
            "fk_teaching_execution_content_bindings_content_version"
        ]
        assert content_fk.ondelete == "RESTRICT"

        obs_fks = {
            fk.name: fk for fk in execution_observations_table.foreign_key_constraints
        }
        assert set(obs_fks) == {"fk_teaching_execution_observations_execution"}

    def test_domain_has_no_infra_http_or_event_imports(self) -> None:
        forbidden_roots = (
            "sqlalchemy",
            "fastapi",
            "starlette",
            "pydantic",
            "nats",
            "alembic",
            "domains.content",
            "school_context",
        )
        for path in DOMAIN_FILES:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imports: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.append(node.module)
            for name in imports:
                assert all(frag not in name for frag in forbidden_roots), (
                    f"{path.name} imports {name}"
                )

    def test_no_preparation_kit_aggregate_in_implementation(self) -> None:
        offenders: list[str] = []
        for path in _impl_sources():
            if "test_" in path.name:
                continue
            text = path.read_text(encoding="utf-8")
            for needle in (
                "class PreparationKit",
                "kit_id",
                "kit_revision",
                "kit_status",
            ):
                if needle in text and "execution" in path.name.lower():
                    offenders.append(f"{path.name}:{needle}")
        assert offenders == []

    def test_openapi_digest_tracks_release_constant(self) -> None:
        # I01 forbade execution HTTP; TOS-DEV07-I02 owns that surface. I01 still
        # freezes digest alignment and that frontend remains out of this repo.
        digest = hashlib.sha256(OPENAPI.read_bytes()).hexdigest().upper()
        assert digest == EXPECTED_OPENAPI_SHA256
        assert not (REPO_ROOT / "frontend").exists()

    def test_no_observation_events_or_new_nats_prefix(self) -> None:
        offenders: list[str] = []
        for path in _impl_sources():
            text = path.read_text(encoding="utf-8")
            for needle in (
                "execution.started.v1",
                "execution.completed.v1",
                "execution.cancelled.v1",
                "execution.observation",
                "PRODUCTION_EVENT_PUBLISH_PREFIXES",
            ):
                if needle in text and "execution" in path.name.lower():
                    offenders.append(f"{path.name}:{needle}")
        assert offenders == []
        # Platform event prefix inventory must not gain execution subjects in I01.
        events_arch = (
            REPO_ROOT / "tests" / "platform" / "events" / "test_architecture.py"
        )
        # Guard against accidental prefix mutation in teaching persistence.
        uow = (
            TEACHING_ROOT / "infrastructure" / "persistence" / "uow.py"
        ).read_text(encoding="utf-8")
        assert "execution.started" not in uow
        assert events_arch.is_file()

    def test_no_learner_or_assignment_array_on_execution(self) -> None:
        source = (TEACHING_ROOT / "domain" / "execution.py").read_text(
            encoding="utf-8"
        )
        for needle in (
            "learner_id",
            "student_id",
            "assignment_id",
            "external_period_ref",
            "timetable_id",
            "period_id",
        ):
            assert needle not in source

    def test_downgrade_does_not_drop_teaching_schema(self) -> None:
        text = MIGRATION.read_text(encoding="utf-8")
        assert "DROP SCHEMA" not in text
        assert "DROP TABLE IF EXISTS teaching.executions" in text
        assert "def downgrade()" in text

    def test_uow_composes_executions_repository(self) -> None:
        uow = (
            TEACHING_ROOT / "infrastructure" / "persistence" / "uow.py"
        ).read_text(encoding="utf-8")
        ports = (TEACHING_ROOT / "application" / "ports.py").read_text(
            encoding="utf-8"
        )
        assert "SqlAlchemyTeachingExecutionRepository" in uow
        assert "self.executions" in uow
        assert "executions: TeachingExecutionRepository" in ports
