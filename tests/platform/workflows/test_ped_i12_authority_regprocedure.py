"""PED-I12 exact workflow candidate-function OID proofs (PostgreSQL)."""

from __future__ import annotations

import pytest
from sqlalchemy import text

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
from tests.conftest import WORKFLOW_CANDIDATE_READER_ROLE, WORKFLOW_DISPATCHER_USER
pytestmark = pytest.mark.postgres_candidate_authority

_WRONG_START = (
    "workflow.list_start_intent_candidates(timestamp with time zone,integer)"
)
_WRONG_COMMAND = (
    "workflow.list_command_intent_candidates(timestamp with time zone,integer)"
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
        database_role=WORKFLOW_DISPATCHER_USER,
        database_connect_timeout_seconds=5,
        temporal_target_host="temporal.example:7233",
        temporal_namespace="ns",
        temporal_api_key="x",
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


def test_authority_probe_uses_exact_dual_regprocedure_oids(
    workflow_dispatcher_engine, bootstrap_engine
) -> None:
    with bootstrap_engine.begin() as conn:
        conn.execute(text("SET LOCAL ROLE aieos_content_owner"))
        conn.execute(
            text(
                """
                CREATE OR REPLACE FUNCTION
                workflow.list_start_intent_candidates(
                    timestamp with time zone, integer
                )
                RETURNS TABLE(tenant_id uuid, eligible_at timestamptz)
                LANGUAGE sql
                AS $$ SELECT NULL::uuid, NULL::timestamptz WHERE false $$
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE OR REPLACE FUNCTION
                workflow.list_command_intent_candidates(
                    timestamp with time zone, integer
                )
                RETURNS TABLE(tenant_id uuid, eligible_at timestamptz)
                LANGUAGE sql
                AS $$ SELECT NULL::uuid, NULL::timestamptz WHERE false $$
                """
            )
        )

    with workflow_dispatcher_engine.connect() as conn:
        start_correct = conn.execute(
            text("SELECT to_regprocedure(:reg)::oid"),
            {"reg": START_CANDIDATE_REGPROCEDURE},
        ).scalar_one()
        command_correct = conn.execute(
            text("SELECT to_regprocedure(:reg)::oid"),
            {"reg": COMMAND_CANDIDATE_REGPROCEDURE},
        ).scalar_one()
        start_wrong = conn.execute(
            text("SELECT to_regprocedure(:reg)::oid"),
            {"reg": _WRONG_START},
        ).scalar_one()
        command_wrong = conn.execute(
            text("SELECT to_regprocedure(:reg)::oid"),
            {"reg": _WRONG_COMMAND},
        ).scalar_one()
        assert int(start_correct) != int(start_wrong)
        assert int(command_correct) != int(command_wrong)

    result = probe_workflow_dispatcher_database_authority(
        workflow_dispatcher_engine, _cfg()
    )
    assert result.current_user == WORKFLOW_DISPATCHER_USER
    assert result.start_function.function_oid == int(start_correct)
    assert result.command_function.function_oid == int(command_correct)
    assert result.start_function.function_owner == result.command_function.function_owner
    assert "integer" in result.start_function.function_identity.lower()
    assert "timestamp" in result.start_function.function_identity.lower()

    with bootstrap_engine.begin() as conn:
        conn.execute(text("SET LOCAL ROLE aieos_content_owner"))
        conn.execute(
            text(
                """
                DROP FUNCTION
                workflow.list_start_intent_candidates(
                    timestamp with time zone, integer
                )
                """
            )
        )
        conn.execute(
            text(
                """
                DROP FUNCTION
                workflow.list_command_intent_candidates(
                    timestamp with time zone, integer
                )
                """
            )
        )


def test_wrong_overload_constants_differ_from_authorized() -> None:
    assert START_CANDIDATE_REGPROCEDURE != _WRONG_START
    assert COMMAND_CANDIDATE_REGPROCEDURE != _WRONG_COMMAND
    assert "timestamp without time zone" not in START_CANDIDATE_REGPROCEDURE
    assert "timestamp without time zone" not in COMMAND_CANDIDATE_REGPROCEDURE


def test_ordinary_outbound_candidate_reader_membership_fails_closed(
    workflow_dispatcher_engine, bootstrap_engine
) -> None:
    """PED-I12R1: candidate-reader -> ordinary NOLOGIN role must fail closed.

    Direction is exact: GRANT ordinary TO workflow_candidate_reader.
    deployment-admin -> candidate-reader remains a separate governed edge and is
    not the subject of this proof.
    """
    ordinary = "aieos_pedi12r1_ordinary_outbound_probe"
    baseline = probe_workflow_dispatcher_database_authority(
        workflow_dispatcher_engine, _cfg()
    )
    assert baseline.current_user == WORKFLOW_DISPATCHER_USER

    try:
        with bootstrap_engine.begin() as conn:
            conn.execute(
                text(
                    f"""
                    CREATE ROLE {ordinary}
                        NOLOGIN NOSUPERUSER NOBYPASSRLS
                        NOCREATEDB NOCREATEROLE NOREPLICATION
                    """
                )
            )
            # candidate-reader -> ordinary (forbidden outbound direction)
            conn.execute(
                text(f"GRANT {ordinary} TO {WORKFLOW_CANDIDATE_READER_ROLE}")
            )

        with pytest.raises(
            RuntimeConfigurationError,
            match="must not be a member of any other PostgreSQL role",
        ):
            probe_workflow_dispatcher_database_authority(
                workflow_dispatcher_engine, _cfg()
            )
    finally:
        with bootstrap_engine.begin() as conn:
            conn.execute(
                text(
                    f"REVOKE {ordinary} FROM {WORKFLOW_CANDIDATE_READER_ROLE}"
                )
            )
            conn.execute(text(f"DROP ROLE IF EXISTS {ordinary}"))

    restored = probe_workflow_dispatcher_database_authority(
        workflow_dispatcher_engine, _cfg()
    )
    assert restored.current_user == WORKFLOW_DISPATCHER_USER
    assert (
        restored.start_function.function_owner
        == restored.command_function.function_owner
        == WORKFLOW_CANDIDATE_READER_ROLE
    )
