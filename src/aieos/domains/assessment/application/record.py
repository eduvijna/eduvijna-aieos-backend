"""RECORD ClassroomAssessment application command."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from aieos.domains.assessment.application.audit import (
    MutationAuditProvenance,
    insert_required_assessment_audit,
)
from aieos.domains.assessment.application.composition import (
    CompositionRequest,
    validate_composition,
)
from aieos.domains.assessment.application.errors import (
    ClassRefNotAssignable,
    ClassroomAssessmentForbidden,
    IdempotencyKeyReused,
    InvalidClassroomAssessmentRequest,
    PersistenceInvariantViolation,
    SchoolContextUnavailable,
)
from aieos.domains.assessment.application.models import (
    ClassroomAssessmentReadModel,
    RecordClassroomAssessmentCommand,
    classroom_assessment_read_model,
)
from aieos.domains.assessment.application.ports import (
    ASSESSMENT_CLASSROOM_RECORD,
    AssessmentUnitOfWorkFactory,
    ClassroomAssessmentAuthorization,
)
from aieos.domains.assessment.domain.classroom_assessment import ClassroomAssessment
from aieos.domains.assessment.domain.errors import InvalidClassroomAssessmentError
from aieos.domains.assessment.domain.identities import AssessmentId
from aieos.domains.teaching.application import errors as teaching_errors
from aieos.domains.teaching.application.school_context import (
    SchoolContextClassAuthority,
)
from aieos.platform.events.models import MutationEventContext
from aieos.platform.idempotency.hashing import fingerprint_material, hash_idempotency_key
from aieos.platform.idempotency.models import (
    ASSESSMENT_CLASSROOM_RECORD_V1,
    IdempotencyOutcome,
    IdempotencyScope,
)
from aieos.platform.resources import ResourceRef
from aieos.platform.security.audit import SecurityAuditAction


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(UTC)


def _normalized_note(note: str | None) -> str | None:
    if note is None:
        return None
    stripped = note.strip()
    return stripped or None


def record_fingerprint(command: RecordClassroomAssessmentCommand) -> str:
    return fingerprint_material(
        {
            "class_ref": command.class_ref.strip(),
            "content_id": str(command.content_id),
            "content_version_id": str(command.content_version_id),
            "class_result_level": str(command.class_result_level).strip(),
            "class_result_note": _normalized_note(command.class_result_note),
            "work_id": str(command.work_id) if command.work_id is not None else None,
            "execution_id": (
                str(command.execution_id) if command.execution_id is not None else None
            ),
            "assignment_id": (
                str(command.assignment_id)
                if command.assignment_id is not None
                else None
            ),
        }
    )


def _require_current_class_ref(
    class_authority: SchoolContextClassAuthority,
    execution_tenant_id: UUID,
    principal_id: UUID,
    class_ref: str,
):
    try:
        return class_authority.require_assignable_class_ref(
            execution_tenant_id, principal_id, class_ref
        )
    except teaching_errors.ClassRefNotAssignable as exc:
        raise ClassRefNotAssignable(str(exc)) from exc
    except teaching_errors.SchoolContextUnavailable as exc:
        raise SchoolContextUnavailable(str(exc)) from exc
    except teaching_errors.SchoolContextContractError as exc:
        raise SchoolContextUnavailable(str(exc)) from exc


class RecordClassroomAssessmentService:
    def __init__(
        self,
        uow_factory: AssessmentUnitOfWorkFactory,
        class_authority: SchoolContextClassAuthority,
        authorization: ClassroomAssessmentAuthorization,
        *,
        idempotency_retention: timedelta,
    ) -> None:
        if idempotency_retention.total_seconds() <= 0:
            raise ValueError("idempotency_retention must be a positive duration")
        self._uow_factory = uow_factory
        self._class_authority = class_authority
        self._authorization = authorization
        self._idempotency_retention = idempotency_retention

    def record(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        command: RecordClassroomAssessmentCommand,
        *,
        idempotency_key: str,
        event_context: MutationEventContext,
        audit_provenance: MutationAuditProvenance,
        now: datetime | None = None,
    ) -> ClassroomAssessmentReadModel:
        self._authorization.authorize(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            capability=ASSESSMENT_CLASSROOM_RECORD,
        )
        recorded_at = _now(now)
        fingerprint = record_fingerprint(command)
        scope = IdempotencyScope(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            operation=ASSESSMENT_CLASSROOM_RECORD_V1,
            key_sha256=hash_idempotency_key(idempotency_key),
        )
        with self._uow_factory(execution_tenant_id) as uow:
            uow.idempotency.acquire_scope(scope)
            existing = uow.idempotency.get(scope)
            if existing is not None:
                if existing.request_fingerprint_sha256 != fingerprint:
                    raise IdempotencyKeyReused("idempotency key already bound")
                replayed = uow.classroom_assessments.get(
                    AssessmentId(existing.result_content_id)
                )
                if replayed is None:
                    raise PersistenceInvariantViolation(
                        "idempotent record outcome is not visible"
                    )
                if replayed.teacher_principal_id != principal_id:
                    raise ClassroomAssessmentForbidden(
                        "ClassroomAssessment is owned by a different teacher"
                    )
                # Current ClassRef must still hold on true replay (no composition
                # re-run; no second mutation/audit).
                _require_current_class_ref(
                    self._class_authority,
                    execution_tenant_id,
                    principal_id,
                    command.class_ref,
                )
                return classroom_assessment_read_model(replayed)

            class_target = _require_current_class_ref(
                self._class_authority,
                execution_tenant_id,
                principal_id,
                command.class_ref,
            )
            validate_composition(
                uow,
                teacher_principal_id=principal_id,
                request=CompositionRequest(
                    class_ref=class_target.class_ref,
                    content_id=command.content_id,
                    content_version_id=command.content_version_id,
                    work_id=command.work_id,
                    execution_id=command.execution_id,
                    assignment_id=command.assignment_id,
                ),
            )
            try:
                assessment = ClassroomAssessment.record(
                    tenant_id=execution_tenant_id,
                    teacher_principal_id=principal_id,
                    class_ref=class_target.class_ref,
                    content_id=command.content_id,
                    content_version_id=command.content_version_id,
                    class_result_level=command.class_result_level,
                    recorded_at=recorded_at,
                    class_result_note=command.class_result_note,
                    work_id=command.work_id,
                    execution_id=command.execution_id,
                    assignment_id=command.assignment_id,
                )
            except InvalidClassroomAssessmentError as exc:
                raise InvalidClassroomAssessmentRequest(
                    "classroom assessment record request is invalid"
                ) from exc

            uow.classroom_assessments.insert(assessment)
            related: list[ResourceRef] = [
                ResourceRef(
                    "content.version",
                    command.content_version_id,
                    None,
                )
            ]
            if command.execution_id is not None:
                related.append(
                    ResourceRef("teaching.execution", command.execution_id, None)
                )
            if command.assignment_id is not None:
                related.append(
                    ResourceRef("teaching.assignment", command.assignment_id, None)
                )
            if command.work_id is not None:
                related.append(ResourceRef("teaching.work", command.work_id, None))
            insert_required_assessment_audit(
                uow,
                tenant_id=execution_tenant_id,
                action=SecurityAuditAction.ASSESSMENT_CLASSROOM_RECORD,
                assessment_id=assessment.assessment_id.value,
                resource_revision_before=None,
                resource_revision_after=0,
                related_resource_refs=tuple(related),
                mutation_event_context=event_context,
                audit_provenance=audit_provenance,
                occurred_at=recorded_at,
            )
            uow.idempotency.insert(
                IdempotencyOutcome(
                    tenant_id=scope.tenant_id,
                    principal_id=scope.principal_id,
                    operation=scope.operation,
                    key_sha256=scope.key_sha256,
                    request_fingerprint_sha256=fingerprint,
                    result_content_id=assessment.assessment_id.value,
                    result_version_id=None,
                    result_review_decision_id=None,
                    result_publication_id=None,
                    result_aggregate_revision=int(assessment.aggregate_revision),
                    created_at=recorded_at,
                    expires_at=recorded_at + self._idempotency_retention,
                )
            )
            uow.commit()
        return classroom_assessment_read_model(assessment)
