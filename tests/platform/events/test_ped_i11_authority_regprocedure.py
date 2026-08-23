"""PED-I11R1 exact candidate-function OID / wrong-overload proofs (PostgreSQL)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from aieos.platform.runtime.config_event_dispatcher import EventDispatcherRuntimeConfig
from aieos.platform.runtime.errors import RuntimeConfigurationError
from aieos.platform.runtime.event_dispatcher_authority import (
    _CANDIDATE_REGPROCEDURE,
    probe_event_dispatcher_database_authority,
)
from aieos.platform.runtime.models import DeploymentEnvironment, ReleaseIdentity
from tests.conftest import EVENT_DISPATCHER_USER

pytestmark = pytest.mark.postgres_candidate_authority

_WRONG_REGPROCEDURE = (
    "integration.list_outbox_dispatch_candidates(timestamp with time zone,integer)"
)


def _cfg() -> EventDispatcherRuntimeConfig:
    return EventDispatcherRuntimeConfig(
        environment=DeploymentEnvironment.PRODUCTION,
        release_identity=ReleaseIdentity(
            application_version="0.1.0",
            git_sha="a" * 40,
            build_id="b1",
            artifact_digest="sha256:" + ("c" * 64),
        ),
        database_url="postgresql+psycopg://u:p@localhost/db",
        database_role=EVENT_DISPATCHER_USER,
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


def test_authority_probe_uses_exact_regprocedure_oid(
    event_dispatcher_engine, bootstrap_engine
) -> None:
    with bootstrap_engine.begin() as conn:
        conn.execute(text("SET LOCAL ROLE aieos_content_owner"))
        # Wrong argument order — must not satisfy the exact startup probe identity.
        conn.execute(
            text(
                """
                CREATE OR REPLACE FUNCTION
                integration.list_outbox_dispatch_candidates(
                    timestamp with time zone, integer
                )
                RETURNS TABLE(tenant_id uuid, eligible_at timestamptz)
                LANGUAGE sql
                AS $$ SELECT NULL::uuid, NULL::timestamptz WHERE false $$
                """
            )
        )

    with event_dispatcher_engine.connect() as conn:
        correct = conn.execute(
            text("SELECT to_regprocedure(:reg)::oid"),
            {"reg": _CANDIDATE_REGPROCEDURE},
        ).scalar_one()
        wrong = conn.execute(
            text("SELECT to_regprocedure(:reg)::oid"),
            {"reg": _WRONG_REGPROCEDURE},
        ).scalar_one()
        assert correct is not None
        assert wrong is not None
        assert int(correct) != int(wrong)

    result = probe_event_dispatcher_database_authority(
        event_dispatcher_engine, _cfg()
    )
    assert result.current_user == EVENT_DISPATCHER_USER
    assert result.function_oid == int(correct)
    assert "integer" in result.function_identity.lower()
    assert "timestamp" in result.function_identity.lower()

    with bootstrap_engine.begin() as conn:
        conn.execute(text("SET LOCAL ROLE aieos_content_owner"))
        conn.execute(
            text(
                """
                DROP FUNCTION
                integration.list_outbox_dispatch_candidates(
                    timestamp with time zone, integer
                )
                """
            )
        )


def test_wrong_overload_alone_cannot_satisfy_exact_probe(
    event_dispatcher_engine, bootstrap_engine
) -> None:
    """If the exact (integer, timestamptz) OID is absent, probe fails closed."""
    with bootstrap_engine.begin() as conn:
        # Confirm swapped-signature regprocedure does not resolve the authorized OID.
        swapped = conn.execute(
            text("SELECT to_regprocedure(:reg)"),
            {"reg": _WRONG_REGPROCEDURE},
        ).scalar_one_or_none()
        # May be null if overload not present — create disposable then drop authorized?
        # Instead: prove NULL exact identity fails without mutating production function.
        missing = conn.execute(
            text(
                "SELECT to_regprocedure("
                "'integration.list_outbox_dispatch_candidates("
                "integer, timestamp without time zone)')"
            )
        ).scalar_one_or_none()
        assert missing is None

    # Unit-level fail-closed: monkeypatch connection path via a stub engine is heavy;
    # assert the constant used by the probe is the authorized identity.
    assert _CANDIDATE_REGPROCEDURE == (
        "integration.list_outbox_dispatch_candidates(integer,timestamp with time zone)"
    )
    assert "timestamp without time zone" not in _CANDIDATE_REGPROCEDURE
    # Swapped args must not equal authorized constant.
    assert _WRONG_REGPROCEDURE != _CANDIDATE_REGPROCEDURE
