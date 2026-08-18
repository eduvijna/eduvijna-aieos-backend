"""Asset SQLAlchemy persistence mappings, current-use reads, and write UoW."""

from aieos.domains.asset.infrastructure.persistence.audit_repository import (
    AssetSecurityMutationAuditRepository,
)
from aieos.domains.asset.infrastructure.persistence.authority_reads import (
    PostgresAssetCurrentUseStore,
    fetch_revision,
    fetch_revision_state,
    fetch_typed_asset,
)
from aieos.domains.asset.infrastructure.persistence.metadata import (
    ASSET_SCHEMA,
    asset_metadata,
)
from aieos.domains.asset.infrastructure.persistence.models import (
    asset_revision_states_table,
    asset_revisions_table,
    assets_table,
    deletion_evidence_table,
)
from aieos.domains.asset.infrastructure.persistence.postgres_use_authority import (
    PostgresAssetUseAuthority,
)
from aieos.domains.asset.infrastructure.persistence.session import (
    asset_authority_read,
)
from aieos.domains.asset.infrastructure.persistence.uow import (
    SqlAlchemyAssetUnitOfWork,
    SqlAlchemyAssetUnitOfWorkFactory,
)
from aieos.domains.asset.infrastructure.persistence.write_repositories import (
    SqlAlchemyAssetRevisionStateWriteRepository,
    SqlAlchemyAssetRevisionWriteRepository,
    SqlAlchemyAssetWriteRepository,
)

__all__ = [
    "ASSET_SCHEMA",
    "AssetSecurityMutationAuditRepository",
    "PostgresAssetCurrentUseStore",
    "PostgresAssetUseAuthority",
    "SqlAlchemyAssetRevisionStateWriteRepository",
    "SqlAlchemyAssetRevisionWriteRepository",
    "SqlAlchemyAssetUnitOfWork",
    "SqlAlchemyAssetUnitOfWorkFactory",
    "SqlAlchemyAssetWriteRepository",
    "asset_authority_read",
    "asset_metadata",
    "asset_revision_states_table",
    "asset_revisions_table",
    "assets_table",
    "deletion_evidence_table",
    "fetch_revision",
    "fetch_revision_state",
    "fetch_typed_asset",
]
