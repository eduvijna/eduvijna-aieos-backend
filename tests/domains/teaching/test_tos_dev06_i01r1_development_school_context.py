"""TOS-DEV06-I01R1 — development School Context reader principal scoping."""

from __future__ import annotations

from uuid import uuid4

import pytest

from aieos.development.school_context import DevelopmentSchoolContextClassReader
from aieos.domains.teaching.application.school_context import AssignableClassRef

pytestmark = pytest.mark.tos_dev06_i01

_EXPECTED = (
    AssignableClassRef(class_ref="class-5a", display_label="Grade 5A"),
    AssignableClassRef(class_ref="class-5b", display_label="Grade 5B"),
)


class TestDevelopmentSchoolContextPrincipalScope:
    def test_matching_tenant_and_principal_returns_synthetic_set(self) -> None:
        tenant_id = uuid4()
        principal_id = uuid4()
        reader = DevelopmentSchoolContextClassReader(
            tenant_id=tenant_id,
            teacher_principal_id=principal_id,
        )

        assert reader.list_assignable_classes(tenant_id, principal_id) == _EXPECTED
        assert reader.calls == [(tenant_id, principal_id)]

    def test_same_tenant_different_principal_returns_empty(self) -> None:
        tenant_id = uuid4()
        principal_id = uuid4()
        reader = DevelopmentSchoolContextClassReader(
            tenant_id=tenant_id,
            teacher_principal_id=principal_id,
        )

        assert reader.list_assignable_classes(tenant_id, uuid4()) == ()

    def test_different_tenant_same_principal_returns_empty(self) -> None:
        tenant_id = uuid4()
        principal_id = uuid4()
        reader = DevelopmentSchoolContextClassReader(
            tenant_id=tenant_id,
            teacher_principal_id=principal_id,
        )

        assert reader.list_assignable_classes(uuid4(), principal_id) == ()

    def test_app_factory_composes_adapter_for_supplied_tenant_principal(
        self,
    ) -> None:
        import ast
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[3]
            / "src"
            / "aieos"
            / "development"
            / "app_factory.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and (
                (
                    isinstance(node.func, ast.Name)
                    and node.func.id == "DevelopmentSchoolContextClassReader"
                )
                or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "DevelopmentSchoolContextClassReader"
                )
            )
        ]
        assert len(calls) == 1
        keywords = {kw.arg: kw.value for kw in calls[0].keywords}
        assert set(keywords) == {"tenant_id", "teacher_principal_id"}
        assert isinstance(keywords["tenant_id"], ast.Name)
        assert keywords["tenant_id"].id == "tenant_id"
        assert isinstance(keywords["teacher_principal_id"], ast.Name)
        assert keywords["teacher_principal_id"].id == "principal_id"
