"""TOS-DEV06-I03 — atomicity guards for assignment command services."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.tos_dev06_i03

REPO_ROOT = Path(__file__).resolve().parents[3]
TEACHING_APP = REPO_ROOT / "src" / "aieos" / "domains" / "teaching" / "application"


def test_create_service_commits_once_after_outbox_audit_idempotency() -> None:
    path = TEACHING_APP / "assignment_create.py"
    source = path.read_text(encoding="utf-8")
    assert source.count("uow.commit()") == 1
    assert "uow.outbox.insert" in source
    assert "insert_required_teaching_audit" in source
    assert "uow.idempotency.insert" in source
    tree = ast.parse(source, filename=str(path))
    assert tree is not None


def test_mutation_services_commit_once() -> None:
    for name in ("assignment_mutations.py",):
        source = (TEACHING_APP / name).read_text(encoding="utf-8")
        assert source.count("uow.commit()") == 3
