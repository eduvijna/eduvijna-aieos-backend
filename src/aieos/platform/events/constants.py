"""Content CloudEvents type constants and conventions."""

from __future__ import annotations

CLOUDEVENTS_SPECVERSION = "1.0"
CLOUDEVENTS_SOURCE = "urn:eduvijna:aieos:content"
CLOUDEVENTS_DATACONTENTTYPE = "application/json"

AGGREGATE_TYPE_CONTENT = "content"

EVENT_CONTENT_CREATED_V1 = "io.eduvijna.aieos.content.content.created.v1"
EVENT_CONTENT_VERSION_CREATED_V1 = "io.eduvijna.aieos.content.content.version_created.v1"
EVENT_CONTENT_SUBMITTED_FOR_REVIEW_V1 = (
    "io.eduvijna.aieos.content.content.submitted_for_review.v1"
)
EVENT_CONTENT_REVIEW_APPROVED_V1 = "io.eduvijna.aieos.content.content.review_approved.v1"
EVENT_CONTENT_REVIEW_CHANGES_REQUESTED_V1 = (
    "io.eduvijna.aieos.content.content.review_changes_requested.v1"
)
EVENT_CONTENT_REVIEW_REJECTED_V1 = "io.eduvijna.aieos.content.content.review_rejected.v1"
EVENT_CONTENT_PUBLISHED_V1 = "io.eduvijna.aieos.content.content.published.v1"
EVENT_CONTENT_ARCHIVED_V1 = "io.eduvijna.aieos.content.content.archived.v1"

EMITTED_CONTENT_EVENT_TYPES = frozenset(
    {
        EVENT_CONTENT_CREATED_V1,
        EVENT_CONTENT_VERSION_CREATED_V1,
        EVENT_CONTENT_SUBMITTED_FOR_REVIEW_V1,
        EVENT_CONTENT_REVIEW_APPROVED_V1,
        EVENT_CONTENT_REVIEW_CHANGES_REQUESTED_V1,
        EVENT_CONTENT_REVIEW_REJECTED_V1,
    }
)

OUTBOX_PENDING = "PENDING"
OUTBOX_CLAIMED = "CLAIMED"
OUTBOX_PUBLISHED = "PUBLISHED"
OUTBOX_QUARANTINED = "QUARANTINED"

ERROR_NATS_UNAVAILABLE = "nats_unavailable"
ERROR_NATS_PUBLISH_REJECTED = "nats_publish_rejected"
ERROR_RETRY_EXHAUSTED = "retry_exhausted"
ERROR_EVENT_CONTRACT_INVALID = "event_contract_invalid"

TEST_STREAM_NAME = "AIEOS_EVENTS"
TEST_STREAM_SUBJECTS = ("io.eduvijna.aieos.>",)


def content_subject(content_id: str) -> str:
    return f"content/{content_id}"
