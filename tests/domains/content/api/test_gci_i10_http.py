"""GCI-I10 append asset_refs HTTP and publish current-use governance."""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.domains.teaching.infrastructure.persistence.uow import (
    SqlAlchemyTeachingUnitOfWorkFactory,
)
from aieos.platform.api.app import create_app
from tests.fakes import (
    AllowAssetCurrentGovernance,
    AllowAssetReferenceValidation,
    AllowPublicationAuthorization,
    AllowPublicationGovernance,
    AllowReviewAuthorization,
    AllowReviewCommentPolicy,
    IDEMPOTENCY_RETENTION,
    FixedPrincipalAuthenticator,
    StubSecurityContextResolver,
    make_test_schema_registry,
)
from tests.platform.workflows.helpers import (
    create_content,
    decide,
    headers,
    submit_review,
)

pytestmark = pytest.mark.gci_i10

CURSOR_KEY = b"gci-i10-test-cursor-signing-key"


def _app(
    runtime_engine: Engine,
    tenant_id: UUID,
    principal_id: UUID,
    *,
    asset_reference_validation=None,
    asset_current_governance=None,
    publication_authorization=None,
):
    return create_app(
        uow_factory=SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
        teaching_uow_factory=SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine),
        request_identity_authenticator=FixedPrincipalAuthenticator(principal_id),
        security_resolver=StubSecurityContextResolver(tenant_id, principal_id),
        content_types=StaticContentTypeCatalog({"test.generic"}),
        cursor_signing_key=CURSOR_KEY,
        schema_registry=make_test_schema_registry(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
        review_authorization=AllowReviewAuthorization(),
        review_comment_policy=AllowReviewCommentPolicy(),
        publication_authorization=publication_authorization
        or AllowPublicationAuthorization(),
        publication_governance=AllowPublicationGovernance(),
        asset_reference_validation=asset_reference_validation
        or AllowAssetReferenceValidation(),
        asset_current_governance=asset_current_governance
        or AllowAssetCurrentGovernance(),
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
    return body


def _asset_ref_body(*, resource_id: UUID | None = None, role: str = "primary", ordinal: int = 0):
    return {
        "resource_ref": {
            "resource_type": "asset.image",
            "resource_id": str(resource_id or uuid.uuid7()),
            "resource_revision": None,
        },
        "role": role,
        "ordinal": ordinal,
        "required": True,
    }


def _append_with_refs(
    client: TestClient,
    tenant_id: UUID,
    content_id: str,
    *,
    etag: str,
    refs: list,
    idempotency_key: str | None = None,
):
    extra = {}
    if idempotency_key is not None:
        extra["Idempotency-Key"] = idempotency_key
    hdrs = headers(tenant_id, **extra)
    hdrs["If-Match"] = etag
    return client.post(
        f"/api/v1/contents/{content_id}/versions",
        json={
            "schema_id": "test.generic",
            "schema_version": 1,
            "payload": {"marker": "v1"},
            "asset_refs": refs,
        },
        headers=hdrs,
    )


def _ref_rows(bootstrap_engine: Engine, content_id: str) -> list:
    with bootstrap_engine.connect() as conn:
        return list(
            conn.execute(
                text(
                    """
                    SELECT role, ordinal, asset_resource_id, required
                    FROM content.version_asset_refs
                    WHERE content_id = :cid
                    ORDER BY role, ordinal
                    """
                ),
                {"cid": content_id},
            ).mappings()
        )


def _content_snapshot(bootstrap_engine: Engine, content_id: str):
    with bootstrap_engine.connect() as conn:
        return conn.execute(
            text(
                """
                SELECT published_version_id, aggregate_revision, stewardship_state,
                       current_version_id
                FROM content.contents WHERE content_id = :cid
                """
            ),
            {"cid": content_id},
        ).one()


class TestAppendAssetRefsHttp:
    def test_append_persists_asset_refs(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, principal_id)
        created = create_content(client, tenant_id)
        content_id = created["content_id"]
        rid = uuid.uuid7()
        response = _append_with_refs(
            client,
            tenant_id,
            content_id,
            etag='"r0"',
            refs=[_asset_ref_body(resource_id=rid)],
        )
        assert response.status_code == 201, response.text
        rows = _ref_rows(bootstrap_engine, content_id)
        assert len(rows) == 1
        assert rows[0]["role"] == "primary"
        assert rows[0]["asset_resource_id"] == rid

    def test_duplicate_slot_is_422(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id = create_content(client, tenant_id)["content_id"]
        response = _append_with_refs(
            client,
            tenant_id,
            content_id,
            etag='"r0"',
            refs=[
                _asset_ref_body(role="primary", ordinal=0),
                _asset_ref_body(role="primary", ordinal=0),
            ],
        )
        _assert_problem(response, status=422, code="asset_reference_invalid")
        assert _ref_rows(bootstrap_engine, content_id) == []

    def test_binding_deny_is_422(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        denied = uuid.uuid7()
        client = _client(
            runtime_engine,
            tenant_id,
            uuid.uuid7(),
            asset_reference_validation=AllowAssetReferenceValidation(deny_ids={denied}),
        )
        content_id = create_content(client, tenant_id)["content_id"]
        response = _append_with_refs(
            client,
            tenant_id,
            content_id,
            etag='"r0"',
            refs=[_asset_ref_body(resource_id=denied)],
        )
        _assert_problem(response, status=422, code="asset_reference_invalid")
        assert _ref_rows(bootstrap_engine, content_id) == []

    def test_binding_runtime_error_is_sanitized_500(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        client = _client(
            runtime_engine,
            tenant_id,
            uuid.uuid7(),
            asset_reference_validation=AllowAssetReferenceValidation(raise_runtime=True),
        )
        content_id = create_content(client, tenant_id)["content_id"]
        response = _append_with_refs(
            client,
            tenant_id,
            content_id,
            etag='"r0"',
            refs=[_asset_ref_body()],
        )
        assert response.status_code == 500
        body = response.json()
        assert "SECRET_ASSET_VALIDATOR_BUG" not in response.text
        assert body["code"] == "internal_error"


class TestPublishCurrentUse:
    def test_quarantined_asset_blocks_publish(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        quarantined = uuid.uuid7()
        client = _client(
            runtime_engine,
            tenant_id,
            principal_id,
            asset_current_governance=AllowAssetCurrentGovernance(
                quarantined_ids={quarantined}
            ),
        )
        content_id = create_content(client, tenant_id)["content_id"]
        appended = _append_with_refs(
            client,
            tenant_id,
            content_id,
            etag='"r0"',
            refs=[_asset_ref_body(resource_id=quarantined)],
        )
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
        hdrs = headers(tenant_id)
        hdrs["If-Match"] = approved.headers["ETag"]
        published = client.post(
            f"/api/v1/contents/{content_id}/actions/publish",
            json={"version_id": version_id},
            headers=hdrs,
        )
        _assert_problem(
            published, status=409, code="publication_asset_validation_failed"
        )


class TestAppendIdempotencyAndNoAssets:
    def test_append_without_asset_refs_still_works(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id = create_content(client, tenant_id)["content_id"]
        response = _append_with_refs(
            client, tenant_id, content_id, etag='"r0"', refs=[]
        )
        assert response.status_code == 201, response.text
        assert _ref_rows(bootstrap_engine, content_id) == []

    def test_semantic_array_order_same_fingerprint(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id = create_content(client, tenant_id)["content_id"]
        a = uuid.uuid7()
        b = uuid.uuid7()
        key = f"order-{uuid.uuid7()}"
        first = _append_with_refs(
            client,
            tenant_id,
            content_id,
            etag='"r0"',
            refs=[
                _asset_ref_body(resource_id=a, role="attachment", ordinal=0),
                _asset_ref_body(resource_id=b, role="attachment", ordinal=1),
            ],
            idempotency_key=key,
        )
        assert first.status_code == 201, first.text
        replay = _append_with_refs(
            client,
            tenant_id,
            content_id,
            etag='"r0"',
            refs=[
                _asset_ref_body(resource_id=b, role="attachment", ordinal=1),
                _asset_ref_body(resource_id=a, role="attachment", ordinal=0),
            ],
            idempotency_key=key,
        )
        assert replay.status_code == 201, replay.text
        assert replay.json()["version_id"] == first.json()["version_id"]
        assert len(_ref_rows(bootstrap_engine, content_id)) == 2

    def test_same_key_changed_resource_ref_is_409(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id = create_content(client, tenant_id)["content_id"]
        key = f"changed-ref-{uuid.uuid7()}"
        first = _append_with_refs(
            client,
            tenant_id,
            content_id,
            etag='"r0"',
            refs=[_asset_ref_body(resource_id=uuid.uuid7())],
            idempotency_key=key,
        )
        assert first.status_code == 201, first.text
        changed = _append_with_refs(
            client,
            tenant_id,
            content_id,
            etag='"r0"',
            refs=[_asset_ref_body(resource_id=uuid.uuid7())],
            idempotency_key=key,
        )
        _assert_problem(changed, status=409, code="idempotency_key_reused")


class TestPublishValidAndReplay:
    def test_publish_with_valid_persisted_refs(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id = create_content(client, tenant_id)["content_id"]
        appended = _append_with_refs(
            client,
            tenant_id,
            content_id,
            etag='"r0"',
            refs=[_asset_ref_body()],
        )
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
        hdrs = headers(tenant_id)
        hdrs["If-Match"] = approved.headers["ETag"]
        published = client.post(
            f"/api/v1/contents/{content_id}/actions/publish",
            json={"version_id": version_id},
            headers=hdrs,
        )
        assert published.status_code == 200, published.text
        with bootstrap_engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT published_version_id, aggregate_revision, stewardship_state
                    FROM content.contents WHERE content_id = :cid
                    """
                ),
                {"cid": content_id},
            ).one()
        assert str(row.published_version_id) == version_id
        assert int(row.aggregate_revision) == 4
        assert row.stewardship_state == "APPROVED"

    def test_publish_replay_skips_current_asset_governance(
        self, runtime_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        gov = AllowAssetCurrentGovernance()
        auth = AllowPublicationAuthorization()
        client = _client(
            runtime_engine,
            tenant_id,
            principal_id,
            asset_current_governance=gov,
            publication_authorization=auth,
        )
        content_id = create_content(client, tenant_id)["content_id"]
        appended = _append_with_refs(
            client, tenant_id, content_id, etag='"r0"', refs=[_asset_ref_body()]
        )
        version_id = appended.json()["version_id"]
        submitted = submit_review(
            client, tenant_id, content_id, version_id, etag=appended.headers["ETag"]
        )
        approved = decide(
            client,
            tenant_id,
            content_id,
            version_id,
            action="approve",
            etag=submitted.headers["ETag"],
        )
        key = f"pub-replay-{uuid.uuid7()}"
        hdrs = headers(tenant_id, **{"Idempotency-Key": key})
        hdrs["If-Match"] = approved.headers["ETag"]
        first = client.post(
            f"/api/v1/contents/{content_id}/actions/publish",
            json={"version_id": version_id},
            headers=hdrs,
        )
        assert first.status_code == 200, first.text
        assert len(gov.calls) == 1
        assert len(auth.calls) == 1
        replay = client.post(
            f"/api/v1/contents/{content_id}/actions/publish",
            json={"version_id": version_id},
            headers=hdrs,
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["publication_id"] == first.json()["publication_id"]
        assert len(gov.calls) == 1
        assert len(auth.calls) == 2


class TestGCIG11PostPublicationQuarantine:
    def test_current_use_fails_after_quarantine_without_mutating_publication(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        from aieos.domains.content.application.asset_governance import (
            ValidateVersionAssetGovernanceService,
        )
        from aieos.domains.content.application.errors import (
            PublicationAssetValidationFailed,
        )
        from aieos.domains.content.domain.identities import ContentId, ContentVersionId
        from aieos.domains.content.infrastructure.persistence.uow import (
            SqlAlchemyContentUnitOfWorkFactory,
        )

        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        asset_id = uuid.uuid7()
        gov = AllowAssetCurrentGovernance()
        client = _client(
            runtime_engine,
            tenant_id,
            principal_id,
            asset_current_governance=gov,
        )
        content_id = create_content(client, tenant_id)["content_id"]
        appended = _append_with_refs(
            client,
            tenant_id,
            content_id,
            etag='"r0"',
            refs=[_asset_ref_body(resource_id=asset_id)],
        )
        assert appended.status_code == 201, appended.text
        version_id = appended.json()["version_id"]
        submitted = submit_review(
            client, tenant_id, content_id, version_id, etag=appended.headers["ETag"]
        )
        approved = decide(
            client,
            tenant_id,
            content_id,
            version_id,
            action="approve",
            etag=submitted.headers["ETag"],
        )
        hdrs = headers(tenant_id)
        hdrs["If-Match"] = approved.headers["ETag"]
        published = client.post(
            f"/api/v1/contents/{content_id}/actions/publish",
            json={"version_id": version_id},
            headers=hdrs,
        )
        assert published.status_code == 200, published.text
        publication_id = published.json()["publication_id"]
        before = _content_snapshot(bootstrap_engine, content_id)
        refs_before = _ref_rows(bootstrap_engine, content_id)

        gov.quarantined_ids.add(asset_id)
        service = ValidateVersionAssetGovernanceService(
            SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
            gov,
        )
        with pytest.raises(PublicationAssetValidationFailed):
            service.validate(
                tenant_id,
                principal_id,
                ContentId(UUID(content_id)),
                ContentVersionId(UUID(version_id)),
            )

        after = _content_snapshot(bootstrap_engine, content_id)
        assert after.published_version_id == before.published_version_id
        assert str(after.published_version_id) == version_id
        assert int(after.aggregate_revision) == int(before.aggregate_revision)
        assert after.stewardship_state == before.stewardship_state
        with bootstrap_engine.connect() as conn:
            pub = conn.execute(
                text(
                    "SELECT publication_id FROM content.publications "
                    "WHERE content_id = :cid"
                ),
                {"cid": content_id},
            ).one()
        assert str(pub.publication_id) == publication_id
        assert [
            (r["role"], r["ordinal"], str(r["asset_resource_id"]), r["required"])
            for r in _ref_rows(bootstrap_engine, content_id)
        ] == [
            (r["role"], r["ordinal"], str(r["asset_resource_id"]), r["required"])
            for r in refs_before
        ]


def _version_count(bootstrap_engine: Engine, content_id: str) -> int:
    with bootstrap_engine.connect() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT count(*) FROM content.content_versions WHERE content_id = :cid"
                ),
                {"cid": content_id},
            ).scalar_one()
        )


def _version_created_count(bootstrap_engine: Engine, content_id: str) -> int:
    with bootstrap_engine.connect() as conn:
        return int(
            conn.execute(
                text(
                    """
                    SELECT count(*) FROM integration.outbox_messages
                    WHERE aggregate_id = :cid
                      AND event_type = 'io.eduvijna.aieos.content.content.version_created.v1'
                    """
                ),
                {"cid": content_id},
            ).scalar_one()
        )


def _append_idempotency_success_count(
    bootstrap_engine: Engine, tenant_id: UUID
) -> int:
    with bootstrap_engine.connect() as conn:
        return int(
            conn.execute(
                text(
                    """
                    SELECT count(*) FROM api.idempotency_records
                    WHERE tenant_id = :tid
                      AND operation = 'content_version_append.v1'
                    """
                ),
                {"tid": tenant_id},
            ).scalar_one()
        )


class TestStrictAssetRefScalarsHttp:
    @pytest.mark.parametrize(
        "mutate",
        [
            pytest.param(
                lambda body: body["resource_ref"].__setitem__(
                    "resource_revision", True
                ),
                id="resource_revision_true",
            ),
            pytest.param(
                lambda body: body["resource_ref"].__setitem__(
                    "resource_revision", "7"
                ),
                id="resource_revision_string",
            ),
            pytest.param(
                lambda body: body.__setitem__("ordinal", True),
                id="ordinal_true",
            ),
            pytest.param(
                lambda body: body.__setitem__("ordinal", "0"),
                id="ordinal_string",
            ),
            pytest.param(
                lambda body: body.__setitem__("required", 1),
                id="required_int_one",
            ),
            pytest.param(
                lambda body: body.__setitem__("required", "true"),
                id="required_string_true",
            ),
        ],
    )
    def test_malformed_scalars_are_422_validation_error_without_side_effects(
        self, runtime_engine, bootstrap_engine, mutate
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        binding = AllowAssetReferenceValidation()
        client = _client(
            runtime_engine,
            tenant_id,
            principal_id,
            asset_reference_validation=binding,
        )
        content_id = create_content(client, tenant_id)["content_id"]
        before = _content_snapshot(bootstrap_engine, content_id)
        assert _version_count(bootstrap_engine, content_id) == 0
        assert _version_created_count(bootstrap_engine, content_id) == 0
        assert _append_idempotency_success_count(bootstrap_engine, tenant_id) == 0

        ref = _asset_ref_body()
        mutate(ref)
        key = f"strict-{uuid.uuid7()}"
        response = _append_with_refs(
            client,
            tenant_id,
            content_id,
            etag='"r0"',
            refs=[ref],
            idempotency_key=key,
        )
        _assert_problem(response, status=422, code="validation_error")
        assert binding.calls == []
        assert _ref_rows(bootstrap_engine, content_id) == []
        assert _version_count(bootstrap_engine, content_id) == 0
        after = _content_snapshot(bootstrap_engine, content_id)
        assert after.current_version_id == before.current_version_id
        assert int(after.aggregate_revision) == int(before.aggregate_revision)
        assert after.stewardship_state == before.stewardship_state
        assert _version_created_count(bootstrap_engine, content_id) == 0
        assert _append_idempotency_success_count(bootstrap_engine, tenant_id) == 0


class TestVersionAssetRefInsertAtomicity:
    def test_insert_many_failure_rolls_back_version_and_head(
        self, runtime_engine, bootstrap_engine, monkeypatch
    ) -> None:
        from aieos.domains.content.application.errors import PersistenceOperationFailed
        from aieos.domains.content.infrastructure.persistence.repositories import (
            SqlAlchemyVersionAssetRefRepository,
        )

        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        binding = AllowAssetReferenceValidation()
        client = _client(
            runtime_engine,
            tenant_id,
            principal_id,
            asset_reference_validation=binding,
        )
        content_id = create_content(client, tenant_id)["content_id"]
        before = _content_snapshot(bootstrap_engine, content_id)
        version_insert_seen = {"called": False}

        def boom(self, refs):
            version_insert_seen["called"] = True
            assert len(refs) >= 1
            raise PersistenceOperationFailed(
                "injected VersionAssetRef insert failure"
            )

        monkeypatch.setattr(
            SqlAlchemyVersionAssetRefRepository, "insert_many", boom
        )
        key = f"partial-{uuid.uuid7()}"
        response = _append_with_refs(
            client,
            tenant_id,
            content_id,
            etag='"r0"',
            refs=[_asset_ref_body()],
            idempotency_key=key,
        )
        _assert_problem(response, status=503, code="persistence_unavailable")
        assert version_insert_seen["called"] is True
        assert len(binding.calls) == 1
        assert _ref_rows(bootstrap_engine, content_id) == []
        assert _version_count(bootstrap_engine, content_id) == 0
        after = _content_snapshot(bootstrap_engine, content_id)
        assert after.current_version_id == before.current_version_id
        assert int(after.aggregate_revision) == int(before.aggregate_revision)
        assert after.stewardship_state == before.stewardship_state
        assert _version_created_count(bootstrap_engine, content_id) == 0
        assert _append_idempotency_success_count(bootstrap_engine, tenant_id) == 0
