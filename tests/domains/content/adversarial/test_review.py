"""GCI-I14 adversarial: review decision gates, concurrency, auth, comment policy."""

from __future__ import annotations

import threading
import uuid

import pytest

from tests.domains.content.adversarial.helpers import (
    assert_problem,
    client,
    content_row,
    decide,
    decision_count,
    headers,
    idempotency_count,
    in_review,
    outbox_count_for_content,
    submit_review,
)
from tests.fakes import (
    AllowReviewAuthorization,
    MarkerReviewCommentPolicy,
    SENSITIVE_TEST_COMMENT,
)
from tests.platform.events.helpers import outbox_rows
from tests.platform.workflows.helpers import append_version

pytestmark = pytest.mark.gci_i14


class TestReviewGates:
    def test_approve_old_version_after_v2_exists(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        c = client(runtime_engine, tenant_id)
        content_id, v1, etag = in_review(c, tenant_id)
        approved = decide(
            c, tenant_id, content_id, v1, action="approve", etag=etag
        )
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
        assert v2.status_code == 201, v2.text
        stale = decide(
            c,
            tenant_id,
            content_id,
            v1,
            action="approve",
            etag=v2.headers["ETag"],
        )
        assert_problem(stale, status=409, code="review_version_not_current")

    def test_request_changes_then_resubmit_requires_new_version(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        c = client(runtime_engine, tenant_id)
        content_id, version_id, etag = in_review(c, tenant_id)
        changed = decide(
            c,
            tenant_id,
            content_id,
            version_id,
            action="request-changes",
            etag=etag,
            body={"comment": "please revise"},
        )
        assert changed.status_code == 200, changed.text
        resubmit = submit_review(
            c,
            tenant_id,
            content_id,
            version_id,
            etag=changed.headers["ETag"],
        )
        assert_problem(resubmit, status=409, code="review_requires_new_version")
        hdrs = headers(tenant_id)
        hdrs["If-Match"] = changed.headers["ETag"]
        v2 = c.post(
            f"/api/v1/contents/{content_id}/versions",
            json={
                "schema_id": "test.generic",
                "schema_version": 1,
                "payload": {"marker": "v2"},
            },
            headers=hdrs,
        )
        assert v2.status_code == 201, v2.text
        submit_v2 = submit_review(
            c,
            tenant_id,
            content_id,
            v2.json()["version_id"],
            etag=v2.headers["ETag"],
        )
        assert submit_v2.status_code == 200, submit_v2.text
        assert content_row(bootstrap_engine, content_id).stewardship_state == "IN_REVIEW"

    def test_reject_then_resubmit_requires_new_version(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        c = client(runtime_engine, tenant_id)
        content_id, version_id, etag = in_review(c, tenant_id)
        rejected = decide(
            c, tenant_id, content_id, version_id, action="reject", etag=etag
        )
        assert rejected.status_code == 200, rejected.text
        resubmit = submit_review(
            c,
            tenant_id,
            content_id,
            version_id,
            etag=rejected.headers["ETag"],
        )
        assert_problem(resubmit, status=409, code="review_requires_new_version")
        hdrs = headers(tenant_id)
        hdrs["If-Match"] = rejected.headers["ETag"]
        v2 = c.post(
            f"/api/v1/contents/{content_id}/versions",
            json={
                "schema_id": "test.generic",
                "schema_version": 1,
                "payload": {"marker": "after-reject"},
            },
            headers=hdrs,
        )
        assert v2.status_code == 201
        submit_v2 = submit_review(
            c,
            tenant_id,
            content_id,
            v2.json()["version_id"],
            etag=v2.headers["ETag"],
        )
        assert submit_v2.status_code == 200


class TestReviewConcurrencyAndGovernance:
    def test_concurrent_approve_vs_reject_one_decision_one_event(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        setup = client(runtime_engine, tenant_id)
        content_id, version_id, etag = in_review(setup, tenant_id)
        barrier = threading.Barrier(2)
        results: list[int] = []
        lock = threading.Lock()

        def approve() -> None:
            local = client(runtime_engine, tenant_id, uuid.uuid7())
            barrier.wait(timeout=10)
            response = decide(
                local,
                tenant_id,
                content_id,
                version_id,
                action="approve",
                etag=etag,
                **{"Idempotency-Key": f"a-{uuid.uuid7()}"},
            )
            with lock:
                results.append(response.status_code)

        def reject() -> None:
            local = client(runtime_engine, tenant_id, uuid.uuid7())
            barrier.wait(timeout=10)
            response = decide(
                local,
                tenant_id,
                content_id,
                version_id,
                action="reject",
                etag=etag,
                **{"Idempotency-Key": f"b-{uuid.uuid7()}"},
            )
            with lock:
                results.append(response.status_code)

        threads = [threading.Thread(target=approve), threading.Thread(target=reject)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(results) == [200, 412]
        assert decision_count(bootstrap_engine, content_id) == 1
        decision_events = [
            row
            for row in outbox_rows(bootstrap_engine, content_id=content_id)
            if row["event_type"]
            in {
                "io.eduvijna.aieos.content.content.review_approved.v1",
                "io.eduvijna.aieos.content.content.review_rejected.v1",
                "io.eduvijna.aieos.content.content.review_changes_requested.v1",
            }
        ]
        assert len(decision_events) == 1

    def test_comment_policy_denial_no_side_effects(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        c = client(
            runtime_engine,
            tenant_id,
            principal_id,
            comment_policy=MarkerReviewCommentPolicy(),
        )
        content_id, version_id, etag = in_review(c, tenant_id)
        before_outbox = outbox_count_for_content(bootstrap_engine, content_id)
        before_idem = idempotency_count(bootstrap_engine, tenant_id)
        denied = decide(
            c,
            tenant_id,
            content_id,
            version_id,
            action="approve",
            etag=etag,
            body={"comment": f"note {SENSITIVE_TEST_COMMENT}"},
        )
        body = assert_problem(denied, status=422, code="review_comment_rejected")
        assert SENSITIVE_TEST_COMMENT not in body.get("detail", "")
        assert decision_count(bootstrap_engine, content_id) == 0
        assert outbox_count_for_content(bootstrap_engine, content_id) == before_outbox
        assert idempotency_count(bootstrap_engine, tenant_id) == before_idem
        assert content_row(bootstrap_engine, content_id).stewardship_state == "IN_REVIEW"

    def test_owner_without_decide_auth_forbidden(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        owner = uuid.uuid7()
        owner_client = client(runtime_engine, tenant_id, owner)
        content_id, version_id, etag = in_review(owner_client, tenant_id)
        denied = client(
            runtime_engine,
            tenant_id,
            owner,
            authorization=AllowReviewAuthorization(allow_decide=False),
        )
        response = decide(
            denied, tenant_id, content_id, version_id, action="approve", etag=etag
        )
        assert_problem(response, status=403, code="forbidden")
        assert decision_count(bootstrap_engine, content_id) == 0

    def test_revoke_decide_auth_new_key_fails_established_replay_rechecks(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        # Frozen contract (I06/I07): auth is re-checked on idempotent replay.
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        auth = AllowReviewAuthorization()
        first = client(
            runtime_engine, tenant_id, principal_id, authorization=auth
        )
        content_id, version_id, etag = in_review(first, tenant_id)
        key = f"established-{uuid.uuid7()}"
        approved = decide(
            first,
            tenant_id,
            content_id,
            version_id,
            action="approve",
            etag=etag,
            **{"Idempotency-Key": key},
        )
        assert approved.status_code == 200

        auth.allow_decide = False
        revoked = client(
            runtime_engine, tenant_id, principal_id, authorization=auth
        )
        new_key = decide(
            revoked,
            tenant_id,
            content_id,
            version_id,
            action="approve",
            etag=etag,
            **{"Idempotency-Key": f"new-{uuid.uuid7()}"},
        )
        assert_problem(new_key, status=403, code="forbidden")

        replay = decide(
            revoked,
            tenant_id,
            content_id,
            version_id,
            action="approve",
            etag=etag,
            **{"Idempotency-Key": key},
        )
        assert_problem(replay, status=403, code="forbidden")
        assert decision_count(bootstrap_engine, content_id) == 1
