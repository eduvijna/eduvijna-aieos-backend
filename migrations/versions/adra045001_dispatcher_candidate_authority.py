"""ADR-AIEOS-045 dispatcher tenant-candidate database authority.

Revision ID: adra045001
Revises: pedi10b6001
Create Date: 2026-08-22

Replaces universal FOR ALL tenant RLS policies with role-scoped policies,
adds candidate-reader SELECT policies and minimum column grants, creates
EVENT/WORKFLOW SECURITY DEFINER candidate functions owned by NOLOGIN
candidate-readers (via temporary SET LOCAL ROLE + schema CREATE), and adds
the two evidence-backed EVENT candidate indexes.

Does not CREATE ROLE, ALTER ROLE, grant candidate-reader membership to the
migrator, implement dispatcher daemons, or provision production identities.
Production migration execution remains NOT AUTHORIZED.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence

from alembic import context, op
from sqlalchemy import text

revision: str = "adra045001"
down_revision: str | None = "pedi10b6001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA_OWNER_ROLE_ENV = "AIEOS_SCHEMA_OWNER_ROLE"
SECURITY_SCHEMA_OWNER_ROLE_ENV = "AIEOS_SECURITY_SCHEMA_OWNER_ROLE"
RUNTIME_ROLE_ENV = "AIEOS_RUNTIME_ROLE"
CONTENT_MIGRATION_RUNTIME_ROLE_ENV = "AIEOS_CONTENT_MIGRATION_RUNTIME_ROLE"
EVENT_DISPATCHER_ROLE_ENV = "AIEOS_EVENT_DISPATCHER_ROLE"
WORKFLOW_DISPATCHER_ROLE_ENV = "AIEOS_WORKFLOW_DISPATCHER_ROLE"
EVENT_CANDIDATE_READER_ROLE_ENV = "AIEOS_EVENT_CANDIDATE_READER_ROLE"
WORKFLOW_CANDIDATE_READER_ROLE_ENV = "AIEOS_WORKFLOW_CANDIDATE_READER_ROLE"

_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")

_CANDIDATE_COLUMNS = ("tenant_id", "status", "available_at", "claimed_until")

_EVENT_FN = "integration.list_outbox_dispatch_candidates(integer, timestamptz)"
_START_FN = "workflow.list_start_intent_candidates(integer, timestamptz)"
_COMMAND_FN = "workflow.list_command_intent_candidates(integer, timestamptz)"

_IX_PENDING = "ix_outbox_messages_candidate_pending"
_IX_CLAIMED = "ix_outbox_messages_candidate_claimed"


def _require_role(env_name: str, *, purpose: str) -> str:
    role = os.environ.get(env_name, "").strip()
    if not role:
        raise RuntimeError(
            f"{env_name} must be set to the {purpose}; Alembic fails closed "
            "before policy replacement when role inputs are missing."
        )
    if not _ROLE_NAME.fullmatch(role):
        raise RuntimeError(
            f"{env_name} must be a lowercase unquoted PostgreSQL identifier"
        )
    return role


def _load_roles() -> dict[str, str]:
    return {
        "content_owner": _require_role(
            SCHEMA_OWNER_ROLE_ENV, purpose="content schema-owner role"
        ),
        "security_owner": _require_role(
            SECURITY_SCHEMA_OWNER_ROLE_ENV, purpose="security schema-owner role"
        ),
        "runtime": _require_role(RUNTIME_ROLE_ENV, purpose="ordinary runtime role"),
        "migration_runtime": _require_role(
            CONTENT_MIGRATION_RUNTIME_ROLE_ENV,
            purpose="content migration runtime role",
        ),
        "event_dispatcher": _require_role(
            EVENT_DISPATCHER_ROLE_ENV, purpose="event dispatcher role"
        ),
        "workflow_dispatcher": _require_role(
            WORKFLOW_DISPATCHER_ROLE_ENV, purpose="workflow dispatcher role"
        ),
        "event_candidate": _require_role(
            EVENT_CANDIDATE_READER_ROLE_ENV,
            purpose="event candidate-reader role",
        ),
        "workflow_candidate": _require_role(
            WORKFLOW_CANDIDATE_READER_ROLE_ENV,
            purpose="workflow candidate-reader role",
        ),
    }


def _assert_distinct(roles: dict[str, str]) -> None:
    pairs = (
        ("runtime", "event_dispatcher"),
        ("runtime", "workflow_dispatcher"),
        ("event_dispatcher", "workflow_dispatcher"),
        ("event_candidate", "workflow_candidate"),
    )
    for left, right in pairs:
        if roles[left] == roles[right]:
            raise RuntimeError(
                f"role distinctness failed: {left} and {right} resolve to "
                f"the same identity '{roles[left]}'"
            )

    protected = (
        "runtime",
        "migration_runtime",
        "event_dispatcher",
        "workflow_dispatcher",
        "content_owner",
        "security_owner",
    )
    for candidate_key in ("event_candidate", "workflow_candidate"):
        for other in protected:
            if roles[candidate_key] == roles[other]:
                raise RuntimeError(
                    f"role distinctness failed: {candidate_key} aliases "
                    f"{other} as '{roles[candidate_key]}'"
                )


def _preflight(roles: dict[str, str]) -> None:
    """Fail closed before dropping universal policies."""
    bind = op.get_bind()
    for key, role in roles.items():
        exists = bind.execute(
            text("SELECT COUNT(*) FROM pg_roles WHERE rolname = :role"),
            {"role": role},
        ).scalar_one()
        if exists != 1:
            raise RuntimeError(
                f"required role '{role}' ({key}) does not exist; Alembic "
                "must not CREATE ROLE"
            )

    for candidate_key in ("event_candidate", "workflow_candidate"):
        role = roles[candidate_key]
        row = bind.execute(
            text(
                """
                SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole,
                       rolreplication, rolbypassrls
                FROM pg_roles
                WHERE rolname = :role
                """
            ),
            {"role": role},
        ).one()
        if row.rolcanlogin:
            raise RuntimeError(f"candidate-reader {role} must be NOLOGIN")
        if row.rolsuper:
            raise RuntimeError(f"candidate-reader {role} must be NOSUPERUSER")
        if row.rolcreatedb:
            raise RuntimeError(f"candidate-reader {role} must be NOCREATEDB")
        if row.rolcreaterole:
            raise RuntimeError(f"candidate-reader {role} must be NOCREATEROLE")
        if row.rolreplication:
            raise RuntimeError(f"candidate-reader {role} must be NOREPLICATION")
        if row.rolbypassrls:
            raise RuntimeError(f"candidate-reader {role} must be NOBYPASSRLS")

        schema_owner_count = bind.execute(
            text(
                """
                SELECT COUNT(*)
                FROM pg_namespace n
                JOIN pg_roles r ON r.oid = n.nspowner
                WHERE r.rolname = :role
                  AND n.nspname IN (
                      'integration', 'workflow', 'content', 'api',
                      'security', 'asset'
                  )
                """
            ),
            {"role": role},
        ).scalar_one()
        if schema_owner_count != 0:
            raise RuntimeError(f"candidate-reader {role} must not own schemas")

        table_owner_count = bind.execute(
            text(
                """
                SELECT COUNT(*)
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                JOIN pg_roles r ON r.oid = c.relowner
                WHERE r.rolname = :role
                  AND c.relkind IN ('r', 'p')
                  AND (
                      (n.nspname = 'integration' AND c.relname = 'outbox_messages')
                      OR (
                          n.nspname = 'workflow'
                          AND c.relname IN (
                              'workflow_start_intents',
                              'workflow_command_intents'
                          )
                      )
                  )
                """
            ),
            {"role": role},
        ).scalar_one()
        if table_owner_count != 0:
            raise RuntimeError(f"candidate-reader {role} must not own queue tables")

        outbound = bind.execute(
            text(
                """
                SELECT COUNT(*)
                FROM pg_auth_members am
                JOIN pg_roles member ON member.oid = am.member
                WHERE member.rolname = :role
                """
            ),
            {"role": role},
        ).scalar_one()
        if outbound != 0:
            raise RuntimeError(
                f"candidate-reader {role} must not be a member of any other role"
            )

    # Direct JIT edge required: pg_has_role(..., 'SET') alone is insufficient.
    for candidate_key in ("event_candidate", "workflow_candidate"):
        role = roles[candidate_key]
        edges = bind.execute(
            text(
                """
                SELECT am.admin_option, am.inherit_option, am.set_option
                FROM pg_auth_members am
                JOIN pg_roles granted ON granted.oid = am.roleid
                JOIN pg_roles member ON member.oid = am.member
                WHERE granted.rolname = :role
                  AND member.oid = (
                      SELECT oid FROM pg_roles WHERE rolname = session_user
                  )
                """
            ),
            {"role": role},
        ).fetchall()
        if len(edges) == 0:
            raise RuntimeError(
                f"migrator session_user lacks direct pg_auth_members JIT edge "
                f"to candidate-reader {role}; Infrastructure JIT grant is "
                "required and Alembic must not GRANT candidate-reader membership"
            )
        if len(edges) != 1:
            raise RuntimeError(
                f"ambiguous direct pg_auth_members JIT state for "
                f"session_user -> {role}: {len(edges)} rows"
            )
        edge = edges[0]
        if (
            edge.admin_option is not False
            or edge.inherit_option is not False
            or edge.set_option is not True
        ):
            raise RuntimeError(
                f"direct JIT edge session_user -> {role} must be "
                "ADMIN false / INHERIT false / SET true; "
                f"got admin={edge.admin_option} inherit={edge.inherit_option} "
                f"set={edge.set_option}"
            )

    # Application runtime/dispatcher identities must not be candidate members.
    forbidden_members = (
        roles["runtime"],
        roles["migration_runtime"],
        roles["event_dispatcher"],
        roles["workflow_dispatcher"],
    )
    for candidate_key in ("event_candidate", "workflow_candidate"):
        role = roles[candidate_key]
        for member_name in forbidden_members:
            inbound = bind.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM pg_auth_members am
                    JOIN pg_roles granted ON granted.oid = am.roleid
                    JOIN pg_roles member ON member.oid = am.member
                    WHERE granted.rolname = :role
                      AND member.rolname = :member
                    """
                ),
                {"role": role, "member": member_name},
            ).scalar_one()
            if inbound != 0:
                raise RuntimeError(
                    f"forbidden inbound candidate membership: {member_name} "
                    f"is a member of {role}"
                )

    _assert_no_preexisting_candidate_privileges(roles)


_EVENT_FORBIDDEN_SELECT_COLUMNS = (
    "envelope",
    "event_type",
    "subject",
    "aggregate_type",
    "aggregate_id",
    "aggregate_revision",
    "attempt_count",
    "claimed_by",
    "published_at",
    "broker_stream",
    "broker_sequence",
    "last_error_code",
    "created_at",
)

_WORKFLOW_START_FORBIDDEN_SELECT_COLUMNS = (
    "input",
    "workflow_start_intent_id",
    "workflow_instance_id",
    "workflow_type",
    "workflow_major_version",
    "temporal_workflow_id",
    "task_queue",
    "business_key",
    "attempt_count",
    "claimed_by",
    "delivered_at",
    "last_error_code",
    "created_at",
)

_WORKFLOW_COMMAND_FORBIDDEN_SELECT_COLUMNS = (
    "payload",
    "workflow_command_intent_id",
    "workflow_instance_id",
    "temporal_workflow_id",
    "command_id",
    "command_type",
    "business_key",
    "attempt_count",
    "claimed_by",
    "delivered_at",
    "last_error_code",
    "created_at",
)

_TABLE_PRIVILEGES = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)


def _assert_no_preexisting_candidate_privileges(roles: dict[str, str]) -> None:
    """Fail closed on unexpected candidate-reader queue/schema authority."""
    bind = op.get_bind()
    targets: tuple[tuple[str, str, tuple[str, ...]], ...] = (
        (
            roles["event_candidate"],
            "integration.outbox_messages",
            _EVENT_FORBIDDEN_SELECT_COLUMNS,
        ),
        (
            roles["workflow_candidate"],
            "workflow.workflow_start_intents",
            _WORKFLOW_START_FORBIDDEN_SELECT_COLUMNS,
        ),
        (
            roles["workflow_candidate"],
            "workflow.workflow_command_intents",
            _WORKFLOW_COMMAND_FORBIDDEN_SELECT_COLUMNS,
        ),
    )
    for role, table, forbidden_cols in targets:
        for priv in _TABLE_PRIVILEGES:
            has_priv = bind.execute(
                text(
                    "SELECT has_table_privilege(:role, :table, :priv)"
                ),
                {"role": role, "table": table, "priv": priv},
            ).scalar_one()
            if has_priv:
                raise RuntimeError(
                    f"candidate-reader {role} already has unexpected "
                    f"table privilege {priv} on {table}"
                )
        for col in _CANDIDATE_COLUMNS:
            has_col = bind.execute(
                text(
                    "SELECT has_column_privilege(:role, :table, :col, 'SELECT')"
                ),
                {"role": role, "table": table, "col": col},
            ).scalar_one()
            if has_col:
                raise RuntimeError(
                    f"candidate-reader {role} already has unexpected "
                    f"column SELECT on {table}.{col}"
                )
        for col in forbidden_cols:
            has_col = bind.execute(
                text(
                    "SELECT has_column_privilege(:role, :table, :col, 'SELECT')"
                ),
                {"role": role, "table": table, "col": col},
            ).scalar_one()
            if has_col:
                raise RuntimeError(
                    f"candidate-reader {role} already has forbidden "
                    f"column SELECT on {table}.{col}"
                )

    for role_key, schema in (
        ("event_candidate", "integration"),
        ("workflow_candidate", "workflow"),
    ):
        role = roles[role_key]
        has_create = bind.execute(
            text("SELECT has_schema_privilege(:role, :schema, 'CREATE')"),
            {"role": role, "schema": schema},
        ).scalar_one()
        if has_create:
            raise RuntimeError(
                f"candidate-reader {role} already has CREATE on schema {schema}"
            )


def _preflight_role_transition(roles: dict[str, str]) -> None:
    """JIT/role checks for SET LOCAL ROLE choreography (upgrade or downgrade).

    Does not assert absence of candidate grants — those exist after upgrade and
    are removed by downgrade itself.
    """
    bind = op.get_bind()
    for key, role in roles.items():
        exists = bind.execute(
            text("SELECT COUNT(*) FROM pg_roles WHERE rolname = :role"),
            {"role": role},
        ).scalar_one()
        if exists != 1:
            raise RuntimeError(
                f"required role '{role}' ({key}) does not exist; Alembic "
                "must not CREATE ROLE"
            )

    for candidate_key in ("event_candidate", "workflow_candidate"):
        role = roles[candidate_key]
        row = bind.execute(
            text(
                """
                SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole,
                       rolreplication, rolbypassrls
                FROM pg_roles
                WHERE rolname = :role
                """
            ),
            {"role": role},
        ).one()
        if row.rolcanlogin:
            raise RuntimeError(f"candidate-reader {role} must be NOLOGIN")
        if row.rolsuper:
            raise RuntimeError(f"candidate-reader {role} must be NOSUPERUSER")
        if row.rolcreatedb:
            raise RuntimeError(f"candidate-reader {role} must be NOCREATEDB")
        if row.rolcreaterole:
            raise RuntimeError(f"candidate-reader {role} must be NOCREATEROLE")
        if row.rolreplication:
            raise RuntimeError(f"candidate-reader {role} must be NOREPLICATION")
        if row.rolbypassrls:
            raise RuntimeError(f"candidate-reader {role} must be NOBYPASSRLS")

        outbound = bind.execute(
            text(
                """
                SELECT COUNT(*)
                FROM pg_auth_members am
                JOIN pg_roles member ON member.oid = am.member
                WHERE member.rolname = :role
                """
            ),
            {"role": role},
        ).scalar_one()
        if outbound != 0:
            raise RuntimeError(
                f"candidate-reader {role} must not be a member of any other role"
            )

    for candidate_key in ("event_candidate", "workflow_candidate"):
        role = roles[candidate_key]
        edges = bind.execute(
            text(
                """
                SELECT am.admin_option, am.inherit_option, am.set_option
                FROM pg_auth_members am
                JOIN pg_roles granted ON granted.oid = am.roleid
                JOIN pg_roles member ON member.oid = am.member
                WHERE granted.rolname = :role
                  AND member.oid = (
                      SELECT oid FROM pg_roles WHERE rolname = session_user
                  )
                """
            ),
            {"role": role},
        ).fetchall()
        if len(edges) == 0:
            raise RuntimeError(
                f"migrator session_user lacks direct pg_auth_members JIT edge "
                f"to candidate-reader {role}; Infrastructure JIT grant is "
                "required and Alembic must not GRANT candidate-reader membership"
            )
        if len(edges) != 1:
            raise RuntimeError(
                f"ambiguous direct pg_auth_members JIT state for "
                f"session_user -> {role}: {len(edges)} rows"
            )
        edge = edges[0]
        if (
            edge.admin_option is not False
            or edge.inherit_option is not False
            or edge.set_option is not True
        ):
            raise RuntimeError(
                f"direct JIT edge session_user -> {role} must be "
                "ADMIN false / INHERIT false / SET true; "
                f"got admin={edge.admin_option} inherit={edge.inherit_option} "
                f"set={edge.set_option}"
            )

    forbidden_members = (
        roles["runtime"],
        roles["migration_runtime"],
        roles["event_dispatcher"],
        roles["workflow_dispatcher"],
    )
    for candidate_key in ("event_candidate", "workflow_candidate"):
        role = roles[candidate_key]
        for member_name in forbidden_members:
            inbound = bind.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM pg_auth_members am
                    JOIN pg_roles granted ON granted.oid = am.roleid
                    JOIN pg_roles member ON member.oid = am.member
                    WHERE granted.rolname = :role
                      AND member.rolname = :member
                    """
                ),
                {"role": role, "member": member_name},
            ).scalar_one()
            if inbound != 0:
                raise RuntimeError(
                    f"forbidden inbound candidate membership: {member_name} "
                    f"is a member of {role}"
                )


def _candidate_select_list() -> str:
    return ", ".join(_CANDIDATE_COLUMNS)


def _event_function_body() -> str:
    return """
CREATE FUNCTION integration.list_outbox_dispatch_candidates(
    p_limit integer DEFAULT 100,
    p_as_of timestamptz DEFAULT statement_timestamp()
)
RETURNS TABLE (tenant_id uuid, eligible_at timestamptz)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path TO pg_catalog, pg_temp
AS $fn$
BEGIN
    IF p_limit IS NULL OR p_limit < 1 OR p_limit > 1000 THEN
        RAISE EXCEPTION
            'list_outbox_dispatch_candidates: p_limit must satisfy 1 <= p_limit <= 1000';
    END IF;
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'list_outbox_dispatch_candidates: p_as_of must not be null';
    END IF;

    RETURN QUERY
    SELECT q.tenant_id, MIN(q.eligible_at) AS eligible_at
    FROM (
        SELECT m.tenant_id, m.available_at AS eligible_at
        FROM integration.outbox_messages AS m
        WHERE m.status = 'PENDING'
          AND m.available_at <= p_as_of
        UNION ALL
        SELECT m.tenant_id, m.claimed_until AS eligible_at
        FROM integration.outbox_messages AS m
        WHERE m.status = 'CLAIMED'
          AND m.claimed_until IS NOT NULL
          AND m.claimed_until <= p_as_of
    ) AS q
    GROUP BY q.tenant_id
    ORDER BY MIN(q.eligible_at) ASC, q.tenant_id ASC
    LIMIT p_limit;
END;
$fn$
"""


def _workflow_function_body(*, schema_fn: str, table: str, name: str) -> str:
    return f"""
CREATE FUNCTION {schema_fn}(
    p_limit integer DEFAULT 100,
    p_as_of timestamptz DEFAULT statement_timestamp()
)
RETURNS TABLE (tenant_id uuid, eligible_at timestamptz)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path TO pg_catalog, pg_temp
AS $fn$
BEGIN
    IF p_limit IS NULL OR p_limit < 1 OR p_limit > 1000 THEN
        RAISE EXCEPTION
            '{name}: p_limit must satisfy 1 <= p_limit <= 1000';
    END IF;
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION '{name}: p_as_of must not be null';
    END IF;

    RETURN QUERY
    SELECT q.tenant_id, MIN(q.eligible_at) AS eligible_at
    FROM (
        SELECT m.tenant_id, m.available_at AS eligible_at
        FROM {table} AS m
        WHERE m.status = 'PENDING'
          AND m.available_at <= p_as_of
        UNION ALL
        SELECT m.tenant_id, m.claimed_until AS eligible_at
        FROM {table} AS m
        WHERE m.status = 'CLAIMED'
          AND m.claimed_until IS NOT NULL
          AND m.claimed_until <= p_as_of
    ) AS q
    GROUP BY q.tenant_id
    ORDER BY MIN(q.eligible_at) ASC, q.tenant_id ASC
    LIMIT p_limit;
END;
$fn$
"""


def _create_owned_function(
    *,
    content_owner: str,
    candidate_role: str,
    schema: str,
    create_sql: str,
    function_reg: str,
    execute_role: str,
) -> None:
    op.execute(f"GRANT CREATE ON SCHEMA {schema} TO {candidate_role}")
    op.execute(f"SET LOCAL ROLE {candidate_role}")
    op.execute(create_sql)
    op.execute(f"REVOKE ALL ON FUNCTION {function_reg} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {function_reg} TO {execute_role}")
    op.execute(f"SET LOCAL ROLE {content_owner}")
    op.execute(f"REVOKE CREATE ON SCHEMA {schema} FROM {candidate_role}")


def _drop_owned_function(
    *,
    content_owner: str,
    candidate_role: str,
    function_reg: str,
) -> None:
    op.execute(f"SET LOCAL ROLE {candidate_role}")
    op.execute(f"REVOKE ALL ON FUNCTION {function_reg} FROM PUBLIC")
    op.execute(f"DROP FUNCTION IF EXISTS {function_reg}")
    op.execute(f"SET LOCAL ROLE {content_owner}")


def upgrade() -> None:
    roles = _load_roles()
    _assert_distinct(roles)
    # Live preflight requires catalogue queries; offline SQL emit still validates
    # role env inputs and emits the same DDL with explicit role transitions.
    if not context.is_offline_mode():
        _preflight(roles)

    content = roles["content_owner"]
    runtime = roles["runtime"]
    migration_runtime = roles["migration_runtime"]
    event_dispatcher = roles["event_dispatcher"]
    workflow_dispatcher = roles["workflow_dispatcher"]
    event_candidate = roles["event_candidate"]
    workflow_candidate = roles["workflow_candidate"]
    cols = _candidate_select_list()

    # --- Replace universal outbox policy ---
    op.execute("DROP POLICY IF EXISTS outbox_messages_tenant_isolation ON integration.outbox_messages")
    op.execute(
        f"""
        CREATE POLICY outbox_messages_owner_tenant_all
            ON integration.outbox_messages
            FOR ALL
            TO {content}
            USING (tenant_id = integration.current_tenant_id())
            WITH CHECK (tenant_id = integration.current_tenant_id())
        """
    )
    op.execute(
        f"""
        CREATE POLICY outbox_messages_runtime_insert
            ON integration.outbox_messages
            FOR INSERT
            TO {runtime}, {migration_runtime}
            WITH CHECK (tenant_id = integration.current_tenant_id())
        """
    )
    op.execute(
        f"""
        CREATE POLICY outbox_messages_event_dispatcher_select
            ON integration.outbox_messages
            FOR SELECT
            TO {event_dispatcher}
            USING (tenant_id = integration.current_tenant_id())
        """
    )
    op.execute(
        f"""
        CREATE POLICY outbox_messages_event_dispatcher_update
            ON integration.outbox_messages
            FOR UPDATE
            TO {event_dispatcher}
            USING (tenant_id = integration.current_tenant_id())
            WITH CHECK (tenant_id = integration.current_tenant_id())
        """
    )
    op.execute(
        f"""
        CREATE POLICY outbox_messages_event_candidate_reader_select
            ON integration.outbox_messages
            FOR SELECT
            TO {event_candidate}
            USING (status IN ('PENDING', 'CLAIMED'))
        """
    )

    # --- Replace universal workflow policies (both tables) ---
    for table, prefix in (
        ("workflow.workflow_start_intents", "workflow_start_intents"),
        ("workflow.workflow_command_intents", "workflow_command_intents"),
    ):
        op.execute(f"DROP POLICY IF EXISTS {prefix}_tenant_isolation ON {table}")
        op.execute(
            f"""
            CREATE POLICY {prefix}_owner_tenant_all
                ON {table}
                FOR ALL
                TO {content}
                USING (tenant_id = workflow.current_tenant_id())
                WITH CHECK (tenant_id = workflow.current_tenant_id())
            """
        )
        op.execute(
            f"""
            CREATE POLICY {prefix}_runtime_select
                ON {table}
                FOR SELECT
                TO {runtime}
                USING (tenant_id = workflow.current_tenant_id())
            """
        )
        op.execute(
            f"""
            CREATE POLICY {prefix}_runtime_insert
                ON {table}
                FOR INSERT
                TO {runtime}
                WITH CHECK (tenant_id = workflow.current_tenant_id())
            """
        )
        op.execute(
            f"""
            CREATE POLICY {prefix}_workflow_dispatcher_select
                ON {table}
                FOR SELECT
                TO {workflow_dispatcher}
                USING (tenant_id = workflow.current_tenant_id())
            """
        )
        op.execute(
            f"""
            CREATE POLICY {prefix}_workflow_dispatcher_update
                ON {table}
                FOR UPDATE
                TO {workflow_dispatcher}
                USING (tenant_id = workflow.current_tenant_id())
                WITH CHECK (tenant_id = workflow.current_tenant_id())
            """
        )
        op.execute(
            f"""
            CREATE POLICY {prefix}_workflow_candidate_reader_select
                ON {table}
                FOR SELECT
                TO {workflow_candidate}
                USING (status IN ('PENDING', 'CLAIMED'))
            """
        )

    # --- Candidate-reader application grants ---
    op.execute(f"GRANT USAGE ON SCHEMA integration TO {event_candidate}")
    op.execute(
        f"GRANT SELECT ({cols}) ON integration.outbox_messages TO {event_candidate}"
    )
    op.execute(f"GRANT USAGE ON SCHEMA workflow TO {workflow_candidate}")
    op.execute(
        f"GRANT SELECT ({cols}) ON workflow.workflow_start_intents "
        f"TO {workflow_candidate}"
    )
    op.execute(
        f"GRANT SELECT ({cols}) ON workflow.workflow_command_intents "
        f"TO {workflow_candidate}"
    )

    # --- EVENT candidate indexes (preserve existing dispatch index) ---
    op.execute(
        f"""
        CREATE INDEX {_IX_PENDING}
            ON integration.outbox_messages (available_at, tenant_id)
            WHERE status = 'PENDING'
        """
    )
    op.execute(
        f"""
        CREATE INDEX {_IX_CLAIMED}
            ON integration.outbox_messages (claimed_until, tenant_id)
            WHERE status = 'CLAIMED' AND claimed_until IS NOT NULL
        """
    )

    # --- SECURITY DEFINER functions owned by candidate-readers ---
    _create_owned_function(
        content_owner=content,
        candidate_role=event_candidate,
        schema="integration",
        create_sql=_event_function_body(),
        function_reg=_EVENT_FN,
        execute_role=event_dispatcher,
    )
    _create_owned_function(
        content_owner=content,
        candidate_role=workflow_candidate,
        schema="workflow",
        create_sql=_workflow_function_body(
            schema_fn="workflow.list_start_intent_candidates",
            table="workflow.workflow_start_intents",
            name="list_start_intent_candidates",
        ),
        function_reg=_START_FN,
        execute_role=workflow_dispatcher,
    )
    _create_owned_function(
        content_owner=content,
        candidate_role=workflow_candidate,
        schema="workflow",
        create_sql=_workflow_function_body(
            schema_fn="workflow.list_command_intent_candidates",
            table="workflow.workflow_command_intents",
            name="list_command_intent_candidates",
        ),
        function_reg=_COMMAND_FN,
        execute_role=workflow_dispatcher,
    )


def downgrade() -> None:
    roles = _load_roles()
    _assert_distinct(roles)
    # Downgrade needs JIT SET ROLE, but must not require absence of candidate
    # grants that this revision itself installed.
    if not context.is_offline_mode():
        _preflight_role_transition(roles)

    content = roles["content_owner"]
    event_candidate = roles["event_candidate"]
    workflow_candidate = roles["workflow_candidate"]
    cols = _candidate_select_list()

    _drop_owned_function(
        content_owner=content,
        candidate_role=event_candidate,
        function_reg=_EVENT_FN,
    )
    _drop_owned_function(
        content_owner=content,
        candidate_role=workflow_candidate,
        function_reg=_START_FN,
    )
    _drop_owned_function(
        content_owner=content,
        candidate_role=workflow_candidate,
        function_reg=_COMMAND_FN,
    )

    op.execute(
        f"REVOKE SELECT ({cols}) ON integration.outbox_messages FROM {event_candidate}"
    )
    op.execute(f"REVOKE USAGE ON SCHEMA integration FROM {event_candidate}")
    op.execute(
        f"REVOKE SELECT ({cols}) ON workflow.workflow_start_intents "
        f"FROM {workflow_candidate}"
    )
    op.execute(
        f"REVOKE SELECT ({cols}) ON workflow.workflow_command_intents "
        f"FROM {workflow_candidate}"
    )
    op.execute(f"REVOKE USAGE ON SCHEMA workflow FROM {workflow_candidate}")

    op.execute(
        "DROP POLICY IF EXISTS outbox_messages_event_candidate_reader_select "
        "ON integration.outbox_messages"
    )
    op.execute(
        "DROP POLICY IF EXISTS outbox_messages_event_dispatcher_update "
        "ON integration.outbox_messages"
    )
    op.execute(
        "DROP POLICY IF EXISTS outbox_messages_event_dispatcher_select "
        "ON integration.outbox_messages"
    )
    op.execute(
        "DROP POLICY IF EXISTS outbox_messages_runtime_insert "
        "ON integration.outbox_messages"
    )
    op.execute(
        "DROP POLICY IF EXISTS outbox_messages_owner_tenant_all "
        "ON integration.outbox_messages"
    )
    op.execute(
        """
        CREATE POLICY outbox_messages_tenant_isolation
            ON integration.outbox_messages
            FOR ALL
            USING (tenant_id = integration.current_tenant_id())
            WITH CHECK (tenant_id = integration.current_tenant_id())
        """
    )

    for table, prefix in (
        ("workflow.workflow_start_intents", "workflow_start_intents"),
        ("workflow.workflow_command_intents", "workflow_command_intents"),
    ):
        op.execute(
            f"DROP POLICY IF EXISTS {prefix}_workflow_candidate_reader_select ON {table}"
        )
        op.execute(
            f"DROP POLICY IF EXISTS {prefix}_workflow_dispatcher_update ON {table}"
        )
        op.execute(
            f"DROP POLICY IF EXISTS {prefix}_workflow_dispatcher_select ON {table}"
        )
        op.execute(f"DROP POLICY IF EXISTS {prefix}_runtime_insert ON {table}")
        op.execute(f"DROP POLICY IF EXISTS {prefix}_runtime_select ON {table}")
        op.execute(f"DROP POLICY IF EXISTS {prefix}_owner_tenant_all ON {table}")
        op.execute(
            f"""
            CREATE POLICY {prefix}_tenant_isolation
                ON {table}
                FOR ALL
                USING (tenant_id = workflow.current_tenant_id())
                WITH CHECK (tenant_id = workflow.current_tenant_id())
            """
        )

    op.execute(f"DROP INDEX IF EXISTS integration.{_IX_PENDING}")
    op.execute(f"DROP INDEX IF EXISTS integration.{_IX_CLAIMED}")
