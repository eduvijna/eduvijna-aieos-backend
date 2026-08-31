"""TOS-DEV06-I03 — transactional outbox and security audit integration."""

from __future__ import annotations

import pytest

from aieos.platform.events.constants import EVENT_TEACHING_ASSIGNMENT_CREATED_V1
from aieos.platform.events.teaching_events import assignment_created_outbox
from aieos.platform.events.models import MutationEventContext
from aieos.domains.teaching.application.audit import assignment_primary_ref
from aieos.platform.security.audit import SecurityAuditAction, build_security_mutation_audit_record
from aieos.platform.security.audit.actions import SecurityAuditExecutionChannel
from aieos.platform.security.audit.persistence.models import audit_records_table
from uuid import uuid4
from datetime import UTC, datetime

pytestmark = pytest.mark.tos_dev06_i03


def test_assignment_created_outbox_envelope_type() -> None:
    tenant_id = uuid4()
    assignment_id = uuid4()
    content_id = uuid4()
    version_id = uuid4()
    now = datetime(2026, 8, 31, tzinfo=UTC)
    ctx = MutationEventContext(
        correlation_id=uuid4(),
        causation_id=uuid4(),
        actor_principal_id=uuid4(),
        effective_actor_id=uuid4(),
    )
    row = assignment_created_outbox(
        tenant_id=tenant_id,
        assignment_id=assignment_id,
        content_id=content_id,
        content_version_id=version_id,
        class_ref="class-5a",
        lifecycle_state="ACTIVE",
        aggregate_revision=0,
        context=ctx,
        created_at=now,
    )
    assert row.event_type == EVENT_TEACHING_ASSIGNMENT_CREATED_V1
    assert row.envelope["type"] == EVENT_TEACHING_ASSIGNMENT_CREATED_V1


def test_teaching_audit_record_revision_semantics() -> None:
    tenant_id = uuid4()
    assignment_id = uuid4()
    ctx = MutationEventContext(
        correlation_id=uuid4(),
        causation_id=uuid4(),
        actor_principal_id=uuid4(),
        effective_actor_id=uuid4(),
    )
    record = build_security_mutation_audit_record(
        tenant_id=tenant_id,
        action=SecurityAuditAction.TEACHING_ASSIGNMENT_CREATE,
        primary_resource_ref=assignment_primary_ref(assignment_id, 0),
        resource_revision_before=None,
        resource_revision_after=0,
        related_resource_refs=(),
        mutation_event_context=ctx,
        executing_principal_id=ctx.actor_principal_id,
        execution_channel=SecurityAuditExecutionChannel.API,
        occurred_at=datetime(2026, 8, 31, tzinfo=UTC),
    )
    assert record.action is SecurityAuditAction.TEACHING_ASSIGNMENT_CREATE


def test_audit_models_include_teaching_actions() -> None:
    action_constraint = next(
        c
        for c in audit_records_table.constraints
        if c.name == "ck_audit_records_action"
    )
    sql = str(action_constraint.sqltext)
    assert "teaching.assignment.create" in sql
