"""GCI-I08 mutation outbox events for Content CloudEvents."""

from __future__ import annotations

import ast
import uuid
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.application.errors import PersistenceOperationFailed
from aieos.domains.content.application.models import AppendContentVersionCommand
from aieos.domains.content.application.review import ReviewCommandService
from aieos.domains.content.application.services import AppendContentVersionService
from aieos.domains.content.domain.identities import (
    AggregateRevision,
    ContentId,
    ContentVersionId,
)
from aieos.domains.content.infrastructure.persistence.uow import SqlAlchemyContentUnitOfWorkFactory
from aieos.platform.events.constants import (
    EVENT_CONTENT_CREATED_V1,
    EVENT_CONTENT_REVIEW_APPROVED_V1,
    EVENT_CONTENT_REVIEW_CHANGES_REQUESTED_V1,
    EVENT_CONTENT_REVIEW_REJECTED_V1,
    EVENT_CONTENT_SUBMITTED_FOR_REVIEW_V1,
    EVENT_CONTENT_VERSION_CREATED_V1,
    OUTBOX_PENDING,
)
from aieos.platform.events.models import MutationEventContext
from aieos.platform.events.persistence.repositories import SqlAlchemyOutboxRepository
from tests.dbutil import REPO_ROOT
from tests.domains.content.infrastructure.persistence.test_gci_i03_append import (
    _make_version,
    _seed_content,
)
from tests.fakes import (
    AllowReviewAuthorization,
    AllowReviewCommentPolicy,
    IDEMPOTENCY_RETENTION,
)
from tests.platform.events.helpers import (
    assert_contract_compatible,
    assert_no_sensitive_material,
    client_for,
    outbox_rows,
)
from tests.platform.workflows.helpers import (
    append_version,
    command_intent_rows,
    content_row,
    create_content,
    decide,
    generated_version,
    headers,
    in_review,
    start_intent_rows,
    submit_review,
)

pytestmark = pytest.mark.gci_i08

APPLICATION_ROOT = REPO_ROOT / "src" / "aieos" / "domains" / "content" / "application"
API_ROUTES = REPO_ROOT / "src" / "aieos" / "domains" / "content" / "api" / "v1" / "routes.py"
FIXED_NOW = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)


def _is_uuid7(value: UUID | str) -> bool:
    parsed = value if isinstance(value, UUID) else UUID(str(value))
    return parsed.version == 7


def _event_context(principal_id: uuid.UUID | None = None) -> MutationEventContext:
    actor = principal_id or uuid.uuid7()
    return MutationEventContext(
        correlation_id=uuid.uuid7(),
        causation_id=uuid.uuid7(),
        actor_principal_id=actor,
        effective_actor_id=actor,
    )


def _content_count(bootstrap_engine: Engine, tenant_id: uuid.UUID) -> int:
    with bootstrap_engine.connect() as conn:
        return int(
            conn.execute(
                text("SELECT count(*) FROM content.contents WHERE tenant_id = :tid"),
                {"tid": tenant_id},
            ).scalar_one()
        )


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


def _idempotency_count(
    bootstrap_engine: Engine, tenant_id: uuid.UUID, operation: str
) -> int:
    with bootstrap_engine.connect() as conn:
        return int(
            conn.execute(
                text(
                    """
                    SELECT count(*) FROM api.idempotency_records
                    WHERE tenant_id = :tid AND operation = :op
                    """
                ),
                {"tid": tenant_id, "op": operation},
            ).scalar_one()
        )


def _decision_count(bootstrap_engine: Engine, content_id: str) -> int:
    with bootstrap_engine.connect() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT count(*) FROM content.review_decisions WHERE content_id = :cid"
                ),
                {"cid": content_id},
            ).scalar_one()
        )


def _direct_append(
    runtime_engine: Engine,
    tenant_id: uuid.UUID,
    content_id,
    *,
    version_number: int,
    parent_version_id,
    expected_revision: int,
) -> None:
    service = AppendContentVersionService(SqlAlchemyContentUnitOfWorkFactory(runtime_engine))
    version = _make_version(
        tenant_id=tenant_id,
        content_id=content_id,
        version_number=version_number,
        parent_version_id=parent_version_id,
    )
    service.append(
        tenant_id,
        AppendContentVersionCommand(
            expected_aggregate_revision=AggregateRevision(expected_revision),
            version=version,
            provenance=None,
        ),
        event_context=_event_context(),
        now=FIXED_NOW,
    )


class TestCreateEvents:
    def test_create_emits_one_contract_safe_event(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = client_for(runtime_engine, tenant_id, uuid.uuid7())
        created = create_content(client, tenant_id)
        rows = outbox_rows(bootstrap_engine, content_id=created["content_id"])
        assert len(rows) == 1
        row = rows[0]
        assert row["event_type"] == EVENT_CONTENT_CREATED_V1
        assert row["status"] == OUTBOX_PENDING
        assert int(row["aggregate_revision"]) == 0
        assert _is_uuid7(row["event_id"])
        envelope = dict(row["envelope"])
        assert envelope["aggregaterevision"] == 0
        assert _is_uuid7(envelope["id"])
        assert_contract_compatible(envelope, event_type=EVENT_CONTENT_CREATED_V1)
        assert_no_sensitive_material(envelope)

    def test_idempotent_create_replay_no_second_event(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = client_for(runtime_engine, tenant_id, uuid.uuid7())
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
        assert first.status_code == 201, first.text
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
        assert len(outbox_rows(bootstrap_engine, content_id=first.json()["content_id"])) == 1

    def test_same_key_changed_body_409_and_no_second_event(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = client_for(runtime_engine, tenant_id, uuid.uuid7())
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
        assert first.status_code == 201, first.text
        changed = client.post(
            "/api/v1/contents",
            json={
                "content_type": "test.generic",
                "title": "Different",
                "description": "Description",
                "locale": "en-IN",
            },
            headers=headers(tenant_id, **{"Idempotency-Key": key}),
        )
        assert changed.status_code == 409, changed.text
        assert changed.json()["code"] == "idempotency_key_reused"
        assert len(outbox_rows(bootstrap_engine, content_id=first.json()["content_id"])) == 1

    def test_outbox_insert_failure_rolls_back_create(
        self, runtime_engine, bootstrap_engine, monkeypatch
    ) -> None:
        tenant_id = uuid.uuid7()
        client = client_for(runtime_engine, tenant_id, uuid.uuid7())
        before = _content_count(bootstrap_engine, tenant_id)
        before_outbox = len(outbox_rows(bootstrap_engine))

        def boom(self, message) -> None:
            raise PersistenceOperationFailed("inject outbox insert failure")

        monkeypatch.setattr(SqlAlchemyOutboxRepository, "insert", boom)
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
        assert _content_count(bootstrap_engine, tenant_id) == before
        assert len(outbox_rows(bootstrap_engine)) == before_outbox


class TestAppendEvents:
    def test_direct_draft_append_emits_version_created(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id=tenant_id)
        _direct_append(
            runtime_engine,
            tenant_id,
            content_id,
            version_number=1,
            parent_version_id=None,
            expected_revision=0,
        )
        rows = outbox_rows(bootstrap_engine, content_id=str(content_id.value))
        version_events = [
            row for row in rows if row["event_type"] == EVENT_CONTENT_VERSION_CREATED_V1
        ]
        assert len(version_events) == 1
        envelope = dict(version_events[0]["envelope"])
        assert int(version_events[0]["aggregate_revision"]) == 1
        assert "payload" not in envelope["data"]
        assert "marker" not in str(envelope)
        assert envelope["data"]["stewardship_state"] == "GENERATED"
        assert_contract_compatible(
            envelope, event_type=EVENT_CONTENT_VERSION_CREATED_V1
        )
        assert_no_sensitive_material(envelope)

    def test_http_append_emits_version_created_and_replays(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = client_for(runtime_engine, tenant_id, uuid.uuid7())
        created = create_content(client, tenant_id)
        content_id = created["content_id"]
        key = f"append-{uuid.uuid7()}"
        hdrs = headers(tenant_id, **{"Idempotency-Key": key})
        hdrs["If-Match"] = '"r0"'
        first = client.post(
            f"/api/v1/contents/{content_id}/versions",
            json={
                "schema_id": "test.generic",
                "schema_version": 1,
                "payload": {"marker": "v1"},
            },
            headers=hdrs,
        )
        assert first.status_code == 201, first.text
        replay_hdrs = headers(tenant_id, **{"Idempotency-Key": key})
        replay_hdrs["If-Match"] = '"r0"'
        replay = client.post(
            f"/api/v1/contents/{content_id}/versions",
            json={
                "schema_id": "test.generic",
                "schema_version": 1,
                "payload": {"marker": "v1"},
            },
            headers=replay_hdrs,
        )
        assert replay.status_code == 201
        rows = outbox_rows(bootstrap_engine, content_id=content_id)
        version_events = [
            row for row in rows if row["event_type"] == EVENT_CONTENT_VERSION_CREATED_V1
        ]
        assert len(version_events) == 1
        assert int(version_events[0]["aggregate_revision"]) == 1

    def test_outbox_failure_rolls_back_version_and_head(
        self, runtime_engine, bootstrap_engine, monkeypatch
    ) -> None:
        tenant_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id=tenant_id)

        def boom(self, message) -> None:
            raise PersistenceOperationFailed("inject outbox insert failure")

        monkeypatch.setattr(SqlAlchemyOutboxRepository, "insert", boom)
        service = AppendContentVersionService(SqlAlchemyContentUnitOfWorkFactory(runtime_engine))
        version = _make_version(
            tenant_id=tenant_id,
            content_id=content_id,
            version_number=1,
            parent_version_id=None,
        )
        with pytest.raises(PersistenceOperationFailed):
            service.append(
                tenant_id,
                AppendContentVersionCommand(
                    expected_aggregate_revision=AggregateRevision(0),
                    version=version,
                    provenance=None,
                ),
                event_context=_event_context(),
                now=FIXED_NOW,
            )
        row = content_row(bootstrap_engine, str(content_id.value))
        assert row.current_version_id is None
        assert int(row.aggregate_revision) == 0
        assert _version_count(bootstrap_engine, str(content_id.value)) == 0
        assert outbox_rows(bootstrap_engine, content_id=str(content_id.value)) == []

    def test_approved_append_keeps_published_pointer_and_emits_generated(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        content_id = _seed_content(
            bootstrap_engine, tenant_id=tenant_id, stewardship_state="APPROVED"
        )
        _direct_append(
            runtime_engine,
            tenant_id,
            content_id,
            version_number=1,
            parent_version_id=None,
            expected_revision=0,
        )
        head = content_row(bootstrap_engine, str(content_id.value))
        published_version_id = head.current_version_id
        with bootstrap_engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE content.contents SET published_version_id = :vid "
                    "WHERE content_id = :cid"
                ),
                {"vid": published_version_id, "cid": content_id.value},
            )
        _direct_append(
            runtime_engine,
            tenant_id,
            content_id,
            version_number=2,
            parent_version_id=ContentVersionId(published_version_id),
            expected_revision=1,
        )
        with bootstrap_engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT published_version_id, stewardship_state
                    FROM content.contents
                    WHERE content_id = :cid
                    """
                ),
                {"cid": content_id.value},
            ).one()
        assert row.published_version_id == published_version_id
        assert row.stewardship_state == "GENERATED"
        latest = outbox_rows(bootstrap_engine, content_id=str(content_id.value))[-1]
        assert latest["event_type"] == EVENT_CONTENT_VERSION_CREATED_V1
        assert dict(latest["envelope"])["data"]["stewardship_state"] == "GENERATED"


class TestSubmitEvents:
    def test_submit_emits_submitted_for_review_and_atomic_side_effects(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = client_for(runtime_engine, tenant_id, uuid.uuid7())
        content_id, version_id, etag = generated_version(client, tenant_id)
        submitted = submit_review(client, tenant_id, content_id, version_id, etag=etag)
        assert submitted.status_code == 200, submitted.text
        rows = outbox_rows(bootstrap_engine, content_id=content_id)
        submit_events = [
            row
            for row in rows
            if row["event_type"] == EVENT_CONTENT_SUBMITTED_FOR_REVIEW_V1
        ]
        assert len(submit_events) == 1
        envelope = dict(submit_events[0]["envelope"])
        assert envelope["data"]["version_id"] == version_id
        assert envelope["data"]["stewardship_state"] == "IN_REVIEW"
        assert int(submit_events[0]["aggregate_revision"]) == 2
        assert_contract_compatible(
            envelope, event_type=EVENT_CONTENT_SUBMITTED_FOR_REVIEW_V1
        )
        assert_no_sensitive_material(envelope)
        assert content_row(bootstrap_engine, content_id).stewardship_state == "IN_REVIEW"
        assert len(start_intent_rows(bootstrap_engine, content_id)) == 1
        assert _idempotency_count(bootstrap_engine, tenant_id, "content_review_submit.v1") == 1

    def test_submit_retry_no_second_event(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        client = client_for(runtime_engine, tenant_id, uuid.uuid7())
        content_id, version_id, etag = generated_version(client, tenant_id)
        key = f"submit-{uuid.uuid7()}"
        first = submit_review(
            client, tenant_id, content_id, version_id, etag=etag, **{"Idempotency-Key": key}
        )
        assert first.status_code == 200
        replay = submit_review(
            client, tenant_id, content_id, version_id, etag=etag, **{"Idempotency-Key": key}
        )
        assert replay.status_code == 200
        submit_events = [
            row
            for row in outbox_rows(bootstrap_engine, content_id=content_id)
            if row["event_type"] == EVENT_CONTENT_SUBMITTED_FOR_REVIEW_V1
        ]
        assert len(submit_events) == 1

    def test_outbox_failure_rolls_back_submit(
        self, runtime_engine, bootstrap_engine, monkeypatch
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id, etag = generated_version(
            client_for(runtime_engine, tenant_id, principal_id), tenant_id
        )
        before = content_row(bootstrap_engine, content_id)

        def boom(self, message) -> None:
            raise PersistenceOperationFailed("inject outbox insert failure")

        monkeypatch.setattr(SqlAlchemyOutboxRepository, "insert", boom)
        service = ReviewCommandService(
            SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
            AllowReviewAuthorization(),
            AllowReviewCommentPolicy(),
            idempotency_retention=IDEMPOTENCY_RETENTION,
        )
        with pytest.raises(PersistenceOperationFailed):
            service.submit(
                tenant_id,
                principal_id,
                content_id=ContentId(UUID(content_id)),
                version_id=ContentVersionId(UUID(version_id)),
                expected_aggregate_revision=AggregateRevision(
                    int(etag.strip('"').lstrip("r"))
                ),
                idempotency_key=f"fail-submit-{uuid.uuid7()}",
                event_context=_event_context(principal_id),
            )
        after = content_row(bootstrap_engine, content_id)
        assert after.stewardship_state == before.stewardship_state
        assert int(after.aggregate_revision) == int(before.aggregate_revision)
        assert start_intent_rows(bootstrap_engine, content_id) == []
        assert _idempotency_count(bootstrap_engine, tenant_id, "content_review_submit.v1") == 0
        assert not any(
            row["event_type"] == EVENT_CONTENT_SUBMITTED_FOR_REVIEW_V1
            for row in outbox_rows(bootstrap_engine, content_id=content_id)
        )


class TestReviewDecisionEvents:
    @pytest.mark.parametrize(
        ("action", "event_type", "decision"),
        [
            ("approve", EVENT_CONTENT_REVIEW_APPROVED_V1, "APPROVE"),
            (
                "request-changes",
                EVENT_CONTENT_REVIEW_CHANGES_REQUESTED_V1,
                "REQUEST_CHANGES",
            ),
            ("reject", EVENT_CONTENT_REVIEW_REJECTED_V1, "REJECT"),
        ],
    )
    def test_decision_event_shape_and_replay(
        self,
        runtime_engine,
        bootstrap_engine,
        action: str,
        event_type: str,
        decision: str,
    ) -> None:
        tenant_id = uuid.uuid7()
        client = client_for(runtime_engine, tenant_id, uuid.uuid7())
        content_id, version_id, etag = in_review(client, tenant_id)
        body = {
            "reason_code": "reason_code_should_not_leak",
            "comment": "SENSITIVE_TEST_COMMENT",
        }
        key = f"{action}-{uuid.uuid7()}"
        first = decide(
            client,
            tenant_id,
            content_id,
            version_id,
            action=action,
            etag=etag,
            body=body,
            **{"Idempotency-Key": key},
        )
        assert first.status_code == 200, first.text
        rows = outbox_rows(bootstrap_engine, content_id=content_id)
        decision_events = [row for row in rows if row["event_type"] == event_type]
        assert len(decision_events) == 1
        envelope = dict(decision_events[0]["envelope"])
        data = envelope["data"]
        assert data["version_id"] == version_id
        assert data["decision"] == decision
        assert _is_uuid7(data["review_decision_id"])
        assert "comment" not in data
        assert "reason_code" not in data
        assert "SENSITIVE_TEST_COMMENT" not in str(envelope)
        assert_contract_compatible(envelope, event_type=event_type)
        assert_no_sensitive_material(envelope)
        replay = decide(
            client,
            tenant_id,
            content_id,
            version_id,
            action=action,
            etag=etag,
            body=body,
            **{"Idempotency-Key": key},
        )
        assert replay.status_code == 200
        assert len([row for row in outbox_rows(bootstrap_engine, content_id=content_id) if row["event_type"] == event_type]) == 1

    def test_outbox_failure_rolls_back_decision_side_effects(
        self, runtime_engine, bootstrap_engine, monkeypatch
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id, etag = in_review(
            client_for(runtime_engine, tenant_id, principal_id), tenant_id
        )
        before = content_row(bootstrap_engine, content_id)

        def boom(self, message) -> None:
            raise PersistenceOperationFailed("inject outbox insert failure")

        monkeypatch.setattr(SqlAlchemyOutboxRepository, "insert", boom)
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
                expected_aggregate_revision=AggregateRevision(
                    int(etag.strip('"').lstrip("r"))
                ),
                reason_code=None,
                comment=None,
                idempotency_key=f"fail-approve-{uuid.uuid7()}",
                event_context=_event_context(principal_id),
            )
        after = content_row(bootstrap_engine, content_id)
        assert after.stewardship_state == before.stewardship_state
        assert int(after.aggregate_revision) == int(before.aggregate_revision)
        assert _decision_count(bootstrap_engine, content_id) == 0
        assert command_intent_rows(bootstrap_engine, content_id) == []
        assert _idempotency_count(bootstrap_engine, tenant_id, "content_review_approve.v1") == 0


class TestRevisionSequence:
    def test_create_append_submit_approve_revision_sequence(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = client_for(runtime_engine, tenant_id, uuid.uuid7())
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
        rows = outbox_rows(bootstrap_engine, content_id=content_id)
        assert [int(row["aggregate_revision"]) for row in rows] == [0, 1, 2, 3]
        event_ids = [row["event_id"] for row in rows]
        assert len(set(event_ids)) == 4
        assert all(_is_uuid7(event_id) for event_id in event_ids)


class TestHttpMutationBoundaries:
    def test_http_mutation_modules_do_not_import_or_call_nats(self) -> None:
        targets = [
            API_ROUTES,
            APPLICATION_ROOT / "create.py",
            APPLICATION_ROOT / "http_append.py",
            APPLICATION_ROOT / "review.py",
        ]
        violations: list[str] = []
        for path in targets:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.split(".")[0] == "nats":
                            violations.append(f"{path.name}: import {alias.name}")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    if node.module.split(".")[0] == "nats":
                        violations.append(f"{path.name}: from {node.module}")
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    if node.func.attr == "publish":
                        violations.append(f"{path.name}: .publish() call")
        assert violations == []
