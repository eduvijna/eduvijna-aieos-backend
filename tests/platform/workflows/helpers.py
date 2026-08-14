"""Shared helpers for GCI-I07 Content review workflow tests."""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.platform.api.app import create_app
from aieos.platform.workflows.persistence.repositories import (
    SqlAlchemyWorkflowDispatcherRepository,
)
from aieos.platform.workflows.temporal.dispatchers import (
    ContentReviewCommandDispatcher,
    ContentReviewStartDispatcher,
    DispatcherConfig,
)
from aieos.platform.workflows.temporal.worker import create_content_review_worker
from tests.fakes import (
    AllowAssetCurrentGovernance,
    AllowAssetReferenceValidation,
    AllowPublicationAuthorization,
    AllowPublicationGovernance,
    AllowReviewAuthorization,
    AllowReviewCommentPolicy,
    IDEMPOTENCY_RETENTION,
    StubSecurityContextResolver,
    make_test_schema_registry,
)

CURSOR_KEY = b"gci-i07-test-cursor-signing-key"
CREATE_BODY = {
    "content_type": "test.generic",
    "title": "Title",
    "description": "Description",
    "locale": "en-IN",
}
APPEND_BODY = {"schema_id": "test.generic", "schema_version": 1, "payload": {"marker": "v1"}}
SENSITIVE_MARKERS = (
    "SENSITIVE_TEST_COMMENT",
    "reason_code_should_not_leak",
    "Title",
    "Description",
    '"marker": "v1"',
)


def run_async(coro):
    return asyncio.run(coro)


def app_for(
    runtime_engine: Engine,
    tenant_id: UUID,
    principal_id: UUID,
    *,
    authorization=None,
    comment_policy=None,
    publication_authorization=None,
    publication_governance=None,
    asset_reference_validation=None,
    asset_current_governance=None,
):
    return create_app(
        uow_factory=SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
        security_resolver=StubSecurityContextResolver(tenant_id, principal_id),
        content_types=StaticContentTypeCatalog({"test.generic"}),
        cursor_signing_key=CURSOR_KEY,
        schema_registry=make_test_schema_registry(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
        review_authorization=authorization or AllowReviewAuthorization(),
        review_comment_policy=comment_policy or AllowReviewCommentPolicy(),
        publication_authorization=publication_authorization
        or AllowPublicationAuthorization(),
        publication_governance=publication_governance or AllowPublicationGovernance(),
        asset_reference_validation=asset_reference_validation
        or AllowAssetReferenceValidation(),
        asset_current_governance=asset_current_governance
        or AllowAssetCurrentGovernance(),
    )


def client_for(runtime_engine: Engine, tenant_id: UUID, principal_id: UUID, **kw) -> TestClient:
    return TestClient(
        app_for(runtime_engine, tenant_id, principal_id, **kw),
        raise_server_exceptions=False,
    )


def headers(tenant_id: UUID, **extra: str) -> dict[str, str]:
    out = {"X-AIEOS-Tenant-ID": str(tenant_id), **extra}
    if "Idempotency-Key" not in out:
        out["Idempotency-Key"] = f"test-{uuid.uuid7()}"
    return out


def create_content(client: TestClient, tenant_id: UUID) -> dict:
    response = client.post("/api/v1/contents", json=CREATE_BODY, headers=headers(tenant_id))
    assert response.status_code == 201, response.text
    return response.json()


def append_version(client: TestClient, tenant_id: UUID, content_id: str, *, etag: str):
    hdrs = headers(tenant_id)
    hdrs["If-Match"] = etag
    return client.post(
        f"/api/v1/contents/{content_id}/versions",
        json=APPEND_BODY,
        headers=hdrs,
    )


def submit_review(client: TestClient, tenant_id: UUID, content_id: str, version_id: str, *, etag: str, **extra):
    hdrs = headers(tenant_id, **extra)
    hdrs["If-Match"] = etag
    return client.post(
        f"/api/v1/contents/{content_id}/versions/{version_id}/actions/submit-for-review",
        headers=hdrs,
    )


def decide(
    client: TestClient,
    tenant_id: UUID,
    content_id: str,
    version_id: str,
    *,
    action: str,
    etag: str,
    body: dict | None = None,
    **extra,
):
    hdrs = headers(tenant_id, **extra)
    hdrs["If-Match"] = etag
    return client.post(
        f"/api/v1/contents/{content_id}/versions/{version_id}/actions/{action}",
        json=body or {},
        headers=hdrs,
    )


def generated_version(client: TestClient, tenant_id: UUID) -> tuple[str, str, str]:
    created = create_content(client, tenant_id)
    content_id = created["content_id"]
    appended = append_version(client, tenant_id, content_id, etag='"r0"')
    assert appended.status_code == 201, appended.text
    return content_id, appended.json()["version_id"], appended.headers["ETag"]


def in_review(client: TestClient, tenant_id: UUID) -> tuple[str, str, str]:
    content_id, version_id, etag = generated_version(client, tenant_id)
    submitted = submit_review(client, tenant_id, content_id, version_id, etag=etag)
    assert submitted.status_code == 200, submitted.text
    return content_id, version_id, submitted.headers["ETag"]


def start_intent_rows(bootstrap_engine: Engine, content_id: str) -> list:
    with bootstrap_engine.connect() as conn:
        return list(
            conn.execute(
                text(
                    """
                    SELECT * FROM workflow.workflow_start_intents
                    WHERE input->>'content_id' = :cid
                    ORDER BY created_at
                    """
                ),
                {"cid": content_id},
            ).mappings()
        )


def command_intent_rows(bootstrap_engine: Engine, content_id: str) -> list:
    with bootstrap_engine.connect() as conn:
        return list(
            conn.execute(
                text(
                    """
                    SELECT * FROM workflow.workflow_command_intents
                    WHERE payload->>'content_id' = :cid
                    ORDER BY created_at
                    """
                ),
                {"cid": content_id},
            ).mappings()
        )


def content_row(bootstrap_engine: Engine, content_id: str):
    with bootstrap_engine.connect() as conn:
        return conn.execute(
            text(
                """
                SELECT stewardship_state, aggregate_revision, current_version_id
                FROM content.contents WHERE content_id = :cid
                """
            ),
            {"cid": content_id},
        ).one()


def decision_count(bootstrap_engine: Engine, content_id: str) -> int:
    with bootstrap_engine.connect() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT count(*) FROM content.review_decisions WHERE content_id = :cid"
                ),
                {"cid": content_id},
            ).scalar_one()
        )


def default_dispatcher_config(*, claimed_by: str = "dispatcher-a") -> DispatcherConfig:
    return DispatcherConfig(
        claim_lease=timedelta(seconds=5),
        max_attempts=3,
        retry_delay=timedelta(milliseconds=1),
        claimed_by=claimed_by,
        result_timeout_seconds=10.0,
        start_reconciliation_timeout_seconds=5.0,
    )


def start_dispatcher(engine: Engine, gateway, *, claimed_by: str = "dispatcher-a"):
    return ContentReviewStartDispatcher(
        SqlAlchemyWorkflowDispatcherRepository(engine),
        gateway,
        default_dispatcher_config(claimed_by=claimed_by),
    )


def command_dispatcher(engine: Engine, gateway, *, claimed_by: str = "dispatcher-a"):
    return ContentReviewCommandDispatcher(
        SqlAlchemyWorkflowDispatcherRepository(engine),
        gateway,
        default_dispatcher_config(claimed_by=claimed_by),
    )


async def with_worker(env, coro):
    async with create_content_review_worker(env.client):
        return await coro
