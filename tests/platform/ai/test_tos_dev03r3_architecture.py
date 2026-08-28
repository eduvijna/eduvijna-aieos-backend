"""TOS-DEV03R3 architecture guards for provider failure taxonomy."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.dbutil import REPO_ROOT

SRC = REPO_ROOT / "src" / "aieos"
GATEWAY = SRC / "platform" / "ai" / "gateway.py"
OPENAI_PKG = SRC / "platform" / "ai" / "providers" / "openai"
FRONTEND_PACKAGE = REPO_ROOT.parent / "eduvijna-aieos-frontend" / "package.json"


def _import_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_openai_import_confined_to_provider_package() -> None:
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        if OPENAI_PKG in path.parents or path.parent == OPENAI_PKG:
            continue
        if "openai" in _import_roots(path):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == []


def test_provider_neutral_gateway_has_no_openai_types() -> None:
    source = GATEWAY.read_text(encoding="utf-8")
    assert "openai" not in source.lower()
    assert "class ModelRequestRejected" in source
    assert "class ModelAdapterContractFailed" in source
    assert "class ModelOutputInvalid" in source
    assert "class ModelProviderUnavailable" in source
    assert "class ModelGenerationFailed" in source


def test_frontend_has_no_openai_dependency_when_checked_out() -> None:
    if not FRONTEND_PACKAGE.is_file():
        pytest.skip("frontend package.json not present beside backend")
    text = FRONTEND_PACKAGE.read_text(encoding="utf-8").lower()
    assert "openai" not in text
