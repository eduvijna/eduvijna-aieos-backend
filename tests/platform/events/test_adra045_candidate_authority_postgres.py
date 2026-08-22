"""ADR-AIEOS-045 PostgreSQL 18 candidate-authority live proofs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DBAPIError, ProgrammingError

from tests.conftest import (
    EVENT_CANDIDATE_READER_ROLE,
    EVENT_DISPATCHER_USER,
    WORKFLOW_CANDIDATE_READER_ROLE,
    WORKFLOW_DISPATCHER_USER,
)
from tests.dbutil import set_tenant

pytestmark = pytest.mark.postgres_candidate_authority

_EVENT_FN = "integration.list_outbox_dispatch_candidates(integer, timestamptz)"
_START_FN = "workflow.list_start_intent_candidates(integer, timestamptz)"
_COMMAND_FN = "workflow.list_command_intent_candidates(integer, timestamptz)"
_CANDIDATE_COLS = ("tenant_id", "status", "available_at", "claimed_until")
_RESULT_COLS = ("tenant_id", "eligible_at")

TENANT_A = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1")
TENANT_B = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2")
TENANT_C = uuid.UUID("cccccccc-cccc-4ccc-8ccc-ccccccccccc3")

EVENT_A_SECRET_PAYLOAD = "EVENT_A_SECRET_PAYLOAD"
WORKFLOW_START_A_SECRET_INPUT = "WORKFLOW_START_A_SECRET_INPUT"
WORKFLOW_COMMAND_A_SECRET_PAYLOAD = "WORKFLOW_COMMAND_A_SECRET_PAYLOAD"

_AS_OF = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
# Far-past eligible timestamps keep synthetic tenants first under shared CI DBs.
_T_ELIGIBLE_A = datetime(1990, 1, 1, 0, 0, 0, tzinfo=UTC)
_T_ELIGIBLE_A_CLAIM = datetime(1990, 1, 1, 1, 0, 0, tzinfo=UTC)
_T_ELIGIBLE_C = datetime(1990, 1, 2, 0, 0, 0, tzinfo=UTC)
_T_MINUS_2H = _AS_OF - timedelta(hours=2)
_T_PLUS_1H = _AS_OF + timedelta(hours=1)

_EVENT_DELIVERY_COLS = (
    "event_id",
    "status",
    "attempt_count",
    "available_at",
    "claimed_by",
    "claimed_until",
    "published_at",
    "broker_stream",
    "broker_sequence",
    "last_error_code",
)
_WORKFLOW_DELIVERY_COLS = (
    "status",
    "attempt_count",
    "available_at",
    "claimed_by",
    "claimed_until",
    "delivered_at",
    "last_error_code",
)


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


def _as_content_owner(conn: Connection) -> None:
    conn.execute(text("SET LOCAL ROLE aieos_content_owner"))


def _insert_outbox(
    conn: Connection,
    *,
    tenant_id: uuid.UUID,
    status: str,
    available_at: datetime,
    claimed_until: datetime | None = None,
    envelope_secret: str | None = None,
    event_id: uuid.UUID | None = None,
) -> uuid.UUID:
    eid = event_id or uuid.uuid7()
    aggregate_id = uuid.uuid7()
    secret = envelope_secret or "benign"
    set_tenant(conn, tenant_id)
    conn.execute(
        text(
            """
            INSERT INTO integration.outbox_messages (
                event_id, tenant_id, event_type, subject, aggregate_type,
                aggregate_id, aggregate_revision, envelope, status,
                attempt_count, available_at, claimed_by, claimed_until,
                published_at, broker_stream, broker_sequence, last_error_code,
                created_at
            ) VALUES (
                :event_id, :tenant_id, :event_type, :subject, 'content',
                :aggregate_id, 0,
                jsonb_build_object('secret', CAST(:secret AS text)),
                :status, 0, :available_at,
                CASE WHEN CAST(:status AS text) = 'CLAIMED' THEN 'claimant' ELSE NULL END,
                :claimed_until,
                CASE WHEN CAST(:status AS text) = 'PUBLISHED' THEN :available_at ELSE NULL END,
                NULL, NULL, NULL, :created_at
            )
            """
        ),
        {
            "event_id": eid,
            "tenant_id": tenant_id,
            "event_type": f"io.eduvijna.aieos.test.{eid}",
            "subject": f"content/{aggregate_id}",
            "aggregate_id": aggregate_id,
            "secret": secret,
            "status": status,
            "available_at": available_at,
            "claimed_until": claimed_until,
            "created_at": available_at,
        },
    )
    return eid


def _insert_start_intent(
    conn: Connection,
    *,
    tenant_id: uuid.UUID,
    status: str,
    available_at: datetime,
    claimed_until: datetime | None = None,
    input_secret: str | None = None,
    workflow_instance_id: uuid.UUID | None = None,
) -> uuid.UUID:
    intent_id = uuid.uuid7()
    wid = workflow_instance_id or uuid.uuid7()
    secret = input_secret or "benign"
    set_tenant(conn, tenant_id)
    conn.execute(
        text(
            """
            INSERT INTO workflow.workflow_start_intents (
                workflow_start_intent_id, tenant_id, workflow_instance_id,
                workflow_type, workflow_major_version, temporal_workflow_id,
                task_queue, business_key, input, status, attempt_count,
                available_at, claimed_by, claimed_until, delivered_at,
                last_error_code, created_at
            ) VALUES (
                :intent_id, :tenant_id, :wid,
                'ContentReviewWorkflowV1', 1, :temporal_id,
                'aieos.content.review', :business_key,
                jsonb_build_object('secret', CAST(:secret AS text)),
                :status, 0, :available_at,
                CASE WHEN CAST(:status AS text) = 'CLAIMED' THEN 'claimant' ELSE NULL END,
                :claimed_until,
                CASE WHEN CAST(:status AS text) = 'DELIVERED' THEN :available_at ELSE NULL END,
                NULL, :created_at
            )
            """
        ),
        {
            "intent_id": intent_id,
            "tenant_id": tenant_id,
            "wid": wid,
            "temporal_id": f"temporal-{wid}",
            "business_key": f"bk-start-{wid}",
            "secret": secret,
            "status": status,
            "available_at": available_at,
            "claimed_until": claimed_until,
            "created_at": available_at,
        },
    )
    return wid


def _insert_command_intent(
    conn: Connection,
    *,
    tenant_id: uuid.UUID,
    workflow_instance_id: uuid.UUID,
    status: str,
    available_at: datetime,
    claimed_until: datetime | None = None,
    payload_secret: str | None = None,
) -> uuid.UUID:
    command_intent_id = uuid.uuid7()
    command_id = uuid.uuid7()
    secret = payload_secret or "benign"
    set_tenant(conn, tenant_id)
    conn.execute(
        text(
            """
            INSERT INTO workflow.workflow_command_intents (
                workflow_command_intent_id, tenant_id, workflow_instance_id,
                temporal_workflow_id, command_id, command_type, business_key,
                payload, status, attempt_count, available_at, claimed_by,
                claimed_until, delivered_at, last_error_code, created_at
            ) VALUES (
                :command_intent_id, :tenant_id, :wid,
                :temporal_id, :command_id, 'SignalCommand', :business_key,
                jsonb_build_object('secret', CAST(:secret AS text)),
                :status, 0, :available_at,
                CASE WHEN CAST(:status AS text) = 'CLAIMED' THEN 'claimant' ELSE NULL END,
                :claimed_until,
                CASE WHEN CAST(:status AS text) = 'DELIVERED' THEN :available_at ELSE NULL END,
                NULL, :created_at
            )
            """
        ),
        {
            "command_intent_id": command_intent_id,
            "tenant_id": tenant_id,
            "wid": workflow_instance_id,
            "temporal_id": f"temporal-{workflow_instance_id}",
            "command_id": command_id,
            "business_key": f"bk-cmd-{command_id}",
            "secret": secret,
            "status": status,
            "available_at": available_at,
            "claimed_until": claimed_until,
            "created_at": available_at,
        },
    )
    return command_intent_id


def _seed_eligibility_matrix(conn: Connection) -> dict[str, list[uuid.UUID]]:
    """Populate EVENT / START / COMMAND rows across TENANT_A/B/C classes."""
    _as_content_owner(conn)
    event_ids: list[uuid.UUID] = []
    start_wids: list[uuid.UUID] = []
    command_ids: list[uuid.UUID] = []

    # (tenant, status, available_at, claimed_until)
    # Classes: eligible PENDING, future PENDING, expired CLAIMED, active CLAIMED,
    # terminal PUBLISHED/DELIVERED, QUARANTINED.
    matrix: list[tuple[uuid.UUID, str, datetime, datetime | None]] = [
        (TENANT_A, "PENDING", _T_ELIGIBLE_A, None),
        (TENANT_A, "PENDING", _T_PLUS_1H, None),
        (TENANT_A, "CLAIMED", _T_MINUS_2H, _T_ELIGIBLE_A_CLAIM),
        (TENANT_A, "CLAIMED", _T_MINUS_2H, _T_PLUS_1H),
        (TENANT_A, "PUBLISHED", _T_MINUS_2H, None),
        (TENANT_A, "QUARANTINED", _T_MINUS_2H, None),
        (TENANT_B, "PENDING", _T_PLUS_1H, None),
        (TENANT_B, "CLAIMED", _T_MINUS_2H, _T_PLUS_1H),
        (TENANT_B, "PUBLISHED", _T_MINUS_2H, None),
        (TENANT_B, "QUARANTINED", _T_MINUS_2H, None),
        (TENANT_C, "PENDING", _T_ELIGIBLE_C, None),
        (TENANT_C, "CLAIMED", _T_MINUS_2H, _T_PLUS_1H),
        (TENANT_C, "DELIVERED", _T_MINUS_2H, None),
        (TENANT_C, "QUARANTINED", _T_MINUS_2H, None),
    ]

    for tenant_id, status, available_at, claimed_until in matrix:
        outbox_status = "PUBLISHED" if status == "DELIVERED" else status
        secret = EVENT_A_SECRET_PAYLOAD if tenant_id == TENANT_A else None
        event_ids.append(
            _insert_outbox(
                conn,
                tenant_id=tenant_id,
                status=outbox_status,
                available_at=available_at,
                claimed_until=claimed_until,
                envelope_secret=secret,
            )
        )

    for tenant_id, status, available_at, claimed_until in matrix:
        wf_status = status if status != "PUBLISHED" else "DELIVERED"
        secret = WORKFLOW_START_A_SECRET_INPUT if tenant_id == TENANT_A else None
        start_wids.append(
            _insert_start_intent(
                conn,
                tenant_id=tenant_id,
                status=wf_status,
                available_at=available_at,
                claimed_until=claimed_until,
                input_secret=secret,
            )
        )

    # Companion start rows for command FK targets (DELIVERED, non-candidate).
    for tenant_id, status, available_at, claimed_until in matrix:
        companion_wid = uuid.uuid7()
        _insert_start_intent(
            conn,
            tenant_id=tenant_id,
            status="DELIVERED",
            available_at=available_at,
            workflow_instance_id=companion_wid,
        )
        wf_status = status if status != "PUBLISHED" else "DELIVERED"
        secret = (
            WORKFLOW_COMMAND_A_SECRET_PAYLOAD if tenant_id == TENANT_A else None
        )
        command_ids.append(
            _insert_command_intent(
                conn,
                tenant_id=tenant_id,
                workflow_instance_id=companion_wid,
                status=wf_status,
                available_at=available_at,
                claimed_until=claimed_until,
                payload_secret=secret,
            )
        )

    return {
        "event_ids": event_ids,
        "start_wids": start_wids,
        "command_ids": command_ids,
    }


def _cleanup_synthetic_tenants(conn: Connection) -> None:
    """TEST-ONLY cleanup. Outbox deletes are blocked by an immutability trigger."""
    _as_content_owner(conn)
    conn.execute(
        text(
            "ALTER TABLE integration.outbox_messages "
            "DISABLE TRIGGER outbox_messages_no_delete"
        )
    )
    try:
        for tenant_id in (TENANT_A, TENANT_B, TENANT_C):
            set_tenant(conn, tenant_id)
            conn.execute(
                text(
                    "DELETE FROM workflow.workflow_command_intents "
                    "WHERE tenant_id = :tenant"
                ),
                {"tenant": tenant_id},
            )
            conn.execute(
                text(
                    "DELETE FROM workflow.workflow_start_intents "
                    "WHERE tenant_id = :tenant"
                ),
                {"tenant": tenant_id},
            )
            conn.execute(
                text(
                    "DELETE FROM integration.outbox_messages WHERE tenant_id = :tenant"
                ),
                {"tenant": tenant_id},
            )
    finally:
        conn.execute(
            text(
                "ALTER TABLE integration.outbox_messages "
                "ENABLE TRIGGER outbox_messages_no_delete"
            )
        )


def _call_candidates(
    conn: Connection,
    schema_fn: str,
    *,
    p_limit: int = 100,
    p_as_of: datetime = _AS_OF,
) -> list[Any]:
    return conn.execute(
        text(f"SELECT * FROM {schema_fn}(:p_limit, :p_as_of)"),
        {"p_limit": p_limit, "p_as_of": p_as_of},
    ).fetchall()


def _assert_result_shape(rows: list[Any]) -> None:
    for row in rows:
        mapping = row._mapping
        assert tuple(mapping.keys()) == _RESULT_COLS
        blob = " ".join(str(v) for v in mapping.values())
        assert EVENT_A_SECRET_PAYLOAD not in blob
        assert WORKFLOW_START_A_SECRET_INPUT not in blob
        assert WORKFLOW_COMMAND_A_SECRET_PAYLOAD not in blob


def _snapshot_outbox(conn: Connection, event_ids: list[uuid.UUID]) -> list[tuple]:
    _as_content_owner(conn)
    rows: list[tuple] = []
    for tenant_id in (TENANT_A, TENANT_B, TENANT_C):
        set_tenant(conn, tenant_id)
        result = conn.execute(
            text(
                f"""
                SELECT {", ".join(_EVENT_DELIVERY_COLS)}
                FROM integration.outbox_messages
                WHERE event_id = ANY(:ids)
                ORDER BY event_id
                """
            ),
            {"ids": event_ids},
        ).fetchall()
        rows.extend(tuple(r) for r in result)
    return rows


def _snapshot_starts(conn: Connection, wids: list[uuid.UUID]) -> list[tuple]:
    _as_content_owner(conn)
    rows: list[tuple] = []
    for tenant_id in (TENANT_A, TENANT_B, TENANT_C):
        set_tenant(conn, tenant_id)
        result = conn.execute(
            text(
                f"""
                SELECT workflow_instance_id, {", ".join(_WORKFLOW_DELIVERY_COLS)}
                FROM workflow.workflow_start_intents
                WHERE workflow_instance_id = ANY(:ids)
                ORDER BY workflow_instance_id
                """
            ),
            {"ids": wids},
        ).fetchall()
        rows.extend(tuple(r) for r in result)
    return rows


def _snapshot_commands(
    conn: Connection, command_ids: list[uuid.UUID]
) -> list[tuple]:
    _as_content_owner(conn)
    rows: list[tuple] = []
    for tenant_id in (TENANT_A, TENANT_B, TENANT_C):
        set_tenant(conn, tenant_id)
        result = conn.execute(
            text(
                f"""
                SELECT workflow_command_intent_id,
                       {", ".join(_WORKFLOW_DELIVERY_COLS)}
                FROM workflow.workflow_command_intents
                WHERE workflow_command_intent_id = ANY(:ids)
                ORDER BY workflow_command_intent_id
                """
            ),
            {"ids": command_ids},
        ).fetchall()
        rows.extend(tuple(r) for r in result)
    return rows


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
    seeded: dict[str, list[uuid.UUID]] | None = None
    try:
        with bootstrap_engine.connect() as conn:
            with conn.begin():
                seeded = _seed_eligibility_matrix(conn)
                before_outbox = _snapshot_outbox(conn, seeded["event_ids"])
                before_start = _snapshot_starts(conn, seeded["start_wids"])
                before_command = _snapshot_commands(conn, seeded["command_ids"])

        with event_dispatcher_engine.connect() as conn:
            conn.execute(
                text(
                    "SELECT * FROM integration.list_outbox_dispatch_candidates("
                    ":p_limit, :p_as_of)"
                ),
                {"p_limit": 5, "p_as_of": _AS_OF},
            ).fetchall()

        with workflow_dispatcher_engine.connect() as conn:
            conn.execute(
                text(
                    "SELECT * FROM workflow.list_start_intent_candidates("
                    ":p_limit, :p_as_of)"
                ),
                {"p_limit": 5, "p_as_of": _AS_OF},
            ).fetchall()
            conn.execute(
                text(
                    "SELECT * FROM workflow.list_command_intent_candidates("
                    ":p_limit, :p_as_of)"
                ),
                {"p_limit": 5, "p_as_of": _AS_OF},
            ).fetchall()

        with bootstrap_engine.connect() as conn:
            with conn.begin():
                assert seeded is not None
                after_outbox = _snapshot_outbox(conn, seeded["event_ids"])
                after_start = _snapshot_starts(conn, seeded["start_wids"])
                after_command = _snapshot_commands(conn, seeded["command_ids"])
        assert after_outbox == before_outbox
        assert after_start == before_start
        assert after_command == before_command
    finally:
        if seeded is not None:
            with bootstrap_engine.connect() as conn:
                with conn.begin():
                    _cleanup_synthetic_tenants(conn)


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


def test_eligibility_matrix_min_order_limit_and_invalid_args(
    bootstrap_engine: Engine,
    event_dispatcher_engine: Engine,
    workflow_dispatcher_engine: Engine,
) -> None:
    seeded: dict[str, list[uuid.UUID]] | None = None
    try:
        with bootstrap_engine.connect() as conn:
            with conn.begin():
                seeded = _seed_eligibility_matrix(conn)

        # MIN(eligible): TENANT_A has PENDING@_T_ELIGIBLE_A and CLAIMED@_T_ELIGIBLE_A_CLAIM
        expected = [
            (TENANT_A, _T_ELIGIBLE_A),
            (TENANT_C, _T_ELIGIBLE_C),
        ]

        cases = (
            (
                event_dispatcher_engine,
                "integration.list_outbox_dispatch_candidates",
            ),
            (
                workflow_dispatcher_engine,
                "workflow.list_start_intent_candidates",
            ),
            (
                workflow_dispatcher_engine,
                "workflow.list_command_intent_candidates",
            ),
        )
        for engine, schema_fn in cases:
            with engine.connect() as conn:
                rows = _call_candidates(
                    conn, schema_fn, p_limit=1000, p_as_of=_AS_OF
                )
                _assert_result_shape(rows)
                got = [(r.tenant_id, r.eligible_at) for r in rows]
                got_synth = [g for g in got if g[0] in {TENANT_A, TENANT_B, TENANT_C}]
                assert got_synth == expected
                assert TENANT_B not in {g[0] for g in got_synth}
                # Ordering: eligible_at ASC, tenant_id ASC among returned rows.
                assert got == sorted(got, key=lambda t: (t[1], t[0]))

                limited = _call_candidates(
                    conn, schema_fn, p_limit=1, p_as_of=_AS_OF
                )
                assert len(limited) == 1
                assert limited[0].tenant_id == TENANT_A
                assert limited[0].eligible_at == _T_ELIGIBLE_A

                with pytest.raises((ProgrammingError, DBAPIError)):
                    conn.execute(
                        text(f"SELECT * FROM {schema_fn}(NULL, :p_as_of)"),
                        {"p_as_of": _AS_OF},
                    )
                with pytest.raises((ProgrammingError, DBAPIError)):
                    _call_candidates(conn, schema_fn, p_limit=0, p_as_of=_AS_OF)
                with pytest.raises((ProgrammingError, DBAPIError)):
                    _call_candidates(conn, schema_fn, p_limit=1001, p_as_of=_AS_OF)
                with pytest.raises((ProgrammingError, DBAPIError)):
                    conn.execute(
                        text(f"SELECT * FROM {schema_fn}(10, NULL)")
                    )
    finally:
        if seeded is not None:
            with bootstrap_engine.connect() as conn:
                with conn.begin():
                    _cleanup_synthetic_tenants(conn)


def test_payload_non_exposure_and_candidate_dml_denied(
    bootstrap_engine: Engine,
    event_dispatcher_engine: Engine,
    workflow_dispatcher_engine: Engine,
) -> None:
    seeded: dict[str, list[uuid.UUID]] | None = None
    try:
        with bootstrap_engine.connect() as conn:
            with conn.begin():
                seeded = _seed_eligibility_matrix(conn)

        with event_dispatcher_engine.connect() as conn:
            rows = _call_candidates(
                conn,
                "integration.list_outbox_dispatch_candidates",
                p_limit=1000,
                p_as_of=_AS_OF,
            )
            _assert_result_shape(rows)
            rendered = repr(rows)
            assert EVENT_A_SECRET_PAYLOAD not in rendered

        with workflow_dispatcher_engine.connect() as conn:
            for schema_fn, sentinel in (
                (
                    "workflow.list_start_intent_candidates",
                    WORKFLOW_START_A_SECRET_INPUT,
                ),
                (
                    "workflow.list_command_intent_candidates",
                    WORKFLOW_COMMAND_A_SECRET_PAYLOAD,
                ),
            ):
                rows = _call_candidates(
                    conn, schema_fn, p_limit=1000, p_as_of=_AS_OF
                )
                _assert_result_shape(rows)
                assert sentinel not in repr(rows)

        with bootstrap_engine.connect() as conn:
            with conn.begin():
                conn.execute(text(f"SET LOCAL ROLE {EVENT_CANDIDATE_READER_ROLE}"))
                with pytest.raises((ProgrammingError, DBAPIError)):
                    conn.execute(
                        text("SELECT envelope FROM integration.outbox_messages")
                    ).fetchall()
                with pytest.raises((ProgrammingError, DBAPIError)):
                    conn.execute(
                        text(
                            """
                            INSERT INTO integration.outbox_messages (
                                event_id, tenant_id, event_type, subject,
                                aggregate_type, aggregate_id, aggregate_revision,
                                envelope, status, attempt_count, available_at,
                                created_at
                            ) VALUES (
                                :id, :tenant, 't', 's', 'content', :agg, 0,
                                '{}'::jsonb, 'PENDING', 0, now(), now()
                            )
                            """
                        ),
                        {
                            "id": uuid.uuid7(),
                            "tenant": TENANT_A,
                            "agg": uuid.uuid7(),
                        },
                    )
                with pytest.raises((ProgrammingError, DBAPIError)):
                    conn.execute(
                        text(
                            "UPDATE integration.outbox_messages "
                            "SET status = 'CLAIMED'"
                        )
                    )
                with pytest.raises((ProgrammingError, DBAPIError)):
                    conn.execute(text("DELETE FROM integration.outbox_messages"))

        with bootstrap_engine.connect() as conn:
            with conn.begin():
                conn.execute(
                    text(f"SET LOCAL ROLE {WORKFLOW_CANDIDATE_READER_ROLE}")
                )
                with pytest.raises((ProgrammingError, DBAPIError)):
                    conn.execute(
                        text("SELECT input FROM workflow.workflow_start_intents")
                    ).fetchall()
                with pytest.raises((ProgrammingError, DBAPIError)):
                    conn.execute(
                        text("SELECT payload FROM workflow.workflow_command_intents")
                    ).fetchall()
                for table in (
                    "workflow.workflow_start_intents",
                    "workflow.workflow_command_intents",
                ):
                    with pytest.raises((ProgrammingError, DBAPIError)):
                        conn.execute(
                            text(f"UPDATE {table} SET status = 'CLAIMED'")
                        )
                    with pytest.raises((ProgrammingError, DBAPIError)):
                        conn.execute(text(f"DELETE FROM {table}"))
                with pytest.raises((ProgrammingError, DBAPIError)):
                    conn.execute(
                        text(
                            """
                            INSERT INTO workflow.workflow_start_intents (
                                workflow_start_intent_id, tenant_id,
                                workflow_instance_id, workflow_type,
                                workflow_major_version, temporal_workflow_id,
                                task_queue, business_key, input, status,
                                attempt_count, available_at, created_at
                            ) VALUES (
                                :id, :tenant, :wid, 'ContentReviewWorkflowV1', 1,
                                'x', 'aieos.content.review', 'bk', '{}'::jsonb,
                                'PENDING', 0, now(), now()
                            )
                            """
                        ),
                        {
                            "id": uuid.uuid7(),
                            "tenant": TENANT_A,
                            "wid": uuid.uuid7(),
                        },
                    )
                with pytest.raises((ProgrammingError, DBAPIError)):
                    conn.execute(
                        text(
                            """
                            INSERT INTO workflow.workflow_command_intents (
                                workflow_command_intent_id, tenant_id,
                                workflow_instance_id, temporal_workflow_id,
                                command_id, command_type, business_key, payload,
                                status, attempt_count, available_at, created_at
                            ) VALUES (
                                :id, :tenant, :wid, 'tw', :cid, 'SignalCommand',
                                'bk', '{}'::jsonb, 'PENDING', 0, now(), now()
                            )
                            """
                        ),
                        {
                            "id": uuid.uuid7(),
                            "tenant": TENANT_A,
                            "wid": uuid.uuid7(),
                            "cid": uuid.uuid7(),
                        },
                    )
    finally:
        if seeded is not None:
            with bootstrap_engine.connect() as conn:
                with conn.begin():
                    _cleanup_synthetic_tenants(conn)


def test_dispatcher_login_tenant_rls_fail_closed_and_local(
    bootstrap_engine: Engine,
    event_dispatcher_engine: Engine,
    workflow_dispatcher_engine: Engine,
) -> None:
    seeded: dict[str, list[uuid.UUID]] | None = None
    try:
        with bootstrap_engine.connect() as conn:
            with conn.begin():
                seeded = _seed_eligibility_matrix(conn)

        with event_dispatcher_engine.connect() as conn:
            with pytest.raises((ProgrammingError, DBAPIError)):
                conn.execute(
                    text("SELECT count(*) FROM integration.outbox_messages")
                ).scalar_one()

            with conn.begin():
                set_tenant(conn, TENANT_A)
                tenants = {
                    r[0]
                    for r in conn.execute(
                        text(
                            "SELECT DISTINCT tenant_id "
                            "FROM integration.outbox_messages"
                        )
                    )
                }
                assert tenants == {TENANT_A}
                assert TENANT_B not in tenants

            with pytest.raises((ProgrammingError, DBAPIError)):
                conn.execute(
                    text("SELECT count(*) FROM integration.outbox_messages")
                ).scalar_one()

        for table in (
            "workflow.workflow_start_intents",
            "workflow.workflow_command_intents",
        ):
            with workflow_dispatcher_engine.connect() as conn:
                with pytest.raises((ProgrammingError, DBAPIError)):
                    conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()

                with conn.begin():
                    set_tenant(conn, TENANT_A)
                    tenants = {
                        r[0]
                        for r in conn.execute(
                            text(f"SELECT DISTINCT tenant_id FROM {table}")
                        )
                    }
                    assert tenants == {TENANT_A}
                    assert TENANT_B not in tenants

                with pytest.raises((ProgrammingError, DBAPIError)):
                    conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
    finally:
        if seeded is not None:
            with bootstrap_engine.connect() as conn:
                with conn.begin():
                    _cleanup_synthetic_tenants(conn)


def test_search_path_attack_uses_schema_qualified_relations(
    bootstrap_engine: Engine,
    event_dispatcher_engine: Engine,
    workflow_dispatcher_engine: Engine,
) -> None:
    with bootstrap_engine.connect() as conn:
        for reg, owner in (
            (_EVENT_FN, EVENT_CANDIDATE_READER_ROLE),
            (_START_FN, WORKFLOW_CANDIDATE_READER_ROLE),
            (_COMMAND_FN, WORKFLOW_CANDIDATE_READER_ROLE),
        ):
            row = _fn_row(conn, reg)
            assert row.prosecdef is True
            assert row.owner == owner
            normalized = (row.proconfig or "").replace(" ", "")
            assert "search_path=pg_catalog,pg_temp" in normalized
            assert "integration" not in normalized
            assert "workflow" not in normalized

    with event_dispatcher_engine.connect() as conn:
        with conn.begin():
            conn.execute(
                text(
                    """
                    CREATE TEMP TABLE outbox_messages (
                        tenant_id uuid,
                        status text,
                        available_at timestamptz,
                        claimed_until timestamptz
                    )
                    """
                )
            )
            conn.execute(
                text(
                    "INSERT INTO outbox_messages VALUES "
                    "(:t, 'PENDING', now(), NULL)"
                ),
                {"t": uuid.UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")},
            )
            rows = conn.execute(
                text(
                    "SELECT * FROM integration.list_outbox_dispatch_candidates("
                    "10, :p_as_of)"
                ),
                {"p_as_of": _AS_OF},
            ).fetchall()
            assert all(
                r.tenant_id != uuid.UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")
                for r in rows
            )

    with workflow_dispatcher_engine.connect() as conn:
        with conn.begin():
            conn.execute(
                text(
                    """
                    CREATE TEMP TABLE workflow_start_intents (
                        tenant_id uuid,
                        status text,
                        available_at timestamptz,
                        claimed_until timestamptz
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE TEMP TABLE workflow_command_intents (
                        tenant_id uuid,
                        status text,
                        available_at timestamptz,
                        claimed_until timestamptz
                    )
                    """
                )
            )
            poison = uuid.UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
            conn.execute(
                text(
                    "INSERT INTO workflow_start_intents VALUES "
                    "(:t, 'PENDING', now(), NULL)"
                ),
                {"t": poison},
            )
            conn.execute(
                text(
                    "INSERT INTO workflow_command_intents VALUES "
                    "(:t, 'PENDING', now(), NULL)"
                ),
                {"t": poison},
            )
            for schema_fn in (
                "workflow.list_start_intent_candidates",
                "workflow.list_command_intent_candidates",
            ):
                rows = conn.execute(
                    text(f"SELECT * FROM {schema_fn}(10, :p_as_of)"),
                    {"p_as_of": _AS_OF},
                ).fetchall()
                assert all(r.tenant_id != poison for r in rows)


def test_event_candidate_indexes_used_under_volume(
    bootstrap_engine: Engine,
) -> None:
    bulk_tenant = uuid.UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
    try:
        with bootstrap_engine.connect() as conn:
            with conn.begin():
                _as_content_owner(conn)
                set_tenant(conn, bulk_tenant)
                # Selective distribution: vast majority non-candidate, sparse eligible.
                conn.execute(
                    text(
                        """
                        INSERT INTO integration.outbox_messages (
                            event_id, tenant_id, event_type, subject,
                            aggregate_type, aggregate_id, aggregate_revision,
                            envelope, status, attempt_count, available_at,
                            claimed_by, claimed_until, published_at,
                            broker_stream, broker_sequence, last_error_code,
                            created_at
                        )
                        SELECT
                            gen_random_uuid(),
                            :tenant,
                            'io.eduvijna.aieos.test.bulk.' || g::text,
                            'content/bulk-' || g::text,
                            'content',
                            gen_random_uuid(),
                            0,
                            '{}'::jsonb,
                            CASE
                                WHEN g <= 50 THEN 'PENDING'
                                WHEN g <= 100 THEN 'CLAIMED'
                                WHEN g <= 40000 THEN 'PUBLISHED'
                                WHEN g <= 70000 THEN 'QUARANTINED'
                                WHEN g <= 85000 THEN 'PENDING'
                                ELSE 'CLAIMED'
                            END,
                            0,
                            CASE
                                WHEN g <= 50 THEN :as_of - make_interval(mins => g)
                                WHEN g <= 100 THEN :as_of - interval '2 hours'
                                WHEN g <= 40000 THEN :as_of - interval '1 day'
                                WHEN g <= 70000 THEN :as_of - interval '1 day'
                                WHEN g <= 85000 THEN :as_of + interval '1 day'
                                ELSE :as_of - interval '2 hours'
                            END,
                            CASE
                                WHEN g <= 50 THEN NULL
                                WHEN g <= 100 THEN 'claimant'
                                WHEN g <= 70000 THEN NULL
                                WHEN g <= 85000 THEN NULL
                                ELSE 'claimant'
                            END,
                            CASE
                                WHEN g <= 50 THEN NULL
                                WHEN g <= 100 THEN :as_of - make_interval(mins => g)
                                WHEN g <= 70000 THEN NULL
                                WHEN g <= 85000 THEN NULL
                                ELSE :as_of + interval '1 day'
                            END,
                            CASE WHEN g > 100 AND g <= 40000
                                 THEN :as_of - interval '1 day' ELSE NULL END,
                            NULL,
                            NULL,
                            NULL,
                            :as_of
                        FROM generate_series(1, 100000) AS g
                        """
                    ),
                    {"tenant": bulk_tenant, "as_of": _AS_OF},
                )
                count = conn.execute(
                    text(
                        "SELECT count(*) FROM integration.outbox_messages "
                        "WHERE tenant_id = :tenant"
                    ),
                    {"tenant": bulk_tenant},
                ).scalar_one()
                assert int(count) >= 100000

                pending_plan = conn.execute(
                    text(
                        """
                        EXPLAIN (ANALYZE, BUFFERS)
                        SELECT m.tenant_id, m.available_at AS eligible_at
                        FROM integration.outbox_messages AS m
                        WHERE m.status = 'PENDING'
                          AND m.available_at <= :p_as_of
                        """
                    ),
                    {"p_as_of": _AS_OF},
                ).fetchall()
                pending_text = "\n".join(r[0] for r in pending_plan)
                print(pending_text)
                assert "ix_outbox_messages_candidate_pending" in pending_text

                claimed_plan = conn.execute(
                    text(
                        """
                        EXPLAIN (ANALYZE, BUFFERS)
                        SELECT m.tenant_id, m.claimed_until AS eligible_at
                        FROM integration.outbox_messages AS m
                        WHERE m.status = 'CLAIMED'
                          AND m.claimed_until IS NOT NULL
                          AND m.claimed_until <= :p_as_of
                        """
                    ),
                    {"p_as_of": _AS_OF},
                ).fetchall()
                claimed_text = "\n".join(r[0] for r in claimed_plan)
                print(claimed_text)
                assert "ix_outbox_messages_candidate_claimed" in claimed_text
    finally:
        with bootstrap_engine.connect() as conn:
            with conn.begin():
                _as_content_owner(conn)
                set_tenant(conn, bulk_tenant)
                conn.execute(
                    text(
                        "ALTER TABLE integration.outbox_messages "
                        "DISABLE TRIGGER outbox_messages_no_delete"
                    )
                )
                try:
                    conn.execute(
                        text(
                            "DELETE FROM integration.outbox_messages "
                            "WHERE tenant_id = :tenant"
                        ),
                        {"tenant": bulk_tenant},
                    )
                finally:
                    conn.execute(
                        text(
                            "ALTER TABLE integration.outbox_messages "
                            "ENABLE TRIGGER outbox_messages_no_delete"
                        )
                    )
