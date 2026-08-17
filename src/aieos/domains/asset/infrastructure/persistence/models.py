"""SQLAlchemy 2 Core table mappings for the Asset PostgreSQL System of Record.

These mappings are persistence representations, not Asset domain authority
objects. Repositories and Unit of Work are not part of PED-I10B2.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

from aieos.domains.asset.infrastructure.persistence.metadata import asset_metadata

_RESOURCE_TYPES = (
    "resource_type IN ("
    "'asset.image', 'asset.document', 'asset.audio', 'asset.video')"
)

assets_table = Table(
    "assets",
    asset_metadata,
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("asset_id", UUID(as_uuid=True), nullable=False),
    Column("resource_type", Text, nullable=False),
    Column("lifecycle", Text, nullable=False),
    Column("quarantine_state", Text, nullable=False),
    Column("current_revision", BigInteger, nullable=True),
    Column("aggregate_revision", BigInteger, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("created_by_principal_id", UUID(as_uuid=True), nullable=False),
    PrimaryKeyConstraint("asset_id", name="pk_assets"),
    UniqueConstraint("tenant_id", "asset_id", name="uq_assets_tenant_asset"),
    UniqueConstraint(
        "tenant_id",
        "asset_id",
        "resource_type",
        name="uq_assets_tenant_asset_resource_type",
    ),
    CheckConstraint(_RESOURCE_TYPES, name="ck_assets_resource_type"),
    CheckConstraint(
        "lifecycle IN ('active', 'withdrawn', 'deleted')",
        name="ck_assets_lifecycle",
    ),
    CheckConstraint(
        "quarantine_state IN ('clear', 'quarantined')",
        name="ck_assets_quarantine_state",
    ),
    CheckConstraint(
        "current_revision IS NULL OR current_revision > 0",
        name="ck_assets_current_revision_positive",
    ),
    CheckConstraint(
        "aggregate_revision >= 0",
        name="ck_assets_aggregate_revision_nonnegative",
    ),
    Index("ix_assets_tenant_id", "tenant_id"),
    Index("ix_assets_tenant_resource_type", "tenant_id", "resource_type"),
    Index("ix_assets_tenant_lifecycle", "tenant_id", "lifecycle"),
    schema="asset",
)

asset_revisions_table = Table(
    "asset_revisions",
    asset_metadata,
    Column("asset_revision_id", UUID(as_uuid=True), nullable=False),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("asset_id", UUID(as_uuid=True), nullable=False),
    Column("revision_number", BigInteger, nullable=False),
    Column("resource_type", Text, nullable=False),
    Column("storage_key", Text, nullable=False),
    Column("media_type", Text, nullable=False),
    Column("byte_size", BigInteger, nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("created_by_principal_id", UUID(as_uuid=True), nullable=False),
    PrimaryKeyConstraint("asset_revision_id", name="pk_asset_revisions"),
    UniqueConstraint(
        "tenant_id",
        "asset_id",
        "revision_number",
        name="uq_asset_revisions_tenant_asset_number",
    ),
    UniqueConstraint(
        "tenant_id",
        "asset_id",
        "asset_revision_id",
        "revision_number",
        name="uq_asset_revisions_tenant_asset_id_number",
    ),
    CheckConstraint(
        "revision_number > 0",
        name="ck_asset_revisions_revision_number_positive",
    ),
    CheckConstraint(_RESOURCE_TYPES, name="ck_asset_revisions_resource_type"),
    CheckConstraint(
        "byte_size >= 0",
        name="ck_asset_revisions_byte_size_nonnegative",
    ),
    CheckConstraint(
        "sha256 ~ '^[0-9a-f]{64}$'",
        name="ck_asset_revisions_sha256",
    ),
    CheckConstraint(
        "btrim(media_type) <> ''",
        name="ck_asset_revisions_media_type_nonempty",
    ),
    CheckConstraint(
        "btrim(storage_key) <> ''",
        name="ck_asset_revisions_storage_key_nonempty",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "asset_id", "resource_type"],
        [
            "asset.assets.tenant_id",
            "asset.assets.asset_id",
            "asset.assets.resource_type",
        ],
        name="fk_asset_revisions_asset_resource",
        ondelete="RESTRICT",
    ),
    schema="asset",
)

assets_table.append_constraint(
    ForeignKeyConstraint(
        ["tenant_id", "asset_id", "current_revision"],
        [
            "asset.asset_revisions.tenant_id",
            "asset.asset_revisions.asset_id",
            "asset.asset_revisions.revision_number",
        ],
        name="fk_assets_current_revision",
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
)

asset_revision_states_table = Table(
    "asset_revision_states",
    asset_metadata,
    Column("asset_revision_id", UUID(as_uuid=True), nullable=False),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("asset_id", UUID(as_uuid=True), nullable=False),
    Column("revision_number", BigInteger, nullable=False),
    Column("safety_state", Text, nullable=False),
    Column("bytes_purged", Boolean, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("asset_revision_id", name="pk_asset_revision_states"),
    CheckConstraint(
        "revision_number > 0",
        name="ck_asset_revision_states_revision_number_positive",
    ),
    CheckConstraint(
        "safety_state IN ('pending', 'passed', 'failed')",
        name="ck_asset_revision_states_safety_state",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "asset_id", "asset_revision_id", "revision_number"],
        [
            "asset.asset_revisions.tenant_id",
            "asset.asset_revisions.asset_id",
            "asset.asset_revisions.asset_revision_id",
            "asset.asset_revisions.revision_number",
        ],
        name="fk_asset_revision_states_revision",
        ondelete="RESTRICT",
    ),
    Index(
        "ix_asset_revision_states_tenant_asset_number",
        "tenant_id",
        "asset_id",
        "revision_number",
    ),
    schema="asset",
)

deletion_evidence_table = Table(
    "deletion_evidence",
    asset_metadata,
    Column("asset_revision_id", UUID(as_uuid=True), nullable=False),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("asset_id", UUID(as_uuid=True), nullable=False),
    Column("revision_number", BigInteger, nullable=False),
    Column("purged_at", DateTime(timezone=True), nullable=False),
    Column("purged_by_principal_id", UUID(as_uuid=True), nullable=False),
    PrimaryKeyConstraint("asset_revision_id", name="pk_deletion_evidence"),
    CheckConstraint(
        "revision_number > 0",
        name="ck_deletion_evidence_revision_number_positive",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "asset_id", "asset_revision_id", "revision_number"],
        [
            "asset.asset_revisions.tenant_id",
            "asset.asset_revisions.asset_id",
            "asset.asset_revisions.asset_revision_id",
            "asset.asset_revisions.revision_number",
        ],
        name="fk_deletion_evidence_revision",
        ondelete="RESTRICT",
    ),
    Index(
        "ix_deletion_evidence_tenant_asset_number",
        "tenant_id",
        "asset_id",
        "revision_number",
    ),
    schema="asset",
)
