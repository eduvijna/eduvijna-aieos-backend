"""PED-I12 unit: exact dual-regprocedure fail-closed without PostgreSQL."""

from __future__ import annotations

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
        "aieos_workflow_dispatcher",  # current_user
        SimpleNamespace(rolcanlogin=True, rolsuper=False, rolbypassrls=False),
        [],  # owned schemas
        None,  # START exact OID missing
    ]
    idx = {"i": 0}

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
