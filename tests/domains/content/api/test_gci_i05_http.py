"""GCI-I05 version append, If-Match, and transactional idempotency."""

from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.application.errors import PersistenceOperationFailed
from aieos.domains.content.application.http_append import HttpAppendContentVersionService
from aieos.domains.content.domain.identities import AggregateRevision, ContentId
from aieos.domains.content.domain.version import ContentPayload
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWork,
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.platform.api.app import create_app
from aieos.platform.api.etag import encode_revision_etag
from aieos.platform.idempotency.hashing import hash_idempotency_key
from tests.fakes import IDEMPOTENCY_RETENTION, StubSecurityContextResolver, make_test_schema_registry

pytestmark = pytest.mark.gci_i05

CURSOR_KEY = b"gci-i05-test-cursor-signing-key"
CREATE_BODY = {
    "content_type": "test.generic",
    "title": "Title",
    "description": "Description",
    "locale": "en-IN",
}
APPEND_BODY = {"schema_id": "test.generic", "schema_version": 1, "payload": {"marker": "v1"}}


def _app(runtime_engine: Engine, tenant_id: UUID, principal_id: UUID):
    return create_app(
        uow_factory=SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
        security_resolver=StubSecurityContextResolver(tenant_id, principal_id),
        content_types=StaticContentTypeCatalog({"test.generic"}),
        cursor_signing_key=CURSOR_KEY,
        schema_registry=make_test_schema_registry(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
    )


def _client(runtime_engine: Engine, tenant_id: UUID, principal_id: UUID) -> TestClient:
    return TestClient(_app(runtime_engine, tenant_id, principal_id), raise_server_exceptions=False)


def _headers(tenant_id: UUID, **extra: str) -> dict[str, str]:
    headers = {"X-AIEOS-Tenant-ID": str(tenant_id), **extra}
    if "Idempotency-Key" not in headers:
        headers["Idempotency-Key"] = f"test-{uuid.uuid7()}"
    return headers


def _create(client: TestClient, tenant_id: UUID, **extra: str) -> dict:
    response = client.post(
        "/api/v1/contents",
        json=CREATE_BODY,
        headers=_headers(tenant_id, **extra),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _append(
    client: TestClient,
    tenant_id: UUID,
    content_id: str,
    *,
    etag: str,
    body: dict | None = None,
    **extra: str,
):
    headers = _headers(tenant_id, **extra)
    headers["If-Match"] = etag
    return client.post(
        f"/api/v1/contents/{content_id}/versions",
        json=body or APPEND_BODY,
        headers=headers,
    )


def _count_versions(bootstrap_engine: Engine, content_id: UUID) -> int:
    with bootstrap_engine.connect() as conn:
        return int(
            conn.execute(
                text("SELECT count(*) FROM content.content_versions WHERE content_id = :cid"),
                {"cid": content_id},
            ).scalar_one()
        )


class TestAppendContract:
    def test_first_and_second_append(self, runtime_engine, bootstrap_engine, postgres18) -> None:
        assert postgres18["server_version"].startswith("18.")
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        created = _create(client, tenant_id)
        content_id = created["content_id"]
        first = _append(client, tenant_id, content_id, etag='"r0"')
        assert first.status_code == 201, first.text
        body = first.json()
        version_id = UUID(body["version_id"])
        assert version_id.version == 7
        assert body["version_number"] == 1
        assert body["parent_version_id"] is None
        assert body["origin"] == "HUMAN"
        assert body["payload"] == {"marker": "v1"}
        assert body["payload_sha256"] == ContentPayload.from_mapping({"marker": "v1"}).sha256.value
        assert "tenant_id" not in body
        assert "created_by_principal_id" not in body
        assert "provenance" not in body
        assert first.headers["Location"] == (
            f"/api/v1/contents/{content_id}/versions/{version_id}"
        )
        assert first.headers["ETag"] == encode_revision_etag(1)
        second = _append(
            client,
            tenant_id,
            content_id,
            etag='"r1"',
            body={"schema_id": "test.generic", "schema_version": 1, "payload": {"marker": "v2"}},
        )
        assert second.status_code == 201, second.text
        assert second.json()["version_number"] == 2
        assert second.json()["parent_version_id"] == str(version_id)
        assert second.headers["ETag"] == encode_revision_etag(2)
        with bootstrap_engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT aggregate_revision, current_version_id, published_version_id,
                           stewardship_state
                    FROM content.contents WHERE content_id = :cid
                    """
                ),
                {"cid": UUID(content_id)},
            ).one()
        assert int(row.aggregate_revision) == 2
        assert row.current_version_id == UUID(second.json()["version_id"])
        assert row.published_version_id is None
        assert row.stewardship_state == "DRAFT"

    def test_body_cannot_smuggle_server_fields(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id = _create(client, tenant_id)["content_id"]
        for smuggled in (
            {"version_id": str(uuid.uuid7())},
            {"version_number": 9},
            {"parent_version_id": str(uuid.uuid7())},
            {"tenant_id": str(uuid.uuid7())},
            {"origin": "AI"},
            {"provenance": {"model": "x"}},
            {"created_by_principal_id": str(uuid.uuid7())},
        ):
            response = _append(
                client, tenant_id, content_id, etag='"r0"', body={**APPEND_BODY, **smuggled}
            )
            assert response.status_code == 422
            assert response.json()["code"] == "validation_error"

    def test_schema_and_payload_validation(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id = _create(client, tenant_id)["content_id"]
        unknown = _append(
            client,
            tenant_id,
            content_id,
            etag='"r0"',
            body={"schema_id": "missing", "schema_version": 1, "payload": {"marker": "x"}},
        )
        assert unknown.status_code == 422
        assert unknown.json()["code"] == "content_schema_not_found"
        mismatch = _append(
            client,
            tenant_id,
            content_id,
            etag='"r0"',
            body={"schema_id": "test.other", "schema_version": 1, "payload": {"marker": "x"}},
        )
        assert mismatch.status_code == 422
        assert mismatch.json()["code"] == "content_schema_mismatch"
        invalid = _append(
            client,
            tenant_id,
            content_id,
            etag='"r0"',
            body={"schema_id": "test.generic", "schema_version": 1, "payload": {"nope": 1}},
        )
        assert invalid.status_code == 422
        assert invalid.json()["code"] == "content_payload_invalid"


class TestIfMatch:
    def test_missing_and_malformed(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id = _create(client, tenant_id)["content_id"]
        missing = client.post(
            f"/api/v1/contents/{content_id}/versions",
            json=APPEND_BODY,
            headers=_headers(tenant_id),
        )
        assert missing.status_code == 428
        assert missing.json()["code"] == "precondition_required"
        for value in ('r0', 'W/"r0"', "*", '"r0", "r1"', '"r-1"', '"r01"'):
            bad = _append(client, tenant_id, content_id, etag=value)
            assert bad.status_code == 400, value
            assert bad.json()["code"] == "invalid_if_match"

    def test_stale_if_match_is_412_and_leaves_no_version(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id = _create(client, tenant_id)["content_id"]
        first = _append(client, tenant_id, content_id, etag='"r0"')
        assert first.status_code == 201
        stale = _append(
            client,
            tenant_id,
            content_id,
            etag='"r0"',
            body={"schema_id": "test.generic", "schema_version": 1, "payload": {"marker": "stale"}},
        )
        assert stale.status_code == 412
        assert stale.json()["code"] == "resource_revision_conflict"
        assert _count_versions(bootstrap_engine, UUID(content_id)) == 1


class TestIdempotencyHttp:
    def test_retry_same_key_same_body(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id = _create(client, tenant_id)["content_id"]
        key = f"retry-{uuid.uuid7()}"
        first = _append(client, tenant_id, content_id, etag='"r0"', **{"Idempotency-Key": key})
        assert first.status_code == 201
        replay = _append(client, tenant_id, content_id, etag='"r0"', **{"Idempotency-Key": key})
        assert replay.status_code == 201
        assert replay.json()["version_id"] == first.json()["version_id"]
        assert replay.headers["Location"] == first.headers["Location"]
        assert replay.headers["ETag"] == first.headers["ETag"] == '"r1"'
        assert _count_versions(bootstrap_engine, UUID(content_id)) == 1

    def test_retry_after_later_aggregate_advance(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id = _create(client, tenant_id)["content_id"]
        key = f"original-{uuid.uuid7()}"
        first = _append(client, tenant_id, content_id, etag='"r0"', **{"Idempotency-Key": key})
        later = _append(
            client,
            tenant_id,
            content_id,
            etag='"r1"',
            body={"schema_id": "test.generic", "schema_version": 1, "payload": {"marker": "later"}},
        )
        assert later.status_code == 201
        replay = _append(client, tenant_id, content_id, etag='"r0"', **{"Idempotency-Key": key})
        assert replay.status_code == 201
        assert replay.json()["version_id"] == first.json()["version_id"]
        assert replay.headers["ETag"] == '"r1"'
        assert _count_versions(bootstrap_engine, UUID(content_id)) == 2

    def test_same_key_changed_payload_or_if_match_conflicts(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id = _create(client, tenant_id)["content_id"]
        key = f"reuse-{uuid.uuid7()}"
        first = _append(client, tenant_id, content_id, etag='"r0"', **{"Idempotency-Key": key})
        assert first.status_code == 201
        changed_payload = _append(
            client,
            tenant_id,
            content_id,
            etag='"r0"',
            body={"schema_id": "test.generic", "schema_version": 1, "payload": {"marker": "other"}},
            **{"Idempotency-Key": key},
        )
        assert changed_payload.status_code == 409
        assert changed_payload.json()["code"] == "idempotency_key_reused"
        changed_if_match = _append(
            client, tenant_id, content_id, etag='"r1"', **{"Idempotency-Key": key}
        )
        assert changed_if_match.status_code == 409
        assert _count_versions(bootstrap_engine, UUID(content_id)) == 1

    def test_create_idempotency(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        key = f"create-{uuid.uuid7()}"
        first = client.post(
            "/api/v1/contents", json=CREATE_BODY, headers=_headers(tenant_id, **{"Idempotency-Key": key})
        )
        replay = client.post(
            "/api/v1/contents", json=CREATE_BODY, headers=_headers(tenant_id, **{"Idempotency-Key": key})
        )
        assert first.status_code == replay.status_code == 201
        assert first.json()["content_id"] == replay.json()["content_id"]
        assert replay.headers["ETag"] == '"r0"'
        changed = client.post(
            "/api/v1/contents",
            json={**CREATE_BODY, "title": "Other"},
            headers=_headers(tenant_id, **{"Idempotency-Key": key}),
        )
        assert changed.status_code == 409
        assert changed.json()["code"] == "idempotency_key_reused"
        with bootstrap_engine.connect() as conn:
            count = conn.execute(
                text("SELECT count(*) FROM content.contents WHERE tenant_id = :tid"),
                {"tid": tenant_id},
            ).scalar_one()
        assert int(count) == 1

    def test_create_retry_after_later_aggregate_advance_replays_original_outcome(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        key = f"create-advance-{uuid.uuid7()}"
        first = client.post(
            "/api/v1/contents",
            json=CREATE_BODY,
            headers=_headers(tenant_id, **{"Idempotency-Key": key}),
        )
        assert first.status_code == 201, first.text
        original = first.json()
        content_id = original["content_id"]
        append = _append(client, tenant_id, content_id, etag='"r0"')
        assert append.status_code == 201, append.text
        version_id = append.json()["version_id"]
        with bootstrap_engine.connect() as conn:
            head = conn.execute(
                text(
                    "SELECT aggregate_revision, current_version_id "
                    "FROM content.contents WHERE content_id = :cid"
                ),
                {"cid": UUID(content_id)},
            ).one()
        assert int(head.aggregate_revision) == 1
        assert str(head.current_version_id) == version_id

        replay = client.post(
            "/api/v1/contents",
            json=CREATE_BODY,
            headers=_headers(tenant_id, **{"Idempotency-Key": key}),
        )
        assert replay.status_code == 201, replay.text
        replayed = replay.json()
        assert replayed == original
        assert replayed["content_id"] == content_id
        assert replay.headers["Location"] == first.headers["Location"]
        assert replay.headers["ETag"] == '"r0"'
        assert replayed["aggregate_revision"] == 0
        assert replayed["current_version_id"] is None
        assert replayed["published_version_id"] is None
        assert replayed["stewardship_state"] == "DRAFT"
        assert replayed["created_at"] == original["created_at"]
        assert replayed["updated_at"] == original["updated_at"]

        changed = client.post(
            "/api/v1/contents",
            json={**CREATE_BODY, "title": "Other"},
            headers=_headers(tenant_id, **{"Idempotency-Key": key}),
        )
        assert changed.status_code == 409
        assert changed.json()["code"] == "idempotency_key_reused"

        with bootstrap_engine.connect() as conn:
            after = conn.execute(
                text(
                    "SELECT aggregate_revision, current_version_id "
                    "FROM content.contents WHERE content_id = :cid"
                ),
                {"cid": UUID(content_id)},
            ).one()
            content_count = conn.execute(
                text("SELECT count(*) FROM content.contents WHERE tenant_id = :tid"),
                {"tid": tenant_id},
            ).scalar_one()
            version_count = conn.execute(
                text(
                    "SELECT count(*) FROM content.content_versions WHERE content_id = :cid"
                ),
                {"cid": UUID(content_id)},
            ).scalar_one()
        assert int(after.aggregate_revision) == 1
        assert str(after.current_version_id) == version_id
        assert int(content_count) == 1
        assert int(version_count) == 1

    def test_create_replay_survives_catalog_drift(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        key = f"create-catalog-{uuid.uuid7()}"
        client = _client(runtime_engine, tenant_id, principal_id)
        first = client.post(
            "/api/v1/contents",
            json=CREATE_BODY,
            headers=_headers(tenant_id, **{"Idempotency-Key": key}),
        )
        assert first.status_code == 201, first.text
        drifted = TestClient(
            create_app(
                uow_factory=SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
                security_resolver=StubSecurityContextResolver(tenant_id, principal_id),
                content_types=StaticContentTypeCatalog({"other.type"}),
                cursor_signing_key=CURSOR_KEY,
                schema_registry=make_test_schema_registry(),
                idempotency_retention=IDEMPOTENCY_RETENTION,
            ),
            raise_server_exceptions=False,
        )
        unknown = drifted.post(
            "/api/v1/contents",
            json={**CREATE_BODY, "title": "Fresh"},
            headers=_headers(tenant_id),
        )
        assert unknown.status_code == 422
        assert unknown.json()["code"] == "unknown_content_type"
        replay = drifted.post(
            "/api/v1/contents",
            json=CREATE_BODY,
            headers=_headers(tenant_id, **{"Idempotency-Key": key}),
        )
        assert replay.status_code == 201, replay.text
        assert replay.json() == first.json()
        assert replay.json()["content_id"] == first.json()["content_id"]
        assert replay.headers["ETag"] == '"r0"'

    def test_scopes_do_not_collide(self, runtime_engine, bootstrap_engine) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        principal_a = uuid.uuid7()
        principal_b = uuid.uuid7()
        raw_key = f"shared-{uuid.uuid7()}"
        client_a = _client(runtime_engine, tenant_a, principal_a)
        client_b = _client(runtime_engine, tenant_b, principal_b)
        client_a2 = _client(runtime_engine, tenant_a, principal_b)
        created = _create(client_a, tenant_a, **{"Idempotency-Key": raw_key})
        content_id = created["content_id"]
        append = _append(
            client_a, tenant_a, content_id, etag='"r0"', **{"Idempotency-Key": raw_key}
        )
        assert append.status_code == 201
        other_tenant = _create(client_b, tenant_b, **{"Idempotency-Key": raw_key})
        assert other_tenant["content_id"] != content_id
        other_principal = _create(client_a2, tenant_a, **{"Idempotency-Key": raw_key})
        assert other_principal["content_id"] != content_id
        with bootstrap_engine.connect() as conn:
            rows = conn.execute(
                text("SELECT idempotency_key_sha256, operation FROM api.idempotency_records")
            ).all()
            blob = json.dumps([tuple(row) for row in rows])
        assert raw_key not in blob
        assert hash_idempotency_key(raw_key) in blob
        assert "content_create.v1" in blob
        assert "content_version_append.v1" in blob

    def test_missing_and_invalid_keys(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        missing = client.post(
            "/api/v1/contents",
            json=CREATE_BODY,
            headers={"X-AIEOS-Tenant-ID": str(tenant_id)},
        )
        assert missing.status_code == 400
        assert missing.json()["code"] == "idempotency_key_required"
        invalid = client.post(
            "/api/v1/contents",
            json=CREATE_BODY,
            headers=_headers(tenant_id, **{"Idempotency-Key": "bad\nkey"}),
        )
        assert invalid.status_code == 400
        assert invalid.json()["code"] == "invalid_idempotency_key"
        assert "bad" not in invalid.text


class TestConcurrency:
    def test_different_keys_same_revision(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        app = _app(runtime_engine, tenant_id, principal_id)
        setup = TestClient(app, raise_server_exceptions=False)
        content_id = _create(setup, tenant_id)["content_id"]
        results: list = []

        def worker(key: str) -> None:
            client = TestClient(app, raise_server_exceptions=False)
            results.append(
                _append(client, tenant_id, content_id, etag='"r0"', **{"Idempotency-Key": key})
            )

        threads = [
            threading.Thread(target=worker, args=(f"k1-{uuid.uuid7()}",)),
            threading.Thread(target=worker, args=(f"k2-{uuid.uuid7()}",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        codes = sorted(item.status_code for item in results)
        assert codes == [201, 412]
        assert _count_versions(bootstrap_engine, UUID(content_id)) == 1

    def test_same_key_same_body(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        app = _app(runtime_engine, tenant_id, principal_id)
        setup = TestClient(app, raise_server_exceptions=False)
        content_id = _create(setup, tenant_id)["content_id"]
        key = f"same-{uuid.uuid7()}"
        results: list = []

        def worker() -> None:
            client = TestClient(app, raise_server_exceptions=False)
            results.append(
                _append(client, tenant_id, content_id, etag='"r0"', **{"Idempotency-Key": key})
            )

        threads = [threading.Thread(target=worker), threading.Thread(target=worker)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert [item.status_code for item in results] == [201, 201]
        assert results[0].json()["version_id"] == results[1].json()["version_id"]
        assert _count_versions(bootstrap_engine, UUID(content_id)) == 1
        with bootstrap_engine.connect() as conn:
            count = conn.execute(
                text("SELECT count(*) FROM api.idempotency_records WHERE tenant_id = :tid"),
                {"tid": tenant_id},
            ).scalar_one()
        assert int(count) == 2  # create + one append outcome


class TestGetVersionAndStewardship:
    def test_get_version_isolation(self, runtime_engine) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        client_a = _client(runtime_engine, tenant_a, uuid.uuid7())
        client_b = _client(runtime_engine, tenant_b, uuid.uuid7())
        content_a = _create(client_a, tenant_a)["content_id"]
        content_a2 = _create(client_a, tenant_a)["content_id"]
        version = _append(client_a, tenant_a, content_a, etag='"r0"').json()
        ok = client_a.get(
            f"/api/v1/contents/{content_a}/versions/{version['version_id']}",
            headers=_headers(tenant_a),
        )
        assert ok.status_code == 200
        assert ok.json()["version_id"] == version["version_id"]
        hidden = client_b.get(
            f"/api/v1/contents/{content_a}/versions/{version['version_id']}",
            headers=_headers(tenant_b),
        )
        missing = client_a.get(
            f"/api/v1/contents/{content_a}/versions/{uuid.uuid7()}",
            headers=_headers(tenant_a),
        )
        wrong_parent = client_a.get(
            f"/api/v1/contents/{content_a2}/versions/{version['version_id']}",
            headers=_headers(tenant_a),
        )
        for response in (hidden, missing, wrong_parent):
            assert response.status_code == 404
            assert response.json()["code"] == "content_version_not_found"
            assert str(tenant_a) not in response.text

    def test_stewardship_gate_and_published_pointer(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        generated_id = UUID(_create(client, tenant_id)["content_id"])
        with bootstrap_engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE content.contents SET stewardship_state = 'GENERATED' "
                    "WHERE content_id = :cid"
                ),
                {"cid": generated_id},
            )
        generated_append = _append(client, tenant_id, str(generated_id), etag='"r0"')
        assert generated_append.status_code == 201, generated_append.text
        content_id = UUID(_create(client, tenant_id)["content_id"])
        first = _append(client, tenant_id, str(content_id), etag='"r0"')
        assert first.status_code == 201
        version_id = UUID(first.json()["version_id"])
        with bootstrap_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE content.contents
                    SET published_version_id = :vid
                    WHERE content_id = :cid
                    """
                ),
                {"vid": version_id, "cid": content_id},
            )
        second = _append(
            client,
            tenant_id,
            str(content_id),
            etag='"r1"',
            body={"schema_id": "test.generic", "schema_version": 1, "payload": {"marker": "v2"}},
        )
        assert second.status_code == 201
        with bootstrap_engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT published_version_id, stewardship_state FROM content.contents "
                    "WHERE content_id = :cid"
                ),
                {"cid": content_id},
            ).one()
        assert row.published_version_id == version_id
        assert row.stewardship_state == "DRAFT"
        for state, archived_at in (
            ("IN_REVIEW", None),
            ("APPROVED", None),
            ("ARCHIVED", datetime(2026, 8, 13, tzinfo=UTC)),
        ):
            with bootstrap_engine.begin() as conn:
                conn.execute(
                    text(
                        """
                        UPDATE content.contents
                        SET stewardship_state = :state,
                            archived_at = :archived_at,
                            published_version_id = CASE WHEN :state = 'ARCHIVED' THEN NULL
                                                        ELSE published_version_id END
                        WHERE content_id = :cid
                        """
                    ),
                    {"state": state, "archived_at": archived_at, "cid": content_id},
                )
            blocked = _append(
                client,
                tenant_id,
                str(content_id),
                etag='"r2"',
                body={"schema_id": "test.generic", "schema_version": 1, "payload": {"marker": state}},
            )
            assert blocked.status_code == 409, state
            assert blocked.json()["code"] == "content_version_append_not_allowed"


class TestRollbackAtomicity:
    def test_failure_before_commit_rolls_back_all(
        self, runtime_engine, bootstrap_engine, monkeypatch
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, principal_id)
        content_id = UUID(_create(client, tenant_id)["content_id"])

        def boom(self) -> None:
            raise PersistenceOperationFailed("injected commit failure")

        monkeypatch.setattr(SqlAlchemyContentUnitOfWork, "commit", boom)
        service = HttpAppendContentVersionService(
            SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
            make_test_schema_registry(),
            idempotency_retention=IDEMPOTENCY_RETENTION,
        )
        with pytest.raises(PersistenceOperationFailed):
            service.append(
                tenant_id,
                principal_id,
                content_id=ContentId(content_id),
                expected_aggregate_revision=AggregateRevision(0),
                schema_id="test.generic",
                schema_version=1,
                payload={"marker": "boom"},
                idempotency_key=f"boom-{uuid.uuid7()}",
            )
        assert _count_versions(bootstrap_engine, content_id) == 0
        with bootstrap_engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT aggregate_revision, current_version_id FROM content.contents "
                    "WHERE content_id = :cid"
                ),
                {"cid": content_id},
            ).one()
            idem = conn.execute(
                text("SELECT count(*) FROM api.idempotency_records WHERE tenant_id = :tid"),
                {"tid": tenant_id},
            ).scalar_one()
        assert int(row.aggregate_revision) == 0
        assert row.current_version_id is None
        assert int(idem) == 1  # create succeeded earlier; append outcome absent
        with bootstrap_engine.connect() as conn:
            append_ops = conn.execute(
                text(
                    "SELECT count(*) FROM api.idempotency_records "
                    "WHERE tenant_id = :tid AND operation = 'content_version_append.v1'"
                ),
                {"tid": tenant_id},
            ).scalar_one()
        assert int(append_ops) == 0
