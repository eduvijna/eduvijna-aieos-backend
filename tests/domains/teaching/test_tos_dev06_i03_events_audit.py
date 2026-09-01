"""TOS-DEV06-I03 — transactional outbox and security audit integration."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.engine import Engine

from aieos.domains.teaching.application.assignment_mutations import (
    CancelTeachingAssignmentService,
    CloseTeachingAssignmentService,
    UpdateTeachingAssignmentDueService,
)
from aieos.domains.teaching.application.audit import assignment_primary_ref
from aieos.domains.teaching.application.audit import api_mutation_audit_provenance
from aieos.domains.teaching.application.models import UpdateTeachingAssignmentDueCommand
from aieos.domains.teaching.domain.identities import AggregateRevision, AssignmentId
from aieos.domains.teaching.infrastructure.persistence.uow import (
    SqlAlchemyTeachingUnitOfWorkFactory,
)
from aieos.platform.events.constants import (
    CLOUDEVENTS_TEACHING_SOURCE,
    EVENT_TEACHING_ASSIGNMENT_CANCELLED_V1,
    EVENT_TEACHING_ASSIGNMENT_CLOSED_V1,
    EVENT_TEACHING_ASSIGNMENT_CREATED_V1,
    EVENT_TEACHING_ASSIGNMENT_DUE_UPDATED_V1,
)
from aieos.platform.events.models import MutationEventContext
from aieos.platform.events.teaching_events import (
    assignment_cancelled_outbox,
    assignment_closed_outbox,
    assignment_created_outbox,
    assignment_due_updated_outbox,
)
from aieos.platform.security.audit import (
    SecurityAuditAction,
    build_security_mutation_audit_record,
)
from aieos.platform.security.audit.actions import SecurityAuditExecutionChannel
from aieos.platform.security.audit.persistence.models import audit_records_table
from tests.domains.teaching.helpers_dev06_i03 import (
    FIXED_NOW,
    IDEMPOTENCY_RETENTION,
    create_assignment,
    event_context,
    fetch_audit,
    fetch_outbox,
    seed_published_worksheet,
)

pytestmark = pytest.mark.tos_dev06_i03

DUE_AT = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


def test_assignment_created_outbox_envelope_type_and_payload() -> None:
    tenant_id = uuid.uuid4()
    assignment_id = uuid.uuid4()
    teacher_id = uuid.uuid4()
    content_id = uuid.uuid4()
    version_id = uuid.uuid4()
    now = datetime(2026, 8, 31, tzinfo=UTC)
    ctx = MutationEventContext(
        correlation_id=uuid.uuid4(),
        causation_id=uuid.uuid4(),
        actor_principal_id=teacher_id,
        effective_actor_id=teacher_id,
    )
    row = assignment_created_outbox(
        tenant_id=tenant_id,
        assignment_id=assignment_id,
        teacher_principal_id=teacher_id,
        content_id=content_id,
        content_version_id=version_id,
        class_ref="class-5a",
        lifecycle_state="ACTIVE",
        available_from=now,
        due_at=None,
        source_work_id=None,
        aggregate_revision=0,
        context=ctx,
        created_at=now,
    )
    assert row.event_type == EVENT_TEACHING_ASSIGNMENT_CREATED_V1
    envelope = row.envelope
    assert envelope["type"] == EVENT_TEACHING_ASSIGNMENT_CREATED_V1
    assert envelope["source"] == CLOUDEVENTS_TEACHING_SOURCE
    assert envelope["tenantid"] == str(tenant_id)
    assert envelope["correlationid"] == str(ctx.correlation_id)
    assert envelope["causationid"] == str(ctx.causation_id)
    assert envelope["actorid"] == str(teacher_id)
    assert envelope["effectiveactorid"] == str(teacher_id)
    assert envelope["aggregaterevision"] == 0
    data = envelope["data"]
    assert data["assignment_id"] == str(assignment_id)
    assert data["teacher_principal_id"] == str(teacher_id)
    assert data["content_id"] == str(content_id)
    assert data["content_version_id"] == str(version_id)
    assert data["class_ref"] == "class-5a"
    assert data["lifecycle_state"] == "ACTIVE"
    assert data["available_from"] == now.isoformat()
    assert "source_work_id" not in data


def test_mutation_outbox_payloads_include_lifecycle_state() -> None:
    tenant_id = uuid.uuid4()
    assignment_id = uuid.uuid4()
    now = datetime(2026, 8, 31, tzinfo=UTC)
    ctx = MutationEventContext(
        correlation_id=uuid.uuid4(),
        causation_id=uuid.uuid4(),
        actor_principal_id=uuid.uuid4(),
        effective_actor_id=uuid.uuid4(),
    )
    due = assignment_due_updated_outbox(
        tenant_id=tenant_id,
        assignment_id=assignment_id,
        lifecycle_state="ACTIVE",
        due_at=now,
        aggregate_revision=1,
        context=ctx,
        created_at=now,
    )
    assert due.event_type == EVENT_TEACHING_ASSIGNMENT_DUE_UPDATED_V1
    assert due.envelope["type"] == EVENT_TEACHING_ASSIGNMENT_DUE_UPDATED_V1
    assert due.envelope["source"] == CLOUDEVENTS_TEACHING_SOURCE
    assert due.envelope["data"]["assignment_id"] == str(assignment_id)
    assert due.envelope["data"]["lifecycle_state"] == "ACTIVE"
    assert due.envelope["data"]["due_at"] == now.isoformat()
    closed = assignment_closed_outbox(
        tenant_id=tenant_id,
        assignment_id=assignment_id,
        lifecycle_state="CLOSED",
        closed_at=now,
        aggregate_revision=2,
        context=ctx,
        created_at=now,
    )
    assert closed.event_type == EVENT_TEACHING_ASSIGNMENT_CLOSED_V1
    assert closed.envelope["data"]["lifecycle_state"] == "CLOSED"
    assert closed.envelope["data"]["closed_at"] == now.isoformat()
    cancelled = assignment_cancelled_outbox(
        tenant_id=tenant_id,
        assignment_id=assignment_id,
        lifecycle_state="CANCELLED",
        cancelled_at=now,
        aggregate_revision=3,
        context=ctx,
        created_at=now,
    )
    assert cancelled.event_type == EVENT_TEACHING_ASSIGNMENT_CANCELLED_V1
    assert cancelled.envelope["data"]["lifecycle_state"] == "CANCELLED"
    assert cancelled.envelope["data"]["cancelled_at"] == now.isoformat()


def test_teaching_audit_record_revision_semantics() -> None:
    tenant_id = uuid.uuid4()
    assignment_id = uuid.uuid4()
    principal = uuid.uuid4()
    ctx = MutationEventContext(
        correlation_id=uuid.uuid4(),
        causation_id=uuid.uuid4(),
        actor_principal_id=principal,
        effective_actor_id=principal,
    )
    record = build_security_mutation_audit_record(
        tenant_id=tenant_id,
        action=SecurityAuditAction.TEACHING_ASSIGNMENT_CREATE,
        primary_resource_ref=assignment_primary_ref(assignment_id, 0),
        resource_revision_before=None,
        resource_revision_after=0,
        related_resource_refs=(),
        mutation_event_context=ctx,
        executing_principal_id=principal,
        execution_channel=SecurityAuditExecutionChannel.API,
        occurred_at=datetime(2026, 8, 31, tzinfo=UTC),
    )
    assert record.action is SecurityAuditAction.TEACHING_ASSIGNMENT_CREATE
    assert record.primary_resource_ref.resource_type == "teaching.assignment"
    assert record.audit_context.executing_principal_id == principal
    assert (
        record.audit_context.mutation_event_context.effective_actor_id == principal
    )
    assert (
        record.audit_context.execution_channel
        is SecurityAuditExecutionChannel.API
    )


def test_audit_models_include_teaching_actions() -> None:
    action_constraint = next(
        c
        for c in audit_records_table.constraints
        if c.name == "ck_audit_records_action"
    )
    sql = str(action_constraint.sqltext)
    for action in (
        "teaching.assignment.create",
        "teaching.assignment.due_update",
        "teaching.assignment.close",
        "teaching.assignment.cancel",
    ):
        assert action in sql


class TestPersistedEventAuditContracts:
    def test_create_persisted_event_and_audit(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        created = create_assignment(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            content_id=content_id,
            content_version_id=version_id,
            idempotency_key="i03-ev-audit-create",
        )
        outbox = fetch_outbox(
            bootstrap_engine,
            tenant_id=tenant_id,
            event_type=EVENT_TEACHING_ASSIGNMENT_CREATED_V1,
            assignment_id=created.assignment_id,
        )
        assert len(outbox) == 1
        envelope = outbox[0]["envelope"]
        if isinstance(envelope, str):
            envelope = json.loads(envelope)
        assert envelope["type"] == EVENT_TEACHING_ASSIGNMENT_CREATED_V1
        assert envelope["source"] == CLOUDEVENTS_TEACHING_SOURCE
        assert envelope["data"]["content_id"] == str(content_id)
        audit = fetch_audit(
            bootstrap_engine,
            tenant_id=tenant_id,
            action="teaching.assignment.create",
            assignment_id=created.assignment_id,
        )
        assert len(audit) == 1
        assert audit[0]["primary_resource_type"] == "teaching.assignment"
        assert audit[0]["primary_resource_revision"] == 0
        assert audit[0]["resource_revision_before"] is None
        assert audit[0]["resource_revision_after"] == 0
        assert audit[0]["execution_channel"] == "API"
        related = audit[0]["related_resource_refs"]
        if isinstance(related, str):
            related = json.loads(related)
        assert any(r["resource_type"] == "content.content_version" for r in related)

    def test_full_lifecycle_persisted_events(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        created = create_assignment(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            content_id=content_id,
            content_version_id=version_id,
            idempotency_key="i03-ev-lifecycle-create",
        )
        assignment_id = AssignmentId(created.assignment_id)
        factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
        UpdateTeachingAssignmentDueService(
            factory, idempotency_retention=IDEMPOTENCY_RETENTION
        ).update_due(
            tenant_id,
            principal_id,
            assignment_id=assignment_id,
            expected_aggregate_revision=AggregateRevision(0),
            command=UpdateTeachingAssignmentDueCommand(due_at=DUE_AT),
            idempotency_key="i03-ev-lifecycle-due",
            event_context=event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        CloseTeachingAssignmentService(
            factory, idempotency_retention=IDEMPOTENCY_RETENTION
        ).close(
            tenant_id,
            principal_id,
            assignment_id=assignment_id,
            expected_aggregate_revision=AggregateRevision(1),
            idempotency_key="i03-ev-lifecycle-close",
            event_context=event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        for event_type, action in (
            (EVENT_TEACHING_ASSIGNMENT_DUE_UPDATED_V1, "teaching.assignment.due_update"),
            (EVENT_TEACHING_ASSIGNMENT_CLOSED_V1, "teaching.assignment.close"),
        ):
            assert (
                len(
                    fetch_outbox(
                        bootstrap_engine,
                        tenant_id=tenant_id,
                        event_type=event_type,
                        assignment_id=created.assignment_id,
                    )
                )
                == 1
            )
            assert (
                len(
                    fetch_audit(
                        bootstrap_engine,
                        tenant_id=tenant_id,
                        action=action,
                        assignment_id=created.assignment_id,
                    )
                )
                == 1
            )

    def test_cancel_persisted_event_contract(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        created = create_assignment(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            content_id=content_id,
            content_version_id=version_id,
            idempotency_key="i03-ev-cancel-create",
        )
        CancelTeachingAssignmentService(
            SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine),
            idempotency_retention=IDEMPOTENCY_RETENTION,
        ).cancel(
            tenant_id,
            principal_id,
            assignment_id=AssignmentId(created.assignment_id),
            expected_aggregate_revision=AggregateRevision(0),
            idempotency_key="i03-ev-cancel",
            event_context=event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        outbox = fetch_outbox(
            bootstrap_engine,
            tenant_id=tenant_id,
            event_type=EVENT_TEACHING_ASSIGNMENT_CANCELLED_V1,
            assignment_id=created.assignment_id,
        )
        assert len(outbox) == 1
        envelope = outbox[0]["envelope"]
        if isinstance(envelope, str):
            envelope = json.loads(envelope)
        assert envelope["data"]["lifecycle_state"] == "CANCELLED"
        assert "cancelled_at" in envelope["data"]
