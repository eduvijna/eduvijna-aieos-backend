"""Shared TOS-DEV03 HTTP helpers with fake Model Gateway wiring."""

from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from aieos.development.schemas import (
    build_development_schema_registry,
    development_content_type_names,
)
from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.domains.teaching.infrastructure.persistence.uow import (
    SqlAlchemyTeachingUnitOfWorkFactory,
)
from aieos.platform.ai.clock import UtcNow
from aieos.platform.ai.fake import FakeStructuredModelGateway
from aieos.platform.ai.gateway import StructuredModelGateway
from aieos.platform.ai.infrastructure.persistence.uow import (
    SqlAlchemyAIUnitOfWorkFactory,
)
from aieos.platform.api.app import create_app
from tests.domains.teaching.worksheet_fixtures import valid_worksheet_model
from tests.fakes import (
    IDEMPOTENCY_RETENTION,
    AllowAIGenerationAuthorization,
    AllowAssetCurrentGovernance,
    AllowAssetReferenceValidation,
    AllowPublicationAuthorization,
    AllowPublicationGovernance,
    AllowReviewAuthorization,
    AllowReviewCommentPolicy,
    FixedPrincipalAuthenticator,
    StubSecurityContextResolver,
)

CURSOR_KEY = b"tos-dev03-lane-b-test-cursor-key"


def build_fake_gateway() -> FakeStructuredModelGateway:
    gateway = FakeStructuredModelGateway(
        result_factory=lambda _request: valid_worksheet_model()
    )
    return gateway


def build_client(
    runtime_engine: Engine,
    tenant_id: UUID,
    principal_id: UUID,
    *,
    model_gateway: StructuredModelGateway | None = None,
    provider_id: str = "fake",
    model_id: str = "fake-model",
    generation_lease_seconds: int = 120,
    generation_clock: UtcNow | None = None,
) -> TestClient:
    gateway = model_gateway if model_gateway is not None else build_fake_gateway()
    app = create_app(
        uow_factory=SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
        teaching_uow_factory=SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine),
        request_identity_authenticator=FixedPrincipalAuthenticator(principal_id),
        security_resolver=StubSecurityContextResolver(tenant_id, principal_id),
        content_types=StaticContentTypeCatalog(development_content_type_names()),
        cursor_signing_key=CURSOR_KEY,
        schema_registry=build_development_schema_registry(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
        review_authorization=AllowReviewAuthorization(),
        review_comment_policy=AllowReviewCommentPolicy(),
        publication_authorization=AllowPublicationAuthorization(),
        publication_governance=AllowPublicationGovernance(),
        asset_reference_validation=AllowAssetReferenceValidation(),
        asset_current_governance=AllowAssetCurrentGovernance(),
        ai_uow_factory=SqlAlchemyAIUnitOfWorkFactory(runtime_engine),
        model_gateway=gateway,
        ai_generation_authorization=AllowAIGenerationAuthorization(),
        ai_provider_id=provider_id,
        ai_model_id=model_id,
        generation_lease_seconds=generation_lease_seconds,
        generation_clock=generation_clock,
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
    goal_text: str = (
        "Tomorrow my Grade 5 students need to understand fractions "
        "using visual examples."
    ),
    target_date: str = "2026-09-01",
    idempotency_key: str,
    class_label: str | None = "Grade 5-A",
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
