"""Idempotency scope and established outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


CONTENT_CREATE_V1 = "content_create.v1"
CONTENT_VERSION_APPEND_V1 = "content_version_append.v1"
CONTENT_REVIEW_SUBMIT_V1 = "content_review_submit.v1"
CONTENT_REVIEW_APPROVE_V1 = "content_review_approve.v1"
CONTENT_REVIEW_REQUEST_CHANGES_V1 = "content_review_request_changes.v1"
CONTENT_REVIEW_REJECT_V1 = "content_review_reject.v1"
CONTENT_PUBLISH_V1 = "content_publish.v1"
TEACHING_WORK_CREATE_V1 = "teaching_work_create.v1"
TEACHING_WORK_FROM_CLASSROOM_ASSESSMENT_CREATE_V1 = (
    "teaching_work_from_classroom_assessment_create.v1"
)
TEACHING_WORK_REFINE_V1 = "teaching_work_refine.v1"
TEACHING_ASSIGNMENT_CREATE_V1 = "teaching_assignment_create.v1"
TEACHING_ASSIGNMENT_DUE_UPDATE_V1 = "teaching_assignment_due_update.v1"
TEACHING_ASSIGNMENT_CLOSE_V1 = "teaching_assignment_close.v1"
TEACHING_ASSIGNMENT_CANCEL_V1 = "teaching_assignment_cancel.v1"
TEACHING_EXECUTION_START_V1 = "teaching_execution_start.v1"
TEACHING_EXECUTION_COMPLETE_V1 = "teaching_execution_complete.v1"
TEACHING_EXECUTION_CANCEL_V1 = "teaching_execution_cancel.v1"
TEACHING_EXECUTION_OBSERVATION_CREATE_V1 = (
    "teaching_execution_observation_create.v1"
)
TEACHING_EXECUTION_OBSERVATION_CORRECT_V1 = (
    "teaching_execution_observation_correct.v1"
)
ASSESSMENT_CLASSROOM_RECORD_V1 = "assessment_classroom_record.v1"
ASSESSMENT_CLASSROOM_CORRECT_V1 = "assessment_classroom_correct.v1"
ASSESSMENT_CLASSROOM_VOID_V1 = "assessment_classroom_void.v1"


@dataclass(frozen=True, slots=True)
class IdempotencyScope:
    tenant_id: UUID
    principal_id: UUID
    operation: str
    key_sha256: str


@dataclass(frozen=True, slots=True)
class IdempotencyOutcome:
    """Established result of one idempotent mutation.

    ``result_content_id`` is the generic primary result identifier of the
    operation. The column name is content-oriented for historical reasons
    (GCI-I05 shipped before non-Content mutations existed); non-Content
    operations such as ``teaching_work_create.v1`` store their own result UUID
    (the Work identity) in it. It is not a foreign key to content.contents.
    """

    tenant_id: UUID
    principal_id: UUID
    operation: str
    key_sha256: str
    request_fingerprint_sha256: str
    result_content_id: UUID
    result_version_id: UUID | None
    result_review_decision_id: UUID | None
    result_publication_id: UUID | None
    result_aggregate_revision: int
    created_at: datetime
    expires_at: datetime
