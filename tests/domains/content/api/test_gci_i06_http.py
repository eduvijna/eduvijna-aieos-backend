"""GCI-I06 review submit/approve/request-changes/reject HTTP foundation."""

from __future__ import annotations

from aieos.domains.content.application.audit import api_mutation_audit_provenance

import json
import threading
import uuid
from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.application.errors import PersistenceOperationFailed
from aieos.domains.content.application.review import ReviewCommandService
from aieos.domains.content.domain.identities import AggregateRevision, ContentId, ContentVersionId
from aieos.domains.content.infrastructure.persistence.repositories import (
    SqlAlchemyReviewDecisionRepository,
)
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.platform.events.models import MutationEventContext
from aieos.platform.api.app import create_app
from aieos.platform.api.etag import encode_revision_etag
from aieos.platform.api.infrastructure.persistence.repositories import (
    SqlAlchemyIdempotencyRepository,
)
from aieos.platform.idempotency.hashing import hash_idempotency_key
from tests.fakes import (
    AllowReviewAuthorization,
    AllowReviewCommentPolicy,
    AllowAssetCurrentGovernance,
    AllowAssetReferenceValidation,
    AllowPublicationAuthorization,
    AllowPublicationGovernance,
    IDEMPOTENCY_RETENTION,
    MarkerReviewCommentPolicy,
    SENSITIVE_TEST_COMMENT,
    FixedPrincipalAuthenticator,
    StubSecurityContextResolver,
    make_test_schema_registry,
)

pytestmark = pytest.mark.gci_i06

CURSOR_KEY = b"gci-i06-test-cursor-signing-key"
CREATE_BODY = {
    "content_type": "test.generic",
    "title": "Title",
    "description": "Description",
    "locale": "en-IN",
}
APPEND_BODY = {"schema_id": "test.generic", "schema_version": 1, "payload": {"marker": "v1"}}
LEAK_NEEDLES = ("sqlalchemy", "psycopg", "Traceback", "password")


def _app(
    runtime_engine: Engine,
    tenant_id: UUID,
    principal_id: UUID,
    *,
    authorization=None,
    comment_policy=None,
):
    return create_app(
        uow_factory=SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
        request_identity_authenticator=FixedPrincipalAuthenticator(principal_id),
        security_resolver=StubSecurityContextResolver(tenant_id, principal_id),
        content_types=StaticContentTypeCatalog({"test.generic"}),
        cursor_signing_key=CURSOR_KEY,
        schema_registry=make_test_schema_registry(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
        review_authorization=authorization or AllowReviewAuthorization(),
        review_comment_policy=comment_policy or AllowReviewCommentPolicy(),
        publication_authorization=AllowPublicationAuthorization(),
        publication_governance=AllowPublicationGovernance(),
        asset_reference_validation=AllowAssetReferenceValidation(),
        asset_current_governance=AllowAssetCurrentGovernance(),
    )


def _client(
    runtime_engine: Engine,
    tenant_id: UUID,
    principal_id: UUID,
    **kw,
) -> TestClient:
    return TestClient(
        _app(runtime_engine, tenant_id, principal_id, **kw),
        raise_server_exceptions=False,
    )


def _headers(tenant_id: UUID, **extra: str) -> dict[str, str]:
    headers = {"X-AIEOS-Tenant-ID": str(tenant_id), **extra}
    if "Idempotency-Key" not in headers:
        headers["Idempotency-Key"] = f"test-{uuid.uuid7()}"
    return headers


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


def _create(client: TestClient, tenant_id: UUID, **extra: str) -> dict:
    response = client.post(
        "/api/v1/contents",
        json=CREATE_BODY,
        headers=_headers(tenant_id, **extra),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _append(client: TestClient, tenant_id: UUID, content_id: str, *, etag: str, **extra: str):
    headers = _headers(tenant_id, **extra)
    headers["If-Match"] = etag
    return client.post(
        f"/api/v1/contents/{content_id}/versions",
        json=APPEND_BODY,
        headers=headers,
    )


def _append_payload(
    client: TestClient,
    tenant_id: UUID,
    content_id: str,
    *,
    etag: str,
    payload: dict,
    **extra: str,
):
    headers = _headers(tenant_id, **extra)
    headers["If-Match"] = etag
    return client.post(
        f"/api/v1/contents/{content_id}/versions",
        json={"schema_id": "test.generic", "schema_version": 1, "payload": payload},
        headers=headers,
    )


def _submit(client: TestClient, tenant_id: UUID, content_id: str, version_id: str, *, etag: str, **extra):
    headers = _headers(tenant_id, **extra)
    headers["If-Match"] = etag
    return client.post(
        f"/api/v1/contents/{content_id}/versions/{version_id}/actions/submit-for-review",
        headers=headers,
    )


def _decide(
    client: TestClient,
    action: str,
    tenant_id: UUID,
    content_id: str,
    version_id: str,
    *,
    etag: str,
    body: dict | None = None,
    **extra: str,
):
    headers = _headers(tenant_id, **extra)
    headers["If-Match"] = etag
    return client.post(
        f"/api/v1/contents/{content_id}/versions/{version_id}/actions/{action}",
        json={} if body is None else body,
        headers=headers,
    )


def _generated_version(client: TestClient, tenant_id: UUID) -> tuple[str, str, str]:
    content_id = _create(client, tenant_id)["content_id"]
    appended = _append(client, tenant_id, content_id, etag='"r0"')
    assert appended.status_code == 201, appended.text
    return content_id, appended.json()["version_id"], appended.headers["ETag"]


def _in_review(client: TestClient, tenant_id: UUID) -> tuple[str, str, str]:
    content_id, version_id, etag = _generated_version(client, tenant_id)
    submitted = _submit(client, tenant_id, content_id, version_id, etag=etag)
    assert submitted.status_code == 200, submitted.text
    return content_id, version_id, submitted.headers["ETag"]


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


def _decision_rows(bootstrap_engine: Engine, content_id: str | UUID):
    with bootstrap_engine.connect() as conn:
        return conn.execute(
            text(
                """
                SELECT review_decision_id, version_id, decision, reviewer_principal_id,
                       effective_actor_id, delegation_id, correlation_id, comment
                FROM content.review_decisions WHERE content_id = :cid
                """
            ),
            {"cid": UUID(str(content_id))},
        ).all()


def _idempotency_count(bootstrap_engine: Engine, tenant_id: UUID, operation: str) -> int:
    with bootstrap_engine.connect() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT count(*) FROM api.idempotency_records "
                    "WHERE tenant_id = :tid AND operation = :op"
                ),
                {"tid": tenant_id, "op": operation},
            ).scalar_one()
        )


class TestAppendAlignment:
    def test_first_and_generated_append_set_generated(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id, version_id, etag = _generated_version(client, tenant_id)
        row = _content_row(bootstrap_engine, content_id)
        assert row.stewardship_state == "GENERATED"
        assert row.current_version_id == UUID(version_id)
        second = _append_payload(
            client, tenant_id, content_id, etag=etag, payload={"marker": "v2"}
        )
        assert second.status_code == 201, second.text
        row = _content_row(bootstrap_engine, content_id)
        assert row.stewardship_state == "GENERATED"
        assert row.current_version_id == UUID(second.json()["version_id"])

    def test_approved_append_invalidates_current_approval(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id, version_id, etag = _in_review(client, tenant_id)
        approved = _decide(
            client, "approve", tenant_id, content_id, version_id, etag=etag
        )
        assert approved.status_code == 200, approved.text
        with bootstrap_engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE content.contents SET published_version_id = :vid "
                    "WHERE content_id = :cid"
                ),
                {"vid": UUID(version_id), "cid": UUID(content_id)},
            )
        appended = _append_payload(
            client,
            tenant_id,
            content_id,
            etag=approved.headers["ETag"],
            payload={"marker": "v2"},
        )
        assert appended.status_code == 201, appended.text
        row = _content_row(bootstrap_engine, content_id)
        assert row.stewardship_state == "GENERATED"
        assert row.current_version_id == UUID(appended.json()["version_id"])
        assert row.published_version_id == UUID(version_id)
        decisions = _decision_rows(bootstrap_engine, content_id)
        assert len(decisions) == 1
        assert decisions[0].version_id == UUID(version_id)
        assert decisions[0].decision == "APPROVE"

    def test_in_review_and_archived_append_still_blocked(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id, version_id, etag = _in_review(client, tenant_id)
        blocked = _append_payload(
            client, tenant_id, content_id, etag=etag, payload={"marker": "blocked"}
        )
        _assert_problem(blocked, status=409, code="content_version_append_not_allowed")
        archived_id = UUID(_create(client, tenant_id)["content_id"])
        with bootstrap_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE content.contents
                    SET stewardship_state = 'ARCHIVED', archived_at = :archived
                    WHERE content_id = :cid
                    """
                ),
                {"archived": datetime(2026, 8, 14, tzinfo=UTC), "cid": archived_id},
            )
        archived = _append(client, tenant_id, str(archived_id), etag='"r0"')
        _assert_problem(archived, status=409, code="content_version_append_not_allowed")


class TestSubmit:
    def test_generated_current_version_submits(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id, version_id, etag = _generated_version(client, tenant_id)
        response = _submit(client, tenant_id, content_id, version_id, etag=etag)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["content_id"] == content_id
        assert body["version_id"] == version_id
        assert body["stewardship_state"] == "IN_REVIEW"
        assert body["aggregate_revision"] == 2
        assert response.headers["ETag"] == encode_revision_etag(2)
        assert "tenant_id" not in body
        row = _content_row(bootstrap_engine, content_id)
        assert row.stewardship_state == "IN_REVIEW"
        assert int(row.aggregate_revision) == 2
        assert _decision_rows(bootstrap_engine, content_id) == []
        assert _idempotency_count(bootstrap_engine, tenant_id, "content_review_submit.v1") == 1

    def test_submit_gates(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        draft_id = _create(client, tenant_id)["content_id"]
        missing_version = _submit(
            client, tenant_id, draft_id, str(uuid.uuid7()), etag='"r0"'
        )
        _assert_problem(missing_version, status=404, code="content_version_not_found")
        content_id, v1, etag = _generated_version(client, tenant_id)
        v2 = _append_payload(
            client, tenant_id, content_id, etag=etag, payload={"marker": "v2"}
        )
        assert v2.status_code == 201
        stale_version = _submit(
            client, tenant_id, content_id, v1, etag=v2.headers["ETag"]
        )
        _assert_problem(stale_version, status=409, code="review_version_not_current")
        approved_id, approved_version, submit_etag = _in_review(client, tenant_id)
        approved = _decide(
            client, "approve", tenant_id, approved_id, approved_version, etag=submit_etag
        )
        assert approved.status_code == 200
        resubmit = _submit(
            client,
            tenant_id,
            approved_id,
            approved_version,
            etag=approved.headers["ETag"],
        )
        _assert_problem(resubmit, status=409, code="review_submit_not_allowed")
        archived_id = UUID(_create(client, tenant_id)["content_id"])
        archived_append = _append(client, tenant_id, str(archived_id), etag='"r0"')
        with bootstrap_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE content.contents
                    SET stewardship_state = 'ARCHIVED', archived_at = :archived
                    WHERE content_id = :cid
                    """
                ),
                {"archived": datetime(2026, 8, 14, tzinfo=UTC), "cid": archived_id},
            )
        archived = _submit(
            client,
            tenant_id,
            str(archived_id),
            archived_append.json()["version_id"],
            etag=archived_append.headers["ETag"],
        )
        _assert_problem(archived, status=409, code="review_submit_not_allowed")

    def test_submit_replay_and_replay_after_approval(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id, version_id, etag = _generated_version(client, tenant_id)
        key = f"submit-{uuid.uuid7()}"
        first = _submit(
            client, tenant_id, content_id, version_id, etag=etag, **{"Idempotency-Key": key}
        )
        assert first.status_code == 200, first.text
        replay = _submit(
            client, tenant_id, content_id, version_id, etag=etag, **{"Idempotency-Key": key}
        )
        assert replay.status_code == 200
        assert replay.json() == first.json()
        assert replay.headers["ETag"] == first.headers["ETag"]
        assert replay.json()["stewardship_state"] == "IN_REVIEW"
        assert _idempotency_count(bootstrap_engine, tenant_id, "content_review_submit.v1") == 1
        approved = _decide(
            client,
            "approve",
            tenant_id,
            content_id,
            version_id,
            etag=first.headers["ETag"],
        )
        assert approved.status_code == 200
        after_approve = _submit(
            client, tenant_id, content_id, version_id, etag=etag, **{"Idempotency-Key": key}
        )
        assert after_approve.status_code == 200
        assert after_approve.json()["stewardship_state"] == "IN_REVIEW"
        assert after_approve.headers["ETag"] == first.headers["ETag"]
        row = _content_row(bootstrap_engine, content_id)
        assert row.stewardship_state == "APPROVED"
        assert int(row.aggregate_revision) == 3


class TestApprove:
    def test_approve_exact_current_in_review(
        self, runtime_engine, bootstrap_engine, postgres18
    ) -> None:
        assert postgres18["server_version"].startswith("18.")
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, principal_id)
        content_id, version_id, etag = _in_review(client, tenant_id)
        correlation = str(uuid.uuid7())
        response = _decide(
            client,
            "approve",
            tenant_id,
            content_id,
            version_id,
            etag=etag,
            body={"reason_code": "ready.v1", "comment": "looks good"},
            **{"X-AIEOS-Correlation-ID": correlation},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["decision"] == "APPROVE"
        assert body["stewardship_state"] == "APPROVED"
        assert body["aggregate_revision"] == 3
        assert body["reason_code"] == "ready.v1"
        assert body["comment"] == "looks good"
        assert response.headers["ETag"] == encode_revision_etag(3)
        assert "tenant_id" not in body
        assert "reviewer_principal_id" not in body
        assert "effective_actor_id" not in body
        assert "delegation_id" not in body
        row = _content_row(bootstrap_engine, content_id)
        assert row.stewardship_state == "APPROVED"
        assert row.published_version_id is None
        assert row.current_version_id == UUID(version_id)
        decisions = _decision_rows(bootstrap_engine, content_id)
        assert len(decisions) == 1
        assert decisions[0].reviewer_principal_id == principal_id
        assert decisions[0].effective_actor_id == principal_id
        assert decisions[0].delegation_id is None
        assert str(decisions[0].correlation_id) == correlation

    def test_approve_before_submit_and_old_version(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id, version_id, etag = _generated_version(client, tenant_id)
        before = _decide(client, "approve", tenant_id, content_id, version_id, etag=etag)
        _assert_problem(before, status=409, code="review_decision_not_allowed")
        in_id, in_version, in_etag = _in_review(client, tenant_id)
        approved = _decide(client, "approve", tenant_id, in_id, in_version, etag=in_etag)
        assert approved.status_code == 200
        v2 = _append_payload(
            client, tenant_id, in_id, etag=approved.headers["ETag"], payload={"marker": "v2"}
        )
        assert v2.status_code == 201
        stale = _decide(
            client, "approve", tenant_id, in_id, in_version, etag=approved.headers["ETag"]
        )
        _assert_problem(stale, status=412, code="resource_revision_conflict")
        old_current_rev = _decide(
            client, "approve", tenant_id, in_id, in_version, etag=v2.headers["ETag"]
        )
        _assert_problem(old_current_rev, status=409, code="review_version_not_current")


class TestNegativeReview:
    def test_request_changes_and_reject_return_generated(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id, version_id, etag = _in_review(client, tenant_id)
        missing_comment = _decide(
            client, "request-changes", tenant_id, content_id, version_id, etag=etag, body={}
        )
        _assert_problem(missing_comment, status=422, code="invalid_content_request")
        changed = _decide(
            client,
            "request-changes",
            tenant_id,
            content_id,
            version_id,
            etag=etag,
            body={"comment": "please revise"},
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["decision"] == "REQUEST_CHANGES"
        assert changed.json()["stewardship_state"] == "GENERATED"
        row = _content_row(bootstrap_engine, content_id)
        assert row.stewardship_state == "GENERATED"
        assert int(row.aggregate_revision) == 3
        resubmit = _submit(
            client, tenant_id, content_id, version_id, etag=changed.headers["ETag"]
        )
        _assert_problem(resubmit, status=409, code="review_requires_new_version")
        v2 = _append_payload(
            client,
            tenant_id,
            content_id,
            etag=changed.headers["ETag"],
            payload={"marker": "v2"},
        )
        assert v2.status_code == 201, v2.text
        submit_v2 = _submit(
            client,
            tenant_id,
            content_id,
            v2.json()["version_id"],
            etag=v2.headers["ETag"],
        )
        assert submit_v2.status_code == 200, submit_v2.text
        assert submit_v2.json()["stewardship_state"] == "IN_REVIEW"

        reject_id, reject_version, reject_etag = _in_review(client, tenant_id)
        rejected = _decide(
            client, "reject", tenant_id, reject_id, reject_version, etag=reject_etag
        )
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["decision"] == "REJECT"
        assert rejected.json()["stewardship_state"] == "GENERATED"
        reject_row = _content_row(bootstrap_engine, reject_id)
        assert reject_row.stewardship_state == "GENERATED"
        assert reject_row.stewardship_state != "REJECTED"
        v2r = _append_payload(
            client,
            tenant_id,
            reject_id,
            etag=rejected.headers["ETag"],
            payload={"marker": "after-reject"},
        )
        assert v2r.status_code == 201
        submit_after_reject = _submit(
            client,
            tenant_id,
            reject_id,
            v2r.json()["version_id"],
            etag=v2r.headers["ETag"],
        )
        assert submit_after_reject.status_code == 200


class TestConcurrency:
    def test_conflicting_reviewers_one_success(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        owner = uuid.uuid7()
        reviewer_a = uuid.uuid7()
        reviewer_b = uuid.uuid7()
        owner_client = _client(runtime_engine, tenant_id, owner)
        content_id, version_id, etag = _in_review(owner_client, tenant_id)
        app_a = _app(runtime_engine, tenant_id, reviewer_a)
        app_b = _app(runtime_engine, tenant_id, reviewer_b)
        results: list = []

        def approve() -> None:
            client = TestClient(app_a, raise_server_exceptions=False)
            results.append(
                _decide(
                    client,
                    "approve",
                    tenant_id,
                    content_id,
                    version_id,
                    etag=etag,
                    **{"Idempotency-Key": f"a-{uuid.uuid7()}"},
                )
            )

        def request_changes() -> None:
            client = TestClient(app_b, raise_server_exceptions=False)
            results.append(
                _decide(
                    client,
                    "request-changes",
                    tenant_id,
                    content_id,
                    version_id,
                    etag=etag,
                    body={"comment": "needs work"},
                    **{"Idempotency-Key": f"b-{uuid.uuid7()}"},
                )
            )

        threads = [threading.Thread(target=approve), threading.Thread(target=request_changes)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        codes = sorted(item.status_code for item in results)
        assert codes == [200, 412]
        assert len(_decision_rows(bootstrap_engine, content_id)) == 1
        row = _content_row(bootstrap_engine, content_id)
        assert int(row.aggregate_revision) == 3

    def test_same_key_concurrent_approve(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        app = _app(runtime_engine, tenant_id, principal_id)
        setup = TestClient(app, raise_server_exceptions=False)
        content_id, version_id, etag = _in_review(setup, tenant_id)
        key = f"same-{uuid.uuid7()}"
        results: list = []

        def worker() -> None:
            client = TestClient(app, raise_server_exceptions=False)
            results.append(
                _decide(
                    client,
                    "approve",
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
        assert results[0].json()["review_decision_id"] == results[1].json()["review_decision_id"]
        assert len(_decision_rows(bootstrap_engine, content_id)) == 1
        row = _content_row(bootstrap_engine, content_id)
        assert int(row.aggregate_revision) == 3


class TestIdempotency:
    def test_decision_replay_and_reuse_conflicts(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_a = uuid.uuid7()
        principal_b = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, principal_a)
        content_id, version_id, etag = _in_review(client, tenant_id)
        key = f"approve-{uuid.uuid7()}"
        first = _decide(
            client,
            "approve",
            tenant_id,
            content_id,
            version_id,
            etag=etag,
            body={"comment": "ok"},
            **{"Idempotency-Key": key},
        )
        assert first.status_code == 200, first.text
        replay = _decide(
            client,
            "approve",
            tenant_id,
            content_id,
            version_id,
            etag=etag,
            body={"comment": "ok"},
            **{"Idempotency-Key": key},
        )
        assert replay.status_code == 200
        assert replay.json()["review_decision_id"] == first.json()["review_decision_id"]
        assert replay.headers["ETag"] == first.headers["ETag"]
        v2 = _append_payload(
            client, tenant_id, content_id, etag=first.headers["ETag"], payload={"marker": "v2"}
        )
        assert v2.status_code == 201
        after_new_version = _decide(
            client,
            "approve",
            tenant_id,
            content_id,
            version_id,
            etag=etag,
            body={"comment": "ok"},
            **{"Idempotency-Key": key},
        )
        assert after_new_version.status_code == 200
        assert after_new_version.json()["review_decision_id"] == first.json()["review_decision_id"]
        assert after_new_version.json()["stewardship_state"] == "APPROVED"
        assert after_new_version.headers["ETag"] == first.headers["ETag"]
        changed_comment = _decide(
            client,
            "approve",
            tenant_id,
            content_id,
            version_id,
            etag=etag,
            body={"comment": "different"},
            **{"Idempotency-Key": key},
        )
        _assert_problem(changed_comment, status=409, code="idempotency_key_reused")
        changed_if_match = _decide(
            client,
            "approve",
            tenant_id,
            content_id,
            version_id,
            etag='"r9"',
            body={"comment": "ok"},
            **{"Idempotency-Key": key},
        )
        _assert_problem(changed_if_match, status=409, code="idempotency_key_reused")
        assert len(_decision_rows(bootstrap_engine, content_id)) == 1

        rc_id, rc_version, rc_etag = _in_review(client, tenant_id)
        rc_key = f"rc-{uuid.uuid7()}"
        rc = _decide(
            client,
            "request-changes",
            tenant_id,
            rc_id,
            rc_version,
            etag=rc_etag,
            body={"comment": "fix this"},
            **{"Idempotency-Key": rc_key},
        )
        assert rc.status_code == 200
        rc_replay = _decide(
            client,
            "request-changes",
            tenant_id,
            rc_id,
            rc_version,
            etag=rc_etag,
            body={"comment": "fix this"},
            **{"Idempotency-Key": rc_key},
        )
        assert rc_replay.json()["review_decision_id"] == rc.json()["review_decision_id"]

        rj_id, rj_version, rj_etag = _in_review(client, tenant_id)
        rj_key = f"rj-{uuid.uuid7()}"
        rejected = _decide(
            client, "reject", tenant_id, rj_id, rj_version, etag=rj_etag, **{"Idempotency-Key": rj_key}
        )
        assert rejected.status_code == 200
        rj_replay = _decide(
            client, "reject", tenant_id, rj_id, rj_version, etag=rj_etag, **{"Idempotency-Key": rj_key}
        )
        assert rj_replay.json()["review_decision_id"] == rejected.json()["review_decision_id"]

        fresh_id, fresh_version, fresh_etag = _generated_version(client, tenant_id)
        submit_same_raw = _submit(
            client,
            tenant_id,
            fresh_id,
            fresh_version,
            etag=fresh_etag,
            **{"Idempotency-Key": key},
        )
        assert submit_same_raw.status_code == 200
        other_principal = _client(runtime_engine, tenant_id, principal_b)
        other_id, other_version, other_etag = _in_review(other_principal, tenant_id)
        other_approve = _decide(
            other_principal,
            "approve",
            tenant_id,
            other_id,
            other_version,
            etag=other_etag,
            body={"comment": "ok"},
            **{"Idempotency-Key": key},
        )
        assert other_approve.status_code == 200
        with bootstrap_engine.connect() as conn:
            blob = json.dumps(
                [
                    tuple(row)
                    for row in conn.execute(
                        text(
                            "SELECT idempotency_key_sha256, operation "
                            "FROM api.idempotency_records WHERE tenant_id = :tid"
                        ),
                        {"tid": tenant_id},
                    ).all()
                ]
            )
        assert key not in blob
        assert hash_idempotency_key(key) in blob
        assert "content_review_approve.v1" in blob
        assert "content_review_submit.v1" in blob

    def test_missing_if_match_and_idempotency_headers(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id, version_id, _etag = _generated_version(client, tenant_id)
        missing_if_match = client.post(
            f"/api/v1/contents/{content_id}/versions/{version_id}/actions/submit-for-review",
            headers=_headers(tenant_id),
        )
        _assert_problem(missing_if_match, status=428, code="precondition_required")
        bad_if_match = _submit(
            client, tenant_id, content_id, version_id, etag="W/\"r1\""
        )
        _assert_problem(bad_if_match, status=400, code="invalid_if_match")


class TestGovernance:
    def test_owner_without_decide_cannot_approve(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        owner = uuid.uuid7()
        owner_client = _client(runtime_engine, tenant_id, owner)
        content_id, version_id, etag = _in_review(owner_client, tenant_id)
        denied = _client(
            runtime_engine,
            tenant_id,
            owner,
            authorization=AllowReviewAuthorization(allow_decide=False),
        )
        response = _decide(denied, "approve", tenant_id, content_id, version_id, etag=etag)
        _assert_problem(response, status=403, code="forbidden")
        assert _decision_rows(bootstrap_engine, content_id) == []
        row = _content_row(bootstrap_engine, content_id)
        assert row.stewardship_state == "IN_REVIEW"
        assert int(row.aggregate_revision) == 2

    def test_denied_submit_and_auth_on_replay(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        allowed = _client(runtime_engine, tenant_id, principal_id)
        content_id, version_id, etag = _generated_version(allowed, tenant_id)
        denied = _client(
            runtime_engine,
            tenant_id,
            principal_id,
            authorization=AllowReviewAuthorization(allow_submit=False),
        )
        blocked = _submit(denied, tenant_id, content_id, version_id, etag=etag)
        _assert_problem(blocked, status=403, code="forbidden")
        row = _content_row(bootstrap_engine, content_id)
        assert row.stewardship_state == "GENERATED"

        auth = AllowReviewAuthorization()
        first_app = _app(
            runtime_engine, tenant_id, principal_id, authorization=auth
        )
        first = TestClient(first_app, raise_server_exceptions=False)
        in_id, in_version, in_etag = _in_review(first, tenant_id)
        key = f"auth-replay-{uuid.uuid7()}"
        approved = _decide(
            first,
            "approve",
            tenant_id,
            in_id,
            in_version,
            etag=in_etag,
            **{"Idempotency-Key": key},
        )
        assert approved.status_code == 200
        later = _client(
            runtime_engine,
            tenant_id,
            principal_id,
            authorization=AllowReviewAuthorization(allow_decide=False),
        )
        replay = _decide(
            later,
            "approve",
            tenant_id,
            in_id,
            in_version,
            etag=in_etag,
            **{"Idempotency-Key": key},
        )
        _assert_problem(replay, status=403, code="forbidden")

    def test_comment_policy_rejection_and_replay_survives_drift(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        rejecting = _client(
            runtime_engine,
            tenant_id,
            principal_id,
            comment_policy=MarkerReviewCommentPolicy(),
        )
        content_id, version_id, etag = _in_review(rejecting, tenant_id)
        before = _content_row(bootstrap_engine, content_id)
        rejected = _decide(
            rejecting,
            "approve",
            tenant_id,
            content_id,
            version_id,
            etag=etag,
            body={"comment": f"note {SENSITIVE_TEST_COMMENT}"},
        )
        body = _assert_problem(rejected, status=422, code="review_comment_rejected")
        assert SENSITIVE_TEST_COMMENT not in rejected.text
        assert SENSITIVE_TEST_COMMENT not in json.dumps(body)
        assert _decision_rows(bootstrap_engine, content_id) == []
        after = _content_row(bootstrap_engine, content_id)
        assert after.aggregate_revision == before.aggregate_revision
        assert after.stewardship_state == "IN_REVIEW"
        assert _idempotency_count(bootstrap_engine, tenant_id, "content_review_approve.v1") == 0

        allowing = _client(runtime_engine, tenant_id, principal_id)
        key = f"policy-drift-{uuid.uuid7()}"
        first = _decide(
            allowing,
            "approve",
            tenant_id,
            content_id,
            version_id,
            etag=etag,
            body={"comment": f"stored {SENSITIVE_TEST_COMMENT}"},
            **{"Idempotency-Key": key},
        )
        assert first.status_code == 200, first.text
        drifted = _client(
            runtime_engine,
            tenant_id,
            principal_id,
            comment_policy=MarkerReviewCommentPolicy(),
        )
        replay = _decide(
            drifted,
            "approve",
            tenant_id,
            content_id,
            version_id,
            etag=etag,
            body={"comment": f"stored {SENSITIVE_TEST_COMMENT}"},
            **{"Idempotency-Key": key},
        )
        assert replay.status_code == 200
        assert replay.json()["review_decision_id"] == first.json()["review_decision_id"]


class TestTenantNonDisclosure:
    def test_cross_tenant_submit_and_approve_are_404(self, runtime_engine) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        client_a = _client(runtime_engine, tenant_a, uuid.uuid7())
        client_b = _client(runtime_engine, tenant_b, uuid.uuid7())
        content_id, version_id, etag = _generated_version(client_a, tenant_a)
        hidden_submit = _submit(client_b, tenant_b, content_id, version_id, etag=etag)
        missing_submit = _submit(
            client_a, tenant_a, str(uuid.uuid7()), str(uuid.uuid7()), etag='"r1"'
        )
        hidden_body = _assert_problem(hidden_submit, status=404, code="content_not_found")
        missing_body = _assert_problem(missing_submit, status=404, code="content_not_found")
        assert hidden_body["title"] == missing_body["title"]
        assert str(tenant_a) not in hidden_submit.text
        in_id, in_version, in_etag = _in_review(client_a, tenant_a)
        hidden_approve = _decide(
            client_b, "approve", tenant_b, in_id, in_version, etag=in_etag
        )
        _assert_problem(hidden_approve, status=404, code="content_not_found")
        assert str(tenant_a) not in hidden_approve.text


class TestAtomicity:
    def test_failure_after_decision_insert_rolls_back(
        self, runtime_engine, bootstrap_engine, monkeypatch
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        setup = _client(runtime_engine, tenant_id, principal_id)
        content_id, version_id, _etag = _in_review(setup, tenant_id)
        original = SqlAlchemyReviewDecisionRepository.insert

        def boom(self, decision):
            original(self, decision)
            raise PersistenceOperationFailed("injected after ReviewDecision insert")

        monkeypatch.setattr(SqlAlchemyReviewDecisionRepository, "insert", boom)
        service = ReviewCommandService(
            SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
            AllowReviewAuthorization(),
            AllowReviewCommentPolicy(),
            idempotency_retention=IDEMPOTENCY_RETENTION,
        )
        with pytest.raises(PersistenceOperationFailed):
            service.approve(
                tenant_id,
                principal_id,
                content_id=ContentId(UUID(content_id)),
                version_id=ContentVersionId(UUID(version_id)),
                expected_aggregate_revision=AggregateRevision(2),
                reason_code=None,
                comment=None,
                idempotency_key=f"boom-insert-{uuid.uuid7()}",
                event_context=MutationEventContext(
                    correlation_id=uuid.uuid7(),
                    causation_id=uuid.uuid7(),
                    actor_principal_id=principal_id,
                    effective_actor_id=principal_id,
                ),
                audit_provenance=api_mutation_audit_provenance(principal_id),
            )
        assert _decision_rows(bootstrap_engine, content_id) == []
        row = _content_row(bootstrap_engine, content_id)
        assert row.stewardship_state == "IN_REVIEW"
        assert int(row.aggregate_revision) == 2
        assert _idempotency_count(bootstrap_engine, tenant_id, "content_review_approve.v1") == 0

    def test_failure_after_transition_before_idempotency_rolls_back(
        self, runtime_engine, bootstrap_engine, monkeypatch
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        setup = _client(runtime_engine, tenant_id, principal_id)
        content_id, version_id, _etag = _in_review(setup, tenant_id)
        original = SqlAlchemyIdempotencyRepository.insert

        def boom(self, outcome):
            raise PersistenceOperationFailed(
                "injected after state transition before idempotency"
            )

        monkeypatch.setattr(SqlAlchemyIdempotencyRepository, "insert", boom)
        service = ReviewCommandService(
            SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
            AllowReviewAuthorization(),
            AllowReviewCommentPolicy(),
            idempotency_retention=IDEMPOTENCY_RETENTION,
        )
        with pytest.raises(PersistenceOperationFailed):
            service.approve(
                tenant_id,
                principal_id,
                content_id=ContentId(UUID(content_id)),
                version_id=ContentVersionId(UUID(version_id)),
                expected_aggregate_revision=AggregateRevision(2),
                reason_code=None,
                comment=None,
                idempotency_key=f"boom-idemp-{uuid.uuid7()}",
                event_context=MutationEventContext(
                    correlation_id=uuid.uuid7(),
                    causation_id=uuid.uuid7(),
                    actor_principal_id=principal_id,
                    effective_actor_id=principal_id,
                ),
                audit_provenance=api_mutation_audit_provenance(principal_id),
            )
        assert _decision_rows(bootstrap_engine, content_id) == []
        row = _content_row(bootstrap_engine, content_id)
        assert row.stewardship_state == "IN_REVIEW"
        assert int(row.aggregate_revision) == 2
        assert _idempotency_count(bootstrap_engine, tenant_id, "content_review_approve.v1") == 0
