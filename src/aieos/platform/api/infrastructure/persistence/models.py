"""SQLAlchemy mapping for api.idempotency_records. Not Content business authority."""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CHAR,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    PrimaryKeyConstraint,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

from aieos.platform.api.infrastructure.persistence.metadata import api_metadata

idempotency_records_table = Table(
    "idempotency_records",
    api_metadata,
    Column("idempotency_record_id", UUID(as_uuid=True), nullable=False),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("actor_principal_id", UUID(as_uuid=True), nullable=False),
    Column("operation", Text, nullable=False),
    Column("idempotency_key_sha256", CHAR(64), nullable=False),
    Column("request_fingerprint_sha256", CHAR(64), nullable=False),
    Column("result_content_id", UUID(as_uuid=True), nullable=False),
    Column("result_version_id", UUID(as_uuid=True), nullable=True),
    Column("result_review_decision_id", UUID(as_uuid=True), nullable=True),
    Column("result_aggregate_revision", BigInteger, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("idempotency_record_id", name="pk_idempotency_records"),
    UniqueConstraint(
        "tenant_id",
        "actor_principal_id",
        "operation",
        "idempotency_key_sha256",
        name="uq_idempotency_scope",
    ),
    CheckConstraint(
        "idempotency_key_sha256 ~ '^[0-9a-f]{64}$'",
        name="ck_idempotency_key_sha256",
    ),
    CheckConstraint(
        "request_fingerprint_sha256 ~ '^[0-9a-f]{64}$'",
        name="ck_idempotency_fingerprint_sha256",
    ),
    CheckConstraint(
        "result_aggregate_revision >= 0",
        name="ck_idempotency_revision_nonnegative",
    ),
    CheckConstraint("btrim(operation) <> ''", name="ck_idempotency_operation_nonempty"),
    CheckConstraint(
        "expires_at > created_at",
        name="ck_idempotency_expires_after_created",
    ),
    Index("ix_idempotency_records_tenant", "tenant_id"),
    schema="api",
)
