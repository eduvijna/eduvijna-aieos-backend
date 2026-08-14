"""Helpers that assemble Content outbox rows for a mutation."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from aieos.platform.events.cloudevents import build_content_cloudevent
from aieos.platform.events.constants import (
    AGGREGATE_TYPE_CONTENT,
    EVENT_CONTENT_CREATED_V1,
    EVENT_CONTENT_REVIEW_APPROVED_V1,
    EVENT_CONTENT_REVIEW_CHANGES_REQUESTED_V1,
    EVENT_CONTENT_REVIEW_REJECTED_V1,
    EVENT_CONTENT_SUBMITTED_FOR_REVIEW_V1,
    EVENT_CONTENT_VERSION_CREATED_V1,
    OUTBOX_PENDING,
    content_subject,
)
from aieos.platform.events.identities import EventId
from aieos.platform.events.models import MutationEventContext, OutboxMessage

_REVIEW_EVENT_TYPES = {
    "APPROVE": EVENT_CONTENT_REVIEW_APPROVED_V1,
    "REQUEST_CHANGES": EVENT_CONTENT_REVIEW_CHANGES_REQUESTED_V1,
    "REJECT": EVENT_CONTENT_REVIEW_REJECTED_V1,
}


def _base(
    *,
    event_id: EventId,
    tenant_id: UUID,
    event_type: str,
    content_id: UUID,
    aggregate_revision: int,
    envelope: dict[str, object],
    created_at: datetime,
) -> OutboxMessage:
    return OutboxMessage(
        event_id=event_id,
        tenant_id=tenant_id,
        event_type=event_type,
        subject=content_subject(str(content_id)),
        aggregate_type=AGGREGATE_TYPE_CONTENT,
        aggregate_id=content_id,
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


def content_created_outbox(
    *,
    tenant_id: UUID,
    content_id: UUID,
    content_type: str,
    context: MutationEventContext,
    created_at: datetime,
) -> OutboxMessage:
    event_id = EventId.generate()
    envelope = build_content_cloudevent(
        event_id=event_id,
        event_type=EVENT_CONTENT_CREATED_V1,
        content_id=content_id,
        time=created_at,
        context=context,
        tenant_id=tenant_id,
        aggregate_revision=0,
        data={
            "content_id": str(content_id),
            "content_type": content_type,
            "stewardship_state": "DRAFT",
        },
    )
    return _base(
        event_id=event_id,
        tenant_id=tenant_id,
        event_type=EVENT_CONTENT_CREATED_V1,
        content_id=content_id,
        aggregate_revision=0,
        envelope=envelope,
        created_at=created_at,
    )


def version_created_outbox(
    *,
    tenant_id: UUID,
    content_id: UUID,
    version_id: UUID,
    version_number: int,
    origin: str,
    aggregate_revision: int,
    context: MutationEventContext,
    created_at: datetime,
) -> OutboxMessage:
    event_id = EventId.generate()
    envelope = build_content_cloudevent(
        event_id=event_id,
        event_type=EVENT_CONTENT_VERSION_CREATED_V1,
        content_id=content_id,
        time=created_at,
        context=context,
        tenant_id=tenant_id,
        aggregate_revision=aggregate_revision,
        data={
            "content_id": str(content_id),
            "version_id": str(version_id),
            "version_number": version_number,
            "origin": origin,
            "stewardship_state": "GENERATED",
        },
    )
    return _base(
        event_id=event_id,
        tenant_id=tenant_id,
        event_type=EVENT_CONTENT_VERSION_CREATED_V1,
        content_id=content_id,
        aggregate_revision=aggregate_revision,
        envelope=envelope,
        created_at=created_at,
    )


def submitted_for_review_outbox(
    *,
    tenant_id: UUID,
    content_id: UUID,
    version_id: UUID,
    aggregate_revision: int,
    context: MutationEventContext,
    created_at: datetime,
) -> OutboxMessage:
    event_id = EventId.generate()
    envelope = build_content_cloudevent(
        event_id=event_id,
        event_type=EVENT_CONTENT_SUBMITTED_FOR_REVIEW_V1,
        content_id=content_id,
        time=created_at,
        context=context,
        tenant_id=tenant_id,
        aggregate_revision=aggregate_revision,
        data={
            "content_id": str(content_id),
            "version_id": str(version_id),
            "stewardship_state": "IN_REVIEW",
        },
    )
    return _base(
        event_id=event_id,
        tenant_id=tenant_id,
        event_type=EVENT_CONTENT_SUBMITTED_FOR_REVIEW_V1,
        content_id=content_id,
        aggregate_revision=aggregate_revision,
        envelope=envelope,
        created_at=created_at,
    )


def review_decision_outbox(
    *,
    tenant_id: UUID,
    content_id: UUID,
    version_id: UUID,
    review_decision_id: UUID,
    decision: str,
    stewardship_state: str,
    aggregate_revision: int,
    context: MutationEventContext,
    created_at: datetime,
) -> OutboxMessage:
    event_type = _REVIEW_EVENT_TYPES[decision]
    event_id = EventId.generate()
    envelope = build_content_cloudevent(
        event_id=event_id,
        event_type=event_type,
        content_id=content_id,
        time=created_at,
        context=context,
        tenant_id=tenant_id,
        aggregate_revision=aggregate_revision,
        data={
            "content_id": str(content_id),
            "version_id": str(version_id),
            "review_decision_id": str(review_decision_id),
            "decision": decision,
            "stewardship_state": stewardship_state,
        },
    )
    return _base(
        event_id=event_id,
        tenant_id=tenant_id,
        event_type=event_type,
        content_id=content_id,
        aggregate_revision=aggregate_revision,
        envelope=envelope,
        created_at=created_at,
    )
