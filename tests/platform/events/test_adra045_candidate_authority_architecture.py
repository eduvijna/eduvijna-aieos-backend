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
    assert "GRANT candidate-reader membership" in source  # documentary denial only
    assert "must not GRANT candidate-reader membership" in source


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
