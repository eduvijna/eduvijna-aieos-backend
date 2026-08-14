"""Shared helpers for GCI-I14 adversarial tests."""

from __future__ import annotations

import json
import uuid
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from tests.platform.workflows.helpers import (
    APPEND_BODY,
    CREATE_BODY,
    app_for,
    append_version,
    client_for,
    create_content,
    decide,
    generated_version,
    headers,
    in_review,
    submit_review,
)

LEAK_NEEDLES = (
    "sqlalchemy",
    "psycopg",
    "postgresql://",
    "postgresql+psycopg://",
    "SELECT ",
    "INSERT ",
    "Traceback",
    "password",
    "SECRET_VALIDATOR_BUG",
    "SECRET_ASSET_VALIDATOR_BUG",
    "SENSITIVE_TEST_COMMENT",
    "reason_code_should_not_leak",
)

__all__ = [
    "APPEND_BODY",
    "CREATE_BODY",
    "LEAK_NEEDLES",
    "app_for",
    "append_version",
    "assert_problem",
    "client_for",
    "create_content",
    "decide",
    "expect_dbapi",
    "generated_version",
    "headers",
    "in_review",
    "submit_review",
]


def assert_problem(response, *, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["code"] == code
    assert body["status"] == status
    blob = json.dumps(body)
    for needle in LEAK_NEEDLES:
        assert needle.lower() not in blob.lower(), needle
    return body


def expect_dbapi(conn, thunk, match: str | None = None) -> None:
    with pytest.raises(DBAPIError, match=match):
        with conn.begin_nested():
            thunk()


def content_row(bootstrap_engine: Engine, content_id: str | UUID):
    with bootstrap_engine.connect() as conn:
        return conn.execute(
            text(
                """
                SELECT stewardship_state, aggregate_revision, current_version_id,
                       published_version_id
                FROM content.contents WHERE content_id = :cid
                """
            ),
            {"cid": UUID(str(content_id))},
        ).one()


def decision_count(bootstrap_engine: Engine, content_id: str | UUID) -> int:
    with bootstrap_engine.connect() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT count(*) FROM content.review_decisions WHERE content_id = :cid"
                ),
                {"cid": UUID(str(content_id))},
            ).scalar_one()
        )


def publication_count(bootstrap_engine: Engine, content_id: str | UUID) -> int:
    with bootstrap_engine.connect() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT count(*) FROM content.publications WHERE content_id = :cid"
                ),
                {"cid": UUID(str(content_id))},
            ).scalar_one()
        )


def version_row(bootstrap_engine: Engine, version_id: str | UUID):
    with bootstrap_engine.connect() as conn:
        return conn.execute(
            text(
                """
                SELECT version_id, content_id, version_number, parent_version_id,
                       origin, payload, payload_sha256, schema_id, schema_version,
                       provenance
                FROM content.content_versions WHERE version_id = :vid
                """
            ),
            {"vid": UUID(str(version_id))},
        ).one()


def idempotency_count(bootstrap_engine: Engine, tenant_id: UUID) -> int:
    with bootstrap_engine.connect() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT count(*) FROM api.idempotency_records WHERE tenant_id = :tid"
                ),
                {"tid": tenant_id},
            ).scalar_one()
        )


def outbox_count_for_content(bootstrap_engine: Engine, content_id: str | UUID) -> int:
    with bootstrap_engine.connect() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT count(*) FROM integration.outbox_messages "
                    "WHERE aggregate_id = :cid"
                ),
                {"cid": str(content_id)},
            ).scalar_one()
        )


def new_idempotency_key(prefix: str = "i14") -> str:
    return f"{prefix}-{uuid.uuid7()}"


def client(runtime_engine: Engine, tenant_id: UUID, principal_id: UUID | None = None, **kw) -> TestClient:
    return client_for(runtime_engine, tenant_id, principal_id or uuid.uuid7(), **kw)
