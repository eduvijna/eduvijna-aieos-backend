"""Fail-closed READ-ONLY WORKFLOW dispatcher database authority probe (PED-I12)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from aieos.platform.runtime.config_workflow_dispatcher import (
    WorkflowDispatcherRuntimeConfig,
)
from aieos.platform.runtime.errors import RuntimeConfigurationError

_GOVERNED_SCHEMAS = ("content", "security", "integration", "workflow", "asset")

# Exact PostgreSQL regprocedure identities for ADR-AIEOS-045 candidate authority.
START_CANDIDATE_REGPROCEDURE = (
    "workflow.list_start_intent_candidates(integer,timestamp with time zone)"
)
COMMAND_CANDIDATE_REGPROCEDURE = (
    "workflow.list_command_intent_candidates(integer,timestamp with time zone)"
)


@dataclass(frozen=True, slots=True)
class WorkflowCandidateFunctionProbe:
    function_oid: int
    function_owner: str
    function_identity: str
    regprocedure: str


@dataclass(frozen=True, slots=True)
class WorkflowDispatcherAuthorityProbeResult:
    current_user: str
    start_function: WorkflowCandidateFunctionProbe
    command_function: WorkflowCandidateFunctionProbe


def _probe_candidate_function(
    conn: Connection,
    *,
    regprocedure: str,
    label: str,
) -> WorkflowCandidateFunctionProbe:
    exact_oid = conn.execute(
        text("SELECT to_regprocedure(:reg)::oid"),
        {"reg": regprocedure},
    ).scalar_one_or_none()
    if exact_oid is None:
        raise RuntimeConfigurationError(
            f"{label} candidate function exact signature is missing"
        )

    fn = (
        conn.execute(
            text(
                """
                SELECT p.oid AS function_oid,
                       p.prosecdef AS security_definer,
                       r.rolname AS owner_name,
                       r.rolcanlogin AS owner_login,
                       r.rolsuper AS owner_super,
                       r.rolbypassrls AS owner_bypassrls,
                       has_function_privilege(current_user, p.oid, 'EXECUTE')
                           AS can_execute,
                       has_function_privilege('public', p.oid, 'EXECUTE')
                           AS public_execute,
                       pg_get_function_identity_arguments(p.oid) AS identity_args
                FROM pg_proc p
                JOIN pg_roles r ON r.oid = p.proowner
                WHERE p.oid = :oid
                """
            ),
            {"oid": int(exact_oid)},
        )
        .mappings()
        .one()
    )

    if not fn["security_definer"]:
        raise RuntimeConfigurationError(
            f"{label} candidate function must be SECURITY DEFINER"
        )
    if fn["owner_login"]:
        raise RuntimeConfigurationError(
            f"{label} candidate function owner must be NOLOGIN"
        )
    if fn["owner_super"]:
        raise RuntimeConfigurationError(
            f"{label} candidate function owner must be NOSUPERUSER"
        )
    if fn["owner_bypassrls"]:
        raise RuntimeConfigurationError(
            f"{label} candidate function owner must be NOBYPASSRLS"
        )
    if not fn["can_execute"]:
        raise RuntimeConfigurationError(
            f"WORKFLOW dispatcher must have EXECUTE on {label} candidate function"
        )
    if fn["public_execute"]:
        raise RuntimeConfigurationError(
            f"PUBLIC must not have EXECUTE on {label} candidate function"
        )

    membership = conn.execute(
        text(
            """
            SELECT COUNT(*)::int
            FROM pg_auth_members am
            JOIN pg_roles granted ON granted.oid = am.roleid
            JOIN pg_roles member ON member.oid = am.member
            WHERE member.rolname = current_user
              AND granted.rolname = :owner
            """
        ),
        {"owner": fn["owner_name"]},
    ).scalar_one()
    if membership:
        raise RuntimeConfigurationError(
            "WORKFLOW dispatcher must not be a member of the candidate-reader role"
        )

    # Practical outbound membership safety for the candidate-reader owner.
    unsafe_outbound = conn.execute(
        text(
            """
            SELECT COUNT(*)::int
            FROM pg_auth_members am
            JOIN pg_roles granted ON granted.oid = am.roleid
            JOIN pg_roles member ON member.oid = am.member
            WHERE member.rolname = :owner
              AND (
                    granted.rolsuper
                 OR granted.rolbypassrls
                 OR granted.rolcanlogin
              )
            """
        ),
        {"owner": fn["owner_name"]},
    ).scalar_one()
    if unsafe_outbound:
        raise RuntimeConfigurationError(
            f"{label} candidate-reader owner has unsafe outbound role membership"
        )

    return WorkflowCandidateFunctionProbe(
        function_oid=int(fn["function_oid"]),
        function_owner=str(fn["owner_name"]),
        function_identity=str(fn["identity_args"]),
        regprocedure=regprocedure,
    )


def probe_workflow_dispatcher_database_authority(
    engine: Engine,
    config: WorkflowDispatcherRuntimeConfig,
) -> WorkflowDispatcherAuthorityProbeResult:
    """Verify LOGIN / NOBYPASSRLS / dual candidate-function EXECUTE boundary.

    Resolves BOTH candidate functions by exact ``to_regprocedure`` OID — never by
    name + argument count + substring matching.

    Never logs database URL/password. Raises RuntimeConfigurationError on failure.
    """
    with engine.connect() as conn:
        current_user = conn.execute(text("SELECT current_user")).scalar_one()
        if current_user != config.database_role:
            raise RuntimeConfigurationError(
                "WORKFLOW dispatcher current_user mismatch "
                f"expected_role={config.database_role}"
            )

        row = conn.execute(
            text(
                """
                SELECT rolcanlogin, rolsuper, rolbypassrls
                FROM pg_roles
                WHERE rolname = current_user
                """
            )
        ).one()
        if not row.rolcanlogin:
            raise RuntimeConfigurationError("WORKFLOW dispatcher role must be LOGIN")
        if row.rolsuper:
            raise RuntimeConfigurationError(
                "WORKFLOW dispatcher role must be NOSUPERUSER"
            )
        if row.rolbypassrls:
            raise RuntimeConfigurationError(
                "WORKFLOW dispatcher role must be NOBYPASSRLS"
            )

        owned = (
            conn.execute(
                text(
                    """
                    SELECT n.nspname
                    FROM pg_namespace n
                    JOIN pg_roles r ON r.oid = n.nspowner
                    WHERE r.rolname = current_user
                      AND n.nspname = ANY(:schemas)
                    """
                ),
                {"schemas": list(_GOVERNED_SCHEMAS)},
            )
            .scalars()
            .all()
        )
        if owned:
            raise RuntimeConfigurationError(
                "WORKFLOW dispatcher role must not own governed AIEOS schemas"
            )

        start_fn = _probe_candidate_function(
            conn,
            regprocedure=START_CANDIDATE_REGPROCEDURE,
            label="START",
        )
        command_fn = _probe_candidate_function(
            conn,
            regprocedure=COMMAND_CANDIDATE_REGPROCEDURE,
            label="COMMAND",
        )

        if start_fn.function_owner != command_fn.function_owner:
            raise RuntimeConfigurationError(
                "START and COMMAND candidate functions must share the same "
                "workflow candidate-reader owner"
            )

        return WorkflowDispatcherAuthorityProbeResult(
            current_user=str(current_user),
            start_function=start_fn,
            command_function=command_fn,
        )
