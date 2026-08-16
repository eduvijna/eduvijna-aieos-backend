"""GCI-I11 AI materialization → review → publish regression (no generation HTTP)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.application.ai_materialization import (
    MaterializeAIGeneratedContentVersionService,
)
from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.application.models import AIGeneratedVersionMaterializationCommand
from aieos.domains.content.domain.identities import AggregateRevision, ContentId
from aieos.domains.content.domain.provenance import AIGenerationProvenanceV1
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.platform.api.app import create_app
from aieos.platform.events.models import MutationEventContext
from aieos.platform.resources import ResourceRef
from aieos.domains.content.application.audit import ai_materialization_audit_provenance
from tests.fakes import (
    AllowAIGenerationAuthorization,
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
from tests.platform.workflows.helpers import create_content, decide, headers, submit_review

pytestmark = pytest.mark.gci_i11

CURSOR_KEY = b"gci-i11-test-cursor-signing-key"
FIXED_NOW = datetime(2026, 8, 14, 21, 0, tzinfo=UTC)


def _client(runtime_engine: Engine, tenant_id: UUID, principal_id: UUID) -> TestClient:
    return TestClient(
        create_app(
            uow_factory=SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
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


class TestAIReviewPublishPipeline:
    def test_ai_version_submit_approve_publish(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, principal_id)
        created = create_content(client, tenant_id)
        content_id = ContentId(UUID(created["content_id"]))
        correlation_id = uuid.uuid7()
        result = MaterializeAIGeneratedContentVersionService(
            SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
            make_test_schema_registry(),
            AllowAssetReferenceValidation(),
            AllowAIGenerationAuthorization(),
        ).materialize(
            tenant_id,
            principal_id,
            AIGeneratedVersionMaterializationCommand(
                content_id=content_id,
                expected_aggregate_revision=AggregateRevision(0),
                schema_id="test.generic",
                schema_version=1,
                payload={"marker": "ai-v1"},
                provenance=AIGenerationProvenanceV1(
                    generation_run_ref=ResourceRef("generation.run", uuid.uuid7(), None),
                    prompt_execution_ref=None,
                    provider_id="test.provider",
                    model_id="neutral-model",
                    capability_id="content.generate.lesson",
                    source_refs=(),
                    policy_refs=(),
                    evaluation_refs=(),
                    correlation_id=correlation_id,
                ),
            ),
            event_context=MutationEventContext(
                correlation_id=correlation_id,
                causation_id=uuid.uuid7(),
                actor_principal_id=principal_id,
                effective_actor_id=principal_id,
            ),
            audit_provenance=ai_materialization_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        version_id = str(result.version_id.value)
        with bootstrap_engine.connect() as conn:
            state = conn.execute(
                text(
                    "SELECT stewardship_state, published_version_id FROM content.contents "
                    "WHERE content_id = :cid"
                ),
                {"cid": content_id.value},
            ).one()
        assert state.stewardship_state == "GENERATED"
        assert state.published_version_id is None

        submitted = submit_review(
            client, tenant_id, str(content_id.value), version_id, etag='"r1"'
        )
        assert submitted.status_code == 200, submitted.text
        approved = decide(
            client,
            tenant_id,
            str(content_id.value),
            version_id,
            action="approve",
            etag=submitted.headers["ETag"],
        )
        assert approved.status_code == 200, approved.text
        hdrs = headers(tenant_id)
        hdrs["If-Match"] = approved.headers["ETag"]
        published = client.post(
            f"/api/v1/contents/{content_id.value}/actions/publish",
            json={"version_id": version_id},
            headers=hdrs,
        )
        assert published.status_code == 200, published.text
        with bootstrap_engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT published_version_id, stewardship_state FROM content.contents "
                    "WHERE content_id = :cid"
                ),
                {"cid": content_id.value},
            ).one()
            pubs = conn.execute(
                text("SELECT count(*) FROM content.publications WHERE content_id = :cid"),
                {"cid": content_id.value},
            ).scalar_one()
        assert str(row.published_version_id) == version_id
        assert row.stewardship_state == "APPROVED"
        assert int(pubs) == 1
