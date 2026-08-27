"""SQLAlchemy 2.0 table mapping for teaching.works.

Persistence representation only; the TeachingWork aggregate remains the domain
authority. There is deliberately no teaching_intents table: a Teaching Intent
is the inbound request that produces a Work, not a durable aggregate. There is
also no mission table: Today's Mission is derived on read.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Index,
    PrimaryKeyConstraint,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

from aieos.domains.teaching.infrastructure.persistence.metadata import teaching_metadata

works_table = Table(
    "works",
    teaching_metadata,
    Column("work_id", UUID(as_uuid=True), nullable=False),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("teacher_principal_id", UUID(as_uuid=True), nullable=False),
    Column("intent_type", Text, nullable=False),
    Column("goal_text", Text, nullable=False),
    # class_label is contextual teacher-entered text (e.g. "Grade 5B").
    # It is NOT a foreign key into any Class System of Record.
    Column("class_label", Text, nullable=True),
    Column("subject", Text, nullable=True),
    Column("topic", Text, nullable=True),
    Column("target_date", Date, nullable=False),
    Column("locale", Text, nullable=False),
    Column("aggregate_revision", BigInteger, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("archived_at", DateTime(timezone=True), nullable=True),
    PrimaryKeyConstraint("work_id", name="pk_teaching_works"),
    UniqueConstraint("tenant_id", "work_id", name="uq_teaching_works_tenant_work"),
    CheckConstraint(
        "aggregate_revision >= 0",
        name="ck_teaching_works_aggregate_revision_nonnegative",
    ),
    # Extend this list in a new migration whenever IntentType gains a member.
    CheckConstraint(
        "intent_type IN ('prepare_tomorrow')",
        name="ck_teaching_works_intent_type",
    ),
    CheckConstraint("btrim(goal_text) <> ''", name="ck_teaching_works_goal_text_nonempty"),
    CheckConstraint("btrim(locale) <> ''", name="ck_teaching_works_locale_nonempty"),
    CheckConstraint(
        "class_label IS NULL OR btrim(class_label) <> ''",
        name="ck_teaching_works_class_label_nonempty",
    ),
    CheckConstraint(
        "subject IS NULL OR btrim(subject) <> ''",
        name="ck_teaching_works_subject_nonempty",
    ),
    CheckConstraint(
        "topic IS NULL OR btrim(topic) <> ''",
        name="ck_teaching_works_topic_nonempty",
    ),
    CheckConstraint(
        "updated_at >= created_at",
        name="ck_teaching_works_updated_after_created",
    ),
    Index("ix_teaching_works_tenant_id", "tenant_id"),
    Index("ix_teaching_works_tenant_teacher", "tenant_id", "teacher_principal_id"),
    Index(
        "ix_teaching_works_tenant_teacher_target_date",
        "tenant_id",
        "teacher_principal_id",
        "target_date",
    ),
    Index("ix_teaching_works_tenant_archived_at", "tenant_id", "archived_at"),
    schema="teaching",
)
