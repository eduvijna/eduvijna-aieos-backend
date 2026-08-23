"""PED-I12 / PED-I12R1 unit: authority probe fail-closed without PostgreSQL."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from aieos.platform.runtime.config_workflow_dispatcher import (
    WorkflowDispatcherRuntimeConfig,
)
from aieos.platform.runtime.errors import RuntimeConfigurationError
from aieos.platform.runtime.models import DeploymentEnvironment, ReleaseIdentity
from aieos.platform.runtime.workflow_dispatcher_authority import (
    COMMAND_CANDIDATE_REGPROCEDURE,
    START_CANDIDATE_REGPROCEDURE,
    probe_workflow_dispatcher_database_authority,
)

pytestmark = pytest.mark.ped_i12

_AUTHORITY_SRC = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "aieos"
    / "platform"
    / "runtime"
    / "workflow_dispatcher_authority.py"
)


def _cfg() -> WorkflowDispatcherRuntimeConfig:
    return WorkflowDispatcherRuntimeConfig(
        environment=DeploymentEnvironment.PRODUCTION,
        release_identity=ReleaseIdentity(
            application_version="0.1.0",
            git_sha="a" * 40,
            build_id="b1",
            artifact_digest="sha256:" + ("c" * 64),
        ),
        database_url="postgresql+psycopg://u:p@localhost/db",
        database_role="aieos_workflow_dispatcher",
        database_connect_timeout_seconds=5,
        temporal_target_host="temporal.example:7233",
        temporal_namespace="ns",
        temporal_api_key="secret",
        temporal_connect_timeout_seconds=5,
        poll_interval_seconds=2,
        candidate_batch_size=10,
        max_intents_per_tenant_per_pass=1,
        claim_lease_seconds=30,
        max_attempts=3,
        retry_delay_seconds=1,
        result_timeout_seconds=30,
        start_reconciliation_timeout_seconds=10,
        shutdown_grace_seconds=5,
    )


def _fn_row(*, oid: int, owner: str) -> dict:
    return {
        "function_oid": oid,
        "security_definer": True,
        "owner_name": owner,
        "owner_login": False,
        "owner_super": False,
        "owner_bypassrls": False,
        "can_execute": True,
        "public_execute": False,
        "identity_args": "integer, timestamp with time zone",
    }


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value

    def scalar_one_or_none(self):
        return self._value

    def one(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return self._value

    def mappings(self):
        return self


def test_authorized_regprocedure_constants_are_exact() -> None:
    assert START_CANDIDATE_REGPROCEDURE == (
        "workflow.list_start_intent_candidates(integer,timestamp with time zone)"
    )
    assert COMMAND_CANDIDATE_REGPROCEDURE == (
        "workflow.list_command_intent_candidates(integer,timestamp with time zone)"
    )
    for const in (START_CANDIDATE_REGPROCEDURE, COMMAND_CANDIDATE_REGPROCEDURE):
        assert "timestamp without time zone" not in const
        assert not const.endswith("(timestamp with time zone,integer)")


def test_probe_fails_closed_when_start_regprocedure_missing() -> None:
    responses = [
        "aieos_workflow_dispatcher",
        SimpleNamespace(rolcanlogin=True, rolsuper=False, rolbypassrls=False),
        [],
        None,
    ]
    idx = {"i": 0}

    class _Conn:
        def execute(self, statement, params=None):
            value = responses[idx["i"]]
            idx["i"] += 1
            return _Result(value)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    engine = MagicMock()
    engine.connect.return_value = _Conn()

    with pytest.raises(RuntimeConfigurationError, match="exact signature is missing"):
        probe_workflow_dispatcher_database_authority(engine, _cfg())


def test_outbound_membership_query_has_no_attribute_filter() -> None:
    """PED-I12R1: zero-outbound proof must not filter on SUPERUSER/BYPASSRLS/LOGIN."""
    source = _AUTHORITY_SRC.read_text(encoding="utf-8")
    assert "WHERE member.rolname = :owner" in source
    outbound_region = source.split("WHERE member.rolname = :owner", 1)[1].split(
        "return WorkflowCandidateFunctionProbe", 1
    )[0]
    assert "granted.rolsuper" not in outbound_region
    assert "granted.rolbypassrls" not in outbound_region
    assert "granted.rolcanlogin" not in outbound_region
    assert "must not be a member of any other PostgreSQL role" in source
    assert "OR granted.rolcanlogin" not in source
    assert "OR granted.rolbypassrls" not in source
    assert "OR granted.rolsuper" not in source


def test_ordinary_outbound_membership_fails_closed() -> None:
    """Any outbound membership fails closed, even without SUPERUSER/BYPASSRLS/LOGIN."""
    owner = "aieos_workflow_candidate_reader"

    class _Conn:
        def execute(self, statement, params=None):
            sql = str(statement)
            if "SELECT current_user" in sql:
                return _Result("aieos_workflow_dispatcher")
            if "rolcanlogin, rolsuper, rolbypassrls" in sql:
                return _Result(
                    SimpleNamespace(rolcanlogin=True, rolsuper=False, rolbypassrls=False)
                )
            if "pg_namespace" in sql:
                return _Result([])
            if "to_regprocedure" in sql:
                return _Result(1001)
            if "FROM pg_proc" in sql:
                return _Result(_fn_row(oid=1001, owner=owner))
            if "granted.rolname = :owner" in sql:
                return _Result(0)
            if "member.rolname = :owner" in sql:
                return _Result(1)
            raise AssertionError(f"unexpected SQL: {sql}")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    engine = MagicMock()
    engine.connect.return_value = _Conn()

    with pytest.raises(
        RuntimeConfigurationError,
        match="must not be a member of any other PostgreSQL role",
    ):
        probe_workflow_dispatcher_database_authority(engine, _cfg())


def test_zero_outbound_membership_passes() -> None:
    """Zero outbound COUNT allows both START and COMMAND probes to succeed."""
    owner = "aieos_workflow_candidate_reader"
    call = {"n": 0}

    class _Conn:
        def execute(self, statement, params=None):
            sql = str(statement)
            if "SELECT current_user" in sql:
                return _Result("aieos_workflow_dispatcher")
            if "rolcanlogin, rolsuper, rolbypassrls" in sql:
                return _Result(
                    SimpleNamespace(rolcanlogin=True, rolsuper=False, rolbypassrls=False)
                )
            if "pg_namespace" in sql:
                return _Result([])
            if "to_regprocedure" in sql:
                call["n"] += 1
                return _Result(1001 if call["n"] == 1 else 1002)
            if "FROM pg_proc" in sql:
                oid = int(params["oid"]) if params else 0
                return _Result(_fn_row(oid=oid, owner=owner))
            if "granted.rolname = :owner" in sql:
                return _Result(0)
            if "member.rolname = :owner" in sql:
                return _Result(0)
            raise AssertionError(f"unexpected SQL: {sql}")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    engine = MagicMock()
    engine.connect.return_value = _Conn()
    result = probe_workflow_dispatcher_database_authority(engine, _cfg())
    assert result.start_function.function_owner == owner
    assert result.command_function.function_owner == owner
    assert result.start_function.function_oid == 1001
    assert result.command_function.function_oid == 1002


def test_dispatcher_to_candidate_reader_membership_still_rejected() -> None:
    owner = "aieos_workflow_candidate_reader"

    class _Conn:
        def execute(self, statement, params=None):
            sql = str(statement)
            if "SELECT current_user" in sql:
                return _Result("aieos_workflow_dispatcher")
            if "rolcanlogin, rolsuper, rolbypassrls" in sql:
                return _Result(
                    SimpleNamespace(rolcanlogin=True, rolsuper=False, rolbypassrls=False)
                )
            if "pg_namespace" in sql:
                return _Result([])
            if "to_regprocedure" in sql:
                return _Result(1001)
            if "FROM pg_proc" in sql:
                return _Result(_fn_row(oid=1001, owner=owner))
            if "granted.rolname = :owner" in sql:
                return _Result(1)
            raise AssertionError(f"unexpected SQL: {sql}")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    engine = MagicMock()
    engine.connect.return_value = _Conn()

    with pytest.raises(
        RuntimeConfigurationError,
        match="must not be a member of the candidate-reader role",
    ):
        probe_workflow_dispatcher_database_authority(engine, _cfg())
