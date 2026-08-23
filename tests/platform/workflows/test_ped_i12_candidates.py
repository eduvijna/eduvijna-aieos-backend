"""PED-I12 candidate repository unit tests (no PostgreSQL / no Temporal Cloud)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from aieos.platform.workflows.persistence.candidates import (
    SqlAlchemyCommandIntentCandidateRepository,
    SqlAlchemyStartIntentCandidateRepository,
    WorkflowDispatchCandidate,
)

pytestmark = pytest.mark.ped_i12


def test_start_candidate_calls_exact_function_and_shape() -> None:
    tenant = uuid4()
    eligible = datetime.now(UTC)
    captured: dict = {}

    class _Result:
        def mappings(self):
            return self

        def all(self):
            return [{"tenant_id": tenant, "eligible_at": eligible}]

    class _Conn:
        def execute(self, statement, params=None):
            captured["sql"] = str(statement)
            captured["params"] = params
            return _Result()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    engine = MagicMock()
    engine.connect.return_value = _Conn()
    repo = SqlAlchemyStartIntentCandidateRepository(engine)
    rows = repo.list_candidates(limit=5, as_of=eligible)
    assert rows == (
        WorkflowDispatchCandidate(tenant_id=tenant, eligible_at=eligible),
    )
    assert "workflow.list_start_intent_candidates(:limit, :as_of)" in captured["sql"]
    assert captured["params"] == {"limit": 5, "as_of": eligible}
    assert set(rows[0].__dataclass_fields__) == {"tenant_id", "eligible_at"}


def test_command_candidate_calls_exact_function() -> None:
    tenant = uuid4()
    eligible = datetime.now(UTC)
    captured: dict = {}

    class _Result:
        def mappings(self):
            return self

        def all(self):
            return [{"tenant_id": tenant, "eligible_at": eligible}]

    class _Conn:
        def execute(self, statement, params=None):
            captured["sql"] = str(statement)
            captured["params"] = params
            return _Result()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    engine = MagicMock()
    engine.connect.return_value = _Conn()
    repo = SqlAlchemyCommandIntentCandidateRepository(engine)
    rows = repo.list_candidates(limit=3, as_of=eligible)
    assert len(rows) == 1
    assert "workflow.list_command_intent_candidates(:limit, :as_of)" in captured["sql"]


@pytest.mark.parametrize("limit", [0, 1001, -1])
def test_limit_bounds(limit: int) -> None:
    engine = MagicMock()
    repo = SqlAlchemyStartIntentCandidateRepository(engine)
    with pytest.raises(ValueError, match="1..1000"):
        repo.list_candidates(limit=limit, as_of=datetime.now(UTC))


def test_as_of_must_be_timezone_aware() -> None:
    engine = MagicMock()
    repo = SqlAlchemyCommandIntentCandidateRepository(engine)
    with pytest.raises(ValueError, match="timezone-aware"):
        repo.list_candidates(limit=1, as_of=datetime.now())


def test_candidate_discovery_does_not_set_tenant_context() -> None:
    source = open(
        "src/aieos/platform/workflows/persistence/candidates.py",
        encoding="utf-8",
    ).read()
    assert "set_config" not in source
    assert "aieos.tenant_id" not in source
    assert "SET ROLE" not in source
