"""PED-I11R1 unit: exact regprocedure fail-closed without PostgreSQL."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from aieos.platform.runtime.config_event_dispatcher import EventDispatcherRuntimeConfig
from aieos.platform.runtime.errors import RuntimeConfigurationError
from aieos.platform.runtime.event_dispatcher_authority import (
    _CANDIDATE_REGPROCEDURE,
    probe_event_dispatcher_database_authority,
)
from aieos.platform.runtime.models import DeploymentEnvironment, ReleaseIdentity

pytestmark = pytest.mark.ped_i11


def test_authorized_regprocedure_constant_is_exact() -> None:
    assert _CANDIDATE_REGPROCEDURE == (
        "integration.list_outbox_dispatch_candidates(integer,timestamp with time zone)"
    )
    assert "timestamp without time zone" not in _CANDIDATE_REGPROCEDURE
    assert not _CANDIDATE_REGPROCEDURE.endswith("(timestamp with time zone,integer)")


def test_probe_fails_closed_when_exact_regprocedure_missing() -> None:
    cfg = EventDispatcherRuntimeConfig(
        environment=DeploymentEnvironment.PRODUCTION,
        release_identity=ReleaseIdentity(
            application_version="0.1.0",
            git_sha="a" * 40,
            build_id="b1",
            artifact_digest="sha256:" + ("c" * 64),
        ),
        database_url="postgresql+psycopg://u:p@localhost/db",
        database_role="aieos_event_dispatcher",
        database_connect_timeout_seconds=5,
        nats_url="tls://nats.example:4222",
        nats_credentials="x",
        nats_connect_timeout_seconds=5,
        nats_ca_bundle_path=None,
        poll_interval_seconds=2,
        candidate_batch_size=10,
        max_messages_per_tenant_per_pass=1,
        claim_lease_seconds=30,
        max_attempts=3,
        retry_delay_seconds=1,
        publish_timeout_seconds=5,
        shutdown_grace_seconds=5,
    )

    responses = [
        "aieos_event_dispatcher",  # current_user
        SimpleNamespace(rolcanlogin=True, rolsuper=False, rolbypassrls=False),
        [],  # owned schemas
        None,  # exact OID missing
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
        probe_event_dispatcher_database_authority(engine, cfg)
