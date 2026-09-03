"""TeachingExecution lifecycle mutations (complete, cancel)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from aieos.domains.teaching.application.audit import (
    MutationAuditProvenance,
    execution_primary_ref,
    insert_required_teaching_execution_audit,
)
from aieos.domains.teaching.application.errors import (
    AggregateRevisionConflict,
    IdempotencyKeyReused,
    PersistenceInvariantViolation,
    TeachingExecutionForbidden,
    TeachingExecutionNotFound,
    TeachingExecutionNotInProgress,
)
from aieos.domains.teaching.application.models import (
    TeachingExecutionReadModel,
    teaching_execution_read_model,
)
from aieos.domains.teaching.application.ports import TeachingUnitOfWorkFactory
from aieos.domains.teaching.application.school_context import (
    SchoolContextClassAuthority,
)
from aieos.domains.teaching.domain.errors import InvalidTeachingExecutionError
from aieos.domains.teaching.domain.identities import AggregateRevision, ExecutionId
from aieos.platform.events.models import MutationEventContext
from aieos.platform.events.teaching_events import (
    execution_cancelled_outbox,
    execution_completed_outbox,
)
from aieos.platform.idempotency.hashing import fingerprint_material, hash_idempotency_key
from aieos.platform.idempotency.models import (
    TEACHING_EXECUTION_CANCEL_V1,
    TEACHING_EXECUTION_COMPLETE_V1,
    IdempotencyOutcome,
    IdempotencyScope,
)
from aieos.platform.security.audit import SecurityAuditAction


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(UTC)


def _lifecycle_fingerprint(
    execution_id: ExecutionId,
    expected_aggregate_revision: AggregateRevision,
    *,
    action: str,
) -> str:
    return fingerprint_material(
        {
            "execution_id": str(execution_id),
            "expected_aggregate_revision": int(expected_aggregate_revision),
            "action": action,
        }
    )


class CompleteTeachingExecutionService:
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

    def complete(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        *,
        execution_id: ExecutionId,
        expected_aggregate_revision: AggregateRevision,
        idempotency_key: str,
        event_context: MutationEventContext,
        audit_provenance: MutationAuditProvenance,
        now: datetime | None = None,
    ) -> TeachingExecutionReadModel:
        fingerprint = _lifecycle_fingerprint(
            execution_id,
            expected_aggregate_revision,
            action="complete",
        )
        scope = IdempotencyScope(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            operation=TEACHING_EXECUTION_COMPLETE_V1,
            key_sha256=hash_idempotency_key(idempotency_key),
        )
        completed_at = _now(now)
        with self._uow_factory(execution_tenant_id) as uow:
            uow.idempotency.acquire_scope(scope)
            established = uow.idempotency.get(scope)
            if established is not None:
                if established.request_fingerprint_sha256 != fingerprint:
                    raise IdempotencyKeyReused("idempotency key already bound")
                replayed = uow.executions.get(
                    ExecutionId(established.result_content_id)
                )
                if replayed is None:
                    raise PersistenceInvariantViolation(
                        "idempotent complete outcome is not visible"
                    )
                return teaching_execution_read_model(replayed)

            completed = _mutate_in_progress(
                uow,
                class_authority=self._class_authority,
                execution_tenant_id=execution_tenant_id,
                principal_id=principal_id,
                execution_id=execution_id,
                expected_aggregate_revision=expected_aggregate_revision,
                mutate=lambda locked: locked.complete(completed_at=completed_at),
            )
            uow.outbox.insert(
                execution_completed_outbox(
                    tenant_id=execution_tenant_id,
                    execution_id=completed.execution_id.value,
                    lifecycle_state=completed.lifecycle_state.value,
                    completed_at=completed_at,
                    aggregate_revision=int(completed.aggregate_revision),
                    context=event_context,
                    created_at=completed_at,
                )
            )
            insert_required_teaching_execution_audit(
                uow,
                tenant_id=execution_tenant_id,
                action=SecurityAuditAction.TEACHING_EXECUTION_COMPLETE,
                primary_resource_ref=execution_primary_ref(
                    completed.execution_id.value,
                    int(completed.aggregate_revision),
                ),
                resource_revision_before=int(expected_aggregate_revision),
                resource_revision_after=int(completed.aggregate_revision),
                related_resource_refs=(),
                mutation_event_context=event_context,
                audit_provenance=audit_provenance,
                occurred_at=completed_at,
            )
            uow.idempotency.insert(
                IdempotencyOutcome(
                    tenant_id=scope.tenant_id,
                    principal_id=scope.principal_id,
                    operation=scope.operation,
                    key_sha256=scope.key_sha256,
                    request_fingerprint_sha256=fingerprint,
                    result_content_id=completed.execution_id.value,
                    result_version_id=None,
                    result_review_decision_id=None,
                    result_publication_id=None,
                    result_aggregate_revision=int(completed.aggregate_revision),
                    created_at=completed_at,
                    expires_at=completed_at + self._idempotency_retention,
                )
            )
            uow.commit()
        return teaching_execution_read_model(completed)


class CancelTeachingExecutionService:
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

    def cancel(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        *,
        execution_id: ExecutionId,
        expected_aggregate_revision: AggregateRevision,
        idempotency_key: str,
        event_context: MutationEventContext,
        audit_provenance: MutationAuditProvenance,
        now: datetime | None = None,
    ) -> TeachingExecutionReadModel:
        fingerprint = _lifecycle_fingerprint(
            execution_id,
            expected_aggregate_revision,
            action="cancel",
        )
        scope = IdempotencyScope(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            operation=TEACHING_EXECUTION_CANCEL_V1,
            key_sha256=hash_idempotency_key(idempotency_key),
        )
        cancelled_at = _now(now)
        with self._uow_factory(execution_tenant_id) as uow:
            uow.idempotency.acquire_scope(scope)
            established = uow.idempotency.get(scope)
            if established is not None:
                if established.request_fingerprint_sha256 != fingerprint:
                    raise IdempotencyKeyReused("idempotency key already bound")
                replayed = uow.executions.get(
                    ExecutionId(established.result_content_id)
                )
                if replayed is None:
                    raise PersistenceInvariantViolation(
                        "idempotent cancel outcome is not visible"
                    )
                return teaching_execution_read_model(replayed)

            cancelled = _mutate_in_progress(
                uow,
                class_authority=self._class_authority,
                execution_tenant_id=execution_tenant_id,
                principal_id=principal_id,
                execution_id=execution_id,
                expected_aggregate_revision=expected_aggregate_revision,
                mutate=lambda locked: locked.cancel(cancelled_at=cancelled_at),
            )
            uow.outbox.insert(
                execution_cancelled_outbox(
                    tenant_id=execution_tenant_id,
                    execution_id=cancelled.execution_id.value,
                    lifecycle_state=cancelled.lifecycle_state.value,
                    cancelled_at=cancelled_at,
                    aggregate_revision=int(cancelled.aggregate_revision),
                    context=event_context,
                    created_at=cancelled_at,
                )
            )
            insert_required_teaching_execution_audit(
                uow,
                tenant_id=execution_tenant_id,
                action=SecurityAuditAction.TEACHING_EXECUTION_CANCEL,
                primary_resource_ref=execution_primary_ref(
                    cancelled.execution_id.value,
                    int(cancelled.aggregate_revision),
                ),
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
                    result_content_id=cancelled.execution_id.value,
                    result_version_id=None,
                    result_review_decision_id=None,
                    result_publication_id=None,
                    result_aggregate_revision=int(cancelled.aggregate_revision),
                    created_at=cancelled_at,
                    expires_at=cancelled_at + self._idempotency_retention,
                )
            )
            uow.commit()
        return teaching_execution_read_model(cancelled)


def _mutate_in_progress(
    uow,
    *,
    class_authority: SchoolContextClassAuthority,
    execution_tenant_id: UUID,
    principal_id: UUID,
    execution_id: ExecutionId,
    expected_aggregate_revision: AggregateRevision,
    mutate,
):
    locked = uow.executions.get_for_update(execution_id)
    if locked is None:
        raise TeachingExecutionNotFound(
            "TeachingExecution is not visible in the execution tenant"
        )
    if locked.teacher_principal_id != principal_id:
        raise TeachingExecutionForbidden(
            "TeachingExecution is owned by a different teacher"
        )
    class_authority.require_assignable_class_ref(
        execution_tenant_id, principal_id, locked.class_ref
    )
    if int(locked.aggregate_revision) != int(expected_aggregate_revision):
        raise AggregateRevisionConflict(
            "If-Match does not match the current aggregate revision"
        )
    try:
        mutated = mutate(locked)
    except InvalidTeachingExecutionError as exc:
        raise TeachingExecutionNotInProgress(str(exc)) from exc
    applied = uow.executions.update(
        mutated, expected_revision=expected_aggregate_revision
    )
    if not applied:
        raise AggregateRevisionConflict(
            "If-Match does not match the current aggregate revision"
        )
    return mutated
