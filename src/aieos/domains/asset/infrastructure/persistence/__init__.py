"""Asset SQLAlchemy persistence mappings. No repositories in PED-I10B2."""

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

__all__ = [
    "ASSET_SCHEMA",
    "asset_metadata",
    "asset_revision_states_table",
    "asset_revisions_table",
    "assets_table",
    "deletion_evidence_table",
]
