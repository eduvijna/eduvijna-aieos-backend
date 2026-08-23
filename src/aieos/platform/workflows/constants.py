"""Stable Content review workflow identity constants."""

from __future__ import annotations

CONTENT_REVIEW_WORKFLOW_TYPE = "ContentReviewWorkflowV1"
CONTENT_REVIEW_WORKFLOW_MAJOR = 1
CONTENT_REVIEW_TASK_QUEUE = "aieos.content.review"

INTENT_PENDING = "PENDING"
INTENT_CLAIMED = "CLAIMED"
INTENT_DELIVERED = "DELIVERED"
INTENT_QUARANTINED = "QUARANTINED"

PROCESS_WAITING = "WAITING_FOR_REVIEW_DECISION"
PROCESS_DECISION_OBSERVED = "DECISION_OBSERVED"

SIGNAL_REVIEW_DECISION_RECORDED = "review_decision_recorded"
QUERY_STATE = "state"

ERROR_TEMPORAL_UNAVAILABLE = "temporal_unavailable"
ERROR_WORKFLOW_IDENTITY_CONFLICT = "workflow_identity_conflict"
ERROR_WORKFLOW_NOT_FOUND = "workflow_not_found"
ERROR_WORKFLOW_TERMINAL_MISMATCH = "workflow_terminal_mismatch"
ERROR_RETRY_EXHAUSTED = "retry_exhausted"
ERROR_TASK_QUEUE_MISMATCH = "task_queue_mismatch"


def content_review_business_key(*, content_id: str, version_id: str) -> str:
    return f"content-review:v1:{content_id}:{version_id}"


def content_review_temporal_workflow_id(workflow_instance_id: str) -> str:
    return f"aieos:content-review:v1:{workflow_instance_id}"


def review_decision_command_business_key(review_decision_id: str) -> str:
    return f"review-decision:{review_decision_id}"
