"""Framework-neutral API idempotency contracts. Not Generic Content authority."""

from aieos.platform.idempotency.models import (
    CONTENT_CREATE_V1,
    CONTENT_REVIEW_APPROVE_V1,
    CONTENT_REVIEW_REJECT_V1,
    CONTENT_REVIEW_REQUEST_CHANGES_V1,
    CONTENT_REVIEW_SUBMIT_V1,
    CONTENT_VERSION_APPEND_V1,
    TEACHING_WORK_FROM_CLASSROOM_ASSESSMENT_CREATE_V1,
    IdempotencyOutcome,
    IdempotencyScope,
)
from aieos.platform.idempotency.ports import IdempotencyRepository

__all__ = [
    "CONTENT_CREATE_V1",
    "CONTENT_REVIEW_APPROVE_V1",
    "CONTENT_REVIEW_REJECT_V1",
    "CONTENT_REVIEW_REQUEST_CHANGES_V1",
    "CONTENT_REVIEW_SUBMIT_V1",
    "CONTENT_VERSION_APPEND_V1",
    "TEACHING_WORK_FROM_CLASSROOM_ASSESSMENT_CREATE_V1",
    "IdempotencyOutcome",
    "IdempotencyRepository",
    "IdempotencyScope",
]
