"""GCI-I12 Teacher OS Review Queue read projection HTTP tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.application.ai_materialization import (
    MaterializeAIGeneratedContentVersionService,
)
from aieos.domains.content.application.models import AIGeneratedVersionMaterializationCommand
from aieos.domains.content.domain.identities import AggregateRevision, ContentId
from aieos.domains.content.domain.provenance import AIGenerationProvenanceV1
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.platform.api.app import create_app
from aieos.platform.api.etag import encode_revision_etag
from aieos.platform.events.models import MutationEventContext
from aieos.platform.resources import ResourceRef
from aieos.domains.content.application.audit import ai_materialization_audit_provenance
from tests.fakes import (
    AllowAIGenerationAuthorization,
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

pytestmark = pytest.mark.gci_i12

CURSOR_KEY = b"gci-i12-test-cursor-signing-key"
FIXED_NOW = datetime(2026, 8, 14, 22, 0, tzinfo=UTC)


def _app(runtime_engine: Engine, tenant_id: UUID, principal_id: UUID):
    return create_app(
        uow_factory=SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
        request_identity_authenticator=FixedPrincipalAuthenticator(principal_id),
        security_resolver=StubSecurityContextResolver(tenant_id, principal_id),
        content_types=StaticContentTypeCatalog({"test.generic"}),
        cursor_signing_key=CURSOR_KEY,
        schema_registry=make_test_schema_registry(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
        review_authorization=AllowReviewAuthorization(),
        review_comment_policy=AllowReviewCommentPolicy(),
        publication_authorization=AllowPublicationAuthorization(),
        publication_governance=AllowPublicationGovernance(),
        asset_reference_validation=AllowAssetReferenceValidation(),
        asset_current_governance=AllowAssetCurrentGovernance(),
    )


def _client(runtime_engine: Engine, tenant_id: UUID, principal_id: UUID) -> TestClient:
    return TestClient(
        _app(runtime_engine, tenant_id, principal_id),
        raise_server_exceptions=False,
    )


def _assert_problem(response, *, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["code"] == code
    return body


def _queue_list(client: TestClient, tenant_id: UUID, **params):
    return client.get(
        "/api/v1/teacher-os/review-queue",
        params=params or None,
        headers=headers(tenant_id),
    )


def _queue_get(client: TestClient, tenant_id: UUID, content_id: str, version_id: str):
    return client.get(
        f"/api/v1/teacher-os/review-queue/{content_id}/versions/{version_id}",
        headers=headers(tenant_id),
    )


def _append(client: TestClient, tenant_id: UUID, content_id: str, *, etag: str, marker: str = "v1"):
    hdrs = headers(tenant_id)
    hdrs["If-Match"] = etag
    return client.post(
        f"/api/v1/contents/{content_id}/versions",
        json={
            "schema_id": "test.generic",
            "schema_version": 1,
            "payload": {"marker": marker},
        },
        headers=hdrs,
    )


def _in_review_item(client: TestClient, tenant_id: UUID, *, marker: str = "v1"):
    created = create_content(client, tenant_id)
    content_id = created["content_id"]
    appended = _append(client, tenant_id, content_id, etag='"r0"', marker=marker)
    assert appended.status_code == 201, appended.text
    version_id = appended.json()["version_id"]
    submitted = submit_review(
        client, tenant_id, content_id, version_id, etag=appended.headers["ETag"]
    )
    assert submitted.status_code == 200, submitted.text
    return content_id, version_id, submitted.headers["ETag"]


class TestQueueEligibility:
    def test_draft_and_generated_excluded(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        created = create_content(client, tenant_id)
        content_id = created["content_id"]
        listed = _queue_list(client, tenant_id)
        assert listed.status_code == 200
        assert listed.json()["items"] == []
        appended = _append(client, tenant_id, content_id, etag='"r0"')
        assert appended.status_code == 201
        listed = _queue_list(client, tenant_id)
        assert listed.json()["items"] == []

    def test_in_review_included_and_exact_version(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id, version_id, _ = _in_review_item(client, tenant_id)
        listed = _queue_list(client, tenant_id)
        assert listed.status_code == 200
        items = listed.json()["items"]
        assert len(items) == 1
        item = items[0]
        assert item["content_id"] == content_id
        assert item["version_id"] == version_id
        assert item["artifact_status"] == "In Review"
        assert item["origin"] == "HUMAN"
        assert "payload" not in item
        assert "provenance" not in item
        assert "tenant_id" not in item
        assert "comment" not in item

    def test_approve_request_changes_reject_remove_item(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        for action in ("approve", "request-changes", "reject"):
            content_id, version_id, etag = _in_review_item(
                client, tenant_id, marker=f"{action}-{uuid.uuid7()}"
            )
            assert len(_queue_list(client, tenant_id).json()["items"]) >= 1
            body = {"comment": "needs revision"} if action == "request-changes" else None
            decided = decide(
                client,
                tenant_id,
                content_id,
                version_id,
                action=action,
                etag=etag,
                body=body,
            )
            assert decided.status_code == 200, decided.text
            remaining = [
                i
                for i in _queue_list(client, tenant_id).json()["items"]
                if i["content_id"] == content_id
            ]
            assert remaining == []
            detail = _queue_get(client, tenant_id, content_id, version_id)
            _assert_problem(detail, status=404, code="review_queue_item_not_found")

    def test_approved_and_archived_excluded(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id, version_id, etag = _in_review_item(client, tenant_id)
        approved = decide(
            client, tenant_id, content_id, version_id, action="approve", etag=etag
        )
        assert approved.status_code == 200
        assert _queue_list(client, tenant_id).json()["items"] == []
        archived_id = create_content(client, tenant_id)["content_id"]
        with bootstrap_engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE content.contents SET stewardship_state = 'ARCHIVED', "
                    "archived_at = now() WHERE content_id = :cid"
                ),
                {"cid": archived_id},
            )
        assert all(
            i["content_id"] != archived_id
            for i in _queue_list(client, tenant_id).json()["items"]
        )

    def test_published_old_current_in_review_included(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id, v1, etag = _in_review_item(client, tenant_id, marker="pub-v1")
        approved = decide(
            client, tenant_id, content_id, v1, action="approve", etag=etag
        )
        hdrs = headers(tenant_id)
        hdrs["If-Match"] = approved.headers["ETag"]
        published = client.post(
            f"/api/v1/contents/{content_id}/actions/publish",
            json={"version_id": v1},
            headers=hdrs,
        )
        assert published.status_code == 200, published.text
        appended = _append(
            client, tenant_id, content_id, etag=published.headers["ETag"], marker="v2"
        )
        assert appended.status_code == 201, appended.text
        v2 = appended.json()["version_id"]
        submitted = submit_review(
            client, tenant_id, content_id, v2, etag=appended.headers["ETag"]
        )
        assert submitted.status_code == 200, submitted.text
        items = _queue_list(client, tenant_id).json()["items"]
        assert len(items) == 1
        assert items[0]["version_id"] == v2
        assert items[0]["published_version_id"] == v1

    def test_historical_negative_does_not_block_v2(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id, v1, etag = _in_review_item(client, tenant_id, marker="hist-v1")
        changed = decide(
            client,
            tenant_id,
            content_id,
            v1,
            action="request-changes",
            etag=etag,
            body={"comment": "revise v1"},
        )
        assert changed.status_code == 200
        appended = _append(
            client, tenant_id, content_id, etag=changed.headers["ETag"], marker="hist-v2"
        )
        v2 = appended.json()["version_id"]
        submitted = submit_review(
            client, tenant_id, content_id, v2, etag=appended.headers["ETag"]
        )
        assert submitted.status_code == 200
        items = _queue_list(client, tenant_id).json()["items"]
        assert [i["version_id"] for i in items if i["content_id"] == content_id] == [v2]


class TestQueueDetail:
    def test_detail_etag_and_payload(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id, version_id, etag = _in_review_item(client, tenant_id)
        detail = _queue_get(client, tenant_id, content_id, version_id)
        assert detail.status_code == 200, detail.text
        body = detail.json()
        assert body["payload"] == {"marker": "v1"}
        assert "provenance" not in body
        assert detail.headers["ETag"] == etag
        assert detail.headers["ETag"] == encode_revision_etag(body["aggregate_revision"])

    def test_stale_version_and_cross_content_404(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id, version_id, etag = _in_review_item(client, tenant_id, marker="a")
        other_id, other_vid, _ = _in_review_item(client, tenant_id, marker="b")
        _assert_problem(
            _queue_get(client, tenant_id, content_id, other_vid),
            status=404,
            code="review_queue_item_not_found",
        )
        decided = decide(
            client, tenant_id, content_id, version_id, action="approve", etag=etag
        )
        appended = _append(
            client, tenant_id, content_id, etag=decided.headers["ETag"], marker="later"
        )
        # old version no longer current / not IN_REVIEW
        _assert_problem(
            _queue_get(client, tenant_id, content_id, version_id),
            status=404,
            code="review_queue_item_not_found",
        )
        assert appended.status_code == 201


class TestQueueTenancyAndPagination:
    def test_tenant_isolation_and_cursor(self, runtime_engine) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        client_a = _client(runtime_engine, tenant_a, uuid.uuid7())
        client_b = _client(runtime_engine, tenant_b, uuid.uuid7())
        content_a, version_a, _ = _in_review_item(client_a, tenant_a, marker="ta")
        content_b, version_b, _ = _in_review_item(client_b, tenant_b, marker="tb")
        items_a = _queue_list(client_a, tenant_a).json()["items"]
        assert [i["content_id"] for i in items_a] == [content_a]
        _assert_problem(
            _queue_get(client_a, tenant_a, content_b, version_b),
            status=404,
            code="review_queue_item_not_found",
        )
        cursor = _queue_list(client_a, tenant_a, limit=1).json().get("next_cursor")
        # single item → no next cursor; create second for cursor bind test
        _in_review_item(client_a, tenant_a, marker="ta2")
        page = _queue_list(client_a, tenant_a, limit=1)
        next_cursor = page.json()["next_cursor"]
        assert next_cursor
        bad = _queue_list(client_b, tenant_b, cursor=next_cursor)
        _assert_problem(bad, status=400, code="invalid_cursor")

    def test_limit_bounds_and_order(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        over = _queue_list(client, tenant_id, limit=101)
        _assert_problem(over, status=400, code="invalid_content_request")
        zero = _queue_list(client, tenant_id, limit=0)
        _assert_problem(zero, status=400, code="invalid_content_request")
        negative = _queue_list(client, tenant_id, limit=-1)
        _assert_problem(negative, status=400, code="invalid_content_request")
        malformed = client.get(
            "/api/v1/teacher-os/review-queue",
            params={"limit": "abc"},
            headers=headers(tenant_id),
        )
        _assert_problem(malformed, status=422, code="validation_error")
        assert _queue_list(client, tenant_id).status_code == 200
        assert _queue_list(client, tenant_id, limit=1).status_code == 200
        assert _queue_list(client, tenant_id, limit=100).status_code == 200
        ids = []
        for i in range(3):
            cid, vid, _ = _in_review_item(client, tenant_id, marker=f"ord-{i}")
            ids.append(cid)
            with bootstrap_engine.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE content.contents SET updated_at = :ts "
                        "WHERE content_id = :cid"
                    ),
                    {
                        "cid": cid,
                        "ts": FIXED_NOW + timedelta(seconds=i),
                    },
                )
        listed = _queue_list(client, tenant_id, limit=2)
        assert listed.status_code == 200
        body = listed.json()
        assert [i["content_id"] for i in body["items"]] == ids[:2]
        assert body["next_cursor"]
        page2 = _queue_list(client, tenant_id, limit=2, cursor=body["next_cursor"])
        assert [i["content_id"] for i in page2.json()["items"]] == ids[2:]
        assert page2.json()["next_cursor"] is None

    def test_list_service_enforces_limit_range(self) -> None:
        from aieos.domains.content.application.errors import ReviewQueueInvalidRequest
        from aieos.domains.content.application.review_queue import (
            ListTeacherReviewQueueService,
        )
        from aieos.domains.content.application.review_queue_models import (
            ListTeacherReviewQueueQuery,
        )

        class _UnusedFactory:
            def __call__(self, execution_tenant_id):  # pragma: no cover
                raise AssertionError("factory must not be called for invalid limits")

        service = ListTeacherReviewQueueService(_UnusedFactory())  # type: ignore[arg-type]
        tenant_id = uuid.uuid7()
        for bad in (0, -1, 101):
            with pytest.raises(ReviewQueueInvalidRequest):
                service.list(
                    tenant_id,
                    ListTeacherReviewQueueQuery(limit=bad),
                )

    def test_no_outbox_or_idempotency_side_effects(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id, version_id, _ = _in_review_item(client, tenant_id)
        with bootstrap_engine.connect() as conn:
            before_outbox = conn.execute(
                text("SELECT count(*) FROM integration.outbox_messages")
            ).scalar_one()
            before_idem = conn.execute(
                text(
                    "SELECT count(*) FROM api.idempotency_records WHERE tenant_id = :tid"
                ),
                {"tid": tenant_id},
            ).scalar_one()
        assert _queue_list(client, tenant_id).status_code == 200
        assert _queue_get(client, tenant_id, content_id, version_id).status_code == 200
        with bootstrap_engine.connect() as conn:
            after_outbox = conn.execute(
                text("SELECT count(*) FROM integration.outbox_messages")
            ).scalar_one()
            after_idem = conn.execute(
                text(
                    "SELECT count(*) FROM api.idempotency_records WHERE tenant_id = :tid"
                ),
                {"tid": tenant_id},
            ).scalar_one()
        assert int(after_outbox) == int(before_outbox)
        assert int(after_idem) == int(before_idem)


class TestQueueAIAndArchitecture:
    def test_ai_origin_in_queue(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, principal_id)
        content_id = ContentId(UUID(create_content(client, tenant_id)["content_id"]))
        correlation_id = uuid.uuid7()
        result = MaterializeAIGeneratedContentVersionService(
            SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
            make_test_schema_registry(),
            AllowAssetReferenceValidation(),
            AllowAIGenerationAuthorization(),
        ).materialize(
            tenant_id,
            principal_id,
            AIGeneratedVersionMaterializationCommand(
                content_id=content_id,
                expected_aggregate_revision=AggregateRevision(0),
                schema_id="test.generic",
                schema_version=1,
                payload={"marker": "ai-q"},
                provenance=AIGenerationProvenanceV1(
                    generation_run_ref=ResourceRef("generation.run", uuid.uuid7(), None),
                    prompt_execution_ref=None,
                    provider_id="test.provider",
                    model_id="neutral-model",
                    capability_id="content.generate.lesson",
                    source_refs=(),
                    policy_refs=(),
                    evaluation_refs=(),
                    correlation_id=correlation_id,
                ),
            ),
            event_context=MutationEventContext(
                correlation_id=correlation_id,
                causation_id=uuid.uuid7(),
                actor_principal_id=principal_id,
                effective_actor_id=principal_id,
            ),
            audit_provenance=ai_materialization_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        version_id = str(result.version_id.value)
        submitted = submit_review(
            client, tenant_id, str(content_id.value), version_id, etag='"r1"'
        )
        assert submitted.status_code == 200, submitted.text
        items = _queue_list(client, tenant_id).json()["items"]
        match = [i for i in items if i["version_id"] == version_id]
        assert len(match) == 1
        assert match[0]["origin"] == "AI"

    def test_application_layer_has_no_temporal_nats(self) -> None:
        from pathlib import Path

        root = Path(__file__).resolve().parents[4] / "src" / "aieos" / "domains" / "content" / "application"
        for name in ("review_queue.py", "review_queue_models.py"):
            text_src = (root / name).read_text(encoding="utf-8")
            for needle in ("temporalio", "nats", "openai", "anthropic"):
                assert needle not in text_src
