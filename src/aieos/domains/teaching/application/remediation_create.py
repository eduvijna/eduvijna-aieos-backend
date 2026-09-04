"""Create a remediation TeachingWork from locked ClassroomAssessment evidence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from aieos.domains.assessment.application.ports import ASSESSMENT_CLASSROOM_READ
from aieos.domains.teaching.application.audit import (
    MutationAuditProvenance,
    insert_required_teaching_execution_audit,
    work_primary_ref,
)
from aieos.domains.teaching.application.errors import (
    ClassRefNotAssignable,
    IdempotencyKeyReused,
    InvalidTeachingWorkRequest,
    PersistenceInvariantViolation,
    RemediationAssessmentForbidden,
    RemediationAssessmentNotFound,
    RemediationAssessmentNotRecorded,
    RemediationAssessmentRevisionConflict,
    RemediationCompositionConflict,
    SchoolContextContractError,
    SchoolContextUnavailable,
)
from aieos.domains.teaching.application.models import (
    CreateRemediationTeachingWorkCommand,
    TeachingWorkReadModel,
    teaching_work_read_model,
)
from aieos.domains.teaching.application.ports import (
    TEACHING_WORK_CREATE,
    RemediationAssessmentSourceSnapshot,
    TeachingUnitOfWork,
    TeachingUnitOfWorkFactory,
    TeachingWorkAuthorization,
)
from aieos.domains.teaching.application.school_context import (
    AssignableClassRef,
    SchoolContextClassAuthority,
)
from aieos.domains.teaching.domain.errors import TeachingDomainError
from aieos.domains.teaching.domain.identities import AssignmentId, ExecutionId, WorkId
from aieos.domains.teaching.domain.remediation_origin import (
    create_remediation_teaching_work_with_origin,
)
from aieos.platform.events.models import MutationEventContext
from aieos.platform.idempotency.hashing import fingerprint_material, hash_idempotency_key
from aieos.platform.idempotency.models import (
    TEACHING_WORK_FROM_CLASSROOM_ASSESSMENT_CREATE_V1,
    IdempotencyOutcome,
    IdempotencyScope,
)
from aieos.platform.resources import ResourceRef
from aieos.platform.security.audit import SecurityAuditAction


def remediation_create_fingerprint(
    command: CreateRemediationTeachingWorkCommand,
) -> str:
    return fingerprint_material(
        {
            "assessment_id": str(command.assessment_id),
            "expected_assessment_aggregate_revision": (
                command.expected_assessment_aggregate_revision
            ),
            "goal_text": command.goal_text,
            "target_date": command.target_date.isoformat(),
            "locale": command.locale,
            "subject": command.subject,
            "topic": command.topic,
        }
    )


def _require_class(
    authority: SchoolContextClassAuthority,
    tenant_id: UUID,
    principal_id: UUID,
    class_ref: str,
) -> AssignableClassRef:
    try:
        return authority.require_assignable_class_ref(
            tenant_id, principal_id, class_ref
        )
    except (ClassRefNotAssignable, SchoolContextUnavailable):
        raise
    except SchoolContextContractError as exc:
        raise SchoolContextUnavailable(str(exc)) from exc
    except Exception as exc:
        raise SchoolContextUnavailable(
            "School Context is temporarily unavailable"
        ) from exc


def _validate_source_composition(
    uow: TeachingUnitOfWork,
    source: RemediationAssessmentSourceSnapshot,
    principal_id: UUID,
) -> None:
    source_work_id: WorkId | None = None
    if source.work_id is not None:
        source_work_id = WorkId(source.work_id)
        work = uow.works.get(source_work_id)
        if work is None:
            raise RemediationCompositionConflict("source TeachingWork is not visible")
        if work.teacher_principal_id != principal_id:
            raise RemediationAssessmentForbidden(
                "source TeachingWork is owned by a different teacher"
            )

    execution_work_id: WorkId | None = None
    if source.execution_id is not None:
        execution = uow.executions.get(ExecutionId(source.execution_id))
        if execution is None:
            raise RemediationCompositionConflict("source TeachingExecution is not visible")
        if execution.teacher_principal_id != principal_id:
            raise RemediationAssessmentForbidden(
                "source TeachingExecution is owned by a different teacher"
            )
        if execution.class_ref != source.class_ref:
            raise RemediationCompositionConflict(
                "source TeachingExecution ClassRef does not match Assessment"
            )
        bindings = uow.executions.list_bindings(execution.execution_id)
        if not any(
            binding.content_id == source.content_id
            and binding.content_version_id == source.content_version_id
            for binding in bindings
        ):
            raise RemediationCompositionConflict(
                "source TeachingExecution ContentVersion does not match Assessment"
            )
        execution_work_id = execution.work_id

    assignment_work_id: WorkId | None = None
    if source.assignment_id is not None:
        assignment = uow.assignments.get(AssignmentId(source.assignment_id))
        if assignment is None:
            raise RemediationCompositionConflict(
                "source TeachingAssignment is not visible"
            )
        if assignment.teacher_principal_id != principal_id:
            raise RemediationAssessmentForbidden(
                "source TeachingAssignment is owned by a different teacher"
            )
        if (
            assignment.class_ref != source.class_ref
            or assignment.content_id != source.content_id
            or assignment.content_version_id != source.content_version_id
        ):
            raise RemediationCompositionConflict(
                "source TeachingAssignment composition does not match Assessment"
            )
        assignment_work_id = assignment.source_work_id

    known_work_ids = {
        item
        for item in (source_work_id, execution_work_id, assignment_work_id)
        if item is not None
    }
    if len(known_work_ids) > 1:
        raise RemediationCompositionConflict(
            "source Teaching composition work identities disagree"
        )


class CreateRemediationTeachingWorkService:
    def __init__(
        self,
        uow_factory: TeachingUnitOfWorkFactory,
        class_authority: SchoolContextClassAuthority,
        authorization: TeachingWorkAuthorization,
        *,
        idempotency_retention: timedelta,
    ) -> None:
        if idempotency_retention.total_seconds() <= 0:
            raise ValueError("idempotency_retention must be a positive duration")
        self._uow_factory = uow_factory
        self._class_authority = class_authority
        self._authorization = authorization
        self._idempotency_retention = idempotency_retention

    def _authorize(self, tenant_id: UUID, principal_id: UUID) -> None:
        for capability in (TEACHING_WORK_CREATE, ASSESSMENT_CLASSROOM_READ):
            self._authorization.authorize(
                tenant_id=tenant_id,
                principal_id=principal_id,
                capability=capability,
            )

    def create(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        command: CreateRemediationTeachingWorkCommand,
        *,
        idempotency_key: str,
        event_context: MutationEventContext,
        audit_provenance: MutationAuditProvenance,
        now: datetime | None = None,
    ) -> TeachingWorkReadModel:
        self._authorize(execution_tenant_id, principal_id)
        if (
            isinstance(command.expected_assessment_aggregate_revision, bool)
            or not isinstance(command.expected_assessment_aggregate_revision, int)
            or command.expected_assessment_aggregate_revision < 0
        ):
            raise InvalidTeachingWorkRequest(
                "expected_assessment_aggregate_revision must be non-negative"
            )
        fingerprint = remediation_create_fingerprint(command)
        scope = IdempotencyScope(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            operation=TEACHING_WORK_FROM_CLASSROOM_ASSESSMENT_CREATE_V1,
            key_sha256=hash_idempotency_key(idempotency_key),
        )
        with self._uow_factory(execution_tenant_id) as uow:
            uow.idempotency.acquire_scope(scope)
            existing = uow.idempotency.get(scope)
            if existing is not None:
                if existing.request_fingerprint_sha256 != fingerprint:
                    raise IdempotencyKeyReused("idempotency key already bound")
                work_id = WorkId(existing.result_content_id)
                work = uow.works.get(work_id)
                origin = uow.remediation_origins.get(work_id)
                if work is None or origin is None:
                    raise PersistenceInvariantViolation(
                        "idempotent remediation outcome is not visible"
                    )
                if work.teacher_principal_id != principal_id:
                    raise RemediationAssessmentForbidden(
                        "remediation TeachingWork is owned by a different teacher"
                    )
                if (
                    origin.source_assessment_id != command.assessment_id
                    or origin.source_assessment_aggregate_revision
                    != command.expected_assessment_aggregate_revision
                ):
                    raise PersistenceInvariantViolation(
                        "idempotent remediation origin does not match request"
                    )
                _require_class(
                    self._class_authority,
                    execution_tenant_id,
                    principal_id,
                    origin.source_class_ref,
                )
                return teaching_work_read_model(work)

            source = uow.load_recorded_assessment_for_update(command.assessment_id)
            if source is None:
                raise RemediationAssessmentNotFound(
                    "ClassroomAssessment was not found"
                )
            if source.lifecycle_state != "RECORDED":
                raise RemediationAssessmentNotRecorded(
                    "ClassroomAssessment must be RECORDED"
                )
            if source.aggregate_revision != command.expected_assessment_aggregate_revision:
                raise RemediationAssessmentRevisionConflict(
                    "ClassroomAssessment revision does not match"
                )
            if source.teacher_principal_id != principal_id:
                raise RemediationAssessmentForbidden(
                    "ClassroomAssessment is owned by a different teacher"
                )
            class_target = _require_class(
                self._class_authority,
                execution_tenant_id,
                principal_id,
                source.class_ref,
            )
            _validate_source_composition(uow, source, principal_id)
            created_at = now if now is not None else datetime.now(UTC)
            try:
                work, origin = create_remediation_teaching_work_with_origin(
                    tenant_id=execution_tenant_id,
                    teacher_principal_id=principal_id,
                    goal_text=command.goal_text,
                    target_date=command.target_date,
                    locale=command.locale,
                    created_at=created_at,
                    source_assessment_id=source.assessment_id,
                    source_assessment_aggregate_revision=source.aggregate_revision,
                    source_class_result_level_snapshot=source.class_result_level,
                    source_class_ref=source.class_ref,
                    source_content_id=source.content_id,
                    source_content_version_id=source.content_version_id,
                    source_work_id=(
                        None if source.work_id is None else WorkId(source.work_id)
                    ),
                    source_execution_id=(
                        None
                        if source.execution_id is None
                        else ExecutionId(source.execution_id)
                    ),
                    source_assignment_id=(
                        None
                        if source.assignment_id is None
                        else AssignmentId(source.assignment_id)
                    ),
                    class_label=class_target.display_label,
                    subject=command.subject,
                    topic=command.topic,
                )
            except TeachingDomainError as exc:
                raise InvalidTeachingWorkRequest(
                    "remediation TeachingWork request is invalid"
                ) from exc

            uow.works.insert(work)
            uow.remediation_origins.insert(origin)
            related = [
                ResourceRef(
                    "assessment.classroom",
                    source.assessment_id,
                    source.aggregate_revision,
                ),
                ResourceRef("content.version", source.content_version_id, None),
            ]
            if source.work_id is not None:
                related.append(ResourceRef("teaching.work", source.work_id, None))
            if source.execution_id is not None:
                related.append(
                    ResourceRef("teaching.execution", source.execution_id, None)
                )
            if source.assignment_id is not None:
                related.append(
                    ResourceRef("teaching.assignment", source.assignment_id, None)
                )
            insert_required_teaching_execution_audit(
                uow,
                tenant_id=execution_tenant_id,
                action=SecurityAuditAction.TEACHING_WORK_REMEDIATION_CREATE,
                primary_resource_ref=work_primary_ref(work.work_id.value, 0),
                resource_revision_before=None,
                resource_revision_after=0,
                related_resource_refs=tuple(related),
                mutation_event_context=event_context,
                audit_provenance=audit_provenance,
                occurred_at=created_at,
            )
            uow.idempotency.insert(
                IdempotencyOutcome(
                    tenant_id=scope.tenant_id,
                    principal_id=scope.principal_id,
                    operation=scope.operation,
                    key_sha256=scope.key_sha256,
                    request_fingerprint_sha256=fingerprint,
                    result_content_id=work.work_id.value,
                    result_version_id=None,
                    result_review_decision_id=None,
                    result_publication_id=None,
                    result_aggregate_revision=0,
                    created_at=created_at,
                    expires_at=created_at + self._idempotency_retention,
                )
            )
            uow.commit()
        return teaching_work_read_model(work)
