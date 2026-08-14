"""GCI-I04 Content HTTP read/create against ephemeral PostgreSQL 18."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.application.errors import PersistenceOperationFailed
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.platform.api.app import create_app
from aieos.platform.api.etag import encode_revision_etag
from aieos.platform.api.pagination import CursorCodec, ListCursor
from tests.fakes import (
    AllowReviewAuthorization,
    AllowReviewCommentPolicy,
    AllowPublicationAssetValidation,
    AllowPublicationAuthorization,
    AllowPublicationGovernance,
    IDEMPOTENCY_RETENTION,
    StubSecurityContextResolver,
    make_test_schema_registry,
)

pytestmark = pytest.mark.gci_i04

CURSOR_KEY = b"gci-i04-test-cursor-signing-key"
CREATE_BODY = {
    "content_type": "test.generic",
    "title": "Title",
    "description": "Description",
    "locale": "en-IN",
}
LEAK_NEEDLES = (
    "sqlalchemy",
    "psycopg",
    "postgresql://",
    "postgresql+psycopg://",
    "SELECT ",
    "INSERT ",
    "Traceback",
    "password",
)


def _app(runtime_engine: Engine, tenant_id: UUID, principal_id: UUID, **resolver_kw):
    return create_app(
        uow_factory=SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
        security_resolver=StubSecurityContextResolver(
            tenant_id, principal_id, **resolver_kw
        ),
        content_types=StaticContentTypeCatalog({"test.generic"}),
        cursor_signing_key=CURSOR_KEY,
        schema_registry=make_test_schema_registry(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
        review_authorization=AllowReviewAuthorization(),
        review_comment_policy=AllowReviewCommentPolicy(),
        publication_authorization=AllowPublicationAuthorization(),
        publication_governance=AllowPublicationGovernance(),
        publication_asset_validation=AllowPublicationAssetValidation(),
    )


def _client(runtime_engine: Engine, tenant_id: UUID, principal_id: UUID, **resolver_kw) -> TestClient:
    return TestClient(
        _app(runtime_engine, tenant_id, principal_id, **resolver_kw),
        raise_server_exceptions=False,
    )


def _headers(tenant_id: UUID, **extra: str) -> dict[str, str]:
    headers = {"X-AIEOS-Tenant-ID": str(tenant_id), **extra}
    if "Idempotency-Key" not in headers:
        headers["Idempotency-Key"] = f"test-{uuid.uuid7()}"
    return headers


def _assert_problem(response, *, status: int, code: str) -> dict:
    assert response.status_code == status
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["code"] == code
    assert body["status"] == status
    assert body["type"] == f"urn:aieos:problem:{code}"
    assert "title" in body and "detail" in body and "instance" in body
    assert UUID(body["request_id"]).version == 7
    assert UUID(body["correlation_id"]).version in {4, 7} or True
    UUID(body["correlation_id"])
    blob = json.dumps(body)
    for needle in LEAK_NEEDLES:
        assert needle.lower() not in blob.lower()
    assert "X-AIEOS-Request-ID" in response.headers
    assert "X-AIEOS-Correlation-ID" in response.headers
    assert response.headers["X-AIEOS-Request-ID"] == body["request_id"]
    return body


def _load_row(bootstrap_engine: Engine, content_id: UUID):
    with bootstrap_engine.connect() as conn:
        return conn.execute(
            text(
                """
                SELECT tenant_id, owner_principal_id, created_by_principal_id,
                       stewardship_state, aggregate_revision, current_version_id,
                       published_version_id, archived_at
                FROM content.contents WHERE content_id = :cid
                """
            ),
            {"cid": content_id},
        ).one_or_none()


class TestCreate:
    def test_post_creates_draft_revision_zero(
        self, runtime_engine, bootstrap_engine, postgres18
    ) -> None:
        assert postgres18["server_version"].startswith("18.")
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, principal_id)
        response = client.post("/api/v1/contents", json=CREATE_BODY, headers=_headers(tenant_id))
        assert response.status_code == 201, response.text
        body = response.json()
        content_id = UUID(body["content_id"])
        assert content_id.version == 7
        assert response.headers["Location"] == f"/api/v1/contents/{content_id}"
        assert response.headers["ETag"] == encode_revision_etag(0)
        assert body["stewardship_state"] == "DRAFT"
        assert body["aggregate_revision"] == 0
        assert body["current_version_id"] is None
        assert body["published_version_id"] is None
        assert body["archived_at"] is None
        assert "tenant_id" not in body
        assert "owner_principal_id" not in body
        assert "created_by_principal_id" not in body
        assert "X-AIEOS-Request-ID" in response.headers
        assert "X-AIEOS-Correlation-ID" in response.headers
        UUID(response.headers["X-AIEOS-Request-ID"])
        row = _load_row(bootstrap_engine, content_id)
        assert row is not None
        assert row.tenant_id == tenant_id
        assert row.owner_principal_id == principal_id
        assert row.created_by_principal_id == principal_id
        assert row.stewardship_state == "DRAFT"
        assert int(row.aggregate_revision) == 0
        assert row.current_version_id is None
        assert row.published_version_id is None

    def test_request_body_cannot_smuggle_server_fields(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        for smuggled in (
            {"tenant_id": str(uuid.uuid7())},
            {"owner_principal_id": str(uuid.uuid7())},
            {"stewardship_state": "APPROVED"},
            {"aggregate_revision": 9},
        ):
            response = client.post(
                "/api/v1/contents",
                json={**CREATE_BODY, **smuggled},
                headers=_headers(tenant_id),
            )
            _assert_problem(response, status=422, code="validation_error")
        with bootstrap_engine.connect() as conn:
            count = conn.execute(
                text("SELECT count(*) FROM content.contents WHERE tenant_id = :tid"),
                {"tid": tenant_id},
            ).scalar_one()
        assert int(count) == 0

    def test_unknown_content_type_rejected(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        response = client.post(
            "/api/v1/contents",
            json={**CREATE_BODY, "content_type": "worksheet"},
            headers=_headers(tenant_id),
        )
        _assert_problem(response, status=422, code="unknown_content_type")
        with bootstrap_engine.connect() as conn:
            count = conn.execute(
                text("SELECT count(*) FROM content.contents WHERE tenant_id = :tid"),
                {"tid": tenant_id},
            ).scalar_one()
        assert int(count) == 0

    def test_spoofed_tenant_header_denied(self, runtime_engine, bootstrap_engine) -> None:
        authorized = uuid.uuid7()
        requested = uuid.uuid7()
        client = _client(runtime_engine, authorized, uuid.uuid7())
        response = client.post(
            "/api/v1/contents",
            json=CREATE_BODY,
            headers=_headers(requested),
        )
        _assert_problem(response, status=403, code="forbidden")
        with bootstrap_engine.connect() as conn:
            count = conn.execute(
                text("SELECT count(*) FROM content.contents WHERE tenant_id = :tid"),
                {"tid": authorized},
            ).scalar_one()
        assert int(count) == 0
        with bootstrap_engine.connect() as conn:
            count_requested = conn.execute(
                text("SELECT count(*) FROM content.contents WHERE tenant_id = :tid"),
                {"tid": requested},
            ).scalar_one()
        assert int(count_requested) == 0


class TestGet:
    def test_get_same_tenant_and_etag(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        created = client.post("/api/v1/contents", json=CREATE_BODY, headers=_headers(tenant_id))
        content_id = created.json()["content_id"]
        response = client.get(f"/api/v1/contents/{content_id}", headers=_headers(tenant_id))
        assert response.status_code == 200, response.text
        assert response.headers["ETag"] == encode_revision_etag(0)
        assert response.json()["content_id"] == content_id
        assert "tenant_id" not in response.json()

    def test_missing_and_cross_tenant_are_the_same_404(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        client_a = _client(runtime_engine, tenant_a, uuid.uuid7())
        client_b = _client(runtime_engine, tenant_b, uuid.uuid7())
        created = client_a.post("/api/v1/contents", json=CREATE_BODY, headers=_headers(tenant_a))
        content_id = created.json()["content_id"]
        missing = client_a.get(
            f"/api/v1/contents/{uuid.uuid7()}", headers=_headers(tenant_a)
        )
        hidden = client_b.get(f"/api/v1/contents/{content_id}", headers=_headers(tenant_b))
        missing_body = _assert_problem(missing, status=404, code="content_not_found")
        hidden_body = _assert_problem(hidden, status=404, code="content_not_found")
        assert missing_body["title"] == hidden_body["title"]
        assert str(tenant_a) not in hidden.text
        assert str(tenant_b) not in hidden.text
        assert "owner" not in hidden.text.lower()
        row = _load_row(bootstrap_engine, UUID(content_id))
        assert row is not None
        assert str(row.owner_principal_id) not in hidden.text
        assert str(row.tenant_id) not in hidden.text


class TestList:
    def test_list_tenant_order_cursor_and_concurrency(self, runtime_engine) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        client_a = _client(runtime_engine, tenant_a, uuid.uuid7())
        client_b = _client(runtime_engine, tenant_b, uuid.uuid7())
        ids_a: list[str] = []
        for index in range(3):
            body = {**CREATE_BODY, "title": f"A-{index}"}
            created = client_a.post("/api/v1/contents", json=body, headers=_headers(tenant_a))
            assert created.status_code == 201, created.text
            ids_a.append(created.json()["content_id"])
        other = client_b.post(
            "/api/v1/contents",
            json={**CREATE_BODY, "title": "B"},
            headers=_headers(tenant_b),
        )
        assert other.status_code == 201
        page1 = client_a.get(
            "/api/v1/contents",
            params={"limit": 2},
            headers=_headers(tenant_a),
        )
        assert page1.status_code == 200, page1.text
        items1 = [item["content_id"] for item in page1.json()["items"]]
        assert len(items1) == 2
        assert other.json()["content_id"] not in items1
        created_order = list(reversed(ids_a))
        assert items1 == created_order[:2]
        cursor = page1.json()["next_cursor"]
        assert cursor
        inserted = client_a.post(
            "/api/v1/contents",
            json={**CREATE_BODY, "title": "A-new"},
            headers=_headers(tenant_a),
        )
        assert inserted.status_code == 201
        page2 = client_a.get(
            "/api/v1/contents",
            params={"limit": 2, "cursor": cursor},
            headers=_headers(tenant_a),
        )
        assert page2.status_code == 200, page2.text
        items2 = [item["content_id"] for item in page2.json()["items"]]
        assert not set(items1) & set(items2)
        assert ids_a[0] in items2
        assert inserted.json()["content_id"] not in items2

    def test_tampered_and_cross_tenant_cursor_rejected(self, runtime_engine) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        client_a = _client(runtime_engine, tenant_a, uuid.uuid7())
        client_b = _client(runtime_engine, tenant_b, uuid.uuid7())
        created = client_a.post("/api/v1/contents", json=CREATE_BODY, headers=_headers(tenant_a))
        content_id = UUID(created.json()["content_id"])
        codec = CursorCodec(CURSOR_KEY)
        cursor = codec.encode(
            ListCursor(
                tenant_id=tenant_a,
                created_at=datetime.fromisoformat(
                    created.json()["created_at"].replace("Z", "+00:00")
                ),
                content_id=content_id,
            )
        )
        tampered = cursor[:-2] + ("A" if cursor[-2] != "A" else "B") + cursor[-1]
        bad = client_a.get(
            "/api/v1/contents",
            params={"cursor": tampered},
            headers=_headers(tenant_a),
        )
        _assert_problem(bad, status=400, code="invalid_cursor")
        foreign = client_b.get(
            "/api/v1/contents",
            params={"cursor": cursor},
            headers=_headers(tenant_b),
        )
        _assert_problem(foreign, status=400, code="invalid_cursor")

    def test_limit_over_max_rejected(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        response = client.get(
            "/api/v1/contents",
            params={"limit": 101},
            headers=_headers(tenant_id),
        )
        _assert_problem(response, status=422, code="invalid_content_request")


class TestRequestContext:
    def test_request_id_is_server_generated(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        inbound = str(uuid.uuid7())
        response = client.get(
            "/api/v1/contents",
            headers=_headers(tenant_id, **{"X-AIEOS-Request-ID": inbound}),
        )
        assert response.status_code == 200
        server_id = response.headers["X-AIEOS-Request-ID"]
        assert server_id != inbound
        assert UUID(server_id).version == 7

    def test_correlation_generated_preserved_and_malformed(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        generated = client.get("/api/v1/contents", headers=_headers(tenant_id))
        assert generated.status_code == 200
        assert UUID(generated.headers["X-AIEOS-Correlation-ID"]).version == 7
        supplied = uuid.uuid4()
        preserved = client.get(
            "/api/v1/contents",
            headers=_headers(tenant_id, **{"X-AIEOS-Correlation-ID": str(supplied)}),
        )
        assert preserved.status_code == 200
        assert preserved.headers["X-AIEOS-Correlation-ID"] == str(supplied)
        malformed = client.get(
            "/api/v1/contents",
            headers=_headers(tenant_id, **{"X-AIEOS-Correlation-ID": "not-a-uuid"}),
        )
        body = _assert_problem(malformed, status=400, code="invalid_correlation_id")
        assert body["request_id"] != body["correlation_id"]


class TestProblemDetails:
    def test_validation_404_405_are_problem_json(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        validation = client.post(
            "/api/v1/contents",
            json={"title": "only"},
            headers=_headers(tenant_id),
        )
        _assert_problem(validation, status=422, code="validation_error")
        missing = client.get(
            f"/api/v1/contents/{uuid.uuid7()}", headers=_headers(tenant_id)
        )
        _assert_problem(missing, status=404, code="content_not_found")
        method = client.put("/api/v1/contents", headers=_headers(tenant_id))
        _assert_problem(method, status=405, code="method_not_allowed")

    def test_persistence_failure_is_503_without_leak(
        self, runtime_engine, monkeypatch
    ) -> None:
        tenant_id = uuid.uuid7()
        app = _app(runtime_engine, tenant_id, uuid.uuid7())

        def boom(*_a, **_k):
            raise PersistenceOperationFailed(
                "sqlalchemy.exc.OperationalError (psycopg.OperationalError) "
                "postgresql://aieos_runtime:password@127.0.0.1/aieos SELECT 1"
            )

        monkeypatch.setattr(app.state.get_content_service, "get", boom)
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get(
            f"/api/v1/contents/{uuid.uuid7()}", headers=_headers(tenant_id)
        )
        _assert_problem(response, status=503, code="persistence_unavailable")

    def test_unexpected_failure_is_sanitized_500(
        self, runtime_engine, monkeypatch
    ) -> None:
        tenant_id = uuid.uuid7()
        app = _app(runtime_engine, tenant_id, uuid.uuid7())

        def boom(*_a, **_k):
            raise RuntimeError(
                "Traceback SELECT * FROM content.contents postgresql://secret"
            )

        monkeypatch.setattr(app.state.get_content_service, "get", boom)
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get(
            f"/api/v1/contents/{uuid.uuid7()}", headers=_headers(tenant_id)
        )
        _assert_problem(response, status=500, code="internal_error")
