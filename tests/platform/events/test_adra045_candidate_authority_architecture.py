"""ADR-AIEOS-045 candidate-authority architecture proofs (no database required)."""

from __future__ import annotations

import importlib.util
import re
from types import ModuleType

import pytest

from tests.dbutil import REPO_ROOT

MIGRATION_PATH = (
    REPO_ROOT
    / "migrations"
    / "versions"
    / "adra045001_dispatcher_candidate_authority.py"
)

_ROLE_ENVS = (
    "AIEOS_SCHEMA_OWNER_ROLE",
    "AIEOS_SECURITY_SCHEMA_OWNER_ROLE",
    "AIEOS_RUNTIME_ROLE",
    "AIEOS_CONTENT_MIGRATION_RUNTIME_ROLE",
    "AIEOS_EVENT_DISPATCHER_ROLE",
    "AIEOS_WORKFLOW_DISPATCHER_ROLE",
    "AIEOS_EVENT_CANDIDATE_READER_ROLE",
    "AIEOS_WORKFLOW_CANDIDATE_READER_ROLE",
)

_VALID_ROLES = {
    "AIEOS_SCHEMA_OWNER_ROLE": "aieos_content_owner",
    "AIEOS_SECURITY_SCHEMA_OWNER_ROLE": "aieos_security_owner",
    "AIEOS_RUNTIME_ROLE": "aieos_runtime",
    "AIEOS_CONTENT_MIGRATION_RUNTIME_ROLE": "aieos_content_migration_runtime",
    "AIEOS_EVENT_DISPATCHER_ROLE": "aieos_event_dispatcher",
    "AIEOS_WORKFLOW_DISPATCHER_ROLE": "aieos_workflow_dispatcher",
    "AIEOS_EVENT_CANDIDATE_READER_ROLE": "aieos_event_candidate_reader",
    "AIEOS_WORKFLOW_CANDIDATE_READER_ROLE": "aieos_workflow_candidate_reader",
}


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("adra045001_mig", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migration_source() -> str:
    return MIGRATION_PATH.read_text(encoding="utf-8")


def _op_execute_sql(source: str) -> str:
    """Concatenate string payloads passed to op.execute (static source scan)."""
    parts: list[str] = []
    for match in re.finditer(
        r"op\.execute\(\s*(?:f)?(?:\"\"\"|'''|\"|')([\s\S]*?)(?:\"\"\"|'''|\"|')\s*\)",
        source,
    ):
        parts.append(match.group(1))
    return "\n".join(parts)


def _set_valid_roles(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _VALID_ROLES.items():
        monkeypatch.setenv(key, value)


def test_revision_chain_adra045001_revises_pedi10b6001() -> None:
    mig = _load_migration()
    assert mig.revision == "adra045001"
    assert mig.down_revision == "pedi10b6001"
    source = _migration_source()
    assert 'revision: str = "adra045001"' in source
    assert 'down_revision: str | None = "pedi10b6001"' in source


def test_missing_role_inputs_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    mig = _load_migration()
    _set_valid_roles(monkeypatch)
    for env_name in _ROLE_ENVS:
        monkeypatch.delenv(env_name, raising=False)
        with pytest.raises(RuntimeError, match=env_name):
            mig._load_roles()
        monkeypatch.setenv(env_name, _VALID_ROLES[env_name])


def test_invalid_role_identifier_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    mig = _load_migration()
    monkeypatch.setenv("AIEOS_EVENT_CANDIDATE_READER_ROLE", "Bad Role!")
    with pytest.raises(RuntimeError, match="lowercase unquoted"):
        mig._require_role(
            "AIEOS_EVENT_CANDIDATE_READER_ROLE",
            purpose="event candidate-reader role",
        )


def test_aliased_roles_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    mig = _load_migration()
    _set_valid_roles(monkeypatch)

    monkeypatch.setenv("AIEOS_EVENT_DISPATCHER_ROLE", "aieos_runtime")
    roles = mig._load_roles()
    with pytest.raises(RuntimeError, match="role distinctness failed"):
        mig._assert_distinct(roles)

    _set_valid_roles(monkeypatch)
    monkeypatch.setenv(
        "AIEOS_EVENT_CANDIDATE_READER_ROLE", "aieos_event_dispatcher"
    )
    roles = mig._load_roles()
    with pytest.raises(RuntimeError, match="aliases"):
        mig._assert_distinct(roles)

    _set_valid_roles(monkeypatch)
    monkeypatch.setenv(
        "AIEOS_WORKFLOW_CANDIDATE_READER_ROLE", "aieos_event_candidate_reader"
    )
    roles = mig._load_roles()
    with pytest.raises(RuntimeError, match="role distinctness failed"):
        mig._assert_distinct(roles)


def test_migration_never_creates_or_alters_roles_or_grants_candidate_to_migrator() -> None:
    source = _migration_source()
    sql = _op_execute_sql(source)
    upper = sql.upper()
    assert "CREATE ROLE" not in upper
    assert "ALTER ROLE" not in upper
    assert not re.search(
        r"GRANT\s+\{(?:event|workflow)_candidate\}\s+TO",
        source,
    )
    assert not re.search(
        r"GRANT\s+[a-z_]*candidate[a-z_]*\s+TO\s+",
        sql,
        flags=re.IGNORECASE,
    )
    assert "GRANT candidate-reader membership" in source  # documentary denial only
    assert "must not GRANT candidate-reader membership" in source
    assert "must not CREATE ROLE" in source
    assert "Alembic must not GRANT candidate-reader membership" in source


def _preflight_source_body(source: str) -> str:
    match = re.search(
        r"def _preflight\(roles: dict\[str, str\]\) -> None:([\s\S]*?)\n\n"
        r"_EVENT_FORBIDDEN_SELECT_COLUMNS",
        source,
    )
    assert match is not None
    return match.group(1)


def test_preflight_requires_direct_pg_auth_members_jit_not_pg_has_role_alone() -> None:
    """Effective JIT acceptance is exact pg_auth_members edge, not pg_has_role alone."""
    source = _migration_source()
    body = _preflight_source_body(source)
    assert "FROM pg_auth_members" in body
    assert "am.admin_option" in body
    assert "am.inherit_option" in body
    assert "am.set_option" in body
    assert "admin_option is not False" in body
    assert "inherit_option is not False" in body
    assert "set_option is not True" in body
    assert "direct pg_auth_members JIT edge" in body
    # Documentary denial may mention pg_has_role; it must not be the acceptance check.
    assert "pg_has_role(..., 'SET') alone is insufficient" in body
    without_denial = body.replace(
        "pg_has_role(..., 'SET') alone is insufficient", ""
    )
    assert "pg_has_role" not in without_denial


def test_preflight_checks_forbidden_runtime_dispatcher_candidate_memberships() -> None:
    source = _migration_source()
    body = _preflight_source_body(source)
    assert "forbidden inbound candidate membership" in body
    forbidden_block = re.search(
        r"forbidden_members = \(([\s\S]*?)\)\n"
        r"\s+for candidate_key in \(\"event_candidate\", \"workflow_candidate\"\)",
        body,
    )
    assert forbidden_block is not None
    members_blob = forbidden_block.group(1)
    for needle in (
        'roles["runtime"]',
        'roles["migration_runtime"]',
        'roles["event_dispatcher"]',
        'roles["workflow_dispatcher"]',
    ):
        assert needle in members_blob
    assert "FROM pg_auth_members am" in body
    assert "member.rolname = :member" in body


def test_preflight_fails_closed_on_preexisting_candidate_privileges() -> None:
    source = _migration_source()
    body = _preflight_source_body(source)
    assert "def _assert_no_preexisting_candidate_privileges" in source
    assert "_assert_no_preexisting_candidate_privileges(roles)" in body
    assert body.rstrip().endswith("_assert_no_preexisting_candidate_privileges(roles)")
    assert "already has unexpected" in source
    assert "already has forbidden" in source
    assert "has_table_privilege" in source
    assert "has_column_privilege" in source
    assert "has_schema_privilege" in source


def test_upgrade_drops_policies_only_after_preflight() -> None:
    source = _migration_source()
    upgrade = re.search(
        r"def upgrade\(\) -> None:([\s\S]*?)\ndef downgrade\(\) -> None:",
        source,
    )
    assert upgrade is not None
    body = upgrade.group(1)
    preflight_at = body.find("_preflight(roles)")
    first_drop_at = body.find("DROP POLICY")
    assert preflight_at != -1
    assert first_drop_at != -1
    assert preflight_at < first_drop_at
    assert "if not context.is_offline_mode():" in body
    assert body.index("if not context.is_offline_mode():") < preflight_at


def test_offline_sql_style_source_scan_mirrors_ci_expectations() -> None:
    """Mirror dedicated CI offline alembic --sql acceptance via source / op.execute scans."""
    source = _migration_source()
    sql = _op_execute_sql(source)
    mig = _load_migration()
    function_sql = "\n".join(
        [
            mig._event_function_body(),
            mig._workflow_function_body(
                schema_fn="workflow.list_start_intent_candidates",
                table="workflow.workflow_start_intents",
                name="list_start_intent_candidates",
            ),
            mig._workflow_function_body(
                schema_fn="workflow.list_command_intent_candidates",
                table="workflow.workflow_command_intents",
                name="list_command_intent_candidates",
            ),
        ]
    )
    emitted = f"{sql}\n{function_sql}"
    emitted_upper = emitted.upper()

    for name in (
        "list_outbox_dispatch_candidates",
        "list_start_intent_candidates",
        "list_command_intent_candidates",
    ):
        assert name in emitted
    assert "SECURITY DEFINER" in emitted
    assert "SET search_path TO pg_catalog, pg_temp" in emitted
    assert "SET LOCAL ROLE {candidate_role}" in source
    assert "SET LOCAL ROLE {content_owner}" in source
    assert "GRANT CREATE ON SCHEMA {schema} TO {candidate_role}" in source
    assert "REVOKE CREATE ON SCHEMA {schema} FROM {candidate_role}" in source
    assert "REVOKE ALL ON FUNCTION {function_reg} FROM PUBLIC" in source

    assert not re.search(r"CREATE\s+ROLE", emitted_upper)
    assert not re.search(r"ALTER\s+ROLE", emitted_upper)
    assert not re.search(
        r"GRANT\s+\{(?:event|workflow)_candidate\}\s+TO",
        source,
    )
    assert not re.search(
        r"GRANT\s+aieos_(?:event|workflow)_candidate_reader\s+TO",
        emitted,
        flags=re.IGNORECASE,
    )
    assert not re.search(
        r"password|digitalocean|doctl|api\.digitalocean",
        emitted,
        flags=re.IGNORECASE,
    )
    assert not re.search(
        r"postgresql(\+[a-z]+)?://[^\s]+:[^\s]+@",
        emitted,
        flags=re.IGNORECASE,
    )


def test_old_universal_policies_dropped_and_role_target_policies_created() -> None:
    source = _migration_source()
    assert "DROP POLICY IF EXISTS outbox_messages_tenant_isolation" in source
    assert "DROP POLICY IF EXISTS {prefix}_tenant_isolation" in source
    assert "outbox_messages_owner_tenant_all" in source
    assert "outbox_messages_runtime_insert" in source
    assert "outbox_messages_event_dispatcher_select" in source
    assert "outbox_messages_event_dispatcher_update" in source
    assert "outbox_messages_event_candidate_reader_select" in source
    assert "workflow_candidate_reader_select" in source
    assert "DISABLE ROW LEVEL SECURITY" not in source.upper()
    assert "NO FORCE ROW LEVEL SECURITY" not in source.upper()
    assert "FORCE ROW LEVEL SECURITY" not in _op_execute_sql(source).upper()


def test_candidate_policies_select_only_without_current_tenant_id() -> None:
    source = _migration_source()
    event_policy = re.search(
        r"CREATE POLICY outbox_messages_event_candidate_reader_select[\s\S]*?"
        r"(?=CREATE POLICY|op\.execute|\# ---)",
        source,
    )
    assert event_policy is not None
    event_body = event_policy.group(0)
    assert "FOR SELECT" in event_body
    assert "current_tenant_id" not in event_body
    assert "FOR ALL" not in event_body
    assert "FOR INSERT" not in event_body
    assert "FOR UPDATE" not in event_body

    workflow_policy = re.search(
        r"CREATE POLICY \{prefix\}_workflow_candidate_reader_select[\s\S]*?"
        r"(?=op\.execute|\# --- Candidate)",
        source,
    )
    assert workflow_policy is not None
    workflow_body = workflow_policy.group(0)
    assert "FOR SELECT" in workflow_body
    assert "current_tenant_id" not in workflow_body


def test_owner_runtime_dispatcher_policies_retain_current_tenant_id() -> None:
    source = _migration_source()
    for needle in (
        "outbox_messages_owner_tenant_all",
        "outbox_messages_runtime_insert",
        "outbox_messages_event_dispatcher_select",
        "outbox_messages_event_dispatcher_update",
        "{prefix}_owner_tenant_all",
        "{prefix}_runtime_select",
        "{prefix}_runtime_insert",
        "{prefix}_workflow_dispatcher_select",
        "{prefix}_workflow_dispatcher_update",
    ):
        assert needle in source
    assert source.count("integration.current_tenant_id()") >= 4
    assert source.count("workflow.current_tenant_id()") >= 8


def test_candidate_select_grants_four_columns_only_no_payload() -> None:
    source = _migration_source()
    mig = _load_migration()
    cols = mig._candidate_select_list()
    assert cols == "tenant_id, status, available_at, claimed_until"
    assert 'f"GRANT SELECT ({cols}) ON integration.outbox_messages TO {event_candidate}"' in source
    assert (
        'f"GRANT SELECT ({cols}) ON workflow.workflow_start_intents "'
        in source
    )
    assert (
        'f"GRANT SELECT ({cols}) ON workflow.workflow_command_intents "'
        in source
    )
    assert mig._CANDIDATE_COLUMNS == (
        "tenant_id",
        "status",
        "available_at",
        "claimed_until",
    )
    for forbidden in (
        "envelope",
        "payload",
        "event_type",
        "broker_stream",
        "input_payload",
        "command_payload",
        "input",
    ):
        assert forbidden not in mig._CANDIDATE_COLUMNS


def test_public_execute_revoked_matching_dispatcher_execute_only() -> None:
    source = _migration_source()
    assert "REVOKE ALL ON FUNCTION {function_reg} FROM PUBLIC" in source
    assert "GRANT EXECUTE ON FUNCTION {function_reg} TO {execute_role}" in source
    assert "execute_role=event_dispatcher" in source
    assert "execute_role=workflow_dispatcher" in source
    assert "list_outbox_dispatch_candidates" in source
    assert "list_start_intent_candidates" in source
    assert "list_command_intent_candidates" in source


def test_set_local_role_candidate_reader_choreography_and_search_path() -> None:
    source = _migration_source()
    assert "SET LOCAL ROLE {candidate_role}" in source
    assert "SET LOCAL ROLE {content_owner}" in source
    assert "SET search_path TO pg_catalog, pg_temp" in source
    event_body = _load_migration()._event_function_body()
    assert "SET search_path TO pg_catalog, pg_temp" in event_body
    assert "SECURITY DEFINER" in event_body


def test_no_dynamic_sql_in_candidate_functions() -> None:
    mig = _load_migration()
    bodies = [
        mig._event_function_body(),
        mig._workflow_function_body(
            schema_fn="workflow.list_start_intent_candidates",
            table="workflow.workflow_start_intents",
            name="list_start_intent_candidates",
        ),
        mig._workflow_function_body(
            schema_fn="workflow.list_command_intent_candidates",
            table="workflow.workflow_command_intents",
            name="list_command_intent_candidates",
        ),
    ]
    for body in bodies:
        upper = body.upper()
        assert "EXECUTE " not in upper
        assert "FORMAT(" not in upper
        assert "RETURN QUERY EXECUTE" not in upper


def test_exactly_two_event_candidate_indexes_zero_workflow_indexes() -> None:
    source = _migration_source()
    mig = _load_migration()
    assert mig._IX_PENDING == "ix_outbox_messages_candidate_pending"
    assert mig._IX_CLAIMED == "ix_outbox_messages_candidate_claimed"
    assert "ix_outbox_messages_candidate_pending" in source
    assert "ix_outbox_messages_candidate_claimed" in source
    assert source.count("CREATE INDEX {_IX_PENDING}") == 1
    assert source.count("CREATE INDEX {_IX_CLAIMED}") == 1
    assert source.count("CREATE INDEX") == 2
    assert not re.search(
        r"CREATE INDEX\s+[^\n]+\s+ON\s+workflow\.",
        source,
        flags=re.IGNORECASE,
    )
