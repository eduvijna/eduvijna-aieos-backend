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

CLOUDEVENTS_TEACHING_SOURCE = "urn:eduvijna:aieos:teaching"
AGGREGATE_TYPE_TEACHING_ASSIGNMENT = "teaching.assignment"
AGGREGATE_TYPE_TEACHING_EXECUTION = "teaching.execution"

EVENT_TEACHING_ASSIGNMENT_CREATED_V1 = (
    "io.eduvijna.aieos.teaching.assignment.created.v1"
)
EVENT_TEACHING_ASSIGNMENT_DUE_UPDATED_V1 = (
    "io.eduvijna.aieos.teaching.assignment.due_updated.v1"
)
EVENT_TEACHING_ASSIGNMENT_CLOSED_V1 = (
    "io.eduvijna.aieos.teaching.assignment.closed.v1"
)
EVENT_TEACHING_ASSIGNMENT_CANCELLED_V1 = (
    "io.eduvijna.aieos.teaching.assignment.cancelled.v1"
)
EVENT_TEACHING_EXECUTION_STARTED_V1 = (
    "io.eduvijna.aieos.teaching.execution.started.v1"
)
EVENT_TEACHING_EXECUTION_COMPLETED_V1 = (
    "io.eduvijna.aieos.teaching.execution.completed.v1"
)
EVENT_TEACHING_EXECUTION_CANCELLED_V1 = (
    "io.eduvijna.aieos.teaching.execution.cancelled.v1"
)

EMITTED_TEACHING_EVENT_TYPES = frozenset(
    {
        EVENT_TEACHING_ASSIGNMENT_CREATED_V1,
        EVENT_TEACHING_ASSIGNMENT_DUE_UPDATED_V1,
        EVENT_TEACHING_ASSIGNMENT_CLOSED_V1,
        EVENT_TEACHING_ASSIGNMENT_CANCELLED_V1,
        EVENT_TEACHING_EXECUTION_STARTED_V1,
        EVENT_TEACHING_EXECUTION_COMPLETED_V1,
        EVENT_TEACHING_EXECUTION_CANCELLED_V1,
    }
)

EMITTED_CONTENT_EVENT_TYPES = frozenset(
    {
        EVENT_CONTENT_CREATED_V1,
        EVENT_CONTENT_VERSION_CREATED_V1,
        EVENT_CONTENT_SUBMITTED_FOR_REVIEW_V1,
        EVENT_CONTENT_REVIEW_APPROVED_V1,
        EVENT_CONTENT_REVIEW_CHANGES_REQUESTED_V1,
        EVENT_CONTENT_REVIEW_REJECTED_V1,
        EVENT_CONTENT_PUBLISHED_V1,
    }
)

OUTBOX_PENDING = "PENDING"
OUTBOX_CLAIMED = "CLAIMED"
OUTBOX_PUBLISHED = "PUBLISHED"
OUTBOX_QUARANTINED = "QUARANTINED"

ERROR_NATS_UNAVAILABLE = "nats_unavailable"
ERROR_NATS_PUBLISH_REJECTED = "nats_publish_rejected"
ERROR_NATS_STREAM_MISMATCH = "nats_stream_mismatch"
ERROR_RETRY_EXHAUSTED = "retry_exhausted"
ERROR_EVENT_CONTRACT_INVALID = "event_contract_invalid"

# TEST-ONLY stream name used by GCI-I08 disposable harnesses. Not production authority.
TEST_STREAM_NAME = "AIEOS_EVENTS"
TEST_STREAM_SUBJECTS = ("io.eduvijna.aieos.>",)

# ADR-AIEOS-046 production event-plane contract.
PRODUCTION_EVENT_STREAM_NAME = "AIEOS_EVENTS_PROD"
PRODUCTION_EVENT_STREAM_SUBJECTS = ("io.eduvijna.aieos.>",)
PRODUCTION_EVENT_PUBLISH_PREFIXES = (
    "io.eduvijna.aieos.content.",
    "io.eduvijna.aieos.teaching.",
)


def content_subject(content_id: str) -> str:
    return f"content/{content_id}"


def teaching_assignment_subject(assignment_id: str) -> str:
    return f"teaching/assignment/{assignment_id}"


def teaching_execution_subject(execution_id: str) -> str:
    return f"teaching/execution/{execution_id}"
