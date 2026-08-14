"""GCI-I14 adversarial: publication gates, concurrency, GCI-G11 quarantine."""

from __future__ import annotations

import threading
import uuid
from uuid import UUID

import pytest

from aieos.domains.content.application.asset_governance import (
    ValidateVersionAssetGovernanceService,
)
from aieos.domains.content.application.errors import PublicationAssetValidationFailed
from aieos.domains.content.domain.identities import ContentId, ContentVersionId
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.platform.events.persistence.repositories import SqlAlchemyOutboxRepository
from tests.domains.content.adversarial.helpers import (
    assert_problem,
    client,
    content_row,
    create_content,
    decide,
    headers,
    in_review,
    publication_count,
    submit_review,
)
from tests.fakes import (
    AllowAssetCurrentGovernance,
    AllowAssetReferenceValidation,
)
from tests.platform.events.helpers import outbox_rows
from tests.platform.workflows.helpers import append_version

pytestmark = pytest.mark.gci_i14


def _approve_current(runtime_engine, tenant_id: uuid.UUID):
    c = client(runtime_engine, tenant_id)
    content_id, version_id, etag = in_review(c, tenant_id)
    approved = decide(
        c, tenant_id, content_id, version_id, action="approve", etag=etag
    )
    assert approved.status_code == 200, approved.text
    return c, content_id, version_id, approved.headers["ETag"]


def _publish(c, tenant_id, content_id, version_id, etag, **extra):
    hdrs = headers(tenant_id, **extra)
    hdrs["If-Match"] = etag
    return c.post(
        f"/api/v1/contents/{content_id}/actions/publish",
        json={"version_id": version_id},
        headers=hdrs,
    )


def _asset_ref_body(resource_id: uuid.UUID | None = None) -> dict:
    return {
        "role": "primary",
        "ordinal": 0,
        "required": True,
        "resource_ref": {
            "resource_type": "asset.file",
            "resource_id": str(resource_id or uuid.uuid7()),
            "resource_revision": None,
        },
    }


class TestPublicationGates:
    def test_publish_without_approve_requires_approval(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        from sqlalchemy import text

        tenant_id = uuid.uuid7()
        c = client(runtime_engine, tenant_id)
        content_id = create_content(c, tenant_id)["content_id"]
        appended = append_version(c, tenant_id, content_id, etag='"r0"')
        assert appended.status_code == 201
        version_id = appended.json()["version_id"]
        # Adversarial: stewardship forced APPROVED without ReviewDecision.
        with bootstrap_engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE content.contents SET stewardship_state = 'APPROVED' "
                    "WHERE content_id = :cid"
                ),
                {"cid": UUID(content_id)},
            )
        response = _publish(
            c, tenant_id, content_id, version_id, appended.headers["ETag"]
        )
        assert_problem(response, status=409, code="publication_approval_required")
        assert publication_count(bootstrap_engine, content_id) == 0

    def test_publish_wrong_version_approval_fails(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        c = client(runtime_engine, tenant_id)
        content_id, v1, etag = in_review(c, tenant_id)
        approved = decide(c, tenant_id, content_id, v1, action="approve", etag=etag)
        assert approved.status_code == 200
        hdrs = headers(tenant_id)
        hdrs["If-Match"] = approved.headers["ETag"]
        v2 = c.post(
            f"/api/v1/contents/{content_id}/versions",
            json={
                "schema_id": "test.generic",
                "schema_version": 1,
                "payload": {"marker": "v2"},
            },
            headers=hdrs,
        )
        assert v2.status_code == 201
        submitted = submit_review(
            c, tenant_id, content_id, v2.json()["version_id"], etag=v2.headers["ETag"]
        )
        assert submitted.status_code == 200
        # try to publish v1 (approved historically) while current is v2 IN_REVIEW
        response = _publish(
            c, tenant_id, content_id, v1, etag=submitted.headers["ETag"]
        )
        assert response.status_code in {409, 412}
        assert response.json()["code"] in {
            "publication_version_not_current",
            "publication_approval_required",
            "resource_revision_conflict",
            "publication_not_allowed",
        }

    def test_concurrent_publish_same_revision_one_publication(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        c, content_id, version_id, etag = _approve_current(runtime_engine, tenant_id)
        barrier = threading.Barrier(2)
        results: list[int] = []
        lock = threading.Lock()

        def worker(key: str) -> None:
            local = client(runtime_engine, tenant_id)
            barrier.wait(timeout=10)
            response = _publish(
                local,
                tenant_id,
                content_id,
                version_id,
                etag,
                **{"Idempotency-Key": key},
            )
            with lock:
                results.append(response.status_code)

        threads = [
            threading.Thread(target=worker, args=(f"a-{uuid.uuid7()}",)),
            threading.Thread(target=worker, args=(f"b-{uuid.uuid7()}",)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(results) == [200, 412]
        assert publication_count(bootstrap_engine, content_id) == 1

    def test_publish_outbox_failure_full_rollback(
        self, runtime_engine, bootstrap_engine, monkeypatch
    ) -> None:
        from aieos.domains.content.application.errors import PersistenceOperationFailed

        tenant_id = uuid.uuid7()
        c, content_id, version_id, etag = _approve_current(runtime_engine, tenant_id)

        def boom(self, *args, **kwargs):  # noqa: ANN001
            raise PersistenceOperationFailed("outbox insert failed")

        monkeypatch.setattr(SqlAlchemyOutboxRepository, "insert", boom)
        response = _publish(c, tenant_id, content_id, version_id, etag)
        assert response.status_code == 503
        assert publication_count(bootstrap_engine, content_id) == 0
        row = content_row(bootstrap_engine, content_id)
        assert row.published_version_id is None
        assert row.stewardship_state == "APPROVED"
        pub_events = [
            r
            for r in outbox_rows(bootstrap_engine, content_id=content_id)
            if r["event_type"] == "io.eduvijna.aieos.content.content.published.v1"
        ]
        assert pub_events == []

    def test_approve_alone_no_publication_queue_empty(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        c = client(runtime_engine, tenant_id)
        content_id, version_id, etag = in_review(c, tenant_id)
        approved = decide(
            c, tenant_id, content_id, version_id, action="approve", etag=etag
        )
        assert approved.status_code == 200
        row = content_row(bootstrap_engine, content_id)
        assert row.published_version_id is None
        assert publication_count(bootstrap_engine, content_id) == 0
        queue = c.get(
            "/api/v1/teacher-os/review-queue",
            headers=headers(tenant_id),
        )
        assert queue.status_code == 200
        assert queue.json()["items"] == []


class TestAssetGovernanceG11:
    def test_publish_then_quarantine_preserves_publication(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        asset_id = uuid.uuid7()
        gov = AllowAssetCurrentGovernance()
        c = client(
            runtime_engine,
            tenant_id,
            principal_id,
            asset_current_governance=gov,
        )
        content_id = create_content(c, tenant_id)["content_id"]
        hdrs = headers(tenant_id)
        hdrs["If-Match"] = '"r0"'
        appended = c.post(
            f"/api/v1/contents/{content_id}/versions",
            json={
                "schema_id": "test.generic",
                "schema_version": 1,
                "payload": {"marker": "v1"},
                "asset_refs": [_asset_ref_body(asset_id)],
            },
            headers=hdrs,
        )
        assert appended.status_code == 201, appended.text
        version_id = appended.json()["version_id"]
        submitted = submit_review(
            c, tenant_id, content_id, version_id, etag=appended.headers["ETag"]
        )
        approved = decide(
            c,
            tenant_id,
            content_id,
            version_id,
            action="approve",
            etag=submitted.headers["ETag"],
        )
        published = _publish(
            c, tenant_id, content_id, version_id, approved.headers["ETag"]
        )
        assert published.status_code == 200, published.text
        publication_id = published.json()["publication_id"]
        before = content_row(bootstrap_engine, content_id)

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
        after = content_row(bootstrap_engine, content_id)
        assert after.published_version_id == before.published_version_id
        assert publication_count(bootstrap_engine, content_id) == 1
        assert published.json()["publication_id"] == publication_id

    def test_bind_time_deny_vs_publish_time_deny(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        asset_id = uuid.uuid7()
        bind_denied = client(
            runtime_engine,
            tenant_id,
            asset_reference_validation=AllowAssetReferenceValidation(
                deny_ids={asset_id}
            ),
        )
        content_id = create_content(bind_denied, tenant_id)["content_id"]
        hdrs = headers(tenant_id)
        hdrs["If-Match"] = '"r0"'
        denied = bind_denied.post(
            f"/api/v1/contents/{content_id}/versions",
            json={
                "schema_id": "test.generic",
                "schema_version": 1,
                "payload": {"marker": "v1"},
                "asset_refs": [_asset_ref_body(asset_id)],
            },
            headers=hdrs,
        )
        assert_problem(denied, status=422, code="asset_reference_invalid")
        row = content_row(bootstrap_engine, content_id)
        assert row.current_version_id is None

        # publish-time deny: bind succeeds, publish fails, no Publication
        gov = AllowAssetCurrentGovernance(deny=True)
        c = client(
            runtime_engine,
            tenant_id,
            asset_current_governance=gov,
        )
        content_id2, version_id, etag = in_review(c, tenant_id)
        # attach assets on a fresh content with refs
        content_id3 = create_content(c, tenant_id)["content_id"]
        hdrs = headers(tenant_id)
        hdrs["If-Match"] = '"r0"'
        appended = c.post(
            f"/api/v1/contents/{content_id3}/versions",
            json={
                "schema_id": "test.generic",
                "schema_version": 1,
                "payload": {"marker": "v1"},
                "asset_refs": [_asset_ref_body()],
            },
            headers=hdrs,
        )
        assert appended.status_code == 201, appended.text
        vid = appended.json()["version_id"]
        submitted = submit_review(
            c, tenant_id, content_id3, vid, etag=appended.headers["ETag"]
        )
        approved = decide(
            c,
            tenant_id,
            content_id3,
            vid,
            action="approve",
            etag=submitted.headers["ETag"],
        )
        failed = _publish(
            c, tenant_id, content_id3, vid, approved.headers["ETag"]
        )
        assert_problem(failed, status=409, code="publication_asset_validation_failed")
        assert publication_count(bootstrap_engine, content_id3) == 0
        assert content_row(bootstrap_engine, content_id3).published_version_id is None
        _ = (content_id2, version_id, etag)
