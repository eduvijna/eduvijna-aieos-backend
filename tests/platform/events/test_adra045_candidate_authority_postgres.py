"""ADR-AIEOS-045 PostgreSQL 18 candidate-authority live proofs."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, ProgrammingError

from tests.conftest import (
    EVENT_CANDIDATE_READER_ROLE,
    EVENT_DISPATCHER_USER,
    WORKFLOW_CANDIDATE_READER_ROLE,
    WORKFLOW_DISPATCHER_USER,
)

pytestmark = pytest.mark.postgres_candidate_authority

_EVENT_FN = "integration.list_outbox_dispatch_candidates(integer, timestamptz)"
_START_FN = "workflow.list_start_intent_candidates(integer, timestamptz)"
_COMMAND_FN = "workflow.list_command_intent_candidates(integer, timestamptz)"
_CANDIDATE_COLS = ("tenant_id", "status", "available_at", "claimed_until")


def _fn_row(conn, regprocedure: str):
    return conn.execute(
        text(
            """
            SELECT p.proname,
                   pg_get_userbyid(p.proowner) AS owner,
                   p.prosecdef,
                   COALESCE(array_to_string(p.proconfig, ','), '') AS proconfig
            FROM pg_proc p
            WHERE p.oid = to_regprocedure(:reg)
            """
        ),
        {"reg": regprocedure},
    ).one()


def test_candidate_functions_exist_owned_prosecdef_search_path(
    bootstrap_engine: Engine,
) -> None:
    expected = {
        _EVENT_FN: EVENT_CANDIDATE_READER_ROLE,
        _START_FN: WORKFLOW_CANDIDATE_READER_ROLE,
        _COMMAND_FN: WORKFLOW_CANDIDATE_READER_ROLE,
    }
    with bootstrap_engine.connect() as conn:
        for reg, owner in expected.items():
            row = _fn_row(conn, reg)
            assert row.owner == owner
            assert row.prosecdef is True
            normalized = row.proconfig.replace(" ", "")
            assert "search_path=pg_catalog,pg_temp" in normalized


def test_event_dispatcher_can_call_event_function_workflow_denied(
    event_dispatcher_engine: Engine,
    workflow_dispatcher_engine: Engine,
) -> None:
    with event_dispatcher_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT * FROM integration.list_outbox_dispatch_candidates(10, now())")
        ).fetchall()
        assert isinstance(rows, list)

    with workflow_dispatcher_engine.connect() as conn:
        with pytest.raises((ProgrammingError, DBAPIError)):
            conn.execute(
                text(
                    "SELECT * FROM integration.list_outbox_dispatch_candidates(10, now())"
                )
            )


def test_workflow_dispatcher_can_call_workflow_functions_event_denied(
    event_dispatcher_engine: Engine,
    workflow_dispatcher_engine: Engine,
) -> None:
    with workflow_dispatcher_engine.connect() as conn:
        start_rows = conn.execute(
            text("SELECT * FROM workflow.list_start_intent_candidates(10, now())")
        ).fetchall()
        command_rows = conn.execute(
            text("SELECT * FROM workflow.list_command_intent_candidates(10, now())")
        ).fetchall()
        assert isinstance(start_rows, list)
        assert isinstance(command_rows, list)

    with event_dispatcher_engine.connect() as conn:
        with pytest.raises((ProgrammingError, DBAPIError)):
            conn.execute(
                text("SELECT * FROM workflow.list_start_intent_candidates(10, now())")
            )
        with pytest.raises((ProgrammingError, DBAPIError)):
            conn.execute(
                text(
                    "SELECT * FROM workflow.list_command_intent_candidates(10, now())"
                )
            )


def test_candidate_select_columns_only(bootstrap_engine: Engine) -> None:
    with bootstrap_engine.connect() as conn:
        with conn.begin():
            conn.execute(text(f"SET ROLE {EVENT_CANDIDATE_READER_ROLE}"))
            for col in _CANDIDATE_COLS:
                assert conn.execute(
                    text(
                        "SELECT has_column_privilege("
                        f"'{EVENT_CANDIDATE_READER_ROLE}', "
                        "'integration.outbox_messages', :col, 'SELECT')"
                    ),
                    {"col": col},
                ).scalar_one()
            assert not conn.execute(
                text(
                    "SELECT has_column_privilege("
                    f"'{EVENT_CANDIDATE_READER_ROLE}', "
                    "'integration.outbox_messages', 'envelope', 'SELECT')"
                )
            ).scalar_one()
            conn.execute(text("RESET ROLE"))

            conn.execute(text(f"SET ROLE {WORKFLOW_CANDIDATE_READER_ROLE}"))
            denied_cols = {
                "workflow.workflow_start_intents": "input",
                "workflow.workflow_command_intents": "payload",
            }
            for table, denied in denied_cols.items():
                for col in _CANDIDATE_COLS:
                    assert conn.execute(
                        text(
                            "SELECT has_column_privilege("
                            f"'{WORKFLOW_CANDIDATE_READER_ROLE}', "
                            f"'{table}', :col, 'SELECT')"
                        ),
                        {"col": col},
                    ).scalar_one()
                assert not conn.execute(
                    text(
                        "SELECT has_column_privilege("
                        f"'{WORKFLOW_CANDIDATE_READER_ROLE}', "
                        f"'{table}', :denied, 'SELECT')"
                    ),
                    {"denied": denied},
                ).scalar_one()
            conn.execute(text("RESET ROLE"))


def test_candidate_function_calls_do_not_mutate_queues(
    bootstrap_engine: Engine,
    event_dispatcher_engine: Engine,
    workflow_dispatcher_engine: Engine,
) -> None:
    with bootstrap_engine.connect() as conn:
        with conn.begin():
            conn.execute(text("SET LOCAL ROLE aieos_content_owner"))
            before_outbox = conn.execute(
                text("SELECT count(*) FROM integration.outbox_messages")
            ).scalar_one()
            before_start = conn.execute(
                text("SELECT count(*) FROM workflow.workflow_start_intents")
            ).scalar_one()
            before_command = conn.execute(
                text("SELECT count(*) FROM workflow.workflow_command_intents")
            ).scalar_one()

    with event_dispatcher_engine.connect() as conn:
        conn.execute(
            text("SELECT * FROM integration.list_outbox_dispatch_candidates(5, now())")
        ).fetchall()

    with workflow_dispatcher_engine.connect() as conn:
        conn.execute(
            text("SELECT * FROM workflow.list_start_intent_candidates(5, now())")
        ).fetchall()
        conn.execute(
            text("SELECT * FROM workflow.list_command_intent_candidates(5, now())")
        ).fetchall()

    with bootstrap_engine.connect() as conn:
        with conn.begin():
            conn.execute(text("SET LOCAL ROLE aieos_content_owner"))
            after_outbox = conn.execute(
                text("SELECT count(*) FROM integration.outbox_messages")
            ).scalar_one()
            after_start = conn.execute(
                text("SELECT count(*) FROM workflow.workflow_start_intents")
            ).scalar_one()
            after_command = conn.execute(
                text("SELECT count(*) FROM workflow.workflow_command_intents")
            ).scalar_one()
    assert after_outbox == before_outbox
    assert after_start == before_start
    assert after_command == before_command


def test_force_rls_still_enabled(bootstrap_engine: Engine) -> None:
    tables = (
        ("integration", "outbox_messages"),
        ("workflow", "workflow_start_intents"),
        ("workflow", "workflow_command_intents"),
    )
    with bootstrap_engine.connect() as conn:
        for schema, name in tables:
            row = conn.execute(
                text(
                    """
                    SELECT c.relrowsecurity, c.relforcerowsecurity
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = :schema AND c.relname = :name
                    """
                ),
                {"schema": schema, "name": name},
            ).one()
            assert row.relrowsecurity is True
            assert row.relforcerowsecurity is True


def test_event_candidate_indexes_exist(bootstrap_engine: Engine) -> None:
    with bootstrap_engine.connect() as conn:
        names = {
            row[0]
            for row in conn.execute(
                text(
                    """
                    SELECT c.relname
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'integration'
                      AND c.relkind = 'i'
                      AND c.relname IN (
                          'ix_outbox_messages_candidate_pending',
                          'ix_outbox_messages_candidate_claimed'
                      )
                    """
                )
            )
        }
        assert names == {
            "ix_outbox_messages_candidate_pending",
            "ix_outbox_messages_candidate_claimed",
        }
        workflow_new = conn.execute(
            text(
                """
                SELECT count(*)
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'workflow'
                  AND c.relkind = 'i'
                  AND c.relname LIKE '%candidate%'
                """
            )
        ).scalar_one()
        assert workflow_new == 0


def test_dispatcher_role_constants_match_fixture_identities() -> None:
    assert EVENT_DISPATCHER_USER == "aieos_event_dispatcher"
    assert WORKFLOW_DISPATCHER_USER == "aieos_workflow_dispatcher"
