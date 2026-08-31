"""TeachingAssignment lifecycle mutations (due update, close, cancel)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from aieos.domains.teaching.application.audit import (
    MutationAuditProvenance,
    insert_required_teaching_audit,
)
from aieos.domains.teaching.application.errors import (
    AggregateRevisionConflict,
    IdempotencyKeyReused,
    InvalidTeachingAssignmentRequest,
    PersistenceInvariantViolation,
    TeachingAssignmentForbidden,
    TeachingAssignmentNotActive,
    TeachingAssignmentNotFound,
)
from aieos.domains.teaching.application.models import (
    TeachingAssignmentReadModel,
    UpdateTeachingAssignmentDueCommand,
    teaching_assignment_read_model,
)
from aieos.domains.teaching.application.ports import TeachingUnitOfWorkFactory
from aieos.domains.teaching.domain.errors import InvalidTeachingAssignmentError
from aieos.domains.teaching.domain.identities import AggregateRevision, AssignmentId
from aieos.platform.events.models import MutationEventContext
from aieos.platform.events.teaching_events import (
    assignment_cancelled_outbox,
    assignment_closed_outbox,
    assignment_due_updated_outbox,
)
from aieos.platform.idempotency.hashing import fingerprint_material, hash_idempotency_key
from aieos.platform.idempotency.models import (
    TEACHING_ASSIGNMENT_CANCEL_V1,
    TEACHING_ASSIGNMENT_CLOSE_V1,
    TEACHING_ASSIGNMENT_DUE_UPDATE_V1,
    IdempotencyOutcome,
    IdempotencyScope,
)
from aieos.platform.security.audit import SecurityAuditAction


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(UTC)


def _due_fingerprint(
    assignment_id: AssignmentId,
    expected_aggregate_revision: AggregateRevision,
    command: UpdateTeachingAssignmentDueCommand,
) -> str:
    return fingerprint_material(
        {
            "assignment_id": str(assignment_id),
            "expected_aggregate_revision": int(expected_aggregate_revision),
            "due_at": (
                None if command.due_at is None else command.due_at.isoformat()
            ),
        }
    )


def _lifecycle_fingerprint(
    assignment_id: AssignmentId,
    expected_aggregate_revision: AggregateRevision,
    *,
    action: str,
) -> str:
    return fingerprint_material(
        {
            "assignment_id": str(assignment_id),
            "expected_aggregate_revision": int(expected_aggregate_revision),
            "action": action,
        }
    )


class UpdateTeachingAssignmentDueService:
    def __init__(
        self,
        uow_factory: TeachingUnitOfWorkFactory,
        *,
        idempotency_retention: timedelta,
    ) -> None:
        if idempotency_retention.total_seconds() <= 0:
            raise ValueError("idempotency_retention must be a positive duration")
        self._uow_factory = uow_factory
        self._idempotency_retention = idempotency_retention

    def update_due(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        *,
        assignment_id: AssignmentId,
        expected_aggregate_revision: AggregateRevision,
        command: UpdateTeachingAssignmentDueCommand,
        idempotency_key: str,
        event_context: MutationEventContext,
        audit_provenance: MutationAuditProvenance,
        now: datetime | None = None,
    ) -> TeachingAssignmentReadModel:
        fingerprint = _due_fingerprint(
            assignment_id, expected_aggregate_revision, command
        )
        scope = IdempotencyScope(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            operation=TEACHING_ASSIGNMENT_DUE_UPDATE_V1,
            key_sha256=hash_idempotency_key(idempotency_key),
        )
        updated_at = _now(now)
        with self._uow_factory(execution_tenant_id) as uow:
            uow.idempotency.acquire_scope(scope)
            established = uow.idempotency.get(scope)
            if established is not None:
                if established.request_fingerprint_sha256 != fingerprint:
                    raise IdempotencyKeyReused("idempotency key already bound")
                replayed = uow.assignments.get(
                    AssignmentId(established.result_content_id)
                )
                if replayed is None:
                    raise PersistenceInvariantViolation(
                        "idempotent due update outcome is not visible"
                    )
                return teaching_assignment_read_model(replayed)

            updated = _mutate_active(
                uow,
                principal_id=principal_id,
                assignment_id=assignment_id,
                expected_aggregate_revision=expected_aggregate_revision,
                mutate=lambda locked: locked.update_due_at(
                    due_at=command.due_at, updated_at=updated_at
                ),
            )
            uow.outbox.insert(
                assignment_due_updated_outbox(
                    tenant_id=execution_tenant_id,
                    assignment_id=updated.assignment_id.value,
                    due_at=updated.due_at,
                    aggregate_revision=int(updated.aggregate_revision),
                    context=event_context,
                    created_at=updated_at,
                )
            )
            insert_required_teaching_audit(
                uow,
                tenant_id=execution_tenant_id,
                action=SecurityAuditAction.TEACHING_ASSIGNMENT_DUE_UPDATE,
                assignment_id=updated.assignment_id.value,
                resource_revision_before=int(expected_aggregate_revision),
                resource_revision_after=int(updated.aggregate_revision),
                related_resource_refs=(),
                mutation_event_context=event_context,
                audit_provenance=audit_provenance,
                occurred_at=updated_at,
            )
            uow.idempotency.insert(
                IdempotencyOutcome(
                    tenant_id=scope.tenant_id,
                    principal_id=scope.principal_id,
                    operation=scope.operation,
                    key_sha256=scope.key_sha256,
                    request_fingerprint_sha256=fingerprint,
                    result_content_id=updated.assignment_id.value,
                    result_version_id=None,
                    result_review_decision_id=None,
                    result_publication_id=None,
                    result_aggregate_revision=int(updated.aggregate_revision),
                    created_at=updated_at,
                    expires_at=updated_at + self._idempotency_retention,
                )
            )
            uow.commit()
        return teaching_assignment_read_model(updated)


class CloseTeachingAssignmentService:
    def __init__(
        self,
        uow_factory: TeachingUnitOfWorkFactory,
        *,
        idempotency_retention: timedelta,
    ) -> None:
        if idempotency_retention.total_seconds() <= 0:
            raise ValueError("idempotency_retention must be a positive duration")
        self._uow_factory = uow_factory
        self._idempotency_retention = idempotency_retention

    def close(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        *,
        assignment_id: AssignmentId,
        expected_aggregate_revision: AggregateRevision,
        idempotency_key: str,
        event_context: MutationEventContext,
        audit_provenance: MutationAuditProvenance,
        now: datetime | None = None,
    ) -> TeachingAssignmentReadModel:
        fingerprint = _lifecycle_fingerprint(
            assignment_id,
            expected_aggregate_revision,
            action="close",
        )
        scope = IdempotencyScope(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            operation=TEACHING_ASSIGNMENT_CLOSE_V1,
            key_sha256=hash_idempotency_key(idempotency_key),
        )
        closed_at = _now(now)
        with self._uow_factory(execution_tenant_id) as uow:
            uow.idempotency.acquire_scope(scope)
            established = uow.idempotency.get(scope)
            if established is not None:
                if established.request_fingerprint_sha256 != fingerprint:
                    raise IdempotencyKeyReused("idempotency key already bound")
                replayed = uow.assignments.get(
                    AssignmentId(established.result_content_id)
                )
                if replayed is None:
                    raise PersistenceInvariantViolation(
                        "idempotent close outcome is not visible"
                    )
                return teaching_assignment_read_model(replayed)

            closed = _mutate_active(
                uow,
                principal_id=principal_id,
                assignment_id=assignment_id,
                expected_aggregate_revision=expected_aggregate_revision,
                mutate=lambda locked: locked.close(closed_at=closed_at),
            )
            uow.outbox.insert(
                assignment_closed_outbox(
                    tenant_id=execution_tenant_id,
                    assignment_id=closed.assignment_id.value,
                    closed_at=closed_at,
                    aggregate_revision=int(closed.aggregate_revision),
                    context=event_context,
                    created_at=closed_at,
                )
            )
            insert_required_teaching_audit(
                uow,
                tenant_id=execution_tenant_id,
                action=SecurityAuditAction.TEACHING_ASSIGNMENT_CLOSE,
                assignment_id=closed.assignment_id.value,
                resource_revision_before=int(expected_aggregate_revision),
                resource_revision_after=int(closed.aggregate_revision),
                related_resource_refs=(),
                mutation_event_context=event_context,
                audit_provenance=audit_provenance,
                occurred_at=closed_at,
            )
            uow.idempotency.insert(
                IdempotencyOutcome(
                    tenant_id=scope.tenant_id,
                    principal_id=scope.principal_id,
                    operation=scope.operation,
                    key_sha256=scope.key_sha256,
                    request_fingerprint_sha256=fingerprint,
                    result_content_id=closed.assignment_id.value,
                    result_version_id=None,
                    result_review_decision_id=None,
                    result_publication_id=None,
                    result_aggregate_revision=int(closed.aggregate_revision),
                    created_at=closed_at,
                    expires_at=closed_at + self._idempotency_retention,
                )
            )
            uow.commit()
        return teaching_assignment_read_model(closed)


class CancelTeachingAssignmentService:
    def __init__(
        self,
        uow_factory: TeachingUnitOfWorkFactory,
        *,
        idempotency_retention: timedelta,
    ) -> None:
        if idempotency_retention.total_seconds() <= 0:
            raise ValueError("idempotency_retention must be a positive duration")
        self._uow_factory = uow_factory
        self._idempotency_retention = idempotency_retention

    def cancel(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        *,
        assignment_id: AssignmentId,
        expected_aggregate_revision: AggregateRevision,
        idempotency_key: str,
        event_context: MutationEventContext,
        audit_provenance: MutationAuditProvenance,
        now: datetime | None = None,
    ) -> TeachingAssignmentReadModel:
        fingerprint = _lifecycle_fingerprint(
            assignment_id,
            expected_aggregate_revision,
            action="cancel",
        )
        scope = IdempotencyScope(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            operation=TEACHING_ASSIGNMENT_CANCEL_V1,
            key_sha256=hash_idempotency_key(idempotency_key),
        )
        cancelled_at = _now(now)
        with self._uow_factory(execution_tenant_id) as uow:
            uow.idempotency.acquire_scope(scope)
            established = uow.idempotency.get(scope)
            if established is not None:
                if established.request_fingerprint_sha256 != fingerprint:
                    raise IdempotencyKeyReused("idempotency key already bound")
                replayed = uow.assignments.get(
                    AssignmentId(established.result_content_id)
                )
                if replayed is None:
                    raise PersistenceInvariantViolation(
                        "idempotent cancel outcome is not visible"
                    )
                return teaching_assignment_read_model(replayed)

            cancelled = _mutate_active(
                uow,
                principal_id=principal_id,
                assignment_id=assignment_id,
                expected_aggregate_revision=expected_aggregate_revision,
                mutate=lambda locked: locked.cancel(cancelled_at=cancelled_at),
            )
            uow.outbox.insert(
                assignment_cancelled_outbox(
                    tenant_id=execution_tenant_id,
                    assignment_id=cancelled.assignment_id.value,
                    cancelled_at=cancelled_at,
                    aggregate_revision=int(cancelled.aggregate_revision),
                    context=event_context,
                    created_at=cancelled_at,
                )
            )
            insert_required_teaching_audit(
                uow,
                tenant_id=execution_tenant_id,
                action=SecurityAuditAction.TEACHING_ASSIGNMENT_CANCEL,
                assignment_id=cancelled.assignment_id.value,
                resource_revision_before=int(expected_aggregate_revision),
                resource_revision_after=int(cancelled.aggregate_revision),
                related_resource_refs=(),
                mutation_event_context=event_context,
                audit_provenance=audit_provenance,
                occurred_at=cancelled_at,
            )
            uow.idempotency.insert(
                IdempotencyOutcome(
                    tenant_id=scope.tenant_id,
                    principal_id=scope.principal_id,
                    operation=scope.operation,
                    key_sha256=scope.key_sha256,
                    request_fingerprint_sha256=fingerprint,
                    result_content_id=cancelled.assignment_id.value,
                    result_version_id=None,
                    result_review_decision_id=None,
                    result_publication_id=None,
                    result_aggregate_revision=int(cancelled.aggregate_revision),
                    created_at=cancelled_at,
                    expires_at=cancelled_at + self._idempotency_retention,
                )
            )
            uow.commit()
        return teaching_assignment_read_model(cancelled)


def _mutate_active(
    uow,
    *,
    principal_id: UUID,
    assignment_id: AssignmentId,
    expected_aggregate_revision: AggregateRevision,
    mutate,
):
    locked = uow.assignments.get_for_update(assignment_id)
    if locked is None:
        raise TeachingAssignmentNotFound(
            "TeachingAssignment is not visible in the execution tenant"
        )
    if locked.teacher_principal_id != principal_id:
        raise TeachingAssignmentForbidden(
            "TeachingAssignment is owned by a different teacher"
        )
    if int(locked.aggregate_revision) != int(expected_aggregate_revision):
        raise AggregateRevisionConflict(
            "If-Match does not match the current aggregate revision"
        )
    try:
        mutated = mutate(locked)
    except InvalidTeachingAssignmentError as exc:
        raise TeachingAssignmentNotActive(str(exc)) from exc
    applied = uow.assignments.update(
        mutated, expected_revision=expected_aggregate_revision
    )
    if not applied:
        raise AggregateRevisionConflict(
            "If-Match does not match the current aggregate revision"
        )
    return mutated
