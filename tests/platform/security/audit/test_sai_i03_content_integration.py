"""SAI-I03 Generic Content API transactional security-audit integration."""

from __future__ import annotations

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
from aieos.domains.content.infrastructure.persistence.audit_repository import (
    ContentSecurityMutationAuditRepository,
)
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.domains.teaching.infrastructure.persistence.uow import (
    SqlAlchemyTeachingUnitOfWorkFactory,
)
from aieos.domains.assessment.infrastructure.persistence.uow import (
    SqlAlchemyAssessmentUnitOfWorkFactory,
)
from aieos.platform.api.app import create_app
from aieos.platform.api.infrastructure.persistence.repositories import (
    SqlAlchemyIdempotencyRepository,
)
from aieos.platform.events.constants import (
    EVENT_CONTENT_PUBLISHED_V1,
    EVENT_CONTENT_VERSION_CREATED_V1,
)
from aieos.platform.security.audit.persistence.errors import SecurityAuditPersistenceError
from aieos.platform.security.audit.persistence.repositories import (
    SqlAlchemySecurityMutationAuditRepository,
)
from tests.fakes import (
    AllowClassroomAssessmentAuthorization,
    AllowAssetCurrentGovernance,
    AllowAssetReferenceValidation,
    AllowPublicationAuthorization,
    AllowPublicationGovernance,
    AllowReviewAuthorization,
    AllowReviewCommentPolicy,
    IDEMPOTENCY_RETENTION,
    MarkerReviewCommentPolicy,
    SENSITIVE_TEST_COMMENT,
    FixedPrincipalAuthenticator,
    StubSecurityContextResolver,
    make_test_schema_registry,
)
from tests.platform.events.helpers import outbox_rows
from aieos.platform.events.constants import (
    EVENT_CONTENT_PUBLISHED_V1,
    EVENT_CONTENT_VERSION_CREATED_V1,
)
from tests.platform.workflows.helpers import (
    create_content,
    decide,
    headers,
    in_review,
    submit_review,
)

pytestmark = pytest.mark.sai_i03

CURSOR_KEY = b"sai-i03-test-cursor-signing-key"
LEAK_NEEDLES = (
    "sqlalchemy",
    "psycopg",
    "Traceback",
    "password",
    "security.audit_records",
    "INSERT INTO",
    "aieos_security",
    "postgresql://",
)


def _app(
    runtime_engine: Engine,
    tenant_id: UUID,
    principal_id: UUID,
    *,
    authorization=None,
    comment_policy=None,
    publication_authorization=None,
):
    return create_app(
        uow_factory=SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
        teaching_uow_factory=SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine),
        assessment_uow_factory=SqlAlchemyAssessmentUnitOfWorkFactory(runtime_engine),
        assessment_authorization=AllowClassroomAssessmentAuthorization(),
        request_identity_authenticator=FixedPrincipalAuthenticator(principal_id),
        security_resolver=StubSecurityContextResolver(tenant_id, principal_id),
        content_types=StaticContentTypeCatalog({"test.generic"}),
        cursor_signing_key=CURSOR_KEY,
        schema_registry=make_test_schema_registry(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
        review_authorization=authorization or AllowReviewAuthorization(),
        review_comment_policy=comment_policy or AllowReviewCommentPolicy(),
        publication_authorization=publication_authorization
        or AllowPublicationAuthorization(),
        publication_governance=AllowPublicationGovernance(),
        asset_reference_validation=AllowAssetReferenceValidation(),
        asset_current_governance=AllowAssetCurrentGovernance(),
    )


def _client(runtime_engine: Engine, tenant_id: UUID, principal_id: UUID, **kw) -> TestClient:
    return TestClient(
        _app(runtime_engine, tenant_id, principal_id, **kw),
        raise_server_exceptions=False,
    )


def _audit_rows(bootstrap_engine: Engine, *, content_id: str | UUID | None = None) -> list[dict]:
    sql = "SELECT * FROM security.audit_records"
    params: dict[str, object] = {}
    if content_id is not None:
        sql += " WHERE primary_resource_id = :cid"
        params["cid"] = UUID(str(content_id))
    sql += " ORDER BY occurred_at, audit_record_id"
    with bootstrap_engine.connect() as conn:
        return [dict(r) for r in conn.execute(text(sql), params).mappings().all()]


def _audit_count(bootstrap_engine: Engine, *, content_id: str | UUID | None = None) -> int:
    return len(_audit_rows(bootstrap_engine, content_id=content_id))


def _content_row(bootstrap_engine: Engine, content_id: str | UUID):
    with bootstrap_engine.connect() as conn:
        return conn.execute(
            text(
                """
                SELECT stewardship_state, aggregate_revision, current_version_id,
                       published_version_id, created_at, updated_at
                FROM content.contents WHERE content_id = :cid
                """
            ),
            {"cid": UUID(str(content_id))},
        ).one_or_none()


def _version_count(bootstrap_engine: Engine, content_id: str | UUID) -> int:
    with bootstrap_engine.connect() as conn:
        return int(
            conn.execute(
                text("SELECT count(*) FROM content.content_versions WHERE content_id = :cid"),
                {"cid": UUID(str(content_id))},
            ).scalar_one()
        )


def _decision_count(bootstrap_engine: Engine, content_id: str | UUID) -> int:
    with bootstrap_engine.connect() as conn:
        return int(
            conn.execute(
                text("SELECT count(*) FROM content.review_decisions WHERE content_id = :cid"),
                {"cid": UUID(str(content_id))},
            ).scalar_one()
        )


def _publication_count(bootstrap_engine: Engine, content_id: str | UUID) -> int:
    with bootstrap_engine.connect() as conn:
        return int(
            conn.execute(
                text("SELECT count(*) FROM content.publications WHERE content_id = :cid"),
                {"cid": UUID(str(content_id))},
            ).scalar_one()
        )


def _idempotency_count(bootstrap_engine: Engine, tenant_id: UUID) -> int:
    with bootstrap_engine.connect() as conn:
        return int(
            conn.execute(
                text("SELECT count(*) FROM api.idempotency_records WHERE tenant_id = :tid"),
                {"tid": tenant_id},
            ).scalar_one()
        )


def _workflow_intent_count(bootstrap_engine: Engine, content_id: str | UUID) -> int:
    cid = str(content_id)
    with bootstrap_engine.connect() as conn:
        starts = int(
            conn.execute(
                text(
                    "SELECT count(*) FROM workflow.workflow_start_intents "
                    "WHERE input->>'content_id' = :cid"
                ),
                {"cid": cid},
            ).scalar_one()
        )
        commands = int(
            conn.execute(
                text(
                    "SELECT count(*) FROM workflow.workflow_command_intents "
                    "WHERE payload->>'content_id' = :cid"
                ),
                {"cid": cid},
            ).scalar_one()
        )
        return starts + commands


def _related(row: dict) -> list[dict]:
    refs = row["related_resource_refs"]
    if isinstance(refs, str):
        return json.loads(refs)
    return list(refs)


def _assert_no_leak(response) -> None:
    blob = response.text + json.dumps(response.json() if response.headers.get("content-type", "").startswith("application/problem") else {})
    lower = blob.lower()
    for needle in LEAK_NEEDLES:
        assert needle.lower() not in lower, needle


def _append(
    client: TestClient,
    tenant_id: UUID,
    content_id: str,
    *,
    etag: str,
    **extra: str,
):
    hdrs = headers(tenant_id, **extra)
    hdrs["If-Match"] = etag
    return client.post(
        f"/api/v1/contents/{content_id}/versions",
        json={
            "schema_id": "test.generic",
            "schema_version": 1,
            "payload": {"marker": "v1"},
        },
        headers=hdrs,
    )


def _lifecycle_to_publish(client: TestClient, tenant_id: UUID) -> tuple[str, str, str]:
    created = create_content(client, tenant_id)
    content_id = created["content_id"]
    appended = _append(client, tenant_id, content_id, etag='"r0"')
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
    published = client.post(
        f"/api/v1/contents/{content_id}/actions/publish",
        json={"version_id": version_id},
        headers={**headers(tenant_id), "If-Match": approved.headers["ETag"]},
    )
    assert published.status_code == 200, published.text
    return content_id, version_id, published.headers["ETag"]


class TestSameConnectionAndInsertOnly:
    def test_uow_audit_outbox_contents_share_connection(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        factory = SqlAlchemyContentUnitOfWorkFactory(runtime_engine)
        with factory(tenant_id) as uow:
            assert uow.audit._delegate._connection is uow.outbox._connection
            assert uow.audit._delegate._connection is uow.contents._connection
            assert uow.audit._delegate._connection is uow.idempotency._connection
            assert hasattr(uow.audit, "insert")
            assert not hasattr(uow.audit, "get")
            assert not hasattr(uow.audit, "list")
            assert not hasattr(uow.audit, "search")


class TestCreateAudit:
    def test_create_shape_actors_channel_and_correlation(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, principal_id)
        created = create_content(client, tenant_id)
        content_id = created["content_id"]
        rows = _audit_rows(bootstrap_engine, content_id=content_id)
        assert len(rows) == 1
        row = rows[0]
        assert row["action"] == "content.create"
        assert row["primary_resource_type"] == "content.content"
        assert row["primary_resource_id"] == UUID(content_id)
        assert row["primary_resource_revision"] == 0
        assert row["resource_revision_before"] is None
        assert row["resource_revision_after"] == 0
        assert _related(row) == []
        assert row["tenant_id"] == tenant_id
        assert row["initiating_principal_id"] == principal_id
        assert row["effective_actor_id"] == principal_id
        assert row["executing_principal_id"] == principal_id
        assert row["execution_channel"] == "API"
        assert row["delegation_id"] is None
        assert row["trace_id"] is None
        content = _content_row(bootstrap_engine, content_id)
        assert row["occurred_at"] == content.created_at
        events = outbox_rows(bootstrap_engine, content_id=content_id)
        assert len(events) == 1
        env = dict(events[0]["envelope"])
        assert row["correlation_id"] == UUID(env["correlationid"])
        assert row["causation_id"] == UUID(env["causationid"])
        assert row["audit_record_id"] != events[0]["event_id"]
        assert "role" not in row
        assert "permission" not in str(row)

    def test_create_replay_and_changed_key_no_second_audit(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        key = f"create-{uuid.uuid7()}"
        first = client.post(
            "/api/v1/contents",
            json={
                "content_type": "test.generic",
                "title": "Title",
                "description": "Description",
                "locale": "en-IN",
            },
            headers=headers(tenant_id, **{"Idempotency-Key": key}),
        )
        assert first.status_code == 201
        content_id = first.json()["content_id"]
        original = _audit_rows(bootstrap_engine, content_id=content_id)
        assert len(original) == 1
        audit_id = original[0]["audit_record_id"]
        replay = client.post(
            "/api/v1/contents",
            json={
                "content_type": "test.generic",
                "title": "Title",
                "description": "Description",
                "locale": "en-IN",
            },
            headers=headers(tenant_id, **{"Idempotency-Key": key}),
        )
        assert replay.status_code == 201
        assert _audit_count(bootstrap_engine, content_id=content_id) == 1
        assert _audit_rows(bootstrap_engine, content_id=content_id)[0]["audit_record_id"] == audit_id
        changed = client.post(
            "/api/v1/contents",
            json={
                "content_type": "test.generic",
                "title": "Other",
                "description": "Description",
                "locale": "en-IN",
            },
            headers=headers(tenant_id, **{"Idempotency-Key": key}),
        )
        assert changed.status_code == 409
        assert changed.json()["code"] == "idempotency_key_reused"
        assert _audit_count(bootstrap_engine, content_id=content_id) == 1

    def test_create_audit_failure_rolls_back(
        self, runtime_engine, bootstrap_engine, monkeypatch
    ) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())

        def boom(self, record) -> None:
            raise PersistenceOperationFailed("content persistence operation failed")

        monkeypatch.setattr(ContentSecurityMutationAuditRepository, "insert", boom)
        before_content = _idempotency_count(bootstrap_engine, tenant_id)
        response = client.post(
            "/api/v1/contents",
            json={
                "content_type": "test.generic",
                "title": "Title",
                "description": "Description",
                "locale": "en-IN",
            },
            headers=headers(tenant_id),
        )
        assert response.status_code == 503
        _assert_no_leak(response)
        with bootstrap_engine.connect() as conn:
            contents = int(
                conn.execute(
                    text("SELECT count(*) FROM content.contents WHERE tenant_id = :tid"),
                    {"tid": tenant_id},
                ).scalar_one()
            )
            outs = int(
                conn.execute(
                    text(
                        "SELECT count(*) FROM integration.outbox_messages "
                        "WHERE tenant_id = :tid"
                    ),
                    {"tid": tenant_id},
                ).scalar_one()
            )
            audits = int(
                conn.execute(
                    text(
                        "SELECT count(*) FROM security.audit_records WHERE tenant_id = :tid"
                    ),
                    {"tid": tenant_id},
                ).scalar_one()
            )
        assert contents == 0
        assert outs == 0
        assert audits == 0
        assert _idempotency_count(bootstrap_engine, tenant_id) == before_content


class TestAppendAudit:
    def test_append_shape_and_version_number_not_revision(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, principal_id)
        content_id = create_content(client, tenant_id)["content_id"]
        appended = _append(client, tenant_id, content_id, etag='"r0"')
        assert appended.status_code == 201
        version_id = appended.json()["version_id"]
        assert appended.json()["version_number"] == 1
        rows = [r for r in _audit_rows(bootstrap_engine, content_id=content_id) if r["action"] == "content.version.create"]
        assert len(rows) == 1
        row = rows[0]
        assert row["resource_revision_before"] == 0
        assert row["resource_revision_after"] == 1
        assert row["primary_resource_revision"] == 1
        related = _related(row)
        assert len(related) == 1
        assert related[0]["resource_type"] == "content.content_version"
        assert related[0]["resource_id"] == version_id
        assert related[0]["resource_revision"] is None
        assert related[0]["resource_revision"] != 1
        content = _content_row(bootstrap_engine, content_id)
        assert row["occurred_at"] == content.updated_at
        events = [
            e
            for e in outbox_rows(bootstrap_engine, content_id=content_id)
            if e["event_type"] == EVENT_CONTENT_VERSION_CREATED_V1
        ]
        assert len(events) == 1
        env = dict(events[0]["envelope"])
        assert row["correlation_id"] == UUID(env["correlationid"])
        assert row["causation_id"] == UUID(env["causationid"])

    def test_append_replay_and_concurrent_one_audit(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id = create_content(client, tenant_id)["content_id"]
        key = f"append-{uuid.uuid7()}"
        first = _append(
            client, tenant_id, content_id, etag='"r0"', **{"Idempotency-Key": key}
        )
        assert first.status_code == 201
        assert _audit_count(bootstrap_engine, content_id=content_id) == 2
        replay = _append(
            client, tenant_id, content_id, etag='"r0"', **{"Idempotency-Key": key}
        )
        assert replay.status_code == 201
        assert _audit_count(bootstrap_engine, content_id=content_id) == 2
        assert _version_count(bootstrap_engine, content_id) == 1

        content_id2 = create_content(client, tenant_id)["content_id"]
        barrier = threading.Barrier(2)
        results: list[int] = []

        def worker(k: str) -> None:
            barrier.wait()
            resp = _append(
                client, tenant_id, content_id2, etag='"r0"', **{"Idempotency-Key": k}
            )
            results.append(resp.status_code)

        threads = [
            threading.Thread(target=worker, args=(f"c1-{uuid.uuid7()}",)),
            threading.Thread(target=worker, args=(f"c2-{uuid.uuid7()}",)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(results)[0] == 201
        assert sorted(results)[1] in {409, 412, 422}
        assert _version_count(bootstrap_engine, content_id2) == 1
        append_audits = [
            r
            for r in _audit_rows(bootstrap_engine, content_id=content_id2)
            if r["action"] == "content.version.create"
        ]
        assert len(append_audits) == 1
        assert len(outbox_rows(bootstrap_engine, content_id=content_id2)) == 2  # create+version

    def test_append_audit_failure_rolls_back(
        self, runtime_engine, bootstrap_engine, monkeypatch
    ) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id = create_content(client, tenant_id)["content_id"]
        assert _content_row(bootstrap_engine, content_id).aggregate_revision == 0

        def boom(self, record) -> None:
            raise PersistenceOperationFailed("content persistence operation failed")

        monkeypatch.setattr(ContentSecurityMutationAuditRepository, "insert", boom)
        response = _append(client, tenant_id, content_id, etag='"r0"')
        assert response.status_code == 503
        row = _content_row(bootstrap_engine, content_id)
        assert int(row.aggregate_revision) == 0
        assert row.current_version_id is None
        assert _version_count(bootstrap_engine, content_id) == 0
        assert len(outbox_rows(bootstrap_engine, content_id=content_id)) == 1  # create only
        assert _audit_count(bootstrap_engine, content_id=content_id) == 1  # create only
        assert _idempotency_count(bootstrap_engine, tenant_id) == 1  # create only


class TestReviewAudit:
    def test_submit_approve_request_changes_reject_shapes(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, principal_id)
        content_id = create_content(client, tenant_id)["content_id"]
        appended = _append(client, tenant_id, content_id, etag='"r0"')
        version_id = appended.json()["version_id"]
        submitted = submit_review(
            client, tenant_id, content_id, version_id, etag=appended.headers["ETag"]
        )
        assert submitted.status_code == 200
        submit_row = [
            r for r in _audit_rows(bootstrap_engine, content_id=content_id) if r["action"] == "content.review.submit"
        ][0]
        submit_related = _related(submit_row)
        assert len(submit_related) == 1
        assert submit_related[0]["resource_type"] == "content.content_version"
        assert submit_related[0]["resource_revision"] is None
        assert _workflow_intent_count(bootstrap_engine, content_id) >= 1

        approved = decide(
            client,
            tenant_id,
            content_id,
            version_id,
            action="approve",
            etag=submitted.headers["ETag"],
        )
        assert approved.status_code == 200
        approve_row = [
            r for r in _audit_rows(bootstrap_engine, content_id=content_id) if r["action"] == "content.review.approve"
        ][0]
        approve_related = _related(approve_row)
        assert [r["resource_type"] for r in approve_related] == [
            "content.content_version",
            "content.review_decision",
        ]
        assert all(r["resource_revision"] is None for r in approve_related)
        assert SENSITIVE_TEST_COMMENT not in json.dumps(approve_row, default=str)
        assert "content.publish" not in [r["action"] for r in _audit_rows(bootstrap_engine, content_id=content_id)]

        # request_changes path on a fresh aggregate
        content_id2 = create_content(client, tenant_id)["content_id"]
        a2 = _append(client, tenant_id, content_id2, etag='"r0"')
        v2 = a2.json()["version_id"]
        s2 = submit_review(client, tenant_id, content_id2, v2, etag=a2.headers["ETag"])
        rc = decide(
            client,
            tenant_id,
            content_id2,
            v2,
            action="request-changes",
            etag=s2.headers["ETag"],
            body={"comment": "please revise"},
        )
        assert rc.status_code == 200
        rc_row = [
            r
            for r in _audit_rows(bootstrap_engine, content_id=content_id2)
            if r["action"] == "content.review.request_changes"
        ][0]
        assert [r["resource_type"] for r in _related(rc_row)] == [
            "content.content_version",
            "content.review_decision",
        ]
        assert "please revise" not in json.dumps(rc_row, default=str)

        content_id3 = create_content(client, tenant_id)["content_id"]
        a3 = _append(client, tenant_id, content_id3, etag='"r0"')
        v3 = a3.json()["version_id"]
        s3 = submit_review(client, tenant_id, content_id3, v3, etag=a3.headers["ETag"])
        rejected = decide(
            client,
            tenant_id,
            content_id3,
            v3,
            action="reject",
            etag=s3.headers["ETag"],
            body={"reason_code": "out_of_scope"},
        )
        assert rejected.status_code == 200
        reject_row = [
            r
            for r in _audit_rows(bootstrap_engine, content_id=content_id3)
            if r["action"] == "content.review.reject"
        ][0]
        assert [r["resource_type"] for r in _related(reject_row)] == [
            "content.content_version",
            "content.review_decision",
        ]

    def test_submit_replay_and_auth_revocation_no_audit(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, principal_id)
        content_id = create_content(client, tenant_id)["content_id"]
        appended = _append(client, tenant_id, content_id, etag='"r0"')
        version_id = appended.json()["version_id"]
        key = f"submit-{uuid.uuid7()}"
        first = submit_review(
            client,
            tenant_id,
            content_id,
            version_id,
            etag=appended.headers["ETag"],
            **{"Idempotency-Key": key},
        )
        assert first.status_code == 200
        before = _audit_count(bootstrap_engine, content_id=content_id)
        intents = _workflow_intent_count(bootstrap_engine, content_id)
        events = len(outbox_rows(bootstrap_engine, content_id=content_id))
        replay = submit_review(
            client,
            tenant_id,
            content_id,
            version_id,
            etag=appended.headers["ETag"],
            **{"Idempotency-Key": key},
        )
        assert replay.status_code == 200
        assert _audit_count(bootstrap_engine, content_id=content_id) == before
        assert _workflow_intent_count(bootstrap_engine, content_id) == intents
        assert len(outbox_rows(bootstrap_engine, content_id=content_id)) == events

        # auth revoked on approve replay
        in_id, in_version, in_etag = in_review(client, tenant_id)
        approve_key = f"approve-{uuid.uuid7()}"
        ok = decide(
            client,
            tenant_id,
            in_id,
            in_version,
            action="approve",
            etag=in_etag,
            **{"Idempotency-Key": approve_key},
        )
        assert ok.status_code == 200
        audit_before = _audit_count(bootstrap_engine, content_id=in_id)
        denied = _client(
            runtime_engine,
            tenant_id,
            principal_id,
            authorization=AllowReviewAuthorization(allow_decide=False),
        )
        blocked = decide(
            denied,
            tenant_id,
            in_id,
            in_version,
            action="approve",
            etag=in_etag,
            **{"Idempotency-Key": approve_key},
        )
        assert blocked.status_code == 403
        assert _audit_count(bootstrap_engine, content_id=in_id) == audit_before

    def test_comment_policy_failure_no_audit(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = _client(
            runtime_engine,
            tenant_id,
            uuid.uuid7(),
            comment_policy=MarkerReviewCommentPolicy(),
        )
        content_id, version_id, etag = in_review(client, tenant_id)
        before = _audit_count(bootstrap_engine, content_id=content_id)
        before_decisions = _decision_count(bootstrap_engine, content_id)
        before_intents = _workflow_intent_count(bootstrap_engine, content_id)
        before_events = len(outbox_rows(bootstrap_engine, content_id=content_id))
        response = decide(
            client,
            tenant_id,
            content_id,
            version_id,
            action="approve",
            etag=etag,
            body={"comment": SENSITIVE_TEST_COMMENT},
        )
        assert response.status_code == 422
        assert _audit_count(bootstrap_engine, content_id=content_id) == before
        assert _decision_count(bootstrap_engine, content_id) == before_decisions
        assert _workflow_intent_count(bootstrap_engine, content_id) == before_intents
        assert len(outbox_rows(bootstrap_engine, content_id=content_id)) == before_events
        row = _content_row(bootstrap_engine, content_id)
        assert row.stewardship_state == "IN_REVIEW"

    def test_submit_and_decision_audit_failure_rollbacks(
        self, runtime_engine, bootstrap_engine, monkeypatch
    ) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id = create_content(client, tenant_id)["content_id"]
        appended = _append(client, tenant_id, content_id, etag='"r0"')
        version_id = appended.json()["version_id"]

        original = ContentSecurityMutationAuditRepository.insert

        def boom_after_create_append(self, record) -> None:
            if str(record.action).startswith("content.review"):
                raise PersistenceOperationFailed("content persistence operation failed")
            return original(self, record)

        monkeypatch.setattr(
            ContentSecurityMutationAuditRepository, "insert", boom_after_create_append
        )
        failed_submit = submit_review(
            client, tenant_id, content_id, version_id, etag=appended.headers["ETag"]
        )
        assert failed_submit.status_code == 503
        row = _content_row(bootstrap_engine, content_id)
        assert row.stewardship_state == "GENERATED"
        assert int(row.aggregate_revision) == 1
        assert _workflow_intent_count(bootstrap_engine, content_id) == 0
        assert _audit_count(bootstrap_engine, content_id=content_id) == 2

        # APPROVE failure
        monkeypatch.undo()
        content_id2, version_id2, etag2 = in_review(client, tenant_id)
        original2 = ContentSecurityMutationAuditRepository.insert

        def boom_approve(self, record) -> None:
            if str(record.action) == "content.review.approve":
                raise PersistenceOperationFailed("content persistence operation failed")
            return original2(self, record)

        monkeypatch.setattr(ContentSecurityMutationAuditRepository, "insert", boom_approve)
        failed_approve = decide(
            client, tenant_id, content_id2, version_id2, action="approve", etag=etag2
        )
        assert failed_approve.status_code == 503
        assert _decision_count(bootstrap_engine, content_id2) == 0
        row2 = _content_row(bootstrap_engine, content_id2)
        assert row2.stewardship_state == "IN_REVIEW"
        assert int(row2.aggregate_revision) == 2

        # REQUEST_CHANGES / REJECT parameterized
        for action, body in (
            ("request-changes", {"comment": "n"},),
            ("reject", {"reason_code": "x"},),
        ):
            monkeypatch.undo()
            cid, vid, et = in_review(client, tenant_id)
            orig = ContentSecurityMutationAuditRepository.insert
            expected = (
                "content.review.request_changes"
                if action == "request-changes"
                else "content.review.reject"
            )

            def boom_decision(self, record, _expected=expected, _orig=orig) -> None:
                if str(record.action) == _expected:
                    raise PersistenceOperationFailed("content persistence operation failed")
                return _orig(self, record)

            monkeypatch.setattr(ContentSecurityMutationAuditRepository, "insert", boom_decision)
            failed = decide(client, tenant_id, cid, vid, action=action, etag=et, body=body)
            assert failed.status_code == 503, action
            assert _decision_count(bootstrap_engine, cid) == 0
            assert _content_row(bootstrap_engine, cid).stewardship_state == "IN_REVIEW"

    def test_concurrent_approve_vs_reject_one_audit(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id, version_id, etag = in_review(client, tenant_id)
        barrier = threading.Barrier(2)
        results: list[int] = []

        def worker(action: str, body: dict | None = None) -> None:
            barrier.wait()
            resp = decide(
                client,
                tenant_id,
                content_id,
                version_id,
                action=action,
                etag=etag,
                body=body or {},
                **{"Idempotency-Key": f"{action}-{uuid.uuid7()}"},
            )
            results.append(resp.status_code)

        threads = [
            threading.Thread(target=worker, args=("approve",)),
            threading.Thread(target=worker, args=("reject", {"reason_code": "no"})),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert 200 in results
        assert 412 in results or results.count(200) == 1
        assert _decision_count(bootstrap_engine, content_id) == 1
        decision_audits = [
            r
            for r in _audit_rows(bootstrap_engine, content_id=content_id)
            if r["action"] in ("content.review.approve", "content.review.reject")
        ]
        assert len(decision_audits) == 1


class TestPublishAudit:
    def test_publish_shape_atomicity_replay_and_concurrent(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, principal_id)
        content_id, version_id, etag = _lifecycle_to_publish(client, tenant_id)
        rows = _audit_rows(bootstrap_engine, content_id=content_id)
        assert [r["action"] for r in rows] == [
            "content.create",
            "content.version.create",
            "content.review.submit",
            "content.review.approve",
            "content.publish",
        ]
        assert len(rows) == 5
        publish = rows[-1]
        related = _related(publish)
        assert [r["resource_type"] for r in related] == [
            "content.content_version",
            "content.review_decision",
            "content.publication",
        ]
        assert all(r["resource_revision"] is None for r in related)
        assert publish["resource_revision_after"] == 4
        assert publish["execution_channel"] == "API"
        assert publish["executing_principal_id"] == principal_id
        content = _content_row(bootstrap_engine, content_id)
        assert publish["occurred_at"] is not None
        events = [
            e
            for e in outbox_rows(bootstrap_engine, content_id=content_id)
            if e["event_type"] == EVENT_CONTENT_PUBLISHED_V1
        ]
        assert len(events) == 1
        env = dict(events[0]["envelope"])
        assert publish["correlation_id"] == UUID(env["correlationid"])
        assert publish["causation_id"] == UUID(env["causationid"])
        assert publish["audit_record_id"] != events[0]["event_id"]

        # replay same key
        key = f"pub-{uuid.uuid7()}"
        content_id2, version_id2, etag2 = in_review(client, tenant_id)
        approved = decide(
            client, tenant_id, content_id2, version_id2, action="approve", etag=etag2
        )
        first = client.post(
            f"/api/v1/contents/{content_id2}/actions/publish",
            json={"version_id": version_id2},
            headers={
                **headers(tenant_id, **{"Idempotency-Key": key}),
                "If-Match": approved.headers["ETag"],
            },
        )
        assert first.status_code == 200
        before = _audit_count(bootstrap_engine, content_id=content_id2)
        pubs = _publication_count(bootstrap_engine, content_id2)
        replay = client.post(
            f"/api/v1/contents/{content_id2}/actions/publish",
            json={"version_id": version_id2},
            headers={
                **headers(tenant_id, **{"Idempotency-Key": key}),
                "If-Match": approved.headers["ETag"],
            },
        )
        assert replay.status_code == 200
        assert _audit_count(bootstrap_engine, content_id=content_id2) == before
        assert _publication_count(bootstrap_engine, content_id2) == pubs

        # replay after head advance
        appended = _append(client, tenant_id, content_id2, etag=first.headers["ETag"])
        assert appended.status_code == 201
        after_append_audits = _audit_count(bootstrap_engine, content_id=content_id2)
        replay2 = client.post(
            f"/api/v1/contents/{content_id2}/actions/publish",
            json={"version_id": version_id2},
            headers={
                **headers(tenant_id, **{"Idempotency-Key": key}),
                "If-Match": approved.headers["ETag"],
            },
        )
        assert replay2.status_code == 200
        assert replay2.json()["aggregate_revision"] == first.json()["aggregate_revision"]
        assert int(_content_row(bootstrap_engine, content_id2).aggregate_revision) == 5
        assert _audit_count(bootstrap_engine, content_id=content_id2) == after_append_audits
        assert _publication_count(bootstrap_engine, content_id2) == 1

        # concurrent publish
        content_id3, version_id3, etag3 = in_review(client, tenant_id)
        approved3 = decide(
            client, tenant_id, content_id3, version_id3, action="approve", etag=etag3
        )
        barrier = threading.Barrier(2)
        results: list[int] = []

        def worker(k: str) -> None:
            barrier.wait()
            resp = client.post(
                f"/api/v1/contents/{content_id3}/actions/publish",
                json={"version_id": version_id3},
                headers={
                    **headers(tenant_id, **{"Idempotency-Key": k}),
                    "If-Match": approved3.headers["ETag"],
                },
            )
            results.append(resp.status_code)

        threads = [
            threading.Thread(target=worker, args=(f"p1-{uuid.uuid7()}",)),
            threading.Thread(target=worker, args=(f"p2-{uuid.uuid7()}",)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(results) == [200, 412]
        assert _publication_count(bootstrap_engine, content_id3) == 1
        pub_audits = [
            r
            for r in _audit_rows(bootstrap_engine, content_id=content_id3)
            if r["action"] == "content.publish"
        ]
        assert len(pub_audits) == 1

    def test_publish_audit_failure_rolls_back(
        self, runtime_engine, bootstrap_engine, monkeypatch
    ) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id, version_id, etag = in_review(client, tenant_id)
        approved = decide(
            client, tenant_id, content_id, version_id, action="approve", etag=etag
        )
        original = ContentSecurityMutationAuditRepository.insert

        def boom(self, record) -> None:
            if str(record.action) == "content.publish":
                raise PersistenceOperationFailed("content persistence operation failed")
            return original(self, record)

        monkeypatch.setattr(ContentSecurityMutationAuditRepository, "insert", boom)
        response = client.post(
            f"/api/v1/contents/{content_id}/actions/publish",
            json={"version_id": version_id},
            headers={**headers(tenant_id), "If-Match": approved.headers["ETag"]},
        )
        assert response.status_code == 503
        assert _publication_count(bootstrap_engine, content_id) == 0
        row = _content_row(bootstrap_engine, content_id)
        assert row.published_version_id is None
        assert int(row.aggregate_revision) == 3
        assert not any(
            e["event_type"] == EVENT_CONTENT_PUBLISHED_V1
            for e in outbox_rows(bootstrap_engine, content_id=content_id)
        )
        assert not any(
            r["action"] == "content.publish"
            for r in _audit_rows(bootstrap_engine, content_id=content_id)
        )


class TestLateFailureAndSanitizedErrors:
    def test_late_idempotency_failure_rolls_back_audit(
        self, runtime_engine, bootstrap_engine, monkeypatch
    ) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        original = SqlAlchemyIdempotencyRepository.insert

        def boom(self, outcome) -> None:
            raise PersistenceOperationFailed("content persistence operation failed")

        monkeypatch.setattr(SqlAlchemyIdempotencyRepository, "insert", boom)
        response = client.post(
            "/api/v1/contents",
            json={
                "content_type": "test.generic",
                "title": "Title",
                "description": "Description",
                "locale": "en-IN",
            },
            headers=headers(tenant_id),
        )
        assert response.status_code == 503
        with bootstrap_engine.connect() as conn:
            contents = int(
                conn.execute(
                    text("SELECT count(*) FROM content.contents WHERE tenant_id = :tid"),
                    {"tid": tenant_id},
                ).scalar_one()
            )
            audits = int(
                conn.execute(
                    text("SELECT count(*) FROM security.audit_records WHERE tenant_id = :tid"),
                    {"tid": tenant_id},
                ).scalar_one()
            )
            outs = int(
                conn.execute(
                    text(
                        "SELECT count(*) FROM integration.outbox_messages WHERE tenant_id = :tid"
                    ),
                    {"tid": tenant_id},
                ).scalar_one()
            )
            idem = int(
                conn.execute(
                    text("SELECT count(*) FROM api.idempotency_records WHERE tenant_id = :tid"),
                    {"tid": tenant_id},
                ).scalar_one()
            )
        assert contents == 0
        assert audits == 0
        assert outs == 0
        assert idem == 0
        # silence unused
        assert original is not None

    def test_platform_audit_error_translates_without_leak(
        self, runtime_engine, bootstrap_engine, monkeypatch
    ) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())

        def boom(self, record) -> None:
            raise SecurityAuditPersistenceError(
                "DETAIL: INSERT INTO security.audit_records failed role=aieos_security "
                "url=postgresql://user:password@host/db constraint=chk_x"
            )

        monkeypatch.setattr(SqlAlchemySecurityMutationAuditRepository, "insert", boom)
        response = client.post(
            "/api/v1/contents",
            json={
                "content_type": "test.generic",
                "title": "Title",
                "description": "Description",
                "locale": "en-IN",
            },
            headers=headers(tenant_id),
        )
        assert response.status_code == 503
        assert response.json()["code"] == "persistence_unavailable"
        _assert_no_leak(response)
        assert "security.audit_records" not in response.text
        assert "password" not in response.text.lower()


class TestCrossTenantCoherence:
    def test_audit_tenant_matches_execution_tenant(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        client_a = _client(runtime_engine, tenant_a, uuid.uuid7())
        client_b = _client(runtime_engine, tenant_b, uuid.uuid7())
        a_id = create_content(client_a, tenant_a)["content_id"]
        b_id = create_content(client_b, tenant_b)["content_id"]
        a_row = _audit_rows(bootstrap_engine, content_id=a_id)[0]
        b_row = _audit_rows(bootstrap_engine, content_id=b_id)[0]
        assert a_row["tenant_id"] == tenant_a
        assert b_row["tenant_id"] == tenant_b
        assert a_row["tenant_id"] != b_row["tenant_id"]
