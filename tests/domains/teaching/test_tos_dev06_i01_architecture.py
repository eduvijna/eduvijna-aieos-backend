"""TOS-DEV06-I01 — architecture guards for School Context ClassRef read."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from aieos.platform.runtime.readiness import EXPECTED_ALEMBIC_HEAD
from tools.release.common import EXPECTED_MIGRATION_HEAD, EXPECTED_OPENAPI_SHA256

pytestmark = pytest.mark.tos_dev06_i01

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src" / "aieos"
TEACHING_ROOT = SRC_ROOT / "domains" / "teaching"
MIGRATIONS = REPO_ROOT / "migrations" / "versions"
OPENAPI_SNAPSHOT = REPO_ROOT / "contracts" / "openapi" / "aieos-v1.json"
RUNTIME_ROOT = SRC_ROOT / "platform" / "runtime"
COMPOSITION = RUNTIME_ROOT / "composition.py"
API_MAIN = RUNTIME_ROOT / "entrypoints" / "api_main.py"


def _teaching_sources() -> list[Path]:
    return sorted(TEACHING_ROOT.rglob("*.py"))


def _openapi() -> dict:
    return json.loads(OPENAPI_SNAPSHOT.read_text(encoding="utf-8"))


class TestNoClassRosterOrAssignmentPersistence:
    def test_alembic_head_is_current(self) -> None:
        assert EXPECTED_ALEMBIC_HEAD == "tosd080002"
        assert EXPECTED_MIGRATION_HEAD == "tosd080002"
        versions = sorted(
            path.name
            for path in MIGRATIONS.glob("*.py")
            if path.name != "__init__.py"
        )
        assert versions[-1].startswith("tosd080002_")
        assert not any("school_context" in name.lower() for name in versions)

    def test_no_class_roster_sqlalchemy_tables(self) -> None:
        forbidden_tokens = (
            "classes",
            "roster",
            "enrollment",
            "teacher_class",
        )
        offenders: list[str] = []
        models = TEACHING_ROOT / "infrastructure" / "persistence" / "models.py"
        source = models.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", getattr(node.func, "attr", ""))
            if name != "Table" or not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                table = first.value.lower()
                if any(token in table for token in forbidden_tokens):
                    offenders.append(table)
        assert offenders == []


class TestSchoolContextPortBoundary:
    def test_application_depends_on_port_not_erp(self) -> None:
        source = (
            TEACHING_ROOT / "application" / "school_context.py"
        ).read_text(encoding="utf-8")
        assert "class SchoolContextClassReader" in source
        assert "Protocol" in source
        lowered = source.lower()
        assert "sqlalchemy" not in lowered
        assert "fastapi" not in lowered
        assert "import erp" not in lowered
        assert "from erp" not in lowered
        assert "import sis" not in lowered
        assert "from sis" not in lowered
        # No concrete vendor adapters in the Teaching application module.
        assert "DevelopmentSchoolContextClassReader" not in source

    def test_teaching_work_class_label_not_consumed_as_classref_authority(self) -> None:
        source = (
            TEACHING_ROOT / "application" / "school_context.py"
        ).read_text(encoding="utf-8")
        assert "class_label" not in source
        routes = (TEACHING_ROOT / "api" / "v1" / "routes.py").read_text(
            encoding="utf-8"
        )
        school_route = routes.split("teacher_os_school_context_classes_list")[1]
        assert "class_label" not in school_route.split("@router.")[0]

    def test_production_runtime_does_not_import_development_adapter(self) -> None:
        for path in (COMPOSITION, API_MAIN):
            text = path.read_text(encoding="utf-8")
            assert "aieos.development" not in text
            assert "DevelopmentSchoolContextClassReader" not in text

    def test_development_adapter_is_non_production(self) -> None:
        path = SRC_ROOT / "development" / "school_context.py"
        text = path.read_text(encoding="utf-8")
        assert "NON_PRODUCTION" in text
        assert "class-5a" in text
        assert "Grade 5A" in text
        assert "teacher_principal_id" in text
        # Constructor binds both tenant and teacher principal.
        assert "def __init__(self, *, tenant_id: UUID, teacher_principal_id: UUID)" in text


class TestOpenApiSchoolContextContract:
    def test_path_operation_and_response(self) -> None:
        schema = _openapi()
        path = "/api/v1/teacher-os/school-context/classes"
        assert path in schema["paths"]
        operation = schema["paths"][path]["get"]
        assert operation["operationId"] == "teacher_os_school_context_classes_list"
        assert "teacher-os" in operation["tags"]
        assert "200" in operation["responses"]
        assert "401" in operation["responses"]
        assert "403" in operation["responses"]
        assert "503" in operation["responses"]
        # GET must not require mutation headers.
        params = operation.get("parameters") or []
        names = {p.get("name") for p in params if isinstance(p, dict)}
        assert "Idempotency-Key" not in names
        assert "If-Match" not in names

    def test_openapi_digest_constant_matches_snapshot(self) -> None:
        import hashlib

        digest = (
            hashlib.sha256(OPENAPI_SNAPSHOT.read_bytes()).hexdigest().upper()
        )
        assert digest == EXPECTED_OPENAPI_SHA256
