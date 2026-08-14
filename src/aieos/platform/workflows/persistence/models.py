"""SQLAlchemy table definitions for workflow intent infrastructure."""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

workflow_metadata = MetaData(schema="workflow")

workflow_start_intents_table = Table(
    "workflow_start_intents",
    workflow_metadata,
    Column("workflow_start_intent_id", UUID(as_uuid=True), primary_key=True),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("workflow_instance_id", UUID(as_uuid=True), nullable=False),
    Column("workflow_type", Text, nullable=False),
    Column("workflow_major_version", Integer, nullable=False),
    Column("temporal_workflow_id", Text, nullable=False),
    Column("task_queue", Text, nullable=False),
    Column("business_key", Text, nullable=False),
    Column("input", JSONB, nullable=False),
    Column("status", Text, nullable=False),
    Column("attempt_count", Integer, nullable=False),
    Column("available_at", DateTime(timezone=True), nullable=False),
    Column("claimed_by", Text, nullable=True),
    Column("claimed_until", DateTime(timezone=True), nullable=True),
    Column("delivered_at", DateTime(timezone=True), nullable=True),
    Column("last_error_code", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("workflow_instance_id", name="uq_workflow_start_intents_instance"),
    UniqueConstraint(
        "temporal_workflow_id", name="uq_workflow_start_intents_temporal_id"
    ),
    UniqueConstraint(
        "tenant_id",
        "workflow_type",
        "business_key",
        name="uq_workflow_start_intents_business_key",
    ),
    CheckConstraint(
        "workflow_major_version > 0", name="ck_workflow_start_intents_major"
    ),
    CheckConstraint("attempt_count >= 0", name="ck_workflow_start_intents_attempts"),
    Index(
        "ix_workflow_start_intents_dispatch",
        "tenant_id",
        "status",
        "available_at",
    ),
)

workflow_command_intents_table = Table(
    "workflow_command_intents",
    workflow_metadata,
    Column("workflow_command_intent_id", UUID(as_uuid=True), primary_key=True),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column(
        "workflow_instance_id",
        UUID(as_uuid=True),
        ForeignKey(
            "workflow.workflow_start_intents.workflow_instance_id",
            name="fk_workflow_command_intents_instance",
            ondelete="RESTRICT",
        ),
        nullable=False,
    ),
    Column("temporal_workflow_id", Text, nullable=False),
    Column("command_id", UUID(as_uuid=True), nullable=False),
    Column("command_type", Text, nullable=False),
    Column("business_key", Text, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("status", Text, nullable=False),
    Column("attempt_count", Integer, nullable=False),
    Column("available_at", DateTime(timezone=True), nullable=False),
    Column("claimed_by", Text, nullable=True),
    Column("claimed_until", DateTime(timezone=True), nullable=True),
    Column("delivered_at", DateTime(timezone=True), nullable=True),
    Column("last_error_code", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("command_id", name="uq_workflow_command_intents_command_id"),
    UniqueConstraint(
        "tenant_id",
        "business_key",
        name="uq_workflow_command_intents_business_key",
    ),
    CheckConstraint("attempt_count >= 0", name="ck_workflow_command_intents_attempts"),
    Index(
        "ix_workflow_command_intents_dispatch",
        "tenant_id",
        "status",
        "available_at",
    ),
)
