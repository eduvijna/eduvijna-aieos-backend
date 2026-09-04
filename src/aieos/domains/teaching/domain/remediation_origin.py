"""Immutable TeachingWorkRemediationOrigin provenance.

Teaching-owned 1:1 origin for remediate_class TeachingWork. Not an Improve
aggregate, not Assessment ownership, not Mastery, not Teacher Memory.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from aieos.domains.teaching.domain.class_result_level_snapshot import (
    ClassResultLevelSnapshot,
    parse_class_result_level_snapshot,
)
from aieos.domains.teaching.domain.errors import InvalidRemediationOriginError
from aieos.domains.teaching.domain.identities import (
    AssignmentId,
    ExecutionId,
    WorkId,
    require_foreign_uuid,
)
from aieos.domains.teaching.domain.intent_type import IntentType
from aieos.domains.teaching.domain.work import (
    MAX_GOAL_TEXT_LENGTH,
    MAX_LABEL_LENGTH,
    TeachingWork,
)


def _require_aware(value: datetime, *, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise InvalidRemediationOriginError(f"{label} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidRemediationOriginError(f"{label} must be timezone-aware")
    return value


def _require_nonempty_text(value: str, *, label: str, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidRemediationOriginError(f"{label} must be a non-empty string")
    stripped = value.strip()
    if len(stripped) > max_length:
        raise InvalidRemediationOriginError(
            f"{label} must be at most {max_length} characters"
        )
    return stripped


def _require_nonnegative_revision(value: int, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidRemediationOriginError(f"{label} must be an integer")
    if value < 0:
        raise InvalidRemediationOriginError(f"{label} must be non-negative")
    return value


@dataclass(frozen=True, slots=True)
class TeachingWorkRemediationOrigin:
    """Immutable Assessment-initiation provenance for one remediation Work."""

    work_id: WorkId
    tenant_id: UUID
    source_assessment_id: UUID
    source_assessment_aggregate_revision: int
    source_class_result_level_snapshot: ClassResultLevelSnapshot
    source_class_ref: str
    source_content_id: UUID
    source_content_version_id: UUID
    source_work_id: WorkId | None
    source_execution_id: ExecutionId | None
    source_assignment_id: AssignmentId | None
    initiating_teacher_principal_id: UUID
    created_at: datetime

    def __post_init__(self) -> None:
        set_ = object.__setattr__
        if not isinstance(self.work_id, WorkId):
            raise InvalidRemediationOriginError("work_id must be a WorkId")
        require_foreign_uuid(self.tenant_id, label="tenant_id")
        require_foreign_uuid(
            self.source_assessment_id, label="source_assessment_id"
        )
        require_foreign_uuid(self.source_content_id, label="source_content_id")
        require_foreign_uuid(
            self.source_content_version_id, label="source_content_version_id"
        )
        require_foreign_uuid(
            self.initiating_teacher_principal_id,
            label="initiating_teacher_principal_id",
        )
        set_(
            self,
            "source_assessment_aggregate_revision",
            _require_nonnegative_revision(
                self.source_assessment_aggregate_revision,
                label="source_assessment_aggregate_revision",
            ),
        )
        set_(
            self,
            "source_class_result_level_snapshot",
            parse_class_result_level_snapshot(
                self.source_class_result_level_snapshot
            ),
        )
        set_(
            self,
            "source_class_ref",
            _require_nonempty_text(
                self.source_class_ref,
                label="source_class_ref",
                max_length=MAX_LABEL_LENGTH,
            ),
        )
        if self.source_work_id is not None and not isinstance(
            self.source_work_id, WorkId
        ):
            raise InvalidRemediationOriginError(
                "source_work_id must be a WorkId when present"
            )
        if self.source_execution_id is not None and not isinstance(
            self.source_execution_id, ExecutionId
        ):
            raise InvalidRemediationOriginError(
                "source_execution_id must be an ExecutionId when present"
            )
        if self.source_assignment_id is not None and not isinstance(
            self.source_assignment_id, AssignmentId
        ):
            raise InvalidRemediationOriginError(
                "source_assignment_id must be an AssignmentId when present"
            )
        _require_aware(self.created_at, label="created_at")

    @classmethod
    def create(
        cls,
        *,
        work_id: WorkId,
        tenant_id: UUID,
        source_assessment_id: UUID,
        source_assessment_aggregate_revision: int,
        source_class_result_level_snapshot: ClassResultLevelSnapshot | str,
        source_class_ref: str,
        source_content_id: UUID,
        source_content_version_id: UUID,
        initiating_teacher_principal_id: UUID,
        created_at: datetime,
        source_work_id: WorkId | None = None,
        source_execution_id: ExecutionId | None = None,
        source_assignment_id: AssignmentId | None = None,
    ) -> TeachingWorkRemediationOrigin:
        return cls(
            work_id=work_id,
            tenant_id=tenant_id,
            source_assessment_id=source_assessment_id,
            source_assessment_aggregate_revision=source_assessment_aggregate_revision,
            source_class_result_level_snapshot=source_class_result_level_snapshot,
            source_class_ref=source_class_ref,
            source_content_id=source_content_id,
            source_content_version_id=source_content_version_id,
            source_work_id=source_work_id,
            source_execution_id=source_execution_id,
            source_assignment_id=source_assignment_id,
            initiating_teacher_principal_id=initiating_teacher_principal_id,
            created_at=created_at,
        )


def create_remediation_teaching_work_with_origin(
    *,
    tenant_id: UUID,
    teacher_principal_id: UUID,
    goal_text: str,
    target_date: date,
    locale: str,
    created_at: datetime,
    source_assessment_id: UUID,
    source_assessment_aggregate_revision: int,
    source_class_result_level_snapshot: ClassResultLevelSnapshot | str,
    source_class_ref: str,
    source_content_id: UUID,
    source_content_version_id: UUID,
    source_work_id: WorkId | None = None,
    source_execution_id: ExecutionId | None = None,
    source_assignment_id: AssignmentId | None = None,
    class_label: str | None = None,
    subject: str | None = None,
    topic: str | None = None,
    work_id: WorkId | None = None,
) -> tuple[TeachingWork, TeachingWorkRemediationOrigin]:
    """Atomically construct a coherent remediate_class Work + immutable origin.

    Does not validate live Assessment authority (DEV09-I02). Does not persist.
    """
    work = TeachingWork.create_from_intent(
        tenant_id=tenant_id,
        teacher_principal_id=teacher_principal_id,
        intent_type=IntentType.REMEDIATE_CLASS,
        goal_text=goal_text,
        target_date=target_date,
        locale=locale,
        created_at=created_at,
        class_label=class_label,
        subject=subject,
        topic=topic,
        work_id=work_id,
    )
    if work.intent_type is not IntentType.REMEDIATE_CLASS:
        raise InvalidRemediationOriginError(
            "remediation construction requires intent_type=remediate_class"
        )
    # goal_text length already validated by TeachingWork; keep bound explicit
    if len(work.goal_text) > MAX_GOAL_TEXT_LENGTH:
        raise InvalidRemediationOriginError("goal_text exceeds maximum length")

    origin = TeachingWorkRemediationOrigin.create(
        work_id=work.work_id,
        tenant_id=work.tenant_id,
        source_assessment_id=source_assessment_id,
        source_assessment_aggregate_revision=source_assessment_aggregate_revision,
        source_class_result_level_snapshot=source_class_result_level_snapshot,
        source_class_ref=source_class_ref,
        source_content_id=source_content_id,
        source_content_version_id=source_content_version_id,
        initiating_teacher_principal_id=work.teacher_principal_id,
        created_at=work.created_at,
        source_work_id=source_work_id,
        source_execution_id=source_execution_id,
        source_assignment_id=source_assignment_id,
    )
    if origin.work_id != work.work_id:
        raise InvalidRemediationOriginError("origin.work_id must equal work.work_id")
    if origin.tenant_id != work.tenant_id:
        raise InvalidRemediationOriginError("origin.tenant_id must equal work.tenant_id")
    if origin.initiating_teacher_principal_id != work.teacher_principal_id:
        raise InvalidRemediationOriginError(
            "origin.initiating_teacher_principal_id must equal work.teacher_principal_id"
        )
    if origin.created_at != work.created_at:
        raise InvalidRemediationOriginError(
            "origin.created_at must equal work.created_at"
        )
    return work, origin
