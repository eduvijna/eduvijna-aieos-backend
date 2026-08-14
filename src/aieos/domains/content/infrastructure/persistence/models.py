"""SQLAlchemy 2.0 table mappings for content.contents and content.content_versions.

These mappings are persistence representations, not the Generic Content domain
authority. Repository and Unit of Work implementations live in sibling modules.
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
    Integer,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from aieos.domains.content.infrastructure.persistence.metadata import content_metadata

contents_table = Table(
    "contents",
    content_metadata,
    Column("content_id", UUID(as_uuid=True), nullable=False),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("owner_principal_id", UUID(as_uuid=True), nullable=False),
    Column("content_type", Text, nullable=False),
    Column("title", Text, nullable=False),
    Column("description", Text, nullable=False),
    Column("locale", Text, nullable=False),
    Column("stewardship_state", Text, nullable=False),
    Column("current_version_id", UUID(as_uuid=True), nullable=True),
    Column("published_version_id", UUID(as_uuid=True), nullable=True),
    Column("aggregate_revision", BigInteger, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("created_by_principal_id", UUID(as_uuid=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("archived_at", DateTime(timezone=True), nullable=True),
    PrimaryKeyConstraint("content_id", name="pk_contents"),
    UniqueConstraint("tenant_id", "content_id", name="uq_contents_tenant_content"),
    CheckConstraint(
        "aggregate_revision >= 0",
        name="ck_contents_aggregate_revision_nonnegative",
    ),
    CheckConstraint("btrim(content_type) <> ''", name="ck_contents_content_type_nonempty"),
    CheckConstraint("btrim(title) <> ''", name="ck_contents_title_nonempty"),
    CheckConstraint("btrim(locale) <> ''", name="ck_contents_locale_nonempty"),
    CheckConstraint(
        "stewardship_state IN ('DRAFT', 'GENERATED', 'IN_REVIEW', 'APPROVED', 'ARCHIVED')",
        name="ck_contents_stewardship_state",
    ),
    CheckConstraint(
        "(stewardship_state = 'ARCHIVED' AND archived_at IS NOT NULL) "
        "OR (stewardship_state <> 'ARCHIVED' AND archived_at IS NULL)",
        name="ck_contents_archive_iff_archived_at",
    ),
    CheckConstraint(
        "stewardship_state <> 'ARCHIVED' OR published_version_id IS NULL",
        name="ck_contents_archived_withdraws_publication",
    ),
    Index("ix_contents_tenant_id", "tenant_id"),
    Index("ix_contents_tenant_content_type", "tenant_id", "content_type"),
    Index("ix_contents_tenant_stewardship_state", "tenant_id", "stewardship_state"),
    Index("ix_contents_tenant_owner_principal", "tenant_id", "owner_principal_id"),
    schema="content",
)

content_versions_table = Table(
    "content_versions",
    content_metadata,
    Column("version_id", UUID(as_uuid=True), nullable=False),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("content_id", UUID(as_uuid=True), nullable=False),
    Column("version_number", BigInteger, nullable=False),
    Column("parent_version_id", UUID(as_uuid=True), nullable=True),
    Column("schema_id", Text, nullable=False),
    Column("schema_version", Integer, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("payload_sha256", String(64), nullable=False),
    Column("origin", Text, nullable=False),
    Column("provenance", JSONB, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("created_by_principal_id", UUID(as_uuid=True), nullable=False),
    PrimaryKeyConstraint("version_id", name="pk_content_versions"),
    UniqueConstraint(
        "tenant_id",
        "content_id",
        "version_number",
        name="uq_content_versions_tenant_content_number",
    ),
    UniqueConstraint(
        "tenant_id",
        "content_id",
        "version_id",
        name="uq_content_versions_tenant_content_version",
    ),
    CheckConstraint("version_number > 0", name="ck_content_versions_version_number_positive"),
    CheckConstraint("schema_version > 0", name="ck_content_versions_schema_version_positive"),
    CheckConstraint(
        "jsonb_typeof(payload) = 'object'",
        name="ck_content_versions_payload_object",
    ),
    CheckConstraint(
        "payload_sha256 ~ '^[0-9a-f]{64}$'",
        name="ck_content_versions_payload_sha256",
    ),
    CheckConstraint(
        "origin IN ('HUMAN', 'AI', 'IMPORT', 'SYSTEM')",
        name="ck_content_versions_origin",
    ),
    CheckConstraint(
        "(version_number = 1 AND parent_version_id IS NULL) "
        "OR (version_number > 1 AND parent_version_id IS NOT NULL)",
        name="ck_content_versions_first_version_lineage",
    ),
    CheckConstraint(
        "origin <> 'AI' OR provenance IS NOT NULL",
        name="ck_content_versions_ai_provenance_required",
    ),
    CheckConstraint(
        "provenance IS NULL OR jsonb_typeof(provenance) = 'object'",
        name="ck_content_versions_provenance_object",
    ),
    CheckConstraint(
        "origin <> 'AI' OR content.ai_generation_provenance_v1_is_valid(provenance)",
        name="ck_content_versions_ai_provenance_v1",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "content_id"],
        ["content.contents.tenant_id", "content.contents.content_id"],
        name="fk_content_versions_contents",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "content_id", "parent_version_id"],
        [
            "content.content_versions.tenant_id",
            "content.content_versions.content_id",
            "content.content_versions.version_id",
        ],
        name="fk_content_versions_parent",
        ondelete="RESTRICT",
    ),
    Index(
        "ix_content_versions_parent",
        "tenant_id",
        "content_id",
        "parent_version_id",
    ),
    schema="content",
)

contents_table.append_constraint(
    ForeignKeyConstraint(
        ["tenant_id", "content_id", "current_version_id"],
        [
            "content.content_versions.tenant_id",
            "content.content_versions.content_id",
            "content.content_versions.version_id",
        ],
        name="fk_contents_current_version",
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
)
contents_table.append_constraint(
    ForeignKeyConstraint(
        ["tenant_id", "content_id", "published_version_id"],
        [
            "content.content_versions.tenant_id",
            "content.content_versions.content_id",
            "content.content_versions.version_id",
        ],
        name="fk_contents_published_version",
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
)

review_decisions_table = Table(
    "review_decisions",
    content_metadata,
    Column("review_decision_id", UUID(as_uuid=True), nullable=False),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("content_id", UUID(as_uuid=True), nullable=False),
    Column("version_id", UUID(as_uuid=True), nullable=False),
    Column("decision", Text, nullable=False),
    Column("reason_code", Text, nullable=True),
    Column("comment", Text, nullable=True),
    Column("reviewer_principal_id", UUID(as_uuid=True), nullable=False),
    Column("effective_actor_id", UUID(as_uuid=True), nullable=False),
    Column("delegation_id", UUID(as_uuid=True), nullable=True),
    Column("decided_at", DateTime(timezone=True), nullable=False),
    Column("correlation_id", UUID(as_uuid=True), nullable=False),
    PrimaryKeyConstraint("review_decision_id", name="pk_review_decisions"),
    UniqueConstraint(
        "tenant_id",
        "content_id",
        "version_id",
        name="uq_review_decisions_tenant_content_version",
    ),
    CheckConstraint(
        "decision IN ('APPROVE', 'REQUEST_CHANGES', 'REJECT')",
        name="ck_review_decisions_decision",
    ),
    CheckConstraint(
        "reason_code IS NULL OR ("
        "char_length(reason_code) <= 64 AND "
        "reason_code ~ '^[a-z0-9][a-z0-9._-]*$'"
        ")",
        name="ck_review_decisions_reason_code",
    ),
    CheckConstraint(
        "comment IS NULL OR (btrim(comment) <> '' AND char_length(comment) <= 4000)",
        name="ck_review_decisions_comment",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "content_id", "version_id"],
        [
            "content.content_versions.tenant_id",
            "content.content_versions.content_id",
            "content.content_versions.version_id",
        ],
        name="fk_review_decisions_version",
        ondelete="RESTRICT",
    ),
    Index("ix_review_decisions_tenant_id", "tenant_id"),
    schema="content",
)

publications_table = Table(
    "publications",
    content_metadata,
    Column("publication_id", UUID(as_uuid=True), nullable=False),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("content_id", UUID(as_uuid=True), nullable=False),
    Column("version_id", UUID(as_uuid=True), nullable=False),
    Column("approval_decision_id", UUID(as_uuid=True), nullable=False),
    Column("published_by_principal_id", UUID(as_uuid=True), nullable=False),
    Column("effective_actor_id", UUID(as_uuid=True), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=False),
    Column("correlation_id", UUID(as_uuid=True), nullable=False),
    PrimaryKeyConstraint("publication_id", name="pk_publications"),
    UniqueConstraint(
        "tenant_id",
        "content_id",
        "version_id",
        name="uq_publications_tenant_content_version",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "content_id", "version_id"],
        [
            "content.content_versions.tenant_id",
            "content.content_versions.content_id",
            "content.content_versions.version_id",
        ],
        name="fk_publications_version",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["approval_decision_id"],
        ["content.review_decisions.review_decision_id"],
        name="fk_publications_approval_decision",
        ondelete="RESTRICT",
    ),
    Index("ix_publications_tenant_id", "tenant_id"),
    Index("ix_publications_content_id", "tenant_id", "content_id"),
    schema="content",
)

version_asset_refs_table = Table(
    "version_asset_refs",
    content_metadata,
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("content_id", UUID(as_uuid=True), nullable=False),
    Column("version_id", UUID(as_uuid=True), nullable=False),
    Column("asset_resource_type", Text, nullable=False),
    Column("asset_resource_id", UUID(as_uuid=True), nullable=False),
    Column("asset_resource_revision", BigInteger, nullable=True),
    Column("role", Text, nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("required", Boolean, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint(
        "tenant_id",
        "content_id",
        "version_id",
        "role",
        "ordinal",
        name="pk_version_asset_refs",
    ),
    CheckConstraint(
        "asset_resource_type ~ '^[a-z][a-z0-9._-]{0,63}$'",
        name="ck_version_asset_refs_resource_type",
    ),
    CheckConstraint(
        "role ~ '^[a-z][a-z0-9._-]{0,63}$'",
        name="ck_version_asset_refs_role",
    ),
    CheckConstraint("ordinal >= 0", name="ck_version_asset_refs_ordinal"),
    CheckConstraint(
        "asset_resource_revision IS NULL OR asset_resource_revision >= 0",
        name="ck_version_asset_refs_revision",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "content_id", "version_id"],
        [
            "content.content_versions.tenant_id",
            "content.content_versions.content_id",
            "content.content_versions.version_id",
        ],
        name="fk_version_asset_refs_version",
        ondelete="RESTRICT",
    ),
    Index("ix_version_asset_refs_tenant_id", "tenant_id"),
    Index(
        "ix_version_asset_refs_version",
        "tenant_id",
        "content_id",
        "version_id",
    ),
    schema="content",
)
