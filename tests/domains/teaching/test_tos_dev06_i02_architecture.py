"""TOS-DEV06-I02 — architecture guards for TeachingAssignment persistence."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

from aieos.domains.teaching.infrastructure.persistence.models import assignments_table
from aieos.platform.runtime.activation import (
    FROZEN_API_MUTATION_OPERATION_IDS,
    READ_ONLY_OPERATION_IDS,
)
from aieos.platform.runtime.readiness import EXPECTED_ALEMBIC_HEAD
from tools.release.common import EXPECTED_MIGRATION_HEAD, EXPECTED_OPENAPI_SHA256

pytestmark = pytest.mark.tos_dev06_i02

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src" / "aieos"
TEACHING_ROOT = SRC_ROOT / "domains" / "teaching"
MIGRATIONS = REPO_ROOT / "migrations" / "versions"
OPENAPI = REPO_ROOT / "contracts" / "openapi" / "aieos-v1.json"
MIGRATION = MIGRATIONS / "tosd060001_teaching_assignments.py"


def _sql_literals(path: Path) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return "\n".join(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )


class TestI02ArchitectureGuards:
    def test_migration_head_and_chain(self) -> None:
        assert EXPECTED_ALEMBIC_HEAD == "tosd060001"
        assert EXPECTED_MIGRATION_HEAD == "tosd060001"
        text = MIGRATION.read_text(encoding="utf-8")
        assert 'revision: str = "tosd060001"' in text
        assert 'down_revision: str | None = "tosd040001"' in text
        assert "DROP SCHEMA" not in text
        assert "teaching.assignments" in text

    def test_no_class_roster_enrollment_delivery_tables(self) -> None:
        sql = _sql_literals(MIGRATION).lower()
        for needle in (
            "create table teaching.classes",
            "create table teaching.roster",
            "create table teaching.enrollment",
            "delivery_attempt",
            "external_assignment_ref",
        ):
            assert needle not in sql

    def test_no_business_uniqueness(self) -> None:
        sql = _sql_literals(MIGRATION).lower()
        assert "unique (tenant_id, teacher_principal_id" not in sql
        assert "unique (teacher_principal_id, content_id" not in sql
        assert "unique (tenant_id, content_id, content_version_id, class_ref" not in sql

    def test_assignments_table_foreign_key_metadata_matches_migration(self) -> None:
        fks = {fk.name: fk for fk in assignments_table.foreign_key_constraints}
        assert set(fks) == {
            "fk_teaching_assignments_content_version",
            "fk_teaching_assignments_source_work",
        }

        content_fk = fks["fk_teaching_assignments_content_version"]
        assert tuple(content_fk.column_keys) == (
            "tenant_id",
            "content_id",
            "content_version_id",
        )
        assert tuple(elem.target_fullname for elem in content_fk.elements) == (
            "content.content_versions.tenant_id",
            "content.content_versions.content_id",
            "content.content_versions.version_id",
        )
        assert content_fk.ondelete == "RESTRICT"

        work_fk = fks["fk_teaching_assignments_source_work"]
        assert tuple(work_fk.column_keys) == ("tenant_id", "source_work_id")
        assert tuple(elem.target_fullname for elem in work_fk.elements) == (
            "teaching.works.tenant_id",
            "teaching.works.work_id",
        )
        assert work_fk.ondelete == "RESTRICT"

        remote_targets = {
            elem.target_fullname
            for fk in assignments_table.foreign_key_constraints
            for elem in fk.elements
        }
        assert remote_targets == {
            "content.content_versions.tenant_id",
            "content.content_versions.content_id",
            "content.content_versions.version_id",
            "teaching.works.tenant_id",
            "teaching.works.work_id",
        }
        for forbidden in (
            "class_ref",
            "classes",
            "roster",
            "enrollment",
            "users",
            "principal",
            "lms",
            "external_assignment",
        ):
            assert not any(forbidden in target for target in remote_targets)

    def test_domain_has_no_school_context_or_content_imports(self) -> None:
        path = TEACHING_ROOT / "domain" / "assignment.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
        assert all("school_context" not in name for name in imports)
        assert all("domains.content" not in name for name in imports)
        source = path.read_text(encoding="utf-8")
        assert "class_ref: str" in source
        assert "TeachingWork.class_label" in source  # explicit non-identity note

    def test_no_api_route_or_openapi_change(self) -> None:
        routes = (TEACHING_ROOT / "api" / "v1" / "routes.py").read_text(encoding="utf-8")
        assert "/assignments" not in routes
        assert 'operation_id="teaching_assignment' not in routes
        schema = json.loads(OPENAPI.read_text(encoding="utf-8"))
        assert "/api/v1/teaching/assignments" not in schema["paths"]
        digest = hashlib.sha256(OPENAPI.read_bytes()).hexdigest().upper()
        assert digest == EXPECTED_OPENAPI_SHA256
        assert digest == "230FBDC9323D5C22D6BA7027E74AF977FC7C2EE8C75927D81C5D18C60457B297"
        assert not any("assignment" in op for op in FROZEN_API_MUTATION_OPERATION_IDS)
        assert not any(
            "assignment" in op and "school_context" not in op
            for op in READ_ONLY_OPERATION_IDS
        )

    def test_no_lms_provider_imports(self) -> None:
        offenders: list[str] = []
        for path in TEACHING_ROOT.rglob("*.py"):
            text = path.read_text(encoding="utf-8").lower()
            for needle in ("lms", "canvas", "moodle", "google_classroom"):
                if needle in text and "assignment" in path.name.lower():
                    offenders.append(f"{path.name}:{needle}")
        assert offenders == []

    def test_downgrade_does_not_drop_teaching_schema(self) -> None:
        text = MIGRATION.read_text(encoding="utf-8")
        assert "DROP SCHEMA" not in text
        assert "DROP TABLE IF EXISTS teaching.assignments" in text
        assert 'def downgrade()' in text
