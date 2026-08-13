"""GCI-I01 architecture-boundary conformance for Generic Content domain."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

DOMAIN_ROOT = (
    Path(__file__).resolve().parents[4]
    / "src"
    / "aieos"
    / "domains"
    / "content"
    / "domain"
)

FORBIDDEN_MODULE_PREFIXES = (
    "sqlalchemy",
    "fastapi",
    "starlette",
    "alembic",
    "asyncpg",
    "psycopg",
    "psycopg2",
    "temporalio",
    "nats",
    "openai",
    "anthropic",
    "google.generativeai",
    "eduvijna",
    "httpx",
)


def _module_root(name: str) -> str:
    return name.split(".")[0]


class ArchitectureConformanceTests(unittest.TestCase):
    def test_domain_package_has_no_forbidden_infrastructure_imports(self) -> None:
        py_files = sorted(DOMAIN_ROOT.rglob("*.py"))
        self.assertTrue(py_files, f"no domain python files under {DOMAIN_ROOT}")
        violations: list[str] = []
        for path in py_files:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root = _module_root(alias.name)
                        if any(
                            alias.name == prefix or alias.name.startswith(prefix + ".")
                            for prefix in FORBIDDEN_MODULE_PREFIXES
                        ) or root in {_module_root(p) for p in FORBIDDEN_MODULE_PREFIXES}:
                            if any(
                                alias.name == prefix or alias.name.startswith(prefix + ".")
                                for prefix in FORBIDDEN_MODULE_PREFIXES
                            ):
                                violations.append(f"{path.name}: import {alias.name}")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    if any(
                        node.module == prefix or node.module.startswith(prefix + ".")
                        for prefix in FORBIDDEN_MODULE_PREFIXES
                    ):
                        violations.append(f"{path.name}: from {node.module} import ...")
        self.assertEqual(violations, [], "forbidden infrastructure imports:\n" + "\n".join(violations))

    def test_domain_sources_do_not_embed_http_or_rfc9457_contracts(self) -> None:
        needles = ("HTTP_STATUS", "rfc9457", "RFC 9457", "application/problem+json")
        hits: list[str] = []
        for path in DOMAIN_ROOT.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for needle in needles:
                if needle.lower() in text.lower():
                    hits.append(f"{path.name}: {needle}")
        self.assertEqual(hits, [])

    def test_domain_does_not_define_shared_platform_identity_types(self) -> None:
        forbidden_classes = {
            "TenantId",
            "PrincipalId",
            "CorrelationId",
            "DelegationId",
        }
        defined: list[str] = []
        for path in DOMAIN_ROOT.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name in forbidden_classes:
                    defined.append(f"{path.name}: class {node.name}")
        self.assertEqual(defined, [])


if __name__ == "__main__":
    unittest.main()

