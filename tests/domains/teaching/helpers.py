"""Shared TOS-DEV02 HTTP helpers backed by real PostgreSQL adapters."""

from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

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
    IDEMPOTENCY_RETENTION,
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

CURSOR_KEY = b"tos-dev02-lane-b-test-cursor-key"


def build_client(
    runtime_engine: Engine, tenant_id: UUID, principal_id: UUID
) -> TestClient:
    app = create_app(
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
    )
    return TestClient(app, raise_server_exceptions=False)


def headers(
    tenant_id: UUID,
    *,
    idempotency_key: str | None = None,
    if_match: str | None = None,
) -> dict[str, str]:
    out = {"X-AIEOS-Tenant-ID": str(tenant_id)}
    if idempotency_key is not None:
        out["Idempotency-Key"] = idempotency_key
    if if_match is not None:
        out["If-Match"] = if_match
    return out


def create_work(
    client: TestClient,
    tenant_id: UUID,
    *,
    goal_text: str,
    target_date: str,
    idempotency_key: str,
    class_label: str | None = "Grade 5B",
    subject: str | None = "Mathematics",
    topic: str | None = "Fractions",
    locale: str = "en-IN",
    intent_type: str = "prepare_tomorrow",
):
    return client.post(
        "/api/v1/teaching/works",
        json={
            "intent_type": intent_type,
            "goal_text": goal_text,
            "target_date": target_date,
            "locale": locale,
            "class_label": class_label,
            "subject": subject,
            "topic": topic,
        },
        headers=headers(tenant_id, idempotency_key=idempotency_key),
    )
