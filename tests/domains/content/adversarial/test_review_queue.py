"""GCI-I14 adversarial: Teacher OS review queue read projection."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from tests.dbutil import REPO_ROOT
from tests.domains.content.adversarial.helpers import (
    assert_problem,
    client,
    decide,
    headers,
    idempotency_count,
    in_review,
    outbox_count_for_content,
    submit_review,
)
from tests.platform.workflows.helpers import create_content

pytestmark = pytest.mark.gci_i14

REVIEW_QUEUE_SRC = (
    REPO_ROOT
    / "src"
    / "aieos"
    / "domains"
    / "content"
    / "application"
    / "review_queue.py"
)


def _queue_list(c, tenant_id, **params):
    return c.get(
        "/api/v1/teacher-os/review-queue",
        params=params or None,
        headers=headers(tenant_id),
    )


def _queue_get(c, tenant_id, content_id, version_id):
    return c.get(
        f"/api/v1/teacher-os/review-queue/{content_id}/versions/{version_id}",
        headers=headers(tenant_id),
    )


class TestReviewQueueAdversarial:
    def test_in_review_appears_decisions_remove(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        c = client(runtime_engine, tenant_id)
        content_id, version_id, etag = in_review(c, tenant_id)
        listed = _queue_list(c, tenant_id)
        assert listed.status_code == 200
        assert [i["version_id"] for i in listed.json()["items"]] == [version_id]

        approved = decide(
            c, tenant_id, content_id, version_id, action="approve", etag=etag
        )
        assert approved.status_code == 200
        assert _queue_list(c, tenant_id).json()["items"] == []

        content_id2, version_id2, etag2 = in_review(c, tenant_id)
        changed = decide(
            c,
            tenant_id,
            content_id2,
            version_id2,
            action="request-changes",
            etag=etag2,
            body={"comment": "revise"},
        )
        assert changed.status_code == 200
        assert all(
            i["content_id"] != content_id2
            for i in _queue_list(c, tenant_id).json()["items"]
        )

        content_id3, version_id3, etag3 = in_review(c, tenant_id)
        rejected = decide(
            c, tenant_id, content_id3, version_id3, action="reject", etag=etag3
        )
        assert rejected.status_code == 200
        assert all(
            i["content_id"] != content_id3
            for i in _queue_list(c, tenant_id).json()["items"]
        )

    def test_published_old_in_review_new_shows_new_version(
        self, runtime_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        c = client(runtime_engine, tenant_id)
        content_id, v1, etag = in_review(c, tenant_id)
        approved = decide(c, tenant_id, content_id, v1, action="approve", etag=etag)
        hdrs = headers(tenant_id)
        hdrs["If-Match"] = approved.headers["ETag"]
        published = c.post(
            f"/api/v1/contents/{content_id}/actions/publish",
            json={"version_id": v1},
            headers=hdrs,
        )
        assert published.status_code == 200
        hdrs = headers(tenant_id)
        hdrs["If-Match"] = published.headers["ETag"]
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
        v2_id = v2.json()["version_id"]
        submitted = submit_review(
            c, tenant_id, content_id, v2_id, etag=v2.headers["ETag"]
        )
        assert submitted.status_code == 200
        items = _queue_list(c, tenant_id).json()["items"]
        match = [i for i in items if i["content_id"] == content_id]
        assert len(match) == 1
        assert match[0]["version_id"] == v2_id
        assert match[0]["version_id"] != v1

    def test_stale_detail_after_decide_404(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        c = client(runtime_engine, tenant_id)
        content_id, version_id, etag = in_review(c, tenant_id)
        detail = _queue_get(c, tenant_id, content_id, version_id)
        assert detail.status_code == 200
        decide(c, tenant_id, content_id, version_id, action="approve", etag=etag)
        stale = _queue_get(c, tenant_id, content_id, version_id)
        assert_problem(stale, status=404, code="review_queue_item_not_found")

    def test_queue_get_creates_no_outbox_or_idempotency(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        c = client(runtime_engine, tenant_id)
        content_id, version_id, _ = in_review(c, tenant_id)
        before_outbox = outbox_count_for_content(bootstrap_engine, content_id)
        before_idem = idempotency_count(bootstrap_engine, tenant_id)
        assert _queue_list(c, tenant_id).status_code == 200
        assert _queue_get(c, tenant_id, content_id, version_id).status_code == 200
        assert outbox_count_for_content(bootstrap_engine, content_id) == before_outbox
        assert idempotency_count(bootstrap_engine, tenant_id) == before_idem

    def test_review_queue_source_has_no_enqueue_dequeue_approve_publish(self) -> None:
        text_src = REVIEW_QUEUE_SRC.read_text(encoding="utf-8").lower()
        for needle in ("enqueue", "dequeue", "approve", "publish", "reject"):
            assert needle not in text_src
