"""SQLAlchemy 2.0 table mapping for ai.generation_runs."""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from aieos.platform.ai.infrastructure.persistence.metadata import ai_metadata

generation_runs_table = Table(
    "generation_runs",
    ai_metadata,
    Column("generation_run_id", UUID(as_uuid=True), nullable=False),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("principal_id", UUID(as_uuid=True), nullable=False),
    Column("work_resource_type", Text, nullable=False),
    Column("work_resource_id", UUID(as_uuid=True), nullable=False),
    Column("work_resource_revision", BigInteger, nullable=False),
    Column("capability_id", Text, nullable=False),
    Column("provider_id", Text, nullable=False),
    Column("model_id", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("request_fingerprint_sha256", Text, nullable=False),
    Column("idempotency_key_sha256", Text, nullable=False),
    Column("provider_response_id", Text, nullable=True),
    Column("input_tokens", Integer, nullable=True),
    Column("output_tokens", Integer, nullable=True),
    Column("total_tokens", Integer, nullable=True),
    Column("educational_quality_summary", JSONB, nullable=True),
    Column("result_content_id", UUID(as_uuid=True), nullable=True),
    Column("result_version_id", UUID(as_uuid=True), nullable=True),
    Column("result_content_revision", BigInteger, nullable=True),
    Column("failure_code", Text, nullable=True),
    Column("aggregate_revision", BigInteger, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    PrimaryKeyConstraint("generation_run_id", name="pk_ai_generation_runs"),
    UniqueConstraint(
        "tenant_id",
        "generation_run_id",
        name="uq_ai_generation_runs_tenant_run",
    ),
    UniqueConstraint(
        "tenant_id",
        "principal_id",
        "idempotency_key_sha256",
        name="uq_ai_generation_runs_tenant_principal_idempotency",
    ),
    CheckConstraint(
        "aggregate_revision >= 0",
        name="ck_ai_generation_runs_aggregate_revision_nonnegative",
    ),
    CheckConstraint(
        "work_resource_revision >= 0",
        name="ck_ai_generation_runs_work_revision_nonnegative",
    ),
    CheckConstraint(
        "status IN ('RUNNING', 'VALIDATED', 'SUCCEEDED', 'FAILED')",
        name="ck_ai_generation_runs_status",
    ),
    CheckConstraint(
        "work_resource_type = 'teaching.work'",
        name="ck_ai_generation_runs_work_resource_type",
    ),
    CheckConstraint(
        "btrim(capability_id) <> ''",
        name="ck_ai_generation_runs_capability_nonempty",
    ),
    CheckConstraint(
        "btrim(provider_id) <> ''",
        name="ck_ai_generation_runs_provider_nonempty",
    ),
    CheckConstraint(
        "btrim(model_id) <> ''",
        name="ck_ai_generation_runs_model_nonempty",
    ),
    CheckConstraint(
        "char_length(request_fingerprint_sha256) = 64",
        name="ck_ai_generation_runs_fingerprint_sha256",
    ),
    CheckConstraint(
        "char_length(idempotency_key_sha256) = 64",
        name="ck_ai_generation_runs_idempotency_sha256",
    ),
    CheckConstraint(
        "updated_at >= created_at",
        name="ck_ai_generation_runs_updated_after_created",
    ),
    Index("ix_ai_generation_runs_tenant_id", "tenant_id"),
    Index("ix_ai_generation_runs_tenant_principal", "tenant_id", "principal_id"),
    Index(
        "ix_ai_generation_runs_tenant_work",
        "tenant_id",
        "work_resource_id",
    ),
    Index("ix_ai_generation_runs_tenant_status", "tenant_id", "status"),
    schema="ai",
)
