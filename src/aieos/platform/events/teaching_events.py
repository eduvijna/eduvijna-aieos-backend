"""Helpers that assemble TeachingAssignment outbox rows for a mutation."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from aieos.platform.events.cloudevents import build_teaching_cloudevent
from aieos.platform.events.constants import (
    AGGREGATE_TYPE_TEACHING_ASSIGNMENT,
    EVENT_TEACHING_ASSIGNMENT_CANCELLED_V1,
    EVENT_TEACHING_ASSIGNMENT_CLOSED_V1,
    EVENT_TEACHING_ASSIGNMENT_CREATED_V1,
    EVENT_TEACHING_ASSIGNMENT_DUE_UPDATED_V1,
    OUTBOX_PENDING,
    teaching_assignment_subject,
)
from aieos.platform.events.identities import EventId
from aieos.platform.events.models import MutationEventContext, OutboxMessage


def _base(
    *,
    event_id: EventId,
    tenant_id: UUID,
    event_type: str,
    assignment_id: UUID,
    aggregate_revision: int,
    envelope: dict[str, object],
    created_at: datetime,
) -> OutboxMessage:
    return OutboxMessage(
        event_id=event_id,
        tenant_id=tenant_id,
        event_type=event_type,
        subject=teaching_assignment_subject(str(assignment_id)),
        aggregate_type=AGGREGATE_TYPE_TEACHING_ASSIGNMENT,
        aggregate_id=assignment_id,
        aggregate_revision=aggregate_revision,
        envelope=envelope,
        status=OUTBOX_PENDING,
        attempt_count=0,
        available_at=created_at,
        claimed_by=None,
        claimed_until=None,
        published_at=None,
        broker_stream=None,
        broker_sequence=None,
        last_error_code=None,
        created_at=created_at,
    )


def assignment_created_outbox(
    *,
    tenant_id: UUID,
    assignment_id: UUID,
    teacher_principal_id: UUID,
    content_id: UUID,
    content_version_id: UUID,
    class_ref: str,
    lifecycle_state: str,
    available_from: datetime,
    due_at: datetime | None,
    source_work_id: UUID | None,
    aggregate_revision: int,
    context: MutationEventContext,
    created_at: datetime,
) -> OutboxMessage:
    event_id = EventId.generate()
    data: dict[str, object] = {
        "assignment_id": str(assignment_id),
        "teacher_principal_id": str(teacher_principal_id),
        "content_id": str(content_id),
        "content_version_id": str(content_version_id),
        "class_ref": class_ref,
        "lifecycle_state": lifecycle_state,
        "available_from": available_from.isoformat(),
        "due_at": None if due_at is None else due_at.isoformat(),
    }
    if source_work_id is not None:
        data["source_work_id"] = str(source_work_id)
    envelope = build_teaching_cloudevent(
        event_id=event_id,
        event_type=EVENT_TEACHING_ASSIGNMENT_CREATED_V1,
        assignment_id=assignment_id,
        time=created_at,
        context=context,
        tenant_id=tenant_id,
        aggregate_revision=aggregate_revision,
        data=data,
    )
    return _base(
        event_id=event_id,
        tenant_id=tenant_id,
        event_type=EVENT_TEACHING_ASSIGNMENT_CREATED_V1,
        assignment_id=assignment_id,
        aggregate_revision=aggregate_revision,
        envelope=envelope,
        created_at=created_at,
    )


def assignment_due_updated_outbox(
    *,
    tenant_id: UUID,
    assignment_id: UUID,
    lifecycle_state: str,
    due_at: datetime | None,
    aggregate_revision: int,
    context: MutationEventContext,
    created_at: datetime,
) -> OutboxMessage:
    event_id = EventId.generate()
    envelope = build_teaching_cloudevent(
        event_id=event_id,
        event_type=EVENT_TEACHING_ASSIGNMENT_DUE_UPDATED_V1,
        assignment_id=assignment_id,
        time=created_at,
        context=context,
        tenant_id=tenant_id,
        aggregate_revision=aggregate_revision,
        data={
            "assignment_id": str(assignment_id),
            "lifecycle_state": lifecycle_state,
            "due_at": None if due_at is None else due_at.isoformat(),
        },
    )
    return _base(
        event_id=event_id,
        tenant_id=tenant_id,
        event_type=EVENT_TEACHING_ASSIGNMENT_DUE_UPDATED_V1,
        assignment_id=assignment_id,
        aggregate_revision=aggregate_revision,
        envelope=envelope,
        created_at=created_at,
    )


def assignment_closed_outbox(
    *,
    tenant_id: UUID,
    assignment_id: UUID,
    lifecycle_state: str,
    closed_at: datetime,
    aggregate_revision: int,
    context: MutationEventContext,
    created_at: datetime,
) -> OutboxMessage:
    event_id = EventId.generate()
    envelope = build_teaching_cloudevent(
        event_id=event_id,
        event_type=EVENT_TEACHING_ASSIGNMENT_CLOSED_V1,
        assignment_id=assignment_id,
        time=created_at,
        context=context,
        tenant_id=tenant_id,
        aggregate_revision=aggregate_revision,
        data={
            "assignment_id": str(assignment_id),
            "lifecycle_state": lifecycle_state,
            "closed_at": closed_at.isoformat(),
        },
    )
    return _base(
        event_id=event_id,
        tenant_id=tenant_id,
        event_type=EVENT_TEACHING_ASSIGNMENT_CLOSED_V1,
        assignment_id=assignment_id,
        aggregate_revision=aggregate_revision,
        envelope=envelope,
        created_at=created_at,
    )


def assignment_cancelled_outbox(
    *,
    tenant_id: UUID,
    assignment_id: UUID,
    lifecycle_state: str,
    cancelled_at: datetime,
    aggregate_revision: int,
    context: MutationEventContext,
    created_at: datetime,
) -> OutboxMessage:
    event_id = EventId.generate()
    envelope = build_teaching_cloudevent(
        event_id=event_id,
        event_type=EVENT_TEACHING_ASSIGNMENT_CANCELLED_V1,
        assignment_id=assignment_id,
        time=created_at,
        context=context,
        tenant_id=tenant_id,
        aggregate_revision=aggregate_revision,
        data={
            "assignment_id": str(assignment_id),
            "lifecycle_state": lifecycle_state,
            "cancelled_at": cancelled_at.isoformat(),
        },
    )
    return _base(
        event_id=event_id,
        tenant_id=tenant_id,
        event_type=EVENT_TEACHING_ASSIGNMENT_CANCELLED_V1,
        assignment_id=assignment_id,
        aggregate_revision=aggregate_revision,
        envelope=envelope,
        created_at=created_at,
    )
