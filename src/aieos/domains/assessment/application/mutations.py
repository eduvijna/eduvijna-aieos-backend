"""CORRECT and VOID ClassroomAssessment application commands."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from aieos.domains.assessment.application.audit import (
    MutationAuditProvenance,
    insert_required_assessment_audit,
)
from aieos.domains.assessment.application.errors import (
    AggregateRevisionConflict,
    ClassRefNotAssignable,
    ClassroomAssessmentForbidden,
    ClassroomAssessmentNotFound,
    ClassroomAssessmentNotRecorded,
    IdempotencyKeyReused,
    InvalidClassroomAssessmentRequest,
    PersistenceInvariantViolation,
    SchoolContextUnavailable,
)
from aieos.domains.assessment.application.models import (
    ClassroomAssessmentReadModel,
    CorrectClassroomAssessmentCommand,
    classroom_assessment_read_model,
)
from aieos.domains.assessment.application.ports import (
    ASSESSMENT_CLASSROOM_CORRECT,
    ASSESSMENT_CLASSROOM_VOID,
    AssessmentUnitOfWorkFactory,
    ClassroomAssessmentAuthorization,
)
from aieos.domains.assessment.domain.errors import InvalidClassroomAssessmentError
from aieos.domains.assessment.domain.identities import AggregateRevision, AssessmentId
from aieos.domains.assessment.domain.lifecycle import AssessmentLifecycleState
from aieos.domains.teaching.application import errors as teaching_errors
from aieos.domains.teaching.application.school_context import (
    SchoolContextClassAuthority,
)
from aieos.platform.events.models import MutationEventContext
from aieos.platform.idempotency.hashing import fingerprint_material, hash_idempotency_key
from aieos.platform.idempotency.models import (
    ASSESSMENT_CLASSROOM_CORRECT_V1,
    ASSESSMENT_CLASSROOM_VOID_V1,
    IdempotencyOutcome,
    IdempotencyScope,
)
from aieos.platform.security.audit import SecurityAuditAction


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(UTC)


def _normalized_note(note: str | None) -> str | None:
    if note is None:
        return None
    stripped = note.strip()
    return stripped or None


def correct_fingerprint(
    assessment_id: AssessmentId,
    expected_revision: AggregateRevision,
    command: CorrectClassroomAssessmentCommand,
) -> str:
    return fingerprint_material(
        {
            "assessment_id": str(assessment_id.value),
            "expected_revision": int(expected_revision),
            "class_result_level": str(command.class_result_level).strip(),
            "class_result_note": _normalized_note(command.class_result_note),
        }
    )


def void_fingerprint(
    assessment_id: AssessmentId,
    expected_revision: AggregateRevision,
) -> str:
    return fingerprint_material(
        {
            "assessment_id": str(assessment_id.value),
            "expected_revision": int(expected_revision),
        }
    )


def _require_current_class_ref(
    class_authority: SchoolContextClassAuthority,
    execution_tenant_id: UUID,
    principal_id: UUID,
    class_ref: str,
) -> None:
    try:
        class_authority.require_assignable_class_ref(
            execution_tenant_id, principal_id, class_ref
        )
    except teaching_errors.ClassRefNotAssignable as exc:
        raise ClassRefNotAssignable(str(exc)) from exc
    except teaching_errors.SchoolContextUnavailable as exc:
        raise SchoolContextUnavailable(str(exc)) from exc
    except teaching_errors.SchoolContextContractError as exc:
        raise SchoolContextUnavailable(str(exc)) from exc


def _load_owned(
    uow,
    *,
    assessment_id: AssessmentId,
    principal_id: UUID,
    for_update: bool,
):
    locked = (
        uow.classroom_assessments.get_for_update(assessment_id)
        if for_update
        else uow.classroom_assessments.get(assessment_id)
    )
    if locked is None:
        raise ClassroomAssessmentNotFound(
            "ClassroomAssessment is not visible in the execution tenant"
        )
    if locked.teacher_principal_id != principal_id:
        raise ClassroomAssessmentForbidden(
            "ClassroomAssessment is owned by a different teacher"
        )
    return locked


def _replay_owned_matching_path(
    uow,
    *,
    path_assessment_id: AssessmentId,
    result_content_id: UUID,
    principal_id: UUID,
    invariant_message: str,
):
    if result_content_id != path_assessment_id.value:
        raise PersistenceInvariantViolation(
            "idempotent outcome assessment_id does not match command path"
        )
    replayed = uow.classroom_assessments.get(AssessmentId(result_content_id))
    if replayed is None:
        raise PersistenceInvariantViolation(invariant_message)
    if replayed.teacher_principal_id != principal_id:
        raise ClassroomAssessmentForbidden(
            "ClassroomAssessment is owned by a different teacher"
        )
    return replayed


class CorrectClassroomAssessmentService:
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

    def correct(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        *,
        assessment_id: AssessmentId,
        expected_aggregate_revision: AggregateRevision,
        command: CorrectClassroomAssessmentCommand,
        idempotency_key: str,
        event_context: MutationEventContext,
        audit_provenance: MutationAuditProvenance,
        now: datetime | None = None,
    ) -> ClassroomAssessmentReadModel:
        self._authorization.authorize(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            capability=ASSESSMENT_CLASSROOM_CORRECT,
        )
        fingerprint = correct_fingerprint(
            assessment_id, expected_aggregate_revision, command
        )
        scope = IdempotencyScope(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            operation=ASSESSMENT_CLASSROOM_CORRECT_V1,
            key_sha256=hash_idempotency_key(idempotency_key),
        )
        corrected_at = _now(now)
        with self._uow_factory(execution_tenant_id) as uow:
            uow.idempotency.acquire_scope(scope)
            established = uow.idempotency.get(scope)
            if established is not None:
                if established.request_fingerprint_sha256 != fingerprint:
                    raise IdempotencyKeyReused("idempotency key already bound")
                replayed = _replay_owned_matching_path(
                    uow,
                    path_assessment_id=assessment_id,
                    result_content_id=established.result_content_id,
                    principal_id=principal_id,
                    invariant_message="idempotent correct outcome is not visible",
                )
                _require_current_class_ref(
                    self._class_authority,
                    execution_tenant_id,
                    principal_id,
                    replayed.class_ref,
                )
                return classroom_assessment_read_model(replayed)

            locked = _load_owned(
                uow,
                assessment_id=assessment_id,
                principal_id=principal_id,
                for_update=True,
            )
            if locked.lifecycle_state is not AssessmentLifecycleState.RECORDED:
                raise ClassroomAssessmentNotRecorded(
                    "only a RECORDED ClassroomAssessment can be corrected"
                )
            _require_current_class_ref(
                self._class_authority,
                execution_tenant_id,
                principal_id,
                locked.class_ref,
            )
            if int(locked.aggregate_revision) != int(expected_aggregate_revision):
                raise AggregateRevisionConflict(
                    "ClassroomAssessment aggregate revision conflict"
                )
            try:
                corrected = locked.correct(
                    class_result_level=command.class_result_level,
                    class_result_note=command.class_result_note,
                    updated_at=corrected_at,
                )
            except InvalidClassroomAssessmentError as exc:
                raise InvalidClassroomAssessmentRequest(
                    "classroom assessment correct request is invalid"
                ) from exc
            if not uow.classroom_assessments.update(
                corrected, expected_revision=expected_aggregate_revision
            ):
                raise AggregateRevisionConflict(
                    "ClassroomAssessment aggregate revision conflict"
                )
            insert_required_assessment_audit(
                uow,
                tenant_id=execution_tenant_id,
                action=SecurityAuditAction.ASSESSMENT_CLASSROOM_CORRECT,
                assessment_id=corrected.assessment_id.value,
                resource_revision_before=int(expected_aggregate_revision),
                resource_revision_after=int(corrected.aggregate_revision),
                related_resource_refs=(),
                mutation_event_context=event_context,
                audit_provenance=audit_provenance,
                occurred_at=corrected_at,
            )
            uow.idempotency.insert(
                IdempotencyOutcome(
                    tenant_id=scope.tenant_id,
                    principal_id=scope.principal_id,
                    operation=scope.operation,
                    key_sha256=scope.key_sha256,
                    request_fingerprint_sha256=fingerprint,
                    result_content_id=corrected.assessment_id.value,
                    result_version_id=None,
                    result_review_decision_id=None,
                    result_publication_id=None,
                    result_aggregate_revision=int(corrected.aggregate_revision),
                    created_at=corrected_at,
                    expires_at=corrected_at + self._idempotency_retention,
                )
            )
            uow.commit()
        return classroom_assessment_read_model(corrected)


class VoidClassroomAssessmentService:
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

    def void(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        *,
        assessment_id: AssessmentId,
        expected_aggregate_revision: AggregateRevision,
        idempotency_key: str,
        event_context: MutationEventContext,
        audit_provenance: MutationAuditProvenance,
        now: datetime | None = None,
    ) -> ClassroomAssessmentReadModel:
        self._authorization.authorize(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            capability=ASSESSMENT_CLASSROOM_VOID,
        )
        fingerprint = void_fingerprint(assessment_id, expected_aggregate_revision)
        scope = IdempotencyScope(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            operation=ASSESSMENT_CLASSROOM_VOID_V1,
            key_sha256=hash_idempotency_key(idempotency_key),
        )
        voided_at = _now(now)
        with self._uow_factory(execution_tenant_id) as uow:
            uow.idempotency.acquire_scope(scope)
            established = uow.idempotency.get(scope)
            if established is not None:
                if established.request_fingerprint_sha256 != fingerprint:
                    raise IdempotencyKeyReused("idempotency key already bound")
                replayed = _replay_owned_matching_path(
                    uow,
                    path_assessment_id=assessment_id,
                    result_content_id=established.result_content_id,
                    principal_id=principal_id,
                    invariant_message="idempotent void outcome is not visible",
                )
                _require_current_class_ref(
                    self._class_authority,
                    execution_tenant_id,
                    principal_id,
                    replayed.class_ref,
                )
                return classroom_assessment_read_model(replayed)

            locked = _load_owned(
                uow,
                assessment_id=assessment_id,
                principal_id=principal_id,
                for_update=True,
            )
            if locked.lifecycle_state is not AssessmentLifecycleState.RECORDED:
                raise ClassroomAssessmentNotRecorded(
                    "only a RECORDED ClassroomAssessment can be voided"
                )
            _require_current_class_ref(
                self._class_authority,
                execution_tenant_id,
                principal_id,
                locked.class_ref,
            )
            if int(locked.aggregate_revision) != int(expected_aggregate_revision):
                raise AggregateRevisionConflict(
                    "ClassroomAssessment aggregate revision conflict"
                )
            try:
                voided = locked.void(voided_at=voided_at)
            except InvalidClassroomAssessmentError as exc:
                raise InvalidClassroomAssessmentRequest(
                    "classroom assessment void request is invalid"
                ) from exc
            if not uow.classroom_assessments.update(
                voided, expected_revision=expected_aggregate_revision
            ):
                raise AggregateRevisionConflict(
                    "ClassroomAssessment aggregate revision conflict"
                )
            insert_required_assessment_audit(
                uow,
                tenant_id=execution_tenant_id,
                action=SecurityAuditAction.ASSESSMENT_CLASSROOM_VOID,
                assessment_id=voided.assessment_id.value,
                resource_revision_before=int(expected_aggregate_revision),
                resource_revision_after=int(voided.aggregate_revision),
                related_resource_refs=(),
                mutation_event_context=event_context,
                audit_provenance=audit_provenance,
                occurred_at=voided_at,
            )
            uow.idempotency.insert(
                IdempotencyOutcome(
                    tenant_id=scope.tenant_id,
                    principal_id=scope.principal_id,
                    operation=scope.operation,
                    key_sha256=scope.key_sha256,
                    request_fingerprint_sha256=fingerprint,
                    result_content_id=voided.assessment_id.value,
                    result_version_id=None,
                    result_review_decision_id=None,
                    result_publication_id=None,
                    result_aggregate_revision=int(voided.aggregate_revision),
                    created_at=voided_at,
                    expires_at=voided_at + self._idempotency_retention,
                )
            )
            uow.commit()
        return classroom_assessment_read_model(voided)
