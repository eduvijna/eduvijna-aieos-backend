"""SAI-I05 adversarial gate: atomicity, replay, concurrency, tenancy, privileges."""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError
from temporalio.testing import WorkflowEnvironment

from aieos.domains.content.application.audit import (
    ai_materialization_audit_provenance,
    migration_audit_provenance,
)
from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.application.errors import PersistenceOperationFailed
from aieos.domains.content.application.models import AIGeneratedVersionMaterializationCommand
from aieos.domains.content.domain.identities import AggregateRevision
from aieos.domains.content.infrastructure.persistence.audit_repository import (
    ContentSecurityMutationAuditRepository,
)
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWork,
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
    ERROR_NATS_UNAVAILABLE,
    EVENT_CONTENT_PUBLISHED_V1,
    EVENT_CONTENT_VERSION_CREATED_V1,
    OUTBOX_PENDING,
)
from aieos.platform.events.models import MutationEventContext
from aieos.platform.security.audit import (
    SecurityAuditAction,
    SecurityAuditExecutionChannel,
    build_security_mutation_audit_record,
)
from aieos.platform.security.audit.persistence.errors import SecurityAuditPersistenceError
from aieos.platform.security.audit.persistence.repositories import (
    SqlAlchemySecurityMutationAuditRepository,
)
from aieos.platform.workflows.temporal.content_review import ContentReviewWorkflowV1
from aieos.platform.workflows.temporal.gateway import TemporalClientReviewGateway
from aieos.platform.workflows.temporal.worker import create_content_review_worker
from tests.dbutil import set_tenant
from tests.domains.content.application.test_gci_i11_materialization import (
    FIXED_NOW as AI_NOW,
    _materializer,
    _provenance,
    _seed_content,
)
from tests.domains.content.application.test_gci_i13_import import (
    DIGEST_B,
    FIXED_NOW as MIG_NOW,
    _candidate,
    _importer,
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
from tests.platform.events.test_gci_i08_dispatcher import (
    FakePublisher,
    PublishResult,
    make_dispatcher,
)
from tests.platform.workflows.helpers import (
    command_intent_rows,
    create_content,
    decide,
    headers,
    in_review,
    run_async,
    start_dispatcher,
    start_intent_rows,
    submit_review,
)

pytestmark = pytest.mark.sai_i05

CURSOR_KEY = b"sai-i05-test-cursor-signing-key"
FIXED_NOW = datetime(2026, 8, 15, 8, 0, tzinfo=UTC)
FROZEN_ACTIONS = {
    "content.create",
    "content.version.create",
    "content.review.submit",
    "content.review.approve",
    "content.review.request_changes",
    "content.review.reject",
    "content.publish",
    "content.ai.materialize",
    "content.migration.import",
}
LEAK_NEEDLES = (
    "sqlalchemy",
    "psycopg",
    "Traceback",
    "password",
    "security.audit_records",
    "INSERT INTO",
    "aieos_security",
    "postgresql://",
    "constraint",
)


def _app(runtime_engine: Engine, tenant_id: UUID, principal_id: UUID, **kw):
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
        review_authorization=kw.get("authorization") or AllowReviewAuthorization(),
        review_comment_policy=kw.get("comment_policy") or AllowReviewCommentPolicy(),
        publication_authorization=kw.get("publication_authorization")
        or AllowPublicationAuthorization(),
        publication_governance=AllowPublicationGovernance(),
        asset_reference_validation=AllowAssetReferenceValidation(),
        asset_current_governance=AllowAssetCurrentGovernance(),
    )


def _client(runtime_engine: Engine, tenant_id: UUID, principal_id: UUID, **kw) -> TestClient:
    return TestClient(_app(runtime_engine, tenant_id, principal_id, **kw), raise_server_exceptions=False)


def _audit_rows(bootstrap_engine: Engine, *, content_id: str | UUID | None = None) -> list[dict]:
    sql = "SELECT * FROM security.audit_records"
    params: dict[str, object] = {}
    if content_id is not None:
        sql += " WHERE primary_resource_id = :cid"
        params["cid"] = UUID(str(content_id))
    sql += " ORDER BY occurred_at, audit_record_id"
    with bootstrap_engine.connect() as conn:
        return [dict(r) for r in conn.execute(text(sql), params).mappings().all()]


def _audit_actions(bootstrap_engine: Engine, *, content_id: str | UUID | None = None) -> list[str]:
    return [r["action"] for r in _audit_rows(bootstrap_engine, content_id=content_id)]


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


def _counts(bootstrap_engine: Engine, content_id: str | UUID) -> dict[str, int]:
    cid = UUID(str(content_id))
    with bootstrap_engine.connect() as conn:
        versions = int(
            conn.execute(
                text("SELECT count(*) FROM content.content_versions WHERE content_id = :cid"),
                {"cid": cid},
            ).scalar_one()
        )
        decisions = int(
            conn.execute(
                text("SELECT count(*) FROM content.review_decisions WHERE content_id = :cid"),
                {"cid": cid},
            ).scalar_one()
        )
        pubs = int(
            conn.execute(
                text("SELECT count(*) FROM content.publications WHERE content_id = :cid"),
                {"cid": cid},
            ).scalar_one()
        )
        assets = int(
            conn.execute(
                text(
                    "SELECT count(*) FROM content.version_asset_refs var "
                    "JOIN content.content_versions cv ON cv.version_id = var.version_id "
                    "WHERE cv.content_id = :cid"
                ),
                {"cid": cid},
            ).scalar_one()
        )
    return {
        "versions": versions,
        "decisions": decisions,
        "publications": pubs,
        "assets": assets,
        "events": len(outbox_rows(bootstrap_engine, content_id=str(content_id))),
        "audits": len(_audit_rows(bootstrap_engine, content_id=content_id)),
    }


def _append(client: TestClient, tenant_id: UUID, content_id: str, *, etag: str, **extra):
    hdrs = headers(tenant_id, **extra)
    hdrs["If-Match"] = etag
    return client.post(
        f"/api/v1/contents/{content_id}/versions",
        json={"schema_id": "test.generic", "schema_version": 1, "payload": {"marker": "v1"}},
        headers=hdrs,
    )


def _event(*, actor: UUID, effective: UUID, correlation: UUID | None = None) -> MutationEventContext:
    return MutationEventContext(
        correlation_id=correlation or uuid.uuid7(),
        causation_id=uuid.uuid7(),
        actor_principal_id=actor,
        effective_actor_id=effective,
    )


def _assert_no_leak(response) -> None:
    blob = (response.text + json.dumps(response.json())).lower()
    for needle in LEAK_NEEDLES:
        assert needle.lower() not in blob, needle


def _record(*, tenant_id: UUID | None = None):
    from aieos.platform.resources import ResourceRef

    tid = tenant_id or uuid.uuid7()
    principal = uuid.uuid7()
    return build_security_mutation_audit_record(
        tenant_id=tid,
        action=SecurityAuditAction.CONTENT_CREATE,
        primary_resource_ref=ResourceRef("content.content", uuid.uuid7(), 0),
        resource_revision_before=None,
        resource_revision_after=0,
        related_resource_refs=(),
        mutation_event_context=_event(actor=principal, effective=principal),
        executing_principal_id=principal,
        execution_channel=SecurityAuditExecutionChannel.API,
        occurred_at=FIXED_NOW,
        delegation_id=None,
        trace_id=None,
    )


class TestExactVocabularyAndCardinality:
    def test_combined_lifecycle_vocabulary_and_one_audit_per_action(
        self, runtime_engine, migration_runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, principal)

        created = create_content(client, tenant_id)
        content_id = created["content_id"]
        appended = _append(client, tenant_id, content_id, etag='"r0"')
        assert appended.status_code == 201, appended.text
        version_id = appended.json()["version_id"]
        submitted = submit_review(
            client, tenant_id, content_id, version_id, etag=appended.headers["ETag"]
        )
        assert submitted.status_code == 200
        # request-changes path (comment present; body must not enter audit)
        rc = decide(
            client,
            tenant_id,
            content_id,
            version_id,
            action="request-changes",
            etag=submitted.headers["ETag"],
            body={"reason_code": "needs_work", "comment": SENSITIVE_TEST_COMMENT},
        )
        assert rc.status_code == 200, rc.text
        # re-submit and reject
        appended2 = _append(client, tenant_id, content_id, etag=rc.headers["ETag"])
        assert appended2.status_code == 201
        v2 = appended2.json()["version_id"]
        submitted2 = submit_review(
            client, tenant_id, content_id, v2, etag=appended2.headers["ETag"]
        )
        rejected = decide(
            client,
            tenant_id,
            content_id,
            v2,
            action="reject",
            etag=submitted2.headers["ETag"],
            body={"reason_code": "no", "comment": SENSITIVE_TEST_COMMENT},
        )
        assert rejected.status_code == 200
        # approve + publish path on a fresh content
        content_id2, version_id2, etag2 = in_review(client, tenant_id)
        approved = decide(
            client, tenant_id, content_id2, version_id2, action="approve", etag=etag2
        )
        published = client.post(
            f"/api/v1/contents/{content_id2}/actions/publish",
            json={"version_id": version_id2},
            headers={**headers(tenant_id), "If-Match": approved.headers["ETag"]},
        )
        assert published.status_code == 200

        # AI materialize
        ai_content = _seed_content(bootstrap_engine, tenant_id)
        corr = uuid.uuid7()
        _materializer(runtime_engine).materialize(
            tenant_id,
            principal,
            AIGeneratedVersionMaterializationCommand(
                content_id=ai_content,
                expected_aggregate_revision=AggregateRevision(0),
                schema_id="test.generic",
                schema_version=1,
                payload={"marker": "ai", "prompt": "should-not-audit"},
                provenance=_provenance(corr),
            ),
            event_context=_event(actor=principal, effective=principal, correlation=corr),
            audit_provenance=ai_materialization_audit_provenance(principal),
            now=AI_NOW,
        )

        # migration import
        mig = _importer(migration_runtime_engine).import_content(
            tenant_id,
            principal,
            _candidate(source_resource_id=f"sai-i05-vocab-{uuid.uuid7()}"),
            event_context=_event(actor=principal, effective=principal),
            audit_provenance=migration_audit_provenance(principal),
            now=MIG_NOW,
        )

        with bootstrap_engine.connect() as conn:
            actions = {
                r[0]
                for r in conn.execute(
                    text(
                        "SELECT DISTINCT action FROM security.audit_records "
                        "WHERE tenant_id = :tid"
                    ),
                    {"tid": tenant_id},
                )
            }
        assert actions == FROZEN_ACTIONS
        assert "content.archive" not in actions
        assert "content.mutate" not in actions
        assert "content.workflow" not in actions

        # cardinality: each action family has matching business success audits
        for cid in (content_id, content_id2, str(ai_content.value), str(mig.content_id.value)):
            rows = _audit_rows(bootstrap_engine, content_id=cid)
            assert len(rows) == len({(r["action"], r["audit_record_id"]) for r in rows})
            for row in rows:
                blob = json.dumps({k: str(v) for k, v in row.items()}, default=str).lower()
                assert SENSITIVE_TEST_COMMENT.lower() not in blob
                assert "should-not-audit" not in blob
                assert "prompt" not in blob or '"prompt"' not in blob


class TestAtomicityInjections:
    def test_create_audit_and_outbox_failure_rollbacks(
        self, runtime_engine, bootstrap_engine, monkeypatch
    ) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())

        monkeypatch.setattr(
            ContentSecurityMutationAuditRepository,
            "insert",
            lambda self, record: (_ for _ in ()).throw(
                PersistenceOperationFailed("content persistence operation failed")
            ),
        )
        r = client.post(
            "/api/v1/contents",
            json={
                "content_type": "test.generic",
                "title": "Title",
                "description": "Description",
                "locale": "en-IN",
            },
            headers=headers(tenant_id),
        )
        assert r.status_code == 503
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT count(*) FROM content.contents WHERE tenant_id = :t"),
                    {"t": tenant_id},
                ).scalar_one()
                == 0
            )
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM security.audit_records WHERE tenant_id = :t"
                    ),
                    {"t": tenant_id},
                ).scalar_one()
                == 0
            )
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM api.idempotency_records WHERE tenant_id = :t"
                    ),
                    {"t": tenant_id},
                ).scalar_one()
                == 0
            )

        monkeypatch.undo()
        tenant_id2 = uuid.uuid7()
        client2 = _client(runtime_engine, tenant_id2, uuid.uuid7())

        from aieos.platform.events.persistence.repositories import (
            SqlAlchemyOutboxRepository,
        )

        def boom_outbox(self, message) -> None:
            raise PersistenceOperationFailed("content persistence operation failed")

        monkeypatch.setattr(SqlAlchemyOutboxRepository, "insert", boom_outbox)
        r2 = client2.post(
            "/api/v1/contents",
            json={
                "content_type": "test.generic",
                "title": "Title",
                "description": "Description",
                "locale": "en-IN",
            },
            headers=headers(tenant_id2),
        )
        assert r2.status_code == 503
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT count(*) FROM content.contents WHERE tenant_id = :t"),
                    {"t": tenant_id2},
                ).scalar_one()
                == 0
            )
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM security.audit_records WHERE tenant_id = :t"
                    ),
                    {"t": tenant_id2},
                ).scalar_one()
                == 0
            )
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM api.idempotency_records WHERE tenant_id = :t"
                    ),
                    {"t": tenant_id2},
                ).scalar_one()
                == 0
            )

    def test_append_submit_decide_publish_audit_failures(
        self, runtime_engine, bootstrap_engine, monkeypatch
    ) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        created = create_content(client, tenant_id)
        content_id = created["content_id"]
        original = ContentSecurityMutationAuditRepository.insert

        def boom_action(action: str):
            def boom(self, record):
                if str(record.action) == action:
                    raise PersistenceOperationFailed(
                        "content persistence operation failed"
                    )
                return original(self, record)

            return boom

        monkeypatch.setattr(
            ContentSecurityMutationAuditRepository,
            "insert",
            boom_action("content.version.create"),
        )
        appended = _append(client, tenant_id, content_id, etag='"r0"')
        assert appended.status_code == 503
        head = _content_row(bootstrap_engine, content_id)
        assert head.current_version_id is None
        assert int(head.aggregate_revision) == 0
        assert _counts(bootstrap_engine, content_id)["versions"] == 0
        assert "content.version.create" not in _audit_actions(
            bootstrap_engine, content_id=content_id
        )

        monkeypatch.undo()
        appended = _append(client, tenant_id, content_id, etag='"r0"')
        assert appended.status_code == 201
        version_id = appended.json()["version_id"]

        monkeypatch.setattr(
            ContentSecurityMutationAuditRepository,
            "insert",
            boom_action("content.review.submit"),
        )
        submitted = submit_review(
            client, tenant_id, content_id, version_id, etag=appended.headers["ETag"]
        )
        assert submitted.status_code == 503
        head = _content_row(bootstrap_engine, content_id)
        assert head.stewardship_state != "IN_REVIEW"
        assert int(head.aggregate_revision) == 1
        assert start_intent_rows(bootstrap_engine, content_id) == []

        monkeypatch.undo()
        submitted = submit_review(
            client, tenant_id, content_id, version_id, etag=appended.headers["ETag"]
        )
        assert submitted.status_code == 200

        for action_name, route in (
            ("content.review.approve", "approve"),
            ("content.review.request_changes", "request-changes"),
            ("content.review.reject", "reject"),
        ):
            # fresh in-review for each decision type
            cid, vid, etag = in_review(client, tenant_id)
            monkeypatch.setattr(
                ContentSecurityMutationAuditRepository,
                "insert",
                boom_action(action_name),
            )
            body = {"reason_code": "x", "comment": "c"} if route != "approve" else {}
            resp = decide(
                client, tenant_id, cid, vid, action=route, etag=etag, body=body
            )
            assert resp.status_code == 503, route
            head = _content_row(bootstrap_engine, cid)
            assert head.stewardship_state == "IN_REVIEW"
            assert _counts(bootstrap_engine, cid)["decisions"] == 0
            assert action_name not in _audit_actions(bootstrap_engine, content_id=cid)
            assert command_intent_rows(bootstrap_engine, cid) == []
            monkeypatch.undo()

        cid, vid, etag = in_review(client, tenant_id)
        approved = decide(client, tenant_id, cid, vid, action="approve", etag=etag)
        monkeypatch.setattr(
            ContentSecurityMutationAuditRepository,
            "insert",
            boom_action("content.publish"),
        )
        published = client.post(
            f"/api/v1/contents/{cid}/actions/publish",
            json={"version_id": vid},
            headers={**headers(tenant_id), "If-Match": approved.headers["ETag"]},
        )
        assert published.status_code == 503
        head = _content_row(bootstrap_engine, cid)
        assert head.published_version_id is None
        assert _counts(bootstrap_engine, cid)["publications"] == 0
        assert not any(
            e["event_type"] == EVENT_CONTENT_PUBLISHED_V1
            for e in outbox_rows(bootstrap_engine, content_id=cid)
        )
        assert "content.publish" not in _audit_actions(bootstrap_engine, content_id=cid)

    def test_late_failure_api_and_internal_remove_inserted_audit(
        self, runtime_engine, migration_runtime_engine, bootstrap_engine, monkeypatch
    ) -> None:
        # API path: late idempotency failure after audit insert
        tenant_a = uuid.uuid7()
        client = _client(runtime_engine, tenant_a, uuid.uuid7())

        def boom_idem(self, outcome) -> None:
            raise PersistenceOperationFailed("content persistence operation failed")

        monkeypatch.setattr(SqlAlchemyIdempotencyRepository, "insert", boom_idem)
        r = client.post(
            "/api/v1/contents",
            json={
                "content_type": "test.generic",
                "title": "Title",
                "description": "Description",
                "locale": "en-IN",
            },
            headers=headers(tenant_a),
        )
        assert r.status_code == 503
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM security.audit_records WHERE tenant_id = :t"
                    ),
                    {"t": tenant_a},
                ).scalar_one()
                == 0
            )
        monkeypatch.undo()

        # Internal AI path: commit failure after audit insert
        tenant_b = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_b)
        corr = uuid.uuid7()

        def boom_commit(self) -> None:
            raise PersistenceOperationFailed("content persistence operation failed")

        monkeypatch.setattr(SqlAlchemyContentUnitOfWork, "commit", boom_commit)
        with pytest.raises(PersistenceOperationFailed):
            _materializer(runtime_engine).materialize(
                tenant_b,
                uuid.uuid7(),
                AIGeneratedVersionMaterializationCommand(
                    content_id=content_id,
                    expected_aggregate_revision=AggregateRevision(0),
                    schema_id="test.generic",
                    schema_version=1,
                    payload={"marker": "ai"},
                    provenance=_provenance(corr),
                ),
                event_context=_event(
                    actor=uuid.uuid7(), effective=uuid.uuid7(), correlation=corr
                ),
                audit_provenance=ai_materialization_audit_provenance(uuid.uuid7()),
                now=AI_NOW,
            )
        assert _audit_rows(bootstrap_engine, content_id=content_id.value) == []
        assert _content_row(bootstrap_engine, content_id.value).current_version_id is None


class TestReplayIdempotencyAndAuthRevocation:
    def test_replays_unchanged_and_changed_key_and_revoked_auth(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal = uuid.uuid7()
        auth = AllowReviewAuthorization()
        pub_auth = AllowPublicationAuthorization()
        client = _client(
            runtime_engine,
            tenant_id,
            principal,
            authorization=auth,
            publication_authorization=pub_auth,
        )

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
        assert len(_audit_rows(bootstrap_engine, content_id=content_id)) == 1
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
        assert len(_audit_rows(bootstrap_engine, content_id=content_id)) == 1
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
        assert len(_audit_rows(bootstrap_engine, content_id=content_id)) == 1

        # append + submit + approve + publish replays
        append_key = f"append-{uuid.uuid7()}"
        appended = _append(
            client, tenant_id, content_id, etag='"r0"', **{"Idempotency-Key": append_key}
        )
        assert appended.status_code == 201
        audits_after_append = len(_audit_rows(bootstrap_engine, content_id=content_id))
        replay_append = _append(
            client, tenant_id, content_id, etag='"r0"', **{"Idempotency-Key": append_key}
        )
        assert replay_append.status_code == 201
        assert len(_audit_rows(bootstrap_engine, content_id=content_id)) == audits_after_append

        version_id = appended.json()["version_id"]
        submit_key = f"submit-{uuid.uuid7()}"
        submitted = submit_review(
            client,
            tenant_id,
            content_id,
            version_id,
            etag=appended.headers["ETag"],
            **{"Idempotency-Key": submit_key},
        )
        assert submitted.status_code == 200
        audits_submit = len(_audit_rows(bootstrap_engine, content_id=content_id))
        events_submit = len(outbox_rows(bootstrap_engine, content_id=content_id))
        intents_submit = len(start_intent_rows(bootstrap_engine, content_id))
        replay_submit = submit_review(
            client,
            tenant_id,
            content_id,
            version_id,
            etag=appended.headers["ETag"],
            **{"Idempotency-Key": submit_key},
        )
        assert replay_submit.status_code == 200
        assert len(_audit_rows(bootstrap_engine, content_id=content_id)) == audits_submit
        assert len(outbox_rows(bootstrap_engine, content_id=content_id)) == events_submit
        assert len(start_intent_rows(bootstrap_engine, content_id)) == intents_submit

        approve_key = f"approve-{uuid.uuid7()}"
        approved = decide(
            client,
            tenant_id,
            content_id,
            version_id,
            action="approve",
            etag=submitted.headers["ETag"],
            **{"Idempotency-Key": approve_key},
        )
        assert approved.status_code == 200
        audits_approve = len(_audit_rows(bootstrap_engine, content_id=content_id))
        replay_approve = decide(
            client,
            tenant_id,
            content_id,
            version_id,
            action="approve",
            etag=submitted.headers["ETag"],
            **{"Idempotency-Key": approve_key},
        )
        assert replay_approve.status_code == 200
        assert len(_audit_rows(bootstrap_engine, content_id=content_id)) == audits_approve

        publish_key = f"publish-{uuid.uuid7()}"
        published = client.post(
            f"/api/v1/contents/{content_id}/actions/publish",
            json={"version_id": version_id},
            headers={
                **headers(tenant_id, **{"Idempotency-Key": publish_key}),
                "If-Match": approved.headers["ETag"],
            },
        )
        assert published.status_code == 200
        audits_pub = len(_audit_rows(bootstrap_engine, content_id=content_id))
        replay_pub = client.post(
            f"/api/v1/contents/{content_id}/actions/publish",
            json={"version_id": version_id},
            headers={
                **headers(tenant_id, **{"Idempotency-Key": publish_key}),
                "If-Match": approved.headers["ETag"],
            },
        )
        assert replay_pub.status_code == 200
        assert len(_audit_rows(bootstrap_engine, content_id=content_id)) == audits_pub

        # revoke decide authority then replay established approve key on fresh IN_REVIEW
        in_id, in_version, in_etag = in_review(client, tenant_id)
        approve_key2 = f"approve-revoke-{uuid.uuid7()}"
        ok_approve = decide(
            client,
            tenant_id,
            in_id,
            in_version,
            action="approve",
            etag=in_etag,
            **{"Idempotency-Key": approve_key2},
        )
        assert ok_approve.status_code == 200
        audit_before_revoke = len(_audit_rows(bootstrap_engine, content_id=in_id))
        revoked_client = _client(
            runtime_engine,
            tenant_id,
            principal,
            authorization=AllowReviewAuthorization(allow_decide=False),
            publication_authorization=pub_auth,
        )
        denied = decide(
            revoked_client,
            tenant_id,
            in_id,
            in_version,
            action="approve",
            etag=in_etag,
            **{"Idempotency-Key": approve_key2},
        )
        assert denied.status_code == 403
        assert len(_audit_rows(bootstrap_engine, content_id=in_id)) == audit_before_revoke


class TestConcurrency:
    def test_concurrent_append_review_publish_ai_migration(
        self, runtime_engine, migration_runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, principal)

        # concurrent append
        created = create_content(client, tenant_id)
        content_id = created["content_id"]
        barrier = threading.Barrier(2)
        results: list[object] = []

        def append_worker(key: str) -> None:
            barrier.wait(timeout=10)
            results.append(
                _append(
                    client,
                    tenant_id,
                    content_id,
                    etag='"r0"',
                    **{"Idempotency-Key": key},
                )
            )

        threads = [
            threading.Thread(target=append_worker, args=(f"a-{uuid.uuid7()}",)),
            threading.Thread(target=append_worker, args=(f"b-{uuid.uuid7()}",)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        successes = [r for r in results if getattr(r, "status_code", None) == 201]
        assert len(successes) == 1
        assert _counts(bootstrap_engine, content_id)["versions"] == 1
        assert (
            _audit_actions(bootstrap_engine, content_id=content_id).count(
                "content.version.create"
            )
            == 1
        )
        assert (
            sum(
                1
                for e in outbox_rows(bootstrap_engine, content_id=content_id)
                if e["event_type"] == EVENT_CONTENT_VERSION_CREATED_V1
            )
            == 1
        )

        # concurrent approve vs reject
        cid, vid, etag = in_review(client, tenant_id)
        barrier2 = threading.Barrier(2)
        decide_results: list[object] = []

        def decide_worker(action: str, body: dict) -> None:
            barrier2.wait(timeout=10)
            decide_results.append(
                decide(client, tenant_id, cid, vid, action=action, etag=etag, body=body)
            )

        threads = [
            threading.Thread(target=decide_worker, args=("approve", {})),
            threading.Thread(
                target=decide_worker, args=("reject", {"reason_code": "no"})
            ),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        ok = [r for r in decide_results if getattr(r, "status_code", None) == 200]
        assert len(ok) == 1
        assert _counts(bootstrap_engine, cid)["decisions"] == 1
        review_audits = [
            a
            for a in _audit_actions(bootstrap_engine, content_id=cid)
            if a.startswith("content.review.") and a != "content.review.submit"
        ]
        assert len(review_audits) == 1

        # concurrent publish
        cid2, vid2, etag2 = in_review(client, tenant_id)
        approved = decide(client, tenant_id, cid2, vid2, action="approve", etag=etag2)
        barrier3 = threading.Barrier(2)
        pub_results: list[object] = []

        def publish_worker(key: str) -> None:
            barrier3.wait(timeout=10)
            pub_results.append(
                client.post(
                    f"/api/v1/contents/{cid2}/actions/publish",
                    json={"version_id": vid2},
                    headers={
                        **headers(tenant_id, **{"Idempotency-Key": key}),
                        "If-Match": approved.headers["ETag"],
                    },
                )
            )

        threads = [
            threading.Thread(target=publish_worker, args=(f"p1-{uuid.uuid7()}",)),
            threading.Thread(target=publish_worker, args=(f"p2-{uuid.uuid7()}",)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sum(1 for r in pub_results if getattr(r, "status_code", None) == 200) == 1
        assert _counts(bootstrap_engine, cid2)["publications"] == 1
        assert (
            _audit_actions(bootstrap_engine, content_id=cid2).count("content.publish")
            == 1
        )

        # concurrent AI
        ai_id = _seed_content(bootstrap_engine, tenant_id)
        barrier4 = threading.Barrier(2)
        ai_results: list[object] = []

        def ai_worker(marker: str) -> None:
            barrier4.wait(timeout=10)
            try:
                corr = uuid.uuid7()
                ai_results.append(
                    _materializer(runtime_engine).materialize(
                        tenant_id,
                        principal,
                        AIGeneratedVersionMaterializationCommand(
                            content_id=ai_id,
                            expected_aggregate_revision=AggregateRevision(0),
                            schema_id="test.generic",
                            schema_version=1,
                            payload={"marker": marker},
                            provenance=_provenance(corr),
                        ),
                        event_context=_event(
                            actor=principal, effective=principal, correlation=corr
                        ),
                        audit_provenance=ai_materialization_audit_provenance(principal),
                        now=AI_NOW,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                ai_results.append(exc)

        threads = [
            threading.Thread(target=ai_worker, args=("left",)),
            threading.Thread(target=ai_worker, args=("right",)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sum(1 for r in ai_results if not isinstance(r, Exception)) == 1
        assert (
            _audit_actions(bootstrap_engine, content_id=ai_id.value).count(
                "content.ai.materialize"
            )
            == 1
        )

        # concurrent migration same source
        source = f"sai-i05-conc-{uuid.uuid7()}"
        barrier5 = threading.Barrier(2)
        mig_results: list[object] = []

        def mig_worker() -> None:
            barrier5.wait(timeout=10)
            try:
                mig_results.append(
                    _importer(migration_runtime_engine).import_content(
                        tenant_id,
                        principal,
                        _candidate(source_resource_id=source),
                        event_context=_event(actor=principal, effective=principal),
                        audit_provenance=migration_audit_provenance(principal),
                        now=MIG_NOW,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                mig_results.append(exc)

        threads = [threading.Thread(target=mig_worker), threading.Thread(target=mig_worker)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        successes = [r for r in mig_results if not isinstance(r, Exception)]
        assert len(successes) >= 1
        targets = {str(r.content_id.value) for r in successes}
        assert len(targets) == 1
        target = next(iter(targets))
        assert (
            _audit_actions(bootstrap_engine, content_id=target).count(
                "content.migration.import"
            )
            == 1
        )

        # changed digest concurrency / conflict
        source2 = f"sai-i05-digest-{uuid.uuid7()}"
        first = _importer(migration_runtime_engine).import_content(
            tenant_id,
            principal,
            _candidate(source_resource_id=source2),
            event_context=_event(actor=principal, effective=principal),
            audit_provenance=migration_audit_provenance(principal),
            now=MIG_NOW,
        )
        from aieos.domains.content.application.errors import MigrationSourceConflict

        with pytest.raises(MigrationSourceConflict):
            _importer(migration_runtime_engine).import_content(
                tenant_id,
                principal,
                _candidate(source_resource_id=source2, digest=DIGEST_B),
                event_context=_event(actor=principal, effective=principal),
                audit_provenance=migration_audit_provenance(principal),
                now=MIG_NOW,
            )
        assert (
            _audit_actions(bootstrap_engine, content_id=first.content_id.value).count(
                "content.migration.import"
            )
            == 1
        )


class TestTenancyAndPrivileges:
    def test_http_tenant_spoof_denied_and_audit_uses_trusted_tenant(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        trusted = uuid.uuid7()
        spoof = uuid.uuid7()
        principal = uuid.uuid7()
        client = _client(runtime_engine, trusted, principal)
        # wrong tenant header
        denied = client.post(
            "/api/v1/contents",
            json={
                "content_type": "test.generic",
                "title": "Title",
                "description": "Description",
                "locale": "en-IN",
            },
            headers=headers(spoof),
        )
        assert denied.status_code in {401, 403}
        # body tenant spoof rejected by schema
        bad_body = client.post(
            "/api/v1/contents",
            json={
                "content_type": "test.generic",
                "title": "Title",
                "description": "Description",
                "locale": "en-IN",
                "tenant_id": str(spoof),
            },
            headers=headers(trusted),
        )
        assert bad_body.status_code == 422
        ok = create_content(client, trusted)
        row = _audit_rows(bootstrap_engine, content_id=ok["content_id"])[0]
        assert row["tenant_id"] == trusted
        assert row["tenant_id"] != spoof

    def test_cross_tenant_missing_and_pooled_context(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        record_a = _record(tenant_id=tenant_a)
        with runtime_engine.connect() as conn:
            with conn.begin():
                set_tenant(conn, tenant_a)
                SqlAlchemySecurityMutationAuditRepository(conn).insert(record_a)
        # cross-tenant write
        record_b = _record(tenant_id=tenant_b)
        with runtime_engine.connect() as conn:
            with conn.begin():
                set_tenant(conn, tenant_a)
                with pytest.raises(SecurityAuditPersistenceError):
                    SqlAlchemySecurityMutationAuditRepository(conn).insert(record_b)
        # missing tenant fail closed
        with runtime_engine.connect() as conn:
            with conn.begin():
                with pytest.raises(SecurityAuditPersistenceError):
                    SqlAlchemySecurityMutationAuditRepository(conn).insert(_record())
        # pooled: A commit, B missing fails, C tenant B ok
        with runtime_engine.connect() as conn:
            with conn.begin():
                set_tenant(conn, tenant_a)
                SqlAlchemySecurityMutationAuditRepository(conn).insert(
                    _record(tenant_id=tenant_a)
                )
            with conn.begin():
                with pytest.raises(SecurityAuditPersistenceError):
                    SqlAlchemySecurityMutationAuditRepository(conn).insert(
                        _record(tenant_id=tenant_b)
                    )
            with conn.begin():
                set_tenant(conn, tenant_b)
                SqlAlchemySecurityMutationAuditRepository(conn).insert(
                    _record(tenant_id=tenant_b)
                )

    def test_cross_tenant_business_event_audit_coherence(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        pa = uuid.uuid7()
        pb = uuid.uuid7()
        ca = create_content(_client(runtime_engine, tenant_a, pa), tenant_a)
        cb = create_content(_client(runtime_engine, tenant_b, pb), tenant_b)
        ra = _audit_rows(bootstrap_engine, content_id=ca["content_id"])[0]
        rb = _audit_rows(bootstrap_engine, content_id=cb["content_id"])[0]
        assert ra["tenant_id"] == tenant_a
        assert rb["tenant_id"] == tenant_b
        assert ra["initiating_principal_id"] == pa
        assert rb["initiating_principal_id"] == pb
        ea = outbox_rows(bootstrap_engine, content_id=ca["content_id"])[0]
        eb = outbox_rows(bootstrap_engine, content_id=cb["content_id"])[0]
        assert ea["tenant_id"] == tenant_a
        assert eb["tenant_id"] == tenant_b
        assert ra["correlation_id"] == UUID(dict(ea["envelope"])["correlationid"])
        assert rb["correlation_id"] == UUID(dict(eb["envelope"])["correlationid"])

    def test_dispatcher_engines_cannot_touch_audit(
        self, event_dispatcher_engine, workflow_dispatcher_engine
    ) -> None:
        record = _record()
        for engine in (event_dispatcher_engine, workflow_dispatcher_engine):
            with engine.connect() as conn:
                with conn.begin():
                    set_tenant(conn, record.tenant_id)
                    with pytest.raises(ProgrammingError):
                        conn.execute(text("SELECT * FROM security.audit_records"))
            with engine.connect() as conn:
                with conn.begin():
                    set_tenant(conn, record.tenant_id)
                    with pytest.raises(
                        (ProgrammingError, SecurityAuditPersistenceError)
                    ):
                        SqlAlchemySecurityMutationAuditRepository(conn).insert(record)
            with engine.connect() as conn:
                with conn.begin():
                    with pytest.raises(ProgrammingError):
                        conn.execute(
                            text(
                                "UPDATE security.audit_records SET action = 'content.publish'"
                            )
                        )
            with engine.connect() as conn:
                with conn.begin():
                    with pytest.raises(ProgrammingError):
                        conn.execute(text("DELETE FROM security.audit_records"))


class TestImmutabilityChannelAndMinimization:
    def test_immutability_and_channel_db_defense(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        record = _record()
        with runtime_engine.connect() as conn:
            with conn.begin():
                set_tenant(conn, record.tenant_id)
                SqlAlchemySecurityMutationAuditRepository(conn).insert(record)
        with bootstrap_engine.connect() as conn:
            with conn.begin():
                with pytest.raises((IntegrityError, DBAPIError)):
                    conn.execute(
                        text(
                            "UPDATE security.audit_records SET action = 'content.publish' "
                            "WHERE audit_record_id = :id"
                        ),
                        {"id": record.audit_record_id.value},
                    )
                with pytest.raises((IntegrityError, DBAPIError)):
                    conn.execute(
                        text(
                            "DELETE FROM security.audit_records WHERE audit_record_id = :id"
                        ),
                        {"id": record.audit_record_id.value},
                    )

    def test_ai_channel_mismatch_rejected_at_application(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        from aieos.domains.content.application.errors import InvalidContentRequest

        tenant_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id)
        corr = uuid.uuid7()
        bad = ai_materialization_audit_provenance(uuid.uuid7())
        # force wrong channel via replace if dataclass
        bad = replace(bad, execution_channel=SecurityAuditExecutionChannel.API)
        with pytest.raises(InvalidContentRequest):
            _materializer(runtime_engine).materialize(
                tenant_id,
                uuid.uuid7(),
                AIGeneratedVersionMaterializationCommand(
                    content_id=content_id,
                    expected_aggregate_revision=AggregateRevision(0),
                    schema_id="test.generic",
                    schema_version=1,
                    payload={"marker": "x"},
                    provenance=_provenance(corr),
                ),
                event_context=_event(
                    actor=uuid.uuid7(), effective=uuid.uuid7(), correlation=corr
                ),
                audit_provenance=bad,
                now=AI_NOW,
            )
        assert _audit_rows(bootstrap_engine, content_id=content_id.value) == []

    def test_comment_policy_and_approved_not_published(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = _client(
            runtime_engine,
            tenant_id,
            uuid.uuid7(),
            comment_policy=MarkerReviewCommentPolicy(),
        )
        cid, vid, etag = in_review(client, tenant_id)
        before = _counts(bootstrap_engine, cid)
        denied = decide(
            client,
            tenant_id,
            cid,
            vid,
            action="reject",
            etag=etag,
            body={"reason_code": "x", "comment": SENSITIVE_TEST_COMMENT},
        )
        assert denied.status_code == 422
        assert _counts(bootstrap_engine, cid) == before
        assert command_intent_rows(bootstrap_engine, cid) == []

        client2 = _client(runtime_engine, tenant_id, uuid.uuid7())
        cid2, vid2, etag2 = in_review(client2, tenant_id)
        approved = decide(
            client2, tenant_id, cid2, vid2, action="approve", etag=etag2
        )
        assert approved.status_code == 200
        actions = _audit_actions(bootstrap_engine, content_id=cid2)
        assert actions.count("content.review.approve") == 1
        assert actions.count("content.publish") == 0
        head = _content_row(bootstrap_engine, cid2)
        assert head.published_version_id is None


class TestWorkflowObservationBrokerAndSanitization:
    def test_workflow_observation_does_not_duplicate_audit(
        self, runtime_engine, workflow_dispatcher_engine, bootstrap_engine, postgres18
    ) -> None:
        from aieos.platform.workflows.constants import (
            INTENT_DELIVERED,
            PROCESS_DECISION_OBSERVED,
            PROCESS_WAITING,
        )
        from tests.platform.workflows.helpers import generated_version

        assert postgres18["server_version"].startswith("18.")

        async def scenario() -> None:
            async with await WorkflowEnvironment.start_time_skipping() as env:
                tenant_id = uuid.uuid7()
                principal = uuid.uuid7()
                client = _client(runtime_engine, tenant_id, principal)
                content_id, version_id, etag = generated_version(client, tenant_id)
                submitted = submit_review(
                    client, tenant_id, content_id, version_id, etag=etag
                )
                assert submitted.status_code == 200
                gateway = TemporalClientReviewGateway(env.client)
                assert await start_dispatcher(
                    workflow_dispatcher_engine, gateway
                ).dispatch_once(tenant_id)
                start_row = start_intent_rows(bootstrap_engine, content_id)[0]
                assert start_row["status"] == INTENT_DELIVERED
                async with create_content_review_worker(env.client):
                    handle = env.client.get_workflow_handle(
                        start_row["temporal_workflow_id"]
                    )
                    state = await handle.query(ContentReviewWorkflowV1.state)
                    assert state["process_status"] == PROCESS_WAITING
                    before = len(_audit_rows(bootstrap_engine, content_id=content_id))
                    approved = decide(
                        client,
                        tenant_id,
                        content_id,
                        version_id,
                        action="approve",
                        etag=submitted.headers["ETag"],
                    )
                    assert approved.status_code == 200
                    after_decide = len(
                        _audit_rows(bootstrap_engine, content_id=content_id)
                    )
                    assert after_decide == before + 1
                    cmd = command_intent_rows(bootstrap_engine, content_id)[0]
                    await handle.signal(
                        "review_decision_recorded", dict(cmd["payload"])
                    )
                    await handle.signal(
                        "review_decision_recorded", dict(cmd["payload"])
                    )
                    result = await handle.result()
                    assert result["process_status"] == PROCESS_DECISION_OBSERVED
                    after_obs = len(
                        _audit_rows(bootstrap_engine, content_id=content_id)
                    )
                    assert after_obs == after_decide

        run_async(scenario())

    def test_broker_outage_preserves_business_outbox_and_audit(
        self, runtime_engine, event_dispatcher_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        created = create_content(client, tenant_id)
        content_id = created["content_id"]
        assert len(_audit_rows(bootstrap_engine, content_id=content_id)) == 1
        publisher = FakePublisher(
            PublishResult(
                published=False, error_code=ERROR_NATS_UNAVAILABLE, permanent=False
            )
        )
        dispatcher = make_dispatcher(event_dispatcher_engine, publisher)
        assert run_async(dispatcher.dispatch_once(tenant_id)) is False
        assert _content_row(bootstrap_engine, content_id) is not None
        assert len(_audit_rows(bootstrap_engine, content_id=content_id)) == 1
        events = outbox_rows(bootstrap_engine, content_id=content_id)
        assert len(events) == 1
        assert events[0]["status"] == OUTBOX_PENDING

    def test_sanitized_audit_persistence_error(
        self, runtime_engine, monkeypatch
    ) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())

        def boom(self, record) -> None:
            raise PersistenceOperationFailed(
                "DETAIL: INSERT INTO security.audit_records failed "
                "role=aieos_security url=postgresql://user:password@host/db"
            )

        monkeypatch.setattr(ContentSecurityMutationAuditRepository, "insert", boom)
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


class TestMigrationFailedSeparationAndTargetOwner:
    def test_failed_evidence_not_success_audit_and_target_owner_separated(
        self, migration_runtime_engine, bootstrap_engine, monkeypatch
    ) -> None:
        tenant_id = uuid.uuid7()
        principal = uuid.uuid7()
        owner = uuid.uuid7()
        assert owner != principal

        def boom(self, record) -> None:
            raise PersistenceOperationFailed("content persistence operation failed")

        monkeypatch.setattr(ContentSecurityMutationAuditRepository, "insert", boom)
        source = f"sai-i05-fail-{uuid.uuid7()}"
        fail_candidate = replace(
            _candidate(source_resource_id=source),
            target_owner_principal_id=owner,
        )
        with pytest.raises(PersistenceOperationFailed):
            _importer(migration_runtime_engine).import_content(
                tenant_id,
                principal,
                fail_candidate,
                event_context=_event(actor=principal, effective=principal),
                audit_provenance=migration_audit_provenance(principal),
                now=MIG_NOW,
            )
        with bootstrap_engine.connect() as conn:
            failed = conn.execute(
                text(
                    "SELECT count(*) FROM content.migration_import_records "
                    "WHERE tenant_id = :t AND outcome = 'FAILED'"
                ),
                {"t": tenant_id},
            ).scalar_one()
            success_audits = conn.execute(
                text(
                    "SELECT count(*) FROM security.audit_records "
                    "WHERE tenant_id = :t AND action = 'content.migration.import'"
                ),
                {"t": tenant_id},
            ).scalar_one()
        assert int(failed) >= 1
        assert int(success_audits) == 0

        monkeypatch.undo()
        result = _importer(migration_runtime_engine).import_content(
            tenant_id,
            principal,
            replace(
                _candidate(source_resource_id=f"sai-i05-owner-{uuid.uuid7()}"),
                target_owner_principal_id=owner,
            ),
            event_context=_event(actor=principal, effective=principal),
            audit_provenance=migration_audit_provenance(principal),
            now=MIG_NOW,
        )
        row = _audit_rows(bootstrap_engine, content_id=result.content_id.value)[0]
        assert row["action"] == "content.migration.import"
        assert row["initiating_principal_id"] == principal
        assert row["effective_actor_id"] == principal
        assert row["executing_principal_id"] == principal
        assert owner not in {
            row["initiating_principal_id"],
            row["effective_actor_id"],
            row["executing_principal_id"],
        }
        blob = json.dumps({k: str(v) for k, v in row.items()}, default=str).lower()
        assert "source_digest" not in blob
        assert "legacy" not in blob
        assert "batch" not in blob
