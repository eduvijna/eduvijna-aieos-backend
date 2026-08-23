"""Fail-closed READ-ONLY EVENT dispatcher database authority probe (PED-I11)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.platform.runtime.config_event_dispatcher import EventDispatcherRuntimeConfig
from aieos.platform.runtime.errors import RuntimeConfigurationError

_GOVERNED_SCHEMAS = ("content", "security", "integration", "workflow", "asset")

# Exact PostgreSQL regprocedure identity for ADR-AIEOS-045 candidate authority.
_CANDIDATE_REGPROCEDURE = (
    "integration.list_outbox_dispatch_candidates(integer,timestamp with time zone)"
)


@dataclass(frozen=True, slots=True)
class EventDispatcherAuthorityProbeResult:
    current_user: str
    function_owner: str
    function_oid: int
    function_identity: str


def probe_event_dispatcher_database_authority(
    engine: Engine,
    config: EventDispatcherRuntimeConfig,
) -> EventDispatcherAuthorityProbeResult:
    """Verify LOGIN / NOBYPASSRLS / EXECUTE-only candidate-function boundary.

    Resolves the candidate function by exact ``to_regprocedure`` OID — never by
    name + argument count + substring matching.

    Never logs database URL/password. Raises RuntimeConfigurationError on failure.
    """
    with engine.connect() as conn:
        current_user = conn.execute(text("SELECT current_user")).scalar_one()
        if current_user != config.database_role:
            raise RuntimeConfigurationError(
                f"EVENT dispatcher current_user mismatch expected_role={config.database_role}"
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
            raise RuntimeConfigurationError("EVENT dispatcher role must be LOGIN")
        if row.rolsuper:
            raise RuntimeConfigurationError("EVENT dispatcher role must be NOSUPERUSER")
        if row.rolbypassrls:
            raise RuntimeConfigurationError("EVENT dispatcher role must be NOBYPASSRLS")

        owned = conn.execute(
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
        ).scalars().all()
        if owned:
            raise RuntimeConfigurationError(
                "EVENT dispatcher role must not own governed AIEOS schemas"
            )

        exact_oid = conn.execute(
            text("SELECT to_regprocedure(:reg)::oid"),
            {"reg": _CANDIDATE_REGPROCEDURE},
        ).scalar_one_or_none()
        if exact_oid is None:
            raise RuntimeConfigurationError(
                "integration.list_outbox_dispatch_candidates(integer, timestamptz) "
                "exact signature is missing"
            )

        fn = conn.execute(
            text(
                """
                SELECT p.oid AS function_oid,
                       p.prosecdef AS security_definer,
                       r.rolname AS owner_name,
                       r.rolcanlogin AS owner_login,
                       r.rolsuper AS owner_super,
                       r.rolbypassrls AS owner_bypassrls,
                       has_function_privilege(current_user, p.oid, 'EXECUTE') AS can_execute,
                       has_function_privilege('public', p.oid, 'EXECUTE') AS public_execute,
                       pg_get_function_identity_arguments(p.oid) AS identity_args
                FROM pg_proc p
                JOIN pg_roles r ON r.oid = p.proowner
                WHERE p.oid = :oid
                """
            ),
            {"oid": int(exact_oid)},
        ).mappings().one()

        if not fn["security_definer"]:
            raise RuntimeConfigurationError(
                "candidate function must be SECURITY DEFINER"
            )
        if fn["owner_login"]:
            raise RuntimeConfigurationError("candidate function owner must be NOLOGIN")
        if fn["owner_super"]:
            raise RuntimeConfigurationError(
                "candidate function owner must be NOSUPERUSER"
            )
        if fn["owner_bypassrls"]:
            raise RuntimeConfigurationError(
                "candidate function owner must be NOBYPASSRLS"
            )
        if not fn["can_execute"]:
            raise RuntimeConfigurationError(
                "EVENT dispatcher must have EXECUTE on candidate function"
            )
        if fn["public_execute"]:
            raise RuntimeConfigurationError(
                "PUBLIC must not have EXECUTE on candidate function"
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
                "EVENT dispatcher must not be a member of the candidate-reader role"
            )

        return EventDispatcherAuthorityProbeResult(
            current_user=str(current_user),
            function_owner=str(fn["owner_name"]),
            function_oid=int(fn["function_oid"]),
            function_identity=str(fn["identity_args"]),
        )
