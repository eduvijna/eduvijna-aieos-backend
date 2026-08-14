"""SQLAlchemy mapping for integration.outbox_messages."""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

integration_metadata = MetaData(schema="integration")

outbox_messages_table = Table(
    "outbox_messages",
    integration_metadata,
    Column("event_id", UUID(as_uuid=True), primary_key=True),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("event_type", Text, nullable=False),
    Column("subject", Text, nullable=False),
    Column("aggregate_type", Text, nullable=False),
    Column("aggregate_id", UUID(as_uuid=True), nullable=False),
    Column("aggregate_revision", BigInteger, nullable=False),
    Column("envelope", JSONB, nullable=False),
    Column("status", Text, nullable=False),
    Column("attempt_count", Integer, nullable=False),
    Column("available_at", DateTime(timezone=True), nullable=False),
    Column("claimed_by", Text, nullable=True),
    Column("claimed_until", DateTime(timezone=True), nullable=True),
    Column("published_at", DateTime(timezone=True), nullable=True),
    Column("broker_stream", Text, nullable=True),
    Column("broker_sequence", BigInteger, nullable=True),
    Column("last_error_code", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "tenant_id",
        "aggregate_type",
        "aggregate_id",
        "aggregate_revision",
        "event_type",
        name="uq_outbox_messages_business_event",
    ),
    CheckConstraint("aggregate_revision >= 0", name="ck_outbox_messages_revision"),
    CheckConstraint("attempt_count >= 0", name="ck_outbox_messages_attempts"),
    Index("ix_outbox_messages_dispatch", "tenant_id", "status", "available_at"),
)
