"""Framework-neutral Content event and outbox contracts."""

from aieos.platform.events.constants import (
    EVENT_CONTENT_ARCHIVED_V1,
    EVENT_CONTENT_CREATED_V1,
    EVENT_CONTENT_PUBLISHED_V1,
    EVENT_CONTENT_REVIEW_APPROVED_V1,
    EVENT_CONTENT_REVIEW_CHANGES_REQUESTED_V1,
    EVENT_CONTENT_REVIEW_REJECTED_V1,
    EVENT_CONTENT_SUBMITTED_FOR_REVIEW_V1,
    EVENT_CONTENT_VERSION_CREATED_V1,
)
from aieos.platform.events.identities import EventId
from aieos.platform.events.models import MutationEventContext, OutboxMessage
from aieos.platform.events.ports import OutboxRepository

__all__ = [
    "EVENT_CONTENT_ARCHIVED_V1",
    "EVENT_CONTENT_CREATED_V1",
    "EVENT_CONTENT_PUBLISHED_V1",
    "EVENT_CONTENT_REVIEW_APPROVED_V1",
    "EVENT_CONTENT_REVIEW_CHANGES_REQUESTED_V1",
    "EVENT_CONTENT_REVIEW_REJECTED_V1",
    "EVENT_CONTENT_SUBMITTED_FOR_REVIEW_V1",
    "EVENT_CONTENT_VERSION_CREATED_V1",
    "EventId",
    "MutationEventContext",
    "OutboxMessage",
    "OutboxRepository",
]
