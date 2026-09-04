"""TOS-DEV06-I03 — TeachingAssignment HTTP contract tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.teaching.application.errors import SchoolContextUnavailable
from aieos.domains.teaching.application.school_context import AssignableClassRef
from aieos.platform.api.app import create_app
from aieos.platform.events.constants import (
    EVENT_TEACHING_ASSIGNMENT_CANCELLED_V1,
    EVENT_TEACHING_ASSIGNMENT_CLOSED_V1,
)
from aieos.platform.idempotency.models import (
    TEACHING_ASSIGNMENT_CANCEL_V1,
    TEACHING_ASSIGNMENT_CLOSE_V1,
)
from tests.domains.teaching.helpers_dev06_i03 import (
    CREATE_PATH,
    build_assignment_client,
    count_rows,
    fetch_audit,
    fetch_outbox,
    headers,
    seed_published_worksheet,
)
from tests.fakes import (
    AllowClassroomAssessmentAuthorization,
    AllowAssetCurrentGovernance,
    AllowAssetReferenceValidation,
    AllowPublicationAuthorization,
    AllowPublicationGovernance,
    AllowReviewAuthorization,
    AllowReviewCommentPolicy,
    FixedPrincipalAuthenticator,
    StubSecurityContextResolver,
    make_test_schema_registry,
)

pytestmark = pytest.mark.tos_dev06_i03

CURSOR_KEY = b"tos-dev06-i03-test-cursor-key"
IDEMPOTENCY_RETENTION = timedelta(hours=24)
DUE_AT = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


class _UnusedUowFactory:
    def __call__(self, execution_tenant_id):
        raise AssertionError("HTTP contract test must not open UoW")


class _Reader:
    def list_assignable_classes(
        self, tenant_id: uuid.UUID, teacher_principal_id: uuid.UUID
    ) -> tuple[AssignableClassRef, ...]:
        return (
            AssignableClassRef(class_ref="class-5a", display_label="Grade 5A"),
        )


class _MutableReader:
    def __init__(self) -> None:
        self._items = (
            AssignableClassRef(class_ref="class-5a", display_label="Grade 5A"),
        )
        self._unavailable = False

    def revoke(self) -> None:
        self._items = ()

    def set_unavailable(self) -> None:
        self._unavailable = True

    def list_assignable_classes(
        self, tenant_id: uuid.UUID, teacher_principal_id: uuid.UUID
    ) -> tuple[AssignableClassRef, ...]:
        if self._unavailable:
            raise SchoolContextUnavailable("School Context is temporarily unavailable")
        return self._items


def _client(tenant_id: uuid.UUID, principal_id: uuid.UUID) -> TestClient:
    app = create_app(
        uow_factory=_UnusedUowFactory(),  # type: ignore[arg-type]
        teaching_uow_factory=_UnusedUowFactory(),
        assessment_uow_factory=_UnusedUowFactory(),  # type: ignore[arg-type]
        assessment_authorization=AllowClassroomAssessmentAuthorization(),
        request_identity_authenticator=FixedPrincipalAuthenticator(principal_id),
        security_resolver=StubSecurityContextResolver(tenant_id, principal_id),
        content_types=StaticContentTypeCatalog({"test.generic", "worksheet"}),
        cursor_signing_key=CURSOR_KEY,
        schema_registry=make_test_schema_registry(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
        review_authorization=AllowReviewAuthorization(),
        review_comment_policy=AllowReviewCommentPolicy(),
        publication_authorization=AllowPublicationAuthorization(),
        publication_governance=AllowPublicationGovernance(),
        asset_reference_validation=AllowAssetReferenceValidation(),
        asset_current_governance=AllowAssetCurrentGovernance(),
        school_context_class_reader=_Reader(),
    )
    return TestClient(app)


def _etag(response) -> str:
    return response.headers["ETag"]


def _count_lifecycle_idempotency(
    bootstrap_engine: Engine,
    *,
    tenant_id: uuid.UUID,
    operation: str,
    assignment_id: uuid.UUID,
) -> int:
    return count_rows(
        bootstrap_engine,
        """
        SELECT count(*) FROM api.idempotency_records
        WHERE tenant_id = :tid AND operation = :op
          AND result_content_id = :aid
        """,
        tenant_id=tenant_id,
        extra={"op": operation, "aid": assignment_id},
    )


def _create_via_http(
    client: TestClient,
    tenant_id: uuid.UUID,
    *,
    content_id: uuid.UUID,
    version_id: uuid.UUID,
    idempotency_key: str,
    extra_json: dict | None = None,
):
    body = {
        "content_id": str(content_id),
        "content_version_id": str(version_id),
        "class_ref": "class-5a",
    }
    if extra_json:
        body.update(extra_json)
    response = client.post(
        CREATE_PATH,
        headers=headers(tenant_id, idempotency_key=idempotency_key),
        json=body,
    )
    assert response.status_code == 201, response.text
    return response


class TestCreateHttpContract:
    def test_create_requires_idempotency_key(self) -> None:
        tenant_id = uuid.uuid4()
        principal_id = uuid.uuid4()
        client = _client(tenant_id, principal_id)
        response = client.post(
            CREATE_PATH,
            headers={"X-AIEOS-Tenant-ID": str(tenant_id)},
            json={
                "content_id": str(uuid.uuid4()),
                "content_version_id": str(uuid.uuid4()),
                "class_ref": "class-5a",
            },
        )
        assert response.status_code == 400
        assert response.json()["code"] == "idempotency_key_required"

    def test_create_without_school_context_returns_503(self) -> None:
        tenant_id = uuid.uuid4()
        principal_id = uuid.uuid4()
        app = create_app(
            uow_factory=_UnusedUowFactory(),  # type: ignore[arg-type]
            teaching_uow_factory=_UnusedUowFactory(),
        assessment_uow_factory=_UnusedUowFactory(),  # type: ignore[arg-type]
        assessment_authorization=AllowClassroomAssessmentAuthorization(),
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
            school_context_class_reader=None,
        )
        client = TestClient(app)
        response = client.post(
            CREATE_PATH,
            headers={
                "X-AIEOS-Tenant-ID": str(tenant_id),
                "Idempotency-Key": "create-without-school-context",
            },
            json={
                "content_id": str(uuid.uuid4()),
                "content_version_id": str(uuid.uuid4()),
                "class_ref": "class-5a",
            },
        )
        assert response.status_code == 503

    @pytest.mark.parametrize(
        "field",
        [
            "teacher_principal_id",
            "principal_id",
            "effective_actor_id",
            "tenant_id",
            "assignment_id",
            "lifecycle_state",
            "aggregate_revision",
            "audience_display_label",
        ],
    )
    def test_create_rejects_caller_controlled_fields(self, field: str) -> None:
        tenant_id = uuid.uuid4()
        principal_id = uuid.uuid4()
        client = _client(tenant_id, principal_id)
        body = {
            "content_id": str(uuid.uuid4()),
            "content_version_id": str(uuid.uuid4()),
            "class_ref": "class-5a",
        }
        if field == "lifecycle_state":
            body[field] = "ACTIVE"
        elif field == "aggregate_revision":
            body[field] = 99
        elif field == "audience_display_label":
            body[field] = "Injected Label"
        else:
            body[field] = str(uuid.uuid4())
        response = client.post(
            CREATE_PATH,
            headers=headers(tenant_id, idempotency_key="i03-spoof-create"),
            json=body,
        )
        assert response.status_code == 422


class TestOwnershipHttp:
    def test_teacher_cannot_get_other_teachers_assignment(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        teacher_a = uuid.uuid7()
        teacher_b = uuid.uuid7()
        content_id, version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        client_a = build_assignment_client(runtime_engine, tenant_id, teacher_a)
        created = _create_via_http(
            client_a,
            tenant_id,
            content_id=content_id,
            version_id=version_id,
            idempotency_key="i03-own-create",
        )
        assignment_id = created.json()["assignment_id"]
        client_b = build_assignment_client(runtime_engine, tenant_id, teacher_b)
        response = client_b.get(
            f"{CREATE_PATH}/{assignment_id}",
            headers=headers(tenant_id),
        )
        assert response.status_code == 403
        assert response.json()["code"] == "teaching_assignment_forbidden"

    def test_teacher_cannot_mutate_other_teachers_assignment(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        teacher_a = uuid.uuid7()
        teacher_b = uuid.uuid7()
        content_id, version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        client_a = build_assignment_client(runtime_engine, tenant_id, teacher_a)
        created = _create_via_http(
            client_a,
            tenant_id,
            content_id=content_id,
            version_id=version_id,
            idempotency_key="i03-own-mut-create",
        )
        assignment_id = created.json()["assignment_id"]
        etag = _etag(created)
        client_b = build_assignment_client(runtime_engine, tenant_id, teacher_b)
        response = client_b.patch(
            f"{CREATE_PATH}/{assignment_id}",
            headers=headers(
                tenant_id,
                idempotency_key="i03-own-mut-due",
                if_match=etag,
            ),
            json={"due_at": DUE_AT.isoformat()},
        )
        assert response.status_code == 403


class TestClassRefAuthorityHttp:
    def test_revoked_class_ref_rejects_new_create(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        reader = _MutableReader()
        client = build_assignment_client(
            runtime_engine,
            tenant_id,
            principal_id,
            school_context_reader=reader,
        )
        reader.revoke()
        response = client.post(
            CREATE_PATH,
            headers=headers(tenant_id, idempotency_key="i03-revoked-http"),
            json={
                "content_id": str(content_id),
                "content_version_id": str(version_id),
                "class_ref": "class-5a",
            },
        )
        assert response.status_code == 403
        assert response.json()["code"] == "class_ref_not_assignable"

    def test_school_context_unavailable_returns_503(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        reader = _MutableReader()
        reader.set_unavailable()
        client = build_assignment_client(
            runtime_engine,
            tenant_id,
            principal_id,
            school_context_reader=reader,
        )
        response = client.post(
            CREATE_PATH,
            headers=headers(tenant_id, idempotency_key="i03-unavail-http"),
            json={
                "content_id": str(content_id),
                "content_version_id": str(version_id),
                "class_ref": "class-5a",
            },
        )
        assert response.status_code == 503


class TestDueUpdateHttp:
    def test_due_update_success(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        client = build_assignment_client(runtime_engine, tenant_id, principal_id)
        created = _create_via_http(
            client,
            tenant_id,
            content_id=content_id,
            version_id=version_id,
            idempotency_key="i03-due-create",
        )
        assignment_id = created.json()["assignment_id"]
        etag = _etag(created)
        updated = client.patch(
            f"{CREATE_PATH}/{assignment_id}",
            headers=headers(
                tenant_id,
                idempotency_key="i03-due-update",
                if_match=etag,
            ),
            json={"due_at": DUE_AT.isoformat()},
        )
        assert updated.status_code == 200, updated.text
        body = updated.json()
        assert body["aggregate_revision"] == 1
        assert body["due_at"] == DUE_AT.isoformat().replace("+00:00", "Z")
        assert _etag(updated) == '"r1"'

    def test_due_update_missing_if_match_428(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        client = build_assignment_client(runtime_engine, tenant_id, principal_id)
        created = _create_via_http(
            client,
            tenant_id,
            content_id=content_id,
            version_id=version_id,
            idempotency_key="i03-due-428-create",
        )
        assignment_id = created.json()["assignment_id"]
        response = client.patch(
            f"{CREATE_PATH}/{assignment_id}",
            headers=headers(tenant_id, idempotency_key="i03-due-428"),
            json={"due_at": DUE_AT.isoformat()},
        )
        assert response.status_code == 428

    def test_due_update_stale_if_match_412(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        client = build_assignment_client(runtime_engine, tenant_id, principal_id)
        created = _create_via_http(
            client,
            tenant_id,
            content_id=content_id,
            version_id=version_id,
            idempotency_key="i03-due-412-create",
        )
        assignment_id = created.json()["assignment_id"]
        response = client.patch(
            f"{CREATE_PATH}/{assignment_id}",
            headers=headers(
                tenant_id,
                idempotency_key="i03-due-412",
                if_match='"r99"',
            ),
            json={"due_at": DUE_AT.isoformat()},
        )
        assert response.status_code == 412

    def test_due_update_idempotent_replay(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        client = build_assignment_client(runtime_engine, tenant_id, principal_id)
        created = _create_via_http(
            client,
            tenant_id,
            content_id=content_id,
            version_id=version_id,
            idempotency_key="i03-due-replay-create",
        )
        assignment_id = created.json()["assignment_id"]
        etag = _etag(created)
        first = client.patch(
            f"{CREATE_PATH}/{assignment_id}",
            headers=headers(
                tenant_id,
                idempotency_key="i03-due-replay",
                if_match=etag,
            ),
            json={"due_at": DUE_AT.isoformat()},
        )
        second = client.patch(
            f"{CREATE_PATH}/{assignment_id}",
            headers=headers(
                tenant_id,
                idempotency_key="i03-due-replay",
                if_match=etag,
            ),
            json={"due_at": DUE_AT.isoformat()},
        )
        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["aggregate_revision"] == second.json()["aggregate_revision"]

    def test_due_update_fingerprint_conflict(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        client = build_assignment_client(runtime_engine, tenant_id, principal_id)
        created = _create_via_http(
            client,
            tenant_id,
            content_id=content_id,
            version_id=version_id,
            idempotency_key="i03-due-conf-create",
        )
        assignment_id = created.json()["assignment_id"]
        etag = _etag(created)
        client.patch(
            f"{CREATE_PATH}/{assignment_id}",
            headers=headers(
                tenant_id,
                idempotency_key="i03-due-conf",
                if_match=etag,
            ),
            json={"due_at": DUE_AT.isoformat()},
        )
        conflict = client.patch(
            f"{CREATE_PATH}/{assignment_id}",
            headers=headers(
                tenant_id,
                idempotency_key="i03-due-conf",
                if_match='"r1"',
            ),
            json={"due_at": datetime(2026, 10, 1, tzinfo=UTC).isoformat()},
        )
        assert conflict.status_code == 409


class TestCloseHttp:
    def test_close_active_assignment(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        client = build_assignment_client(runtime_engine, tenant_id, principal_id)
        created = _create_via_http(
            client,
            tenant_id,
            content_id=content_id,
            version_id=version_id,
            idempotency_key="i03-close-create",
        )
        assignment_id = created.json()["assignment_id"]
        etag = _etag(created)
        closed = client.post(
            f"{CREATE_PATH}/{assignment_id}/actions/close",
            headers=headers(
                tenant_id,
                idempotency_key="i03-close",
                if_match=etag,
            ),
        )
        assert closed.status_code == 200, closed.text
        body = closed.json()
        assert body["lifecycle_state"] == "CLOSED"
        assert body["closed_at"] is not None
        assert body["aggregate_revision"] == 1
        assert _etag(closed) == '"r1"'

    def test_close_missing_if_match_428(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        client = build_assignment_client(runtime_engine, tenant_id, principal_id)
        created = _create_via_http(
            client,
            tenant_id,
            content_id=content_id,
            version_id=version_id,
            idempotency_key="i03-close-428-create",
        )
        assignment_id = created.json()["assignment_id"]
        response = client.post(
            f"{CREATE_PATH}/{assignment_id}/actions/close",
            headers=headers(tenant_id, idempotency_key="i03-close-428"),
        )
        assert response.status_code == 428

    def test_close_stale_if_match_412(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        client = build_assignment_client(runtime_engine, tenant_id, principal_id)
        created = _create_via_http(
            client,
            tenant_id,
            content_id=content_id,
            version_id=version_id,
            idempotency_key="i03-close-412-create",
        )
        assignment_id = uuid.UUID(created.json()["assignment_id"])
        assert created.json()["lifecycle_state"] == "ACTIVE"
        assert created.json()["aggregate_revision"] == 0
        response = client.post(
            f"{CREATE_PATH}/{assignment_id}/actions/close",
            headers=headers(
                tenant_id,
                idempotency_key="i03-close-412",
                if_match='"r99"',
            ),
        )
        assert response.status_code == 412
        current = client.get(
            f"{CREATE_PATH}/{assignment_id}",
            headers=headers(tenant_id),
        )
        assert current.status_code == 200
        assert current.json()["lifecycle_state"] == "ACTIVE"
        assert current.json()["aggregate_revision"] == 0
        assert (
            len(
                fetch_outbox(
                    bootstrap_engine,
                    tenant_id=tenant_id,
                    event_type=EVENT_TEACHING_ASSIGNMENT_CLOSED_V1,
                    assignment_id=assignment_id,
                )
            )
            == 0
        )
        assert (
            len(
                fetch_audit(
                    bootstrap_engine,
                    tenant_id=tenant_id,
                    action="teaching.assignment.close",
                    assignment_id=assignment_id,
                )
            )
            == 0
        )
        assert (
            _count_lifecycle_idempotency(
                bootstrap_engine,
                tenant_id=tenant_id,
                operation=TEACHING_ASSIGNMENT_CLOSE_V1,
                assignment_id=assignment_id,
            )
            == 0
        )

    def test_close_idempotent_replay(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        client = build_assignment_client(runtime_engine, tenant_id, principal_id)
        created = _create_via_http(
            client,
            tenant_id,
            content_id=content_id,
            version_id=version_id,
            idempotency_key="i03-close-replay-create",
        )
        assignment_id = uuid.UUID(created.json()["assignment_id"])
        etag = '"r0"'
        close_headers = headers(
            tenant_id,
            idempotency_key="i03-close-replay",
            if_match=etag,
        )
        first = client.post(
            f"{CREATE_PATH}/{assignment_id}/actions/close",
            headers=close_headers,
        )
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["lifecycle_state"] == "CLOSED"
        assert body["aggregate_revision"] == 1
        assert (
            len(
                fetch_outbox(
                    bootstrap_engine,
                    tenant_id=tenant_id,
                    event_type=EVENT_TEACHING_ASSIGNMENT_CLOSED_V1,
                    assignment_id=assignment_id,
                )
            )
            == 1
        )
        assert (
            len(
                fetch_audit(
                    bootstrap_engine,
                    tenant_id=tenant_id,
                    action="teaching.assignment.close",
                    assignment_id=assignment_id,
                )
            )
            == 1
        )
        replay = client.post(
            f"{CREATE_PATH}/{assignment_id}/actions/close",
            headers=close_headers,
        )
        assert replay.status_code == 200, replay.text
        replay_body = replay.json()
        assert replay_body["assignment_id"] == str(assignment_id)
        assert replay_body["lifecycle_state"] == "CLOSED"
        assert replay_body["aggregate_revision"] == 1
        assert (
            len(
                fetch_outbox(
                    bootstrap_engine,
                    tenant_id=tenant_id,
                    event_type=EVENT_TEACHING_ASSIGNMENT_CLOSED_V1,
                    assignment_id=assignment_id,
                )
            )
            == 1
        )
        assert (
            len(
                fetch_audit(
                    bootstrap_engine,
                    tenant_id=tenant_id,
                    action="teaching.assignment.close",
                    assignment_id=assignment_id,
                )
            )
            == 1
        )
        assert (
            _count_lifecycle_idempotency(
                bootstrap_engine,
                tenant_id=tenant_id,
                operation=TEACHING_ASSIGNMENT_CLOSE_V1,
                assignment_id=assignment_id,
            )
            == 1
        )

    def test_close_terminal_state(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        client = build_assignment_client(runtime_engine, tenant_id, principal_id)
        created = _create_via_http(
            client,
            tenant_id,
            content_id=content_id,
            version_id=version_id,
            idempotency_key="i03-close-term-create",
        )
        assignment_id = created.json()["assignment_id"]
        etag = _etag(created)
        closed = client.post(
            f"{CREATE_PATH}/{assignment_id}/actions/close",
            headers=headers(
                tenant_id,
                idempotency_key="i03-close-term",
                if_match=etag,
            ),
        )
        closed_etag = _etag(closed)
        due = client.patch(
            f"{CREATE_PATH}/{assignment_id}",
            headers=headers(
                tenant_id,
                idempotency_key="i03-after-close-due",
                if_match=closed_etag,
            ),
            json={"due_at": DUE_AT.isoformat()},
        )
        assert due.status_code == 409
        cancel = client.post(
            f"{CREATE_PATH}/{assignment_id}/actions/cancel",
            headers=headers(
                tenant_id,
                idempotency_key="i03-after-close-cancel",
                if_match=closed_etag,
            ),
        )
        assert cancel.status_code == 409
        again = client.post(
            f"{CREATE_PATH}/{assignment_id}/actions/close",
            headers=headers(
                tenant_id,
                idempotency_key="i03-close-again",
                if_match=closed_etag,
            ),
        )
        assert again.status_code == 409


class TestCancelHttp:
    def test_cancel_active_assignment(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        client = build_assignment_client(runtime_engine, tenant_id, principal_id)
        created = _create_via_http(
            client,
            tenant_id,
            content_id=content_id,
            version_id=version_id,
            idempotency_key="i03-cancel-create",
        )
        assignment_id = created.json()["assignment_id"]
        etag = _etag(created)
        cancelled = client.post(
            f"{CREATE_PATH}/{assignment_id}/actions/cancel",
            headers=headers(
                tenant_id,
                idempotency_key="i03-cancel",
                if_match=etag,
            ),
        )
        assert cancelled.status_code == 200, cancelled.text
        body = cancelled.json()
        assert body["lifecycle_state"] == "CANCELLED"
        assert body["cancelled_at"] is not None
        assert body["aggregate_revision"] == 1

    def test_cancel_missing_if_match_428(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        client = build_assignment_client(runtime_engine, tenant_id, principal_id)
        created = _create_via_http(
            client,
            tenant_id,
            content_id=content_id,
            version_id=version_id,
            idempotency_key="i03-cancel-428-create",
        )
        assignment_id = created.json()["assignment_id"]
        response = client.post(
            f"{CREATE_PATH}/{assignment_id}/actions/cancel",
            headers=headers(tenant_id, idempotency_key="i03-cancel-428"),
        )
        assert response.status_code == 428

    def test_cancel_stale_if_match_412(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        client = build_assignment_client(runtime_engine, tenant_id, principal_id)
        created = _create_via_http(
            client,
            tenant_id,
            content_id=content_id,
            version_id=version_id,
            idempotency_key="i03-cancel-412-create",
        )
        assignment_id = uuid.UUID(created.json()["assignment_id"])
        assert created.json()["lifecycle_state"] == "ACTIVE"
        assert created.json()["aggregate_revision"] == 0
        response = client.post(
            f"{CREATE_PATH}/{assignment_id}/actions/cancel",
            headers=headers(
                tenant_id,
                idempotency_key="i03-cancel-412",
                if_match='"r99"',
            ),
        )
        assert response.status_code == 412
        current = client.get(
            f"{CREATE_PATH}/{assignment_id}",
            headers=headers(tenant_id),
        )
        assert current.status_code == 200
        assert current.json()["lifecycle_state"] == "ACTIVE"
        assert current.json()["aggregate_revision"] == 0
        assert (
            len(
                fetch_outbox(
                    bootstrap_engine,
                    tenant_id=tenant_id,
                    event_type=EVENT_TEACHING_ASSIGNMENT_CANCELLED_V1,
                    assignment_id=assignment_id,
                )
            )
            == 0
        )
        assert (
            len(
                fetch_audit(
                    bootstrap_engine,
                    tenant_id=tenant_id,
                    action="teaching.assignment.cancel",
                    assignment_id=assignment_id,
                )
            )
            == 0
        )
        assert (
            _count_lifecycle_idempotency(
                bootstrap_engine,
                tenant_id=tenant_id,
                operation=TEACHING_ASSIGNMENT_CANCEL_V1,
                assignment_id=assignment_id,
            )
            == 0
        )

    def test_cancel_idempotent_replay(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        client = build_assignment_client(runtime_engine, tenant_id, principal_id)
        created = _create_via_http(
            client,
            tenant_id,
            content_id=content_id,
            version_id=version_id,
            idempotency_key="i03-cancel-replay-create",
        )
        assignment_id = uuid.UUID(created.json()["assignment_id"])
        etag = '"r0"'
        cancel_headers = headers(
            tenant_id,
            idempotency_key="i03-cancel-replay",
            if_match=etag,
        )
        first = client.post(
            f"{CREATE_PATH}/{assignment_id}/actions/cancel",
            headers=cancel_headers,
        )
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["lifecycle_state"] == "CANCELLED"
        assert body["aggregate_revision"] == 1
        assert (
            len(
                fetch_outbox(
                    bootstrap_engine,
                    tenant_id=tenant_id,
                    event_type=EVENT_TEACHING_ASSIGNMENT_CANCELLED_V1,
                    assignment_id=assignment_id,
                )
            )
            == 1
        )
        assert (
            len(
                fetch_audit(
                    bootstrap_engine,
                    tenant_id=tenant_id,
                    action="teaching.assignment.cancel",
                    assignment_id=assignment_id,
                )
            )
            == 1
        )
        replay = client.post(
            f"{CREATE_PATH}/{assignment_id}/actions/cancel",
            headers=cancel_headers,
        )
        assert replay.status_code == 200, replay.text
        replay_body = replay.json()
        assert replay_body["assignment_id"] == str(assignment_id)
        assert replay_body["lifecycle_state"] == "CANCELLED"
        assert replay_body["aggregate_revision"] == 1
        assert (
            len(
                fetch_outbox(
                    bootstrap_engine,
                    tenant_id=tenant_id,
                    event_type=EVENT_TEACHING_ASSIGNMENT_CANCELLED_V1,
                    assignment_id=assignment_id,
                )
            )
            == 1
        )
        assert (
            len(
                fetch_audit(
                    bootstrap_engine,
                    tenant_id=tenant_id,
                    action="teaching.assignment.cancel",
                    assignment_id=assignment_id,
                )
            )
            == 1
        )
        assert (
            _count_lifecycle_idempotency(
                bootstrap_engine,
                tenant_id=tenant_id,
                operation=TEACHING_ASSIGNMENT_CANCEL_V1,
                assignment_id=assignment_id,
            )
            == 1
        )

    def test_cancel_terminal_state(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        client = build_assignment_client(runtime_engine, tenant_id, principal_id)
        created = _create_via_http(
            client,
            tenant_id,
            content_id=content_id,
            version_id=version_id,
            idempotency_key="i03-cancel-term-create",
        )
        assignment_id = created.json()["assignment_id"]
        etag = _etag(created)
        cancelled = client.post(
            f"{CREATE_PATH}/{assignment_id}/actions/cancel",
            headers=headers(
                tenant_id,
                idempotency_key="i03-cancel-term",
                if_match=etag,
            ),
        )
        cancelled_etag = _etag(cancelled)
        due = client.patch(
            f"{CREATE_PATH}/{assignment_id}",
            headers=headers(
                tenant_id,
                idempotency_key="i03-after-cancel-due",
                if_match=cancelled_etag,
            ),
            json={"due_at": DUE_AT.isoformat()},
        )
        assert due.status_code == 409
        close = client.post(
            f"{CREATE_PATH}/{assignment_id}/actions/close",
            headers=headers(
                tenant_id,
                idempotency_key="i03-after-cancel-close",
                if_match=cancelled_etag,
            ),
        )
        assert close.status_code == 409
