"""SQLAlchemy 2.0 mapping for security.audit_records.

Persistence representation only. Alembic remains sole schema authority.
Runtime DDL via MetaData helpers is forbidden.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    PrimaryKeyConstraint,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from aieos.platform.security.audit.persistence.metadata import security_metadata

audit_records_table = Table(
    "audit_records",
    security_metadata,
    Column("audit_record_id", UUID(as_uuid=True), nullable=False),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("action", Text, nullable=False),
    Column("primary_resource_type", Text, nullable=False),
    Column("primary_resource_id", UUID(as_uuid=True), nullable=False),
    Column("primary_resource_revision", BigInteger, nullable=True),
    Column("resource_revision_before", BigInteger, nullable=True),
    Column("resource_revision_after", BigInteger, nullable=False),
    Column("related_resource_refs", JSONB, nullable=False),
    Column("initiating_principal_id", UUID(as_uuid=True), nullable=False),
    Column("effective_actor_id", UUID(as_uuid=True), nullable=False),
    Column("executing_principal_id", UUID(as_uuid=True), nullable=False),
    Column("delegation_id", UUID(as_uuid=True), nullable=True),
    Column("execution_channel", Text, nullable=False),
    Column("correlation_id", UUID(as_uuid=True), nullable=False),
    Column("causation_id", UUID(as_uuid=True), nullable=False),
    Column("trace_id", Text, nullable=True),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("audit_record_id", name="pk_audit_records"),
    CheckConstraint(
        "(get_byte(uuid_send(audit_record_id), 6) >> 4) = 7",
        name="ck_audit_records_id_uuidv7",
    ),
    CheckConstraint(
        "action IN ("
        "'content.create', "
        "'content.version.create', "
        "'content.review.submit', "
        "'content.review.approve', "
        "'content.review.request_changes', "
        "'content.review.reject', "
        "'content.publish', "
        "'content.ai.materialize', "
        "'content.migration.import', "
        "'asset.create', "
        "'asset.revision.register', "
        "'asset.revision.activate', "
        "'asset.lifecycle.withdraw', "
        "'asset.lifecycle.restore', "
        "'asset.lifecycle.delete', "
        "'asset.quarantine.set', "
        "'asset.quarantine.clear', "
        "'asset.safety.pass', "
        "'asset.safety.fail', "
        "'teaching.assignment.create', "
        "'teaching.assignment.due_update', "
        "'teaching.assignment.close', "
        "'teaching.assignment.cancel', "
        "'teaching.execution.start', "
        "'teaching.execution.complete', "
        "'teaching.execution.cancel', "
        "'teaching.execution.observation.create', "
        "'teaching.execution.observation.correct'"
        ")",
        name="ck_audit_records_action",
    ),
    CheckConstraint(
        "execution_channel IN ("
        "'API', "
        "'WORKFLOW_ACTIVITY', "
        "'AI_MATERIALIZATION', "
        "'MIGRATION', "
        "'SYSTEM'"
        ")",
        name="ck_audit_records_execution_channel",
    ),
    CheckConstraint(
        "primary_resource_type ~ '^[a-z][a-z0-9._-]{0,63}$'",
        name="ck_audit_records_primary_resource_type",
    ),
    CheckConstraint(
        "resource_revision_before IS NULL OR resource_revision_before >= 0",
        name="ck_audit_records_before_nonneg",
    ),
    CheckConstraint(
        "resource_revision_after >= 0",
        name="ck_audit_records_after_nonneg",
    ),
    CheckConstraint(
        "primary_resource_revision IS NULL OR primary_resource_revision >= 0",
        name="ck_audit_records_primary_rev_nonneg",
    ),
    CheckConstraint(
        "("
        "action IN ("
        "'content.create', "
        "'content.version.create', "
        "'content.review.submit', "
        "'content.review.approve', "
        "'content.review.request_changes', "
        "'content.review.reject', "
        "'content.publish', "
        "'content.ai.materialize', "
        "'content.migration.import', "
        "'teaching.assignment.create', "
        "'teaching.assignment.due_update', "
        "'teaching.assignment.close', "
        "'teaching.assignment.cancel', "
        "'teaching.execution.start', "
        "'teaching.execution.complete', "
        "'teaching.execution.cancel', "
        "'teaching.execution.observation.create', "
        "'teaching.execution.observation.correct'"
        ") "
        "AND primary_resource_revision IS NOT NULL "
        "AND primary_resource_revision = resource_revision_after"
        ") OR ("
        "action IN ("
        "'asset.create', "
        "'asset.revision.register', "
        "'asset.revision.activate', "
        "'asset.lifecycle.withdraw', "
        "'asset.lifecycle.restore', "
        "'asset.lifecycle.delete', "
        "'asset.quarantine.set', "
        "'asset.quarantine.clear', "
        "'asset.safety.pass', "
        "'asset.safety.fail'"
        ") "
        "AND primary_resource_revision IS NULL"
        ")",
        name="ck_audit_records_primary_revision_family",
    ),
    CheckConstraint(
        "("
        "action = 'content.create' "
        "AND resource_revision_before IS NULL "
        "AND resource_revision_after = 0"
        ") OR ("
        "action = 'content.migration.import' "
        "AND resource_revision_before IS NULL "
        "AND resource_revision_after = 1"
        ") OR ("
        "action IN ("
        "'content.version.create', "
        "'content.review.submit', "
        "'content.review.approve', "
        "'content.review.request_changes', "
        "'content.review.reject', "
        "'content.publish', "
        "'content.ai.materialize'"
        ") "
        "AND resource_revision_before IS NOT NULL "
        "AND resource_revision_after = resource_revision_before + 1"
        ") OR ("
        "action = 'asset.create' "
        "AND resource_revision_before IS NULL "
        "AND resource_revision_after = 0"
        ") OR ("
        "action = 'asset.revision.register' "
        "AND resource_revision_before IS NOT NULL "
        "AND resource_revision_after = resource_revision_before"
        ") OR ("
        "action IN ("
        "'asset.revision.activate', "
        "'asset.lifecycle.withdraw', "
        "'asset.lifecycle.restore', "
        "'asset.lifecycle.delete', "
        "'asset.quarantine.set', "
        "'asset.quarantine.clear', "
        "'asset.safety.pass', "
        "'asset.safety.fail'"
        ") "
        "AND resource_revision_before IS NOT NULL "
        "AND resource_revision_after = resource_revision_before + 1"
        ") OR ("
        "action IN ("
        "'teaching.assignment.create', "
        "'teaching.execution.start', "
        "'teaching.execution.observation.create'"
        ") "
        "AND resource_revision_before IS NULL "
        "AND resource_revision_after = 0"
        ") OR ("
        "action IN ("
        "'teaching.assignment.due_update', "
        "'teaching.assignment.close', "
        "'teaching.assignment.cancel', "
        "'teaching.execution.complete', "
        "'teaching.execution.cancel', "
        "'teaching.execution.observation.correct'"
        ") "
        "AND resource_revision_before IS NOT NULL "
        "AND resource_revision_after = resource_revision_before + 1"
        ")",
        name="ck_audit_records_revision_semantics",
    ),
    CheckConstraint(
        "security.related_resource_refs_are_valid("
        "related_resource_refs, "
        "primary_resource_type, "
        "primary_resource_id, "
        "primary_resource_revision"
        ")",
        name="ck_audit_records_related_refs_valid",
    ),
    CheckConstraint(
        "trace_id IS NULL OR ("
        "trace_id ~ '^[0-9a-f]{32}$' "
        "AND trace_id <> repeat('0', 32)"
        ")",
        name="ck_audit_records_trace_id",
    ),
    schema="security",
)
