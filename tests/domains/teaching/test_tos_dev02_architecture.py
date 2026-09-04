"""TOS-DEV02 Lane B — adversarial constitutional guards.

These tests fail if a future change smuggles in a Teaching Intent System of
Record, a persisted Mission, or AI generation into this slice.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.tos_dev02

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src" / "aieos"
TEACHING_ROOT = SRC_ROOT / "domains" / "teaching"
MIGRATIONS = REPO_ROOT / "migrations" / "versions"
OPENAPI_SNAPSHOT = REPO_ROOT / "contracts" / "openapi" / "aieos-v1.json"
MIGRATION = MIGRATIONS / "tosd020001_teaching_work.py"


def _docstring_ids(tree: ast.AST) -> set[int]:
    """Identify docstring nodes so prose never satisfies a DDL assertion."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ):
            continue
        if not node.body:
            continue
        first = node.body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            if isinstance(first.value.value, str):
                ids.add(id(first.value))
    return ids


def _sql_literals(path: Path) -> str:
    """Executable SQL only: docstrings and ``#`` comments are excluded."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    skip = _docstring_ids(tree)
    return "\n".join(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in skip
    )


def _migration_sql() -> str:
    return _sql_literals(MIGRATION).lower()


def _all_migration_sql() -> str:
    return "\n".join(
        _sql_literals(path)
        for path in sorted(MIGRATIONS.glob("*.py"))
        if path.name != "__init__.py"
    ).lower()


def _executable_source(path: Path) -> str:
    """Module source with every docstring removed, for write-path scanning."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    skip = _docstring_ids(tree)
    for node in ast.walk(tree):
        if isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ) and node.body:
            first = node.body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and id(first.value) in skip
            ):
                node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(ast.fix_missing_locations(tree))


def _teaching_sources() -> list[Path]:
    return sorted(TEACHING_ROOT.rglob("*.py"))


def _openapi() -> dict:
    return json.loads(OPENAPI_SNAPSHOT.read_text(encoding="utf-8"))


class TestNoTeachingIntentSystemOfRecord:
    def test_no_migration_creates_a_teaching_intent_table(self) -> None:
        sql = _all_migration_sql()
        assert "teaching_intents" not in sql
        assert "teaching.intents" not in sql
        assert "teaching_intent" not in sql

    def test_no_sqlalchemy_table_is_declared_for_intents(self) -> None:
        offenders = []
        for path in SRC_ROOT.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            in_teaching = TEACHING_ROOT in path.parents
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = (
                    func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                )
                if name != "Table" or not node.args:
                    continue
                first = node.args[0]
                if not isinstance(first, ast.Constant) or not isinstance(
                    first.value, str
                ):
                    continue
                table = first.value.lower()
                forbidden = (
                    "intent" in table or "mission" in table
                    if in_teaching
                    else "teaching_intent" in table or "mission" in table
                )
                if forbidden:
                    offenders.append(f"{path.name}:{table}")
        assert offenders == []

    def test_teaching_declares_exactly_one_sqlalchemy_table(self) -> None:
        source = (
            TEACHING_ROOT / "infrastructure" / "persistence" / "models.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        tables = [
            node.args[0].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", getattr(node.func, "attr", "")) == "Table"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ]
        assert tables == [
            "works",
            "assignments",
            "executions",
            "execution_content_bindings",
            "execution_observations",
            "work_remediation_origins",
        ]

    def test_intent_type_is_a_value_object_not_an_aggregate(self) -> None:
        source = (TEACHING_ROOT / "domain" / "intent_type.py").read_text(
            encoding="utf-8"
        )
        assert "class IntentType" in source
        assert "prepare_tomorrow" in source
        # A value object never carries its own identity or revision.
        assert "IntentId" not in source
        assert "aggregate_revision" not in source

    def test_teaching_repository_ports_expose_works_only(self) -> None:
        source = (TEACHING_ROOT / "application" / "ports.py").read_text(
            encoding="utf-8"
        )
        assert "class TeachingWorkRepository" in source
        assert "IntentRepository" not in source
        assert "MissionRepository" not in source

    def test_unit_of_work_has_no_intent_or_mission_repository(self) -> None:
        source = (TEACHING_ROOT / "infrastructure" / "persistence" / "uow.py").read_text(
            encoding="utf-8"
        )
        assert "self.works" in source
        assert "intent" not in source.lower().replace("intent_type", "")


class TestMissionIsAProjection:
    def test_no_migration_creates_a_mission_table(self) -> None:
        sql = _all_migration_sql()
        assert "mission" not in sql

    def test_teaching_migration_creates_exactly_one_table(self) -> None:
        sql = _migration_sql()
        assert sql.count("create table") == 1
        assert "create table teaching.works" in sql

    def test_mission_service_never_writes(self) -> None:
        source = _executable_source(
            TEACHING_ROOT / "application" / "mission.py"
        ).lower()
        for forbidden in (
            "insert",
            "update(",
            ".commit(",
            "save(",
            "persist",
            "delete",
        ):
            assert forbidden not in source, forbidden

    def test_mission_models_declare_no_revision_or_identity(self) -> None:
        source = (TEACHING_ROOT / "application" / "mission_models.py").read_text(
            encoding="utf-8"
        )
        assert "class TeacherOsMission" in source
        assert "MissionId" not in source
        assert "created_at" not in source


class TestTeachingWorkIsDurable:
    def test_migration_declares_the_durable_work_columns(self) -> None:
        lowered = _migration_sql()
        for column in (
            "work_id",
            "tenant_id",
            "teacher_principal_id",
            "intent_type",
            "goal_text",
            "class_label",
            "subject",
            "topic",
            "target_date",
            "locale",
            "aggregate_revision",
            "created_at",
            "updated_at",
            "archived_at",
        ):
            assert column in lowered, column

    def test_migration_enables_and_forces_row_level_security(self) -> None:
        lowered = _migration_sql()
        assert "enable row level security" in lowered
        assert "force row level security" in lowered
        assert "create policy" in lowered
        assert "teaching.current_tenant_id()" in lowered

    def test_migration_declares_the_required_indexes(self) -> None:
        lowered = _migration_sql()
        assert "(tenant_id)" in lowered
        assert "(tenant_id, teacher_principal_id)" in lowered
        assert "(tenant_id, teacher_principal_id, target_date)" in lowered
        assert "(tenant_id, archived_at)" in lowered

    def test_migration_revision_chain(self) -> None:
        source = MIGRATION.read_text(encoding="utf-8")
        assert 'revision: str = "tosd020001"' in source
        assert 'down_revision: str | None = "adra045001"' in source

    def test_downgrade_drops_only_the_teaching_schema(self) -> None:
        tree = ast.parse(MIGRATION.read_text(encoding="utf-8"))
        downgrade = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "downgrade"
        )
        statements = [
            node.value.lower()
            for node in ast.walk(downgrade)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ]
        assert statements == ["drop schema if exists teaching cascade"]

    def test_migration_touches_no_other_domain_schema(self) -> None:
        lowered = _migration_sql()
        for foreign in ("content.", "asset.", "security.", "edu.", "api."):
            assert f"alter table {foreign}" not in lowered
            assert f"create table {foreign}" not in lowered
            assert f"drop schema {foreign.rstrip('.')}" not in lowered

    def test_class_label_is_not_a_foreign_key(self) -> None:
        migration = _migration_sql()
        models_path = TEACHING_ROOT / "infrastructure" / "persistence" / "models.py"
        models = models_path.read_text(encoding="utf-8")
        assert "foreign key" not in migration
        assert "references" not in migration
        assert "class_label" in models
        from aieos.domains.teaching.infrastructure.persistence.models import (
            assignments_table,
            works_table,
        )

        assert not works_table.c.class_label.foreign_keys
        assert not assignments_table.c.class_ref.foreign_keys


class TestOpenApiContract:
    def test_snapshot_exposes_the_teaching_operations(self) -> None:
        spec = _openapi()
        operations = {
            operation["operationId"]
            for path_item in spec["paths"].values()
            for method, operation in path_item.items()
            if method in {"get", "post", "patch", "put", "delete"}
        }
        assert {
            "teaching_work_create",
            "teaching_work_list",
            "teaching_work_get",
            "teaching_work_refine",
            "teaching_work_generate",
            "teaching_work_prepare",
            "teaching_work_artifacts_list",
            "teacher_os_today_mission",
        } <= operations

    def test_snapshot_exposes_no_intent_or_mission_mutation(self) -> None:
        spec = _openapi()
        for path, path_item in spec["paths"].items():
            assert "teaching-intents" not in path
            assert "teaching/intents" not in path
            for method in ("post", "patch", "put", "delete"):
                if method in path_item:
                    assert "mission" not in path, (method, path)

    def test_mutations_require_idempotency_and_refine_requires_if_match(self) -> None:
        spec = _openapi()
        create = spec["paths"]["/api/v1/teaching/works"]["post"]
        refine = spec["paths"]["/api/v1/teaching/works/{work_id}"]["patch"]
        prepare = spec["paths"]["/api/v1/teaching/works/{work_id}/actions/prepare"][
            "post"
        ]
        create_headers = {p["name"] for p in create.get("parameters", [])}
        refine_headers = {p["name"] for p in refine.get("parameters", [])}
        prepare_headers = {p["name"] for p in prepare.get("parameters", [])}
        assert "Idempotency-Key" in create_headers
        assert "Idempotency-Key" in refine_headers
        assert "Idempotency-Key" in prepare_headers
        assert "If-Match" in refine_headers
        assert "If-Match" in prepare_headers
        assert prepare["operationId"] == "teaching_work_prepare"

    def test_mission_declares_the_required_mission_date_parameter(self) -> None:
        spec = _openapi()
        mission = spec["paths"]["/api/v1/teacher-os/today/mission"]["get"]
        params = {p["name"]: p for p in mission.get("parameters", [])}
        assert params["mission_date"]["required"] is True
        assert params["mission_date"]["in"] == "query"


class TestNoGenerationInThisSlice:
    """DEV03: generation orchestration is allowed in teaching application.

    Provider SDKs and agent frameworks remain forbidden in teaching sources.
    Legitimate identifiers such as ``prompt_execution_ref`` are allowed.
    """

    _FORBIDDEN_IMPORT_ROOTS = (
        "openai",
        "anthropic",
        "mcp",
        "langchain",
        "langgraph",
        "langsmith",
        "autogen",
        "crewai",
        "semantic_kernel",
        "haystack",
        "llama_index",
        "agents",
    )

    def test_generation_orchestration_lives_in_teaching_application(self) -> None:
        generate = TEACHING_ROOT / "application" / "generate.py"
        artifacts = TEACHING_ROOT / "application" / "artifacts.py"
        assert generate.is_file()
        assert artifacts.is_file()
        generate_source = generate.read_text(encoding="utf-8")
        artifacts_source = artifacts.read_text(encoding="utf-8")
        assert "class GenerateTeachingWorkService" in generate_source
        assert "prompt_execution_ref" in generate_source
        assert "class ListTeachingWorkArtifactsService" in artifacts_source
        routes = (TEACHING_ROOT / "api" / "v1" / "routes.py").read_text(
            encoding="utf-8"
        )
        assert 'operation_id="teaching_work_generate"' in routes
        assert 'operation_id="teaching_work_artifacts_list"' in routes

    def test_teaching_sources_import_no_provider_sdk_or_agent_framework(self) -> None:
        offenders: list[str] = []
        for path in _teaching_sources():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                modules: list[str] = []
                if isinstance(node, ast.ImportFrom):
                    if node.module:
                        modules.append(node.module)
                elif isinstance(node, ast.Import):
                    modules.extend(alias.name for alias in node.names)
                for module in modules:
                    root = module.split(".", 1)[0]
                    if root in self._FORBIDDEN_IMPORT_ROOTS or root.startswith(
                        "agent_"
                    ):
                        offenders.append(f"{path.name}:{module}")
        assert offenders == []

    def test_teaching_domain_layer_imports_no_infrastructure(self) -> None:
        offenders = []
        for path in sorted((TEACHING_ROOT / "domain").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                module = None
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                elif isinstance(node, ast.Import):
                    module = node.names[0].name
                if module is None:
                    continue
                if (
                    "sqlalchemy" in module
                    or "fastapi" in module
                    or "infrastructure" in module
                    or module.startswith("aieos.platform.api")
                ):
                    offenders.append(f"{path.name}:{module}")
        assert offenders == []

    def test_teaching_application_layer_imports_no_web_or_orm_framework(self) -> None:
        offenders = []
        for path in sorted((TEACHING_ROOT / "application").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                module = None
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                elif isinstance(node, ast.Import):
                    module = node.names[0].name
                if module is None:
                    continue
                if "sqlalchemy" in module or "fastapi" in module:
                    offenders.append(f"{path.name}:{module}")
        assert offenders == []


class TestStableTeachingWorkErrorCodes:
    def test_teaching_work_not_found_machine_code_is_stable(self) -> None:
        from aieos.domains.teaching.application.errors import TeachingWorkNotFound
        from aieos.platform.api.problems import _TEACHING_PROBLEMS

        status, code, _title, _detail = _TEACHING_PROBLEMS[TeachingWorkNotFound]
        assert status == 404
        assert code == "teaching_work_not_found"


class TestDevelopmentSeedingIsExplicit:
    def test_scenario_is_never_invoked_from_runtime_composition(self) -> None:
        runtime_root = SRC_ROOT / "platform" / "runtime"
        offenders = [
            path.name
            for path in runtime_root.rglob("*.py")
            if "teaching_work_scenario" in path.read_text(encoding="utf-8")
        ]
        assert offenders == []

    def test_app_factory_does_not_auto_seed_teaching_work(self) -> None:
        source = (SRC_ROOT / "platform" / "api" / "app.py").read_text(encoding="utf-8")
        assert "scenario" not in source.lower()
