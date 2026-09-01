"""Create one durable TeachingAssignment with dual CREATE gates."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from aieos.domains.content.domain.identities import ContentId, ContentVersionId
from aieos.domains.teaching.application.audit import (
    MutationAuditProvenance,
    content_version_ref,
    insert_required_teaching_audit,
    source_work_ref,
)
from aieos.domains.teaching.application.errors import (
    IdempotencyKeyReused,
    InvalidTeachingAssignmentRequest,
    PersistenceInvariantViolation,
    SourceWorkForbidden,
    SourceWorkNotFound,
)
from aieos.domains.teaching.application.models import (
    CreateTeachingAssignmentCommand,
    TeachingAssignmentReadModel,
    teaching_assignment_read_model,
)
from aieos.domains.teaching.application.ports import TeachingUnitOfWorkFactory
from aieos.domains.teaching.application.school_context import (
    SchoolContextClassAuthority,
)
from aieos.domains.teaching.domain.assignment import TeachingAssignment
from aieos.domains.teaching.domain.errors import InvalidTeachingAssignmentError
from aieos.domains.teaching.domain.identities import AssignmentId, WorkId
from aieos.platform.events.models import MutationEventContext
from aieos.platform.events.teaching_events import assignment_created_outbox
from aieos.platform.idempotency.hashing import fingerprint_material, hash_idempotency_key
from aieos.platform.idempotency.models import (
    TEACHING_ASSIGNMENT_CREATE_V1,
    IdempotencyOutcome,
    IdempotencyScope,
)
from aieos.platform.security.audit import SecurityAuditAction


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(UTC)


def _create_fingerprint(command: CreateTeachingAssignmentCommand) -> str:
    return fingerprint_material(
        {
            "content_id": str(command.content_id),
            "content_version_id": str(command.content_version_id),
            "class_ref": command.class_ref.strip(),
            "source_work_id": (
                None
                if command.source_work_id is None
                else str(command.source_work_id)
            ),
            "available_from": (
                None
                if command.available_from is None
                else command.available_from.isoformat()
            ),
            "due_at": None if command.due_at is None else command.due_at.isoformat(),
        }
    )


class CreateTeachingAssignmentService:
    def __init__(
        self,
        uow_factory: TeachingUnitOfWorkFactory,
        class_authority: SchoolContextClassAuthority,
        *,
        idempotency_retention: timedelta,
    ) -> None:
        if idempotency_retention.total_seconds() <= 0:
            raise ValueError("idempotency_retention must be a positive duration")
        self._uow_factory = uow_factory
        self._class_authority = class_authority
        self._idempotency_retention = idempotency_retention

    def create(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        command: CreateTeachingAssignmentCommand,
        *,
        idempotency_key: str,
        event_context: MutationEventContext,
        audit_provenance: MutationAuditProvenance,
        now: datetime | None = None,
    ) -> TeachingAssignmentReadModel:
        assigned_at = _now(now)
        fingerprint = _create_fingerprint(command)
        scope = IdempotencyScope(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            operation=TEACHING_ASSIGNMENT_CREATE_V1,
            key_sha256=hash_idempotency_key(idempotency_key),
        )
        content_id = ContentId(command.content_id)
        content_version_id = ContentVersionId(command.content_version_id)
        with self._uow_factory(execution_tenant_id) as uow:
            uow.idempotency.acquire_scope(scope)
            existing = uow.idempotency.get(scope)
            if existing is not None:
                if existing.request_fingerprint_sha256 != fingerprint:
                    raise IdempotencyKeyReused("idempotency key already bound")
                replayed = uow.assignments.get(
                    AssignmentId(existing.result_content_id)
                )
                if replayed is None:
                    raise PersistenceInvariantViolation(
                        "idempotent create outcome is not visible"
                    )
                return teaching_assignment_read_model(replayed)

            class_target = self._class_authority.require_assignable_class_ref(
                execution_tenant_id, principal_id, command.class_ref
            )
            uow.content_eligibility.verify_published_learner_content_with_lock(
                content_id=content_id,
                content_version_id=content_version_id,
            )
            source_work_id: WorkId | None = None
            if command.source_work_id is not None:
                source_work_id = WorkId(command.source_work_id)
                source = uow.works.get(source_work_id)
                if source is None:
                    raise SourceWorkNotFound(
                        "source TeachingWork is not visible in the execution tenant"
                    )
                if source.teacher_principal_id != principal_id:
                    raise SourceWorkForbidden(
                        "source TeachingWork is owned by a different teacher"
                    )

            try:
                assignment = TeachingAssignment.create(
                    tenant_id=execution_tenant_id,
                    teacher_principal_id=principal_id,
                    content_id=command.content_id,
                    content_version_id=command.content_version_id,
                    class_ref=class_target.class_ref,
                    assigned_at=assigned_at,
                    audience_display_label=class_target.display_label,
                    source_work_id=source_work_id,
                    available_from=command.available_from,
                    due_at=command.due_at,
                )
            except InvalidTeachingAssignmentError as exc:
                raise InvalidTeachingAssignmentRequest(
                    "teaching assignment create request is invalid"
                ) from exc
            uow.assignments.insert(assignment)
            uow.outbox.insert(
                assignment_created_outbox(
                    tenant_id=execution_tenant_id,
                    assignment_id=assignment.assignment_id.value,
                    teacher_principal_id=assignment.teacher_principal_id,
                    content_id=assignment.content_id,
                    content_version_id=assignment.content_version_id,
                    class_ref=assignment.class_ref,
                    lifecycle_state=assignment.lifecycle_state.value,
                    available_from=assignment.available_from,
                    due_at=assignment.due_at,
                    source_work_id=(
                        None
                        if source_work_id is None
                        else source_work_id.value
                    ),
                    aggregate_revision=int(assignment.aggregate_revision),
                    context=event_context,
                    created_at=assigned_at,
                )
            )
            related = (content_version_ref(assignment.content_version_id),)
            if source_work_id is not None:
                related = related + (source_work_ref(source_work_id.value),)
            insert_required_teaching_audit(
                uow,
                tenant_id=execution_tenant_id,
                action=SecurityAuditAction.TEACHING_ASSIGNMENT_CREATE,
                assignment_id=assignment.assignment_id.value,
                resource_revision_before=None,
                resource_revision_after=int(assignment.aggregate_revision),
                related_resource_refs=related,
                mutation_event_context=event_context,
                audit_provenance=audit_provenance,
                occurred_at=assigned_at,
            )
            uow.idempotency.insert(
                IdempotencyOutcome(
                    tenant_id=scope.tenant_id,
                    principal_id=scope.principal_id,
                    operation=scope.operation,
                    key_sha256=scope.key_sha256,
                    request_fingerprint_sha256=fingerprint,
                    result_content_id=assignment.assignment_id.value,
                    result_version_id=None,
                    result_review_decision_id=None,
                    result_publication_id=None,
                    result_aggregate_revision=int(assignment.aggregate_revision),
                    created_at=assigned_at,
                    expires_at=assigned_at + self._idempotency_retention,
                )
            )
            uow.commit()
        return teaching_assignment_read_model(assignment)
