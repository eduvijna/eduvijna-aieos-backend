"""Current-use VersionAssetRef governance orchestration."""

from __future__ import annotations

from uuid import UUID

from aieos.domains.content.application.errors import ContentVersionNotFound
from aieos.domains.content.application.ports import (
    AssetCurrentGovernancePort,
    ContentUnitOfWorkFactory,
)
from aieos.domains.content.domain.identities import ContentId, ContentVersionId


class ValidateVersionAssetGovernanceService:
    def __init__(
        self,
        uow_factory: ContentUnitOfWorkFactory,
        asset_governance: AssetCurrentGovernancePort,
    ) -> None:
        self._uow_factory = uow_factory
        self._asset_governance = asset_governance

    def validate(
        self,
        tenant_id: UUID,
        principal_id: UUID,
        content_id: ContentId,
        version_id: ContentVersionId,
    ) -> None:
        with self._uow_factory(tenant_id) as uow:
            version = uow.versions.get(version_id)
            if version is None or version.content_id != content_id:
                raise ContentVersionNotFound(
                    "ContentVersion is not visible for the requested Content"
                )
            refs = uow.version_asset_refs.list_for_version(content_id, version_id)
            self._asset_governance.validate_current_use(
                tenant_id=tenant_id,
                principal_id=principal_id,
                content_id=content_id,
                version_id=version_id,
                asset_refs=refs,
            )
