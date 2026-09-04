"""TOS-DEV01 Lane B — PostgreSQL-backed Teacher OS review product spine proofs.

Uses the real SqlAlchemy persistence adapter and existing Review Queue /
review decision HTTP contracts. Synthetic tenant/principal/content only.
"""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from aieos.development.app_factory import build_development_review_scenario_app
from aieos.development.schemas import DEV_CONTENT_TYPE, DEV_SCHEMA_ID, DEV_SCHEMA_VERSION
from aieos.development.teacher_os_review_scenario import (
    ARTIFACT_SPECS,
    ensure_teacher_os_review_scenario,
)
from aieos.domains.content.application.catalog import StaticContentTypeCatalog
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
from tests.platform.workflows.helpers import decide, headers

pytestmark = pytest.mark.gci_i12

CURSOR_KEY = b"tos-dev01-lane-b-test-cursor-key"


def _client(runtime_engine: Engine, tenant_id: UUID, principal_id: UUID) -> TestClient:
    app = build_development_review_scenario_app(
        runtime_engine, tenant_id=tenant_id, principal_id=principal_id
    )
    return TestClient(app, raise_server_exceptions=False)


def _foreign_client(
    runtime_engine: Engine, tenant_id: UUID, principal_id: UUID
) -> TestClient:
    """Separate tenant app for isolation proofs (same engine, different authz)."""
    return TestClient(
        create_app(
            uow_factory=SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
            teaching_uow_factory=SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine),
        assessment_uow_factory=SqlAlchemyAssessmentUnitOfWorkFactory(runtime_engine),
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
        ),
        raise_server_exceptions=False,
    )


def _queue_list(client: TestClient, tenant_id: UUID, **params):
    return client.get(
        "/api/v1/teacher-os/review-queue",
        params=params or None,
        headers={"X-AIEOS-Tenant-ID": str(tenant_id)},
    )


def _queue_get(client: TestClient, tenant_id: UUID, content_id: str, version_id: str):
    return client.get(
        f"/api/v1/teacher-os/review-queue/{content_id}/versions/{version_id}",
        headers={"X-AIEOS-Tenant-ID": str(tenant_id)},
    )


def _in_review(
    client: TestClient, tenant_id: UUID, *, title: str, marker: str
) -> tuple[str, str, str]:
    created = client.post(
        "/api/v1/contents",
        json={
            "content_type": DEV_CONTENT_TYPE,
            "title": title,
            "description": "synthetic TOS-DEV01 spine item",
            "locale": "en-IN",
        },
        headers=headers(tenant_id),
    )
    assert created.status_code == 201, created.text
    content_id = created.json()["content_id"]
    appended = client.post(
        f"/api/v1/contents/{content_id}/versions",
        json={
            "schema_id": DEV_SCHEMA_ID,
            "schema_version": DEV_SCHEMA_VERSION,
            "payload": {"marker": marker, "synthetic": True},
        },
        headers={**headers(tenant_id), "If-Match": created.headers["ETag"]},
    )
    assert appended.status_code == 201, appended.text
    version_id = appended.json()["version_id"]
    submitted = client.post(
        f"/api/v1/contents/{content_id}/versions/{version_id}/actions/submit-for-review",
        headers={**headers(tenant_id), "If-Match": appended.headers["ETag"]},
    )
    assert submitted.status_code == 200, submitted.text
    return content_id, version_id, submitted.headers["ETag"]


class TestTosDev01ScenarioBuilder:
    def test_scenario_seeds_three_queue_items_idempotently(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, principal_id)

        first = ensure_teacher_os_review_scenario(
            client, tenant_id=tenant_id, principal_id=principal_id
        )
        assert len(first.artifacts) == 3
        assert first.reused_existing is False
        assert {a.key for a in first.artifacts} == {s.key for s in ARTIFACT_SPECS}

        listed = _queue_list(client, tenant_id)
        assert listed.status_code == 200
        assert len(listed.json()["items"]) == 3

        second = ensure_teacher_os_review_scenario(
            client, tenant_id=tenant_id, principal_id=principal_id
        )
        assert second.reused_existing is True
        assert {a.content_id for a in second.artifacts} == {
            a.content_id for a in first.artifacts
        }
        assert len(_queue_list(client, tenant_id).json()["items"]) == 3


class TestTosDev01ApproveSpine:
    def test_approve_removes_pending_queue_item(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id, version_id, etag = _in_review(
            client, tenant_id, title="approve-spine", marker="approve-spine"
        )

        detail = _queue_get(client, tenant_id, content_id, version_id)
        assert detail.status_code == 200, detail.text
        assert detail.headers["ETag"] == etag
        body = detail.json()
        assert body["version_id"] == version_id
        assert body["artifact_status"] == "In Review"
        assert body["payload"]["marker"] == "approve-spine"

        approved = decide(
            client, tenant_id, content_id, version_id, action="approve", etag=etag
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["decision"] == "APPROVE"
        assert approved.json()["stewardship_state"] == "APPROVED"

        remaining = [
            i
            for i in _queue_list(client, tenant_id).json()["items"]
            if i["content_id"] == content_id
        ]
        assert remaining == []
        missing = _queue_get(client, tenant_id, content_id, version_id)
        assert missing.status_code == 404


class TestTosDev01RequestChangesSpine:
    def test_request_changes_keeps_version_historical_and_requires_new_version(
        self, runtime_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id, version_id, etag = _in_review(
            client,
            tenant_id,
            title="request-changes-spine",
            marker="request-changes-spine",
        )

        decided = decide(
            client,
            tenant_id,
            content_id,
            version_id,
            action="request-changes",
            etag=etag,
            body={"comment": "Please clarify fraction examples."},
        )
        assert decided.status_code == 200, decided.text
        assert decided.json()["decision"] == "REQUEST_CHANGES"
        assert decided.json()["stewardship_state"] == "GENERATED"
        assert decided.json()["comment"] == "Please clarify fraction examples."

        assert _queue_get(client, tenant_id, content_id, version_id).status_code == 404

        # Exact reviewed version cannot be resubmitted (frozen Content semantics).
        resubmit_same = client.post(
            f"/api/v1/contents/{content_id}/versions/{version_id}/actions/submit-for-review",
            headers={**headers(tenant_id), "If-Match": decided.headers["ETag"]},
        )
        assert resubmit_same.status_code == 409, resubmit_same.text
        assert resubmit_same.json()["code"] == "review_requires_new_version"

        # New immutable version is required for resubmission.
        appended = client.post(
            f"/api/v1/contents/{content_id}/versions",
            json={
                "schema_id": DEV_SCHEMA_ID,
                "schema_version": DEV_SCHEMA_VERSION,
                "payload": {"marker": "request-changes-spine-v2", "synthetic": True},
            },
            headers={**headers(tenant_id), "If-Match": decided.headers["ETag"]},
        )
        assert appended.status_code == 201, appended.text
        new_version_id = appended.json()["version_id"]
        assert new_version_id != version_id
        assert appended.json()["version_number"] == 2

        submitted = client.post(
            f"/api/v1/contents/{content_id}/versions/{new_version_id}/actions/submit-for-review",
            headers={**headers(tenant_id), "If-Match": appended.headers["ETag"]},
        )
        assert submitted.status_code == 200, submitted.text
        assert (
            _queue_get(client, tenant_id, content_id, new_version_id).status_code == 200
        )


class TestTosDev01RejectSpine:
    def test_reject_persists_and_exact_version_is_not_approved(
        self, runtime_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, uuid.uuid7())
        content_id, version_id, etag = _in_review(
            client, tenant_id, title="reject-spine", marker="reject-spine"
        )

        rejected = decide(
            client,
            tenant_id,
            content_id,
            version_id,
            action="reject",
            etag=etag,
            body={"comment": "Not suitable for this cohort."},
        )
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["decision"] == "REJECT"
        assert rejected.json()["stewardship_state"] == "GENERATED"
        assert rejected.json()["stewardship_state"] != "APPROVED"

        assert _queue_get(client, tenant_id, content_id, version_id).status_code == 404
        content = client.get(
            f"/api/v1/contents/{content_id}",
            headers={"X-AIEOS-Tenant-ID": str(tenant_id)},
        )
        assert content.status_code == 200
        assert content.json()["stewardship_state"] == "GENERATED"
        assert content.json()["stewardship_state"] != "APPROVED"


class TestTosDev01HttpContract:
    def test_etag_if_match_idempotency_412_and_tenant_isolation(
        self, runtime_engine
    ) -> None:
        tenant_a = uuid.uuid7()
        principal_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        client_a = _client(runtime_engine, tenant_a, principal_a)
        client_b = _foreign_client(runtime_engine, tenant_b, uuid.uuid7())

        content_id, version_id, etag = _in_review(
            client_a, tenant_a, title="contract-spine", marker="contract-spine"
        )

        detail = _queue_get(client_a, tenant_a, content_id, version_id)
        assert detail.status_code == 200
        assert detail.headers["ETag"] == etag

        # Tenant B cannot see tenant A queue item.
        foreign_list = _queue_list(client_b, tenant_b)
        assert foreign_list.status_code == 200
        assert all(i["content_id"] != content_id for i in foreign_list.json()["items"])
        foreign_detail = _queue_get(client_b, tenant_b, content_id, version_id)
        assert foreign_detail.status_code in (403, 404)

        # Missing If-Match → 428
        missing_precondition = client_a.post(
            f"/api/v1/contents/{content_id}/versions/{version_id}/actions/approve",
            json={},
            headers=headers(tenant_a),
        )
        assert missing_precondition.status_code == 428, missing_precondition.text

        # Stale If-Match → 412
        stale = client_a.post(
            f"/api/v1/contents/{content_id}/versions/{version_id}/actions/approve",
            json={},
            headers={**headers(tenant_a), "If-Match": '"r0"'},
        )
        assert stale.status_code == 412, stale.text

        # Fresh approve with Idempotency-Key + If-Match
        key = f"tos-dev01-idem-{uuid.uuid7()}"
        first = client_a.post(
            f"/api/v1/contents/{content_id}/versions/{version_id}/actions/approve",
            json={},
            headers={
                "X-AIEOS-Tenant-ID": str(tenant_a),
                "If-Match": etag,
                "Idempotency-Key": key,
            },
        )
        assert first.status_code == 200, first.text

        replay = client_a.post(
            f"/api/v1/contents/{content_id}/versions/{version_id}/actions/approve",
            json={},
            headers={
                "X-AIEOS-Tenant-ID": str(tenant_a),
                "If-Match": etag,
                "Idempotency-Key": key,
            },
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["decision"] == first.json()["decision"]
        assert _queue_get(client_a, tenant_a, content_id, version_id).status_code == 404
