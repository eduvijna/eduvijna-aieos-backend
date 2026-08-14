"""GCI-I09 Content publish HTTP foundation."""

from __future__ import annotations

import json
import threading
import uuid
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.application.errors import PersistenceOperationFailed
from aieos.domains.content.domain.errors import InvalidPayloadError
from aieos.domains.content.domain.schema import ContentSchemaRegistry, SchemaId, SchemaVersion
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.platform.api.app import create_app
from aieos.platform.api.etag import encode_revision_etag
from aieos.platform.events.persistence.repositories import SqlAlchemyOutboxRepository
from tests.fakes import (
    AllowPublicationAssetValidation,
    AllowPublicationAuthorization,
    AllowPublicationGovernance,
    AllowReviewAuthorization,
    AllowReviewCommentPolicy,
    IDEMPOTENCY_RETENTION,
    StubSecurityContextResolver,
    make_test_schema_registry,
)
from tests.platform.workflows.helpers import (
    append_version,
    create_content,
    decide,
    generated_version,
    headers,
    in_review,
    submit_review,
)

pytestmark = pytest.mark.gci_i09

CURSOR_KEY = b"gci-i09-test-cursor-signing-key"
LEAK_NEEDLES = ("sqlalchemy", "psycopg", "Traceback", "password", "SECRET_VALIDATOR_BUG")


def _app(
    runtime_engine: Engine,
    tenant_id: UUID,
    principal_id: UUID,
    *,
    publication_authorization=None,
    publication_governance=None,
    publication_asset_validation=None,
    schema_registry=None,
):
    return create_app(
        uow_factory=SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
        security_resolver=StubSecurityContextResolver(tenant_id, principal_id),
        content_types=StaticContentTypeCatalog({"test.generic"}),
        cursor_signing_key=CURSOR_KEY,
        schema_registry=schema_registry or make_test_schema_registry(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
        review_authorization=AllowReviewAuthorization(),
        review_comment_policy=AllowReviewCommentPolicy(),
        publication_authorization=publication_authorization
        or AllowPublicationAuthorization(),
        publication_governance=publication_governance or AllowPublicationGovernance(),
        publication_asset_validation=publication_asset_validation
        or AllowPublicationAssetValidation(),
    )


def _client(runtime_engine: Engine, tenant_id: UUID, principal_id: UUID, **kw) -> TestClient:
    return TestClient(
        _app(runtime_engine, tenant_id, principal_id, **kw),
        raise_server_exceptions=False,
    )


def _assert_problem(response, *, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["code"] == code
    assert body["status"] == status
    blob = json.dumps(body)
    for needle in LEAK_NEEDLES:
        assert needle.lower() not in blob.lower()
    return body


def _publish(
    client: TestClient,
    tenant_id: UUID,
    content_id: str,
    version_id: str,
    *,
    etag: str,
    **extra: str,
):
    hdrs = headers(tenant_id, **extra)
    hdrs["If-Match"] = etag
    return client.post(
        f"/api/v1/contents/{content_id}/actions/publish",
        json={"version_id": version_id},
        headers=hdrs,
    )


def _approved(client: TestClient, tenant_id: UUID) -> tuple[str, str, str]:
    content_id, version_id, etag = in_review(client, tenant_id)
    approved = decide(
        client, tenant_id, content_id, version_id, action="approve", etag=etag
    )
    assert approved.status_code == 200, approved.text
    return content_id, version_id, approved.headers["ETag"]


def _content_row(bootstrap_engine: Engine, content_id: str | UUID):
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


def _publication_rows(bootstrap_engine: Engine, content_id: str | UUID):
    with bootstrap_engine.connect() as conn:
        return conn.execute(
            text(
                """
                SELECT publication_id, version_id, approval_decision_id
                FROM content.publications WHERE content_id = :cid
                ORDER BY published_at, publication_id
                """
            ),
            {"cid": UUID(str(content_id))},
        ).all()


def _idempotency_count(bootstrap_engine: Engine, tenant_id: UUID) -> int:
    with bootstrap_engine.connect() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT count(*) FROM api.idempotency_records "
                    "WHERE tenant_id = :tid AND operation = 'content_publish.v1'"
                ),
                {"tid": tenant_id},
            ).scalar_one()
        )


def _outbox_count(bootstrap_engine: Engine, content_id: str) -> int:
    with bootstrap_engine.connect() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT count(*) FROM integration.outbox_messages "
                    "WHERE aggregate_id = :cid"
                ),
                {"cid": content_id},
            ).scalar_one()
        )


class TestHappyPath:
    def test_publish_approved_current_version(
        self, runtime_engine, bootstrap_engine, postgres18
    ) -> None:
        assert postgres18["server_version"].startswith("18.")
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id, version_id, etag = _approved(client, tenant_id)
        before = _content_row(bootstrap_engine, content_id)
        assert before.stewardship_state == "APPROVED"
        assert before.published_version_id is None
        response = _publish(client, tenant_id, content_id, version_id, etag=etag)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["content_id"] == content_id
        assert body["version_id"] == version_id
        assert body["published_version_id"] == version_id
        assert body["aggregate_revision"] == 4
        assert UUID(body["publication_id"]).version == 7
        assert response.headers["ETag"] == encode_revision_etag(4)
        assert "tenant_id" not in body
        assert "published_by_principal_id" not in body
        row = _content_row(bootstrap_engine, content_id)
        assert row.stewardship_state == "APPROVED"
        assert row.stewardship_state != "PUBLISHED"
        assert row.current_version_id == UUID(version_id)
        assert row.published_version_id == UUID(version_id)
        assert int(row.aggregate_revision) == 4
        pubs = _publication_rows(bootstrap_engine, content_id)
        assert len(pubs) == 1
        assert pubs[0].publication_id == UUID(body["publication_id"])
        assert _idempotency_count(bootstrap_engine, tenant_id) == 1


class TestApprovalGates:
    def test_no_approval_requires_exact_approve_decision(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id, version_id, etag = generated_version(client, tenant_id)
        generated = _publish(client, tenant_id, content_id, version_id, etag=etag)
        _assert_problem(generated, status=409, code="publication_not_allowed")

        in_id, in_version, in_etag = in_review(client, tenant_id)
        in_review_pub = _publish(client, tenant_id, in_id, in_version, etag=in_etag)
        _assert_problem(in_review_pub, status=409, code="publication_not_allowed")

        forced_id, forced_version, forced_etag = generated_version(client, tenant_id)
        with bootstrap_engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE content.contents SET stewardship_state = 'APPROVED' "
                    "WHERE content_id = :cid"
                ),
                {"cid": UUID(forced_id)},
            )
        missing_decision = _publish(
            client, tenant_id, forced_id, forced_version, etag=forced_etag
        )
        _assert_problem(
            missing_decision, status=409, code="publication_approval_required"
        )
        assert _publication_rows(bootstrap_engine, forced_id) == []

    def test_request_changes_and_reject_do_not_authorize_publish(
        self, runtime_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        rc_id, rc_version, rc_etag = in_review(client, tenant_id)
        changed = decide(
            client,
            tenant_id,
            rc_id,
            rc_version,
            action="request-changes",
            etag=rc_etag,
            body={"comment": "please revise"},
        )
        assert changed.status_code == 200, changed.text
        rc_pub = _publish(
            client, tenant_id, rc_id, rc_version, etag=changed.headers["ETag"]
        )
        _assert_problem(rc_pub, status=409, code="publication_not_allowed")

        rj_id, rj_version, rj_etag = in_review(client, tenant_id)
        rejected = decide(
            client, tenant_id, rj_id, rj_version, action="reject", etag=rj_etag
        )
        assert rejected.status_code == 200, rejected.text
        rj_pub = _publish(
            client, tenant_id, rj_id, rj_version, etag=rejected.headers["ETag"]
        )
        _assert_problem(rj_pub, status=409, code="publication_not_allowed")

    def test_old_approved_version_not_current(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id, v1, etag = _approved(client, tenant_id)
        v2 = append_version(client, tenant_id, content_id, etag=etag)
        assert v2.status_code == 201, v2.text
        v2_id = v2.json()["version_id"]
        submitted = submit_review(
            client, tenant_id, content_id, v2_id, etag=v2.headers["ETag"]
        )
        assert submitted.status_code == 200, submitted.text
        approved_v2 = decide(
            client,
            tenant_id,
            content_id,
            v2_id,
            action="approve",
            etag=submitted.headers["ETag"],
        )
        assert approved_v2.status_code == 200, approved_v2.text
        stale = _publish(
            client,
            tenant_id,
            content_id,
            v1,
            etag=approved_v2.headers["ETag"],
        )
        _assert_problem(stale, status=409, code="publication_version_not_current")


class TestConcurrencyAndPreconditions:
    def test_stale_if_match_is_412(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id, version_id, _etag = _approved(client, tenant_id)
        stale = _publish(client, tenant_id, content_id, version_id, etag='"r2"')
        _assert_problem(stale, status=412, code="resource_revision_conflict")

    def test_two_keys_same_revision_one_success_one_412(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        app = _app(runtime_engine, tenant_id, principal_id)
        setup = TestClient(app, raise_server_exceptions=False)
        content_id, version_id, etag = _approved(setup, tenant_id)
        results: list = []

        def worker(key: str) -> None:
            client = TestClient(app, raise_server_exceptions=False)
            results.append(
                _publish(
                    client,
                    tenant_id,
                    content_id,
                    version_id,
                    etag=etag,
                    **{"Idempotency-Key": key},
                )
            )

        threads = [
            threading.Thread(target=worker, args=(f"a-{uuid.uuid7()}",)),
            threading.Thread(target=worker, args=(f"b-{uuid.uuid7()}",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        codes = sorted(item.status_code for item in results)
        assert codes == [200, 412]
        assert len(_publication_rows(bootstrap_engine, content_id)) == 1
        row = _content_row(bootstrap_engine, content_id)
        assert int(row.aggregate_revision) == 4

    def test_same_key_concurrent_publish(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        app = _app(runtime_engine, tenant_id, principal_id)
        setup = TestClient(app, raise_server_exceptions=False)
        content_id, version_id, etag = _approved(setup, tenant_id)
        key = f"same-{uuid.uuid7()}"
        results: list = []

        def worker() -> None:
            client = TestClient(app, raise_server_exceptions=False)
            results.append(
                _publish(
                    client,
                    tenant_id,
                    content_id,
                    version_id,
                    etag=etag,
                    **{"Idempotency-Key": key},
                )
            )

        threads = [threading.Thread(target=worker), threading.Thread(target=worker)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert [item.status_code for item in results] == [200, 200]
        assert results[0].json()["publication_id"] == results[1].json()["publication_id"]
        assert len(_publication_rows(bootstrap_engine, content_id)) == 1
        assert int(_content_row(bootstrap_engine, content_id).aggregate_revision) == 4


class TestAuthorization:
    def test_owner_without_publish_capability_is_403(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        owner = uuid.uuid7()
        owner_client = _client(runtime_engine, tenant_id, owner)
        content_id, version_id, etag = _approved(owner_client, tenant_id)
        denied = _client(
            runtime_engine,
            tenant_id,
            owner,
            publication_authorization=AllowPublicationAuthorization(allow=False),
        )
        response = _publish(denied, tenant_id, content_id, version_id, etag=etag)
        _assert_problem(response, status=403, code="forbidden")
        assert _publication_rows(bootstrap_engine, content_id) == []
        row = _content_row(bootstrap_engine, content_id)
        assert row.published_version_id is None
        assert int(row.aggregate_revision) == 3

    def test_auth_revocation_on_replay_is_403(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        auth = AllowPublicationAuthorization(allow=True)
        first_app = _app(
            runtime_engine, tenant_id, principal_id, publication_authorization=auth
        )
        first = TestClient(first_app, raise_server_exceptions=False)
        content_id, version_id, etag = _approved(first, tenant_id)
        key = f"auth-replay-{uuid.uuid7()}"
        published = _publish(
            first,
            tenant_id,
            content_id,
            version_id,
            etag=etag,
            **{"Idempotency-Key": key},
        )
        assert published.status_code == 200, published.text
        later = _client(
            runtime_engine,
            tenant_id,
            principal_id,
            publication_authorization=AllowPublicationAuthorization(allow=False),
        )
        replay = _publish(
            later,
            tenant_id,
            content_id,
            version_id,
            etag=etag,
            **{"Idempotency-Key": key},
        )
        _assert_problem(replay, status=403, code="forbidden")
        assert len(_publication_rows(bootstrap_engine, content_id)) == 1


class TestIdempotency:
    def test_same_key_replay_returns_same_publication(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id, version_id, etag = _approved(client, tenant_id)
        key = f"publish-{uuid.uuid7()}"
        first = _publish(
            client,
            tenant_id,
            content_id,
            version_id,
            etag=etag,
            **{"Idempotency-Key": key},
        )
        assert first.status_code == 200, first.text
        replay = _publish(
            client,
            tenant_id,
            content_id,
            version_id,
            etag=etag,
            **{"Idempotency-Key": key},
        )
        assert replay.status_code == 200
        assert replay.json()["publication_id"] == first.json()["publication_id"]
        assert replay.headers["ETag"] == first.headers["ETag"]
        assert len(_publication_rows(bootstrap_engine, content_id)) == 1
        assert _idempotency_count(bootstrap_engine, tenant_id) == 1

    def test_same_key_changed_version_or_if_match_is_409(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id, version_id, etag = _approved(client, tenant_id)
        key = f"reuse-{uuid.uuid7()}"
        first = _publish(
            client,
            tenant_id,
            content_id,
            version_id,
            etag=etag,
            **{"Idempotency-Key": key},
        )
        assert first.status_code == 200, first.text
        changed_version = _publish(
            client,
            tenant_id,
            content_id,
            str(uuid.uuid7()),
            etag=etag,
            **{"Idempotency-Key": key},
        )
        _assert_problem(changed_version, status=409, code="idempotency_key_reused")
        changed_if_match = _publish(
            client,
            tenant_id,
            content_id,
            version_id,
            etag='"r9"',
            **{"Idempotency-Key": key},
        )
        _assert_problem(changed_if_match, status=409, code="idempotency_key_reused")

    def test_already_published_new_key_is_409(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id, version_id, etag = _approved(client, tenant_id)
        first = _publish(client, tenant_id, content_id, version_id, etag=etag)
        assert first.status_code == 200, first.text
        second = _publish(
            client,
            tenant_id,
            content_id,
            version_id,
            etag=first.headers["ETag"],
            **{"Idempotency-Key": f"other-{uuid.uuid7()}"},
        )
        _assert_problem(second, status=409, code="content_version_already_published")
        assert len(_publication_rows(bootstrap_engine, content_id)) == 1
        assert int(_content_row(bootstrap_engine, content_id).aggregate_revision) == 4


class TestPublicationHistory:
    def test_publish_append_preserves_then_publish_v2(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id, v1, etag = _approved(client, tenant_id)
        p1 = _publish(client, tenant_id, content_id, v1, etag=etag)
        assert p1.status_code == 200, p1.text
        v2 = append_version(client, tenant_id, content_id, etag=p1.headers["ETag"])
        assert v2.status_code == 201, v2.text
        row = _content_row(bootstrap_engine, content_id)
        assert row.published_version_id == UUID(v1)
        assert row.current_version_id == UUID(v2.json()["version_id"])
        assert row.stewardship_state == "GENERATED"
        v2_id = v2.json()["version_id"]
        submitted = submit_review(
            client, tenant_id, content_id, v2_id, etag=v2.headers["ETag"]
        )
        assert submitted.status_code == 200, submitted.text
        approved = decide(
            client,
            tenant_id,
            content_id,
            v2_id,
            action="approve",
            etag=submitted.headers["ETag"],
        )
        assert approved.status_code == 200, approved.text
        p2 = _publish(
            client, tenant_id, content_id, v2_id, etag=approved.headers["ETag"]
        )
        assert p2.status_code == 200, p2.text
        pubs = _publication_rows(bootstrap_engine, content_id)
        assert len(pubs) == 2
        assert {str(p.version_id) for p in pubs} == {v1, v2_id}
        assert p1.json()["publication_id"] != p2.json()["publication_id"]
        final = _content_row(bootstrap_engine, content_id)
        assert final.published_version_id == UUID(v2_id)
        assert final.current_version_id == UUID(v2_id)
        assert final.stewardship_state == "APPROVED"


class TestAssetAndGovernance:
    def test_asset_deny_no_mutation(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        setup = _client(runtime_engine, tenant_id, principal_id)
        content_id, version_id, etag = _approved(setup, tenant_id)
        before = _content_row(bootstrap_engine, content_id)
        before_outbox = _outbox_count(bootstrap_engine, content_id)
        denied = _client(
            runtime_engine,
            tenant_id,
            principal_id,
            publication_asset_validation=AllowPublicationAssetValidation(allow=False),
        )
        response = _publish(denied, tenant_id, content_id, version_id, etag=etag)
        _assert_problem(
            response, status=409, code="publication_asset_validation_failed"
        )
        after = _content_row(bootstrap_engine, content_id)
        assert after.aggregate_revision == before.aggregate_revision
        assert after.published_version_id is None
        assert _publication_rows(bootstrap_engine, content_id) == []
        assert _idempotency_count(bootstrap_engine, tenant_id) == 0
        assert _outbox_count(bootstrap_engine, content_id) == before_outbox

    def test_governance_deny(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        setup = _client(runtime_engine, tenant_id, principal_id)
        content_id, version_id, etag = _approved(setup, tenant_id)
        denied = _client(
            runtime_engine,
            tenant_id,
            principal_id,
            publication_governance=AllowPublicationGovernance(allow=False),
        )
        response = _publish(denied, tenant_id, content_id, version_id, etag=etag)
        _assert_problem(response, status=409, code="publication_governance_rejected")
        assert _publication_rows(bootstrap_engine, content_id) == []
        assert _content_row(bootstrap_engine, content_id).published_version_id is None


class TestSchemaGates:
    def test_schema_unavailable_invalid_and_defect(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        setup = _client(runtime_engine, tenant_id, principal_id)
        content_id, version_id, etag = _approved(setup, tenant_id)

        empty = _client(
            runtime_engine,
            tenant_id,
            principal_id,
            schema_registry=ContentSchemaRegistry(),
        )
        unavailable = _publish(empty, tenant_id, content_id, version_id, etag=etag)
        _assert_problem(unavailable, status=503, code="publication_schema_unavailable")
        assert _publication_rows(bootstrap_engine, content_id) == []

        class _InvalidSchema:
            content_type = "test.generic"
            schema_id = SchemaId("test.generic")
            schema_version = SchemaVersion(1)

            def validate(self, payload: dict) -> None:
                raise InvalidPayloadError("payload drifted")

        invalid_registry = ContentSchemaRegistry()
        invalid_registry.register(_InvalidSchema())
        invalid_client = _client(
            runtime_engine,
            tenant_id,
            principal_id,
            schema_registry=invalid_registry,
        )
        invalid = _publish(invalid_client, tenant_id, content_id, version_id, etag=etag)
        _assert_problem(invalid, status=409, code="publication_payload_invalid")
        assert _publication_rows(bootstrap_engine, content_id) == []

        class _BrokenSchema:
            content_type = "test.generic"
            schema_id = SchemaId("test.generic")
            schema_version = SchemaVersion(1)

            def validate(self, payload: dict) -> None:
                raise RuntimeError("SECRET_VALIDATOR_BUG")

        broken_registry = ContentSchemaRegistry()
        broken_registry.register(_BrokenSchema())
        broken_client = _client(
            runtime_engine,
            tenant_id,
            principal_id,
            schema_registry=broken_registry,
        )
        defect = _publish(broken_client, tenant_id, content_id, version_id, etag=etag)
        assert defect.status_code == 500, defect.text
        assert "SECRET_VALIDATOR_BUG" not in defect.text
        assert _publication_rows(bootstrap_engine, content_id) == []


class TestReplaySideEffects:
    def test_replay_skips_asset_governance_schema_but_reruns_auth(
        self, runtime_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        setup = _client(runtime_engine, tenant_id, principal_id)
        content_id, version_id, etag = _approved(setup, tenant_id)

        auth = AllowPublicationAuthorization()
        asset = AllowPublicationAssetValidation()
        gov = AllowPublicationGovernance()

        class _CountingSchema:
            content_type = "test.generic"
            schema_id = SchemaId("test.generic")
            schema_version = SchemaVersion(1)
            get_calls = 0
            validate_calls = 0

            def validate(self, payload: dict) -> None:
                type(self).validate_calls += 1
                if "marker" not in payload:
                    raise InvalidPayloadError("missing marker")

        counting = _CountingSchema()
        registry = ContentSchemaRegistry()
        registry.register(counting)
        original_get = registry.get

        def counting_get(schema_id: str, schema_version: int):
            _CountingSchema.get_calls += 1
            return original_get(schema_id, schema_version)

        registry.get = counting_get  # type: ignore[method-assign]
        client = _client(
            runtime_engine,
            tenant_id,
            principal_id,
            publication_authorization=auth,
            publication_asset_validation=asset,
            publication_governance=gov,
            schema_registry=registry,
        )
        key = f"replay-side-{uuid.uuid7()}"
        first = _publish(
            client,
            tenant_id,
            content_id,
            version_id,
            etag=etag,
            **{"Idempotency-Key": key},
        )
        assert first.status_code == 200, first.text
        assert len(auth.calls) == 1
        assert len(asset.calls) == 1
        assert len(gov.calls) == 1
        assert _CountingSchema.get_calls == 1
        assert _CountingSchema.validate_calls == 1

        replay = _publish(
            client,
            tenant_id,
            content_id,
            version_id,
            etag=etag,
            **{"Idempotency-Key": key},
        )
        assert replay.status_code == 200
        assert len(auth.calls) == 2
        assert len(asset.calls) == 1
        assert len(gov.calls) == 1
        assert _CountingSchema.get_calls == 1
        assert _CountingSchema.validate_calls == 1


class TestTenantAndHeaders:
    def test_cross_tenant_publish_is_404(self, runtime_engine) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        client_a = _client(runtime_engine, tenant_a, uuid.uuid7())
        client_b = _client(runtime_engine, tenant_b, uuid.uuid7())
        content_id, version_id, etag = _approved(client_a, tenant_a)
        hidden = _publish(client_b, tenant_b, content_id, version_id, etag=etag)
        missing = _publish(
            client_a, tenant_a, str(uuid.uuid7()), str(uuid.uuid7()), etag='"r3"'
        )
        hidden_body = _assert_problem(hidden, status=404, code="content_not_found")
        missing_body = _assert_problem(missing, status=404, code="content_not_found")
        assert hidden_body["title"] == missing_body["title"]
        assert str(tenant_a) not in hidden.text

    def test_missing_if_match_and_malformed(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id, version_id, _etag = _approved(client, tenant_id)
        missing = client.post(
            f"/api/v1/contents/{content_id}/actions/publish",
            json={"version_id": version_id},
            headers=headers(tenant_id),
        )
        _assert_problem(missing, status=428, code="precondition_required")
        bad = _publish(
            client, tenant_id, content_id, version_id, etag='W/"r3"'
        )
        _assert_problem(bad, status=400, code="invalid_if_match")


class TestAtomicityAndSequence:
    def test_outbox_failure_rolls_back_publish(
        self, runtime_engine, bootstrap_engine, monkeypatch
    ) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id, version_id, etag = _approved(client, tenant_id)
        before = _content_row(bootstrap_engine, content_id)
        before_outbox = _outbox_count(bootstrap_engine, content_id)

        def boom(self, message) -> None:
            raise PersistenceOperationFailed("inject outbox insert failure")

        monkeypatch.setattr(SqlAlchemyOutboxRepository, "insert", boom)
        response = _publish(client, tenant_id, content_id, version_id, etag=etag)
        assert response.status_code == 503, response.text
        after = _content_row(bootstrap_engine, content_id)
        assert after.published_version_id is None
        assert int(after.aggregate_revision) == int(before.aggregate_revision)
        assert after.stewardship_state == "APPROVED"
        assert _publication_rows(bootstrap_engine, content_id) == []
        assert _idempotency_count(bootstrap_engine, tenant_id) == 0
        assert _outbox_count(bootstrap_engine, content_id) == before_outbox

    def test_event_sequence_create_append_submit_approve_publish(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        created = create_content(client, tenant_id)
        content_id = created["content_id"]
        appended = append_version(client, tenant_id, content_id, etag='"r0"')
        assert appended.status_code == 201, appended.text
        version_id = appended.json()["version_id"]
        submitted = submit_review(
            client, tenant_id, content_id, version_id, etag=appended.headers["ETag"]
        )
        assert submitted.status_code == 200, submitted.text
        approved = decide(
            client,
            tenant_id,
            content_id,
            version_id,
            action="approve",
            etag=submitted.headers["ETag"],
        )
        assert approved.status_code == 200, approved.text
        published = _publish(
            client,
            tenant_id,
            content_id,
            version_id,
            etag=approved.headers["ETag"],
        )
        assert published.status_code == 200, published.text
        with bootstrap_engine.connect() as conn:
            revisions = [
                int(row[0])
                for row in conn.execute(
                    text(
                        """
                        SELECT aggregate_revision FROM integration.outbox_messages
                        WHERE aggregate_id = :cid
                        ORDER BY aggregate_revision, created_at, event_id
                        """
                    ),
                    {"cid": content_id},
                )
            ]
        assert revisions == [0, 1, 2, 3, 4]
        assert published.headers["ETag"] == encode_revision_etag(4)
