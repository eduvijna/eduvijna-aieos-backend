"""GCI-I10 direct AppendContentVersionService asset_refs binding validation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.application.errors import AssetReferenceValidationFailed
from aieos.domains.content.application.models import AppendContentVersionCommand
from aieos.domains.content.application.services import AppendContentVersionService
from aieos.domains.content.domain.identities import (
    AggregateRevision,
    ContentId,
    ContentVersionId,
    VersionNumber,
)
from aieos.domains.content.domain.origin import ContentOrigin
from aieos.domains.content.domain.schema import SchemaId, SchemaVersion
from aieos.domains.content.domain.version import ContentPayload, ContentVersion
from aieos.domains.content.domain.version_asset_ref import VersionAssetRef
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.platform.events.models import MutationEventContext
from aieos.platform.resources import ResourceRef
from tests.fakes import AllowAssetReferenceValidation

pytestmark = pytest.mark.gci_i10

FIXED_NOW = datetime(2026, 8, 14, 15, 0, tzinfo=UTC)


def _event_context(actor: uuid.UUID | None = None) -> MutationEventContext:
    actor = actor or uuid.uuid7()
    return MutationEventContext(
        correlation_id=uuid.uuid7(),
        causation_id=uuid.uuid7(),
        actor_principal_id=actor,
        effective_actor_id=actor,
    )


def _seed_content(bootstrap_engine: Engine, tenant_id: uuid.UUID) -> ContentId:
    content_id = ContentId.generate()
    owner = uuid.uuid7()
    with bootstrap_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO content.contents (
                    content_id, tenant_id, owner_principal_id, content_type, title,
                    description, locale, stewardship_state, current_version_id,
                    published_version_id, aggregate_revision, created_at,
                    created_by_principal_id, updated_at, archived_at
                ) VALUES (
                    :content_id, :tenant_id, :owner, 'test.generic', 'Title',
                    'Description', 'en-IN', 'DRAFT', NULL,
                    NULL, 0, :now, :owner, :now, NULL
                )
                """
            ),
            {
                "content_id": content_id.value,
                "tenant_id": tenant_id,
                "owner": owner,
                "now": FIXED_NOW,
            },
        )
    return content_id


def _version(tenant_id: uuid.UUID, content_id: ContentId, principal_id: uuid.UUID) -> ContentVersion:
    return ContentVersion(
        version_id=ContentVersionId.generate(),
        tenant_id=tenant_id,
        content_id=content_id,
        version_number=VersionNumber(1),
        parent_version_id=None,
        schema_id=SchemaId("test.generic"),
        schema_version=SchemaVersion(1),
        payload=ContentPayload.from_mapping({"marker": "v1"}),
        origin=ContentOrigin.HUMAN,
        created_at=FIXED_NOW,
        created_by_principal_id=principal_id,
    )


class TestDirectAppendAssetRefs:
    def test_validates_bindings_before_persist(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        denied = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id)
        version = _version(tenant_id, content_id, principal_id)
        ref = VersionAssetRef(
            tenant_id=tenant_id,
            content_id=content_id,
            version_id=version.version_id,
            resource_ref=ResourceRef("asset.image", denied, None),
            role="primary",
            ordinal=0,
            required=True,
            created_at=FIXED_NOW,
        )
        service = AppendContentVersionService(
            SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
            AllowAssetReferenceValidation(deny_ids={denied}),
        )
        with pytest.raises(AssetReferenceValidationFailed):
            service.append(
                tenant_id,
                AppendContentVersionCommand(
                    expected_aggregate_revision=AggregateRevision(0),
                    version=version,
                    asset_refs=(ref,),
                ),
                event_context=_event_context(principal_id),
                principal_id=principal_id,
                now=FIXED_NOW,
            )
        with bootstrap_engine.connect() as conn:
            count = conn.execute(
                text("SELECT count(*) FROM content.version_asset_refs WHERE content_id = :cid"),
                {"cid": content_id.value},
            ).scalar_one()
        assert int(count) == 0

    def test_persists_refs_when_bindings_allowed(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id)
        version = _version(tenant_id, content_id, principal_id)
        rid = uuid.uuid7()
        ref = VersionAssetRef(
            tenant_id=tenant_id,
            content_id=content_id,
            version_id=version.version_id,
            resource_ref=ResourceRef("asset.image", rid, 1),
            role="primary",
            ordinal=0,
            required=True,
            created_at=FIXED_NOW,
        )
        service = AppendContentVersionService(
            SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
            AllowAssetReferenceValidation(),
        )
        result = service.append(
            tenant_id,
            AppendContentVersionCommand(
                expected_aggregate_revision=AggregateRevision(0),
                version=version,
                asset_refs=(ref,),
            ),
            event_context=_event_context(principal_id),
            principal_id=principal_id,
            now=FIXED_NOW,
        )
        assert result.version_id == version.version_id
        with bootstrap_engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT asset_resource_id, asset_resource_revision "
                    "FROM content.version_asset_refs WHERE content_id = :cid"
                ),
                {"cid": content_id.value},
            ).one()
        assert row.asset_resource_id == rid
        assert int(row.asset_resource_revision) == 1
