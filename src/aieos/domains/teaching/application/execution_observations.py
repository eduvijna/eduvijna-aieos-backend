"""TeachingExecutionObservation create/correct application services."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from aieos.domains.teaching.application.audit import (
    MutationAuditProvenance,
    execution_primary_ref,
    insert_required_teaching_execution_audit,
    observation_primary_ref,
)
from aieos.domains.teaching.application.errors import (
    IdempotencyKeyReused,
    InvalidTeachingExecutionRequest,
    ObservationNotFound,
    ObservationRevisionConflict,
    PersistenceInvariantViolation,
    TeachingExecutionForbidden,
    TeachingExecutionNotFound,
    TeachingExecutionNotInProgress,
    UnsupportedObservationKind,
)
from aieos.domains.teaching.application.models import (
    CorrectTeachingExecutionObservationCommand,
    CreateTeachingExecutionObservationCommand,
    TeachingExecutionObservationReadModel,
    teaching_execution_observation_read_model,
)
from aieos.domains.teaching.application.ports import TeachingUnitOfWorkFactory
from aieos.domains.teaching.application.school_context import (
    SchoolContextClassAuthority,
)
from aieos.domains.teaching.domain.errors import (
    InvalidTeachingExecutionObservationError,
)
from aieos.domains.teaching.domain.identities import (
    ExecutionId,
    ObservationId,
    ObservationRevision,
)
from aieos.platform.events.models import MutationEventContext
from aieos.platform.idempotency.hashing import fingerprint_material, hash_idempotency_key
from aieos.platform.idempotency.models import (
    TEACHING_EXECUTION_OBSERVATION_CORRECT_V1,
    TEACHING_EXECUTION_OBSERVATION_CREATE_V1,
    IdempotencyOutcome,
    IdempotencyScope,
)
from aieos.platform.security.audit import SecurityAuditAction


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(UTC)


def _create_fingerprint(
    execution_id: ExecutionId,
    command: CreateTeachingExecutionObservationCommand,
) -> str:
    return fingerprint_material(
        {
            "execution_id": str(execution_id),
            "observation_kind": command.observation_kind,
            "body": command.body,
        }
    )


def _correct_fingerprint(
    observation_id: ObservationId,
    expected_revision: ObservationRevision,
    command: CorrectTeachingExecutionObservationCommand,
) -> str:
    return fingerprint_material(
        {
            "observation_id": str(observation_id),
            "expected_revision": int(expected_revision),
            "body": command.body,
        }
    )


def _map_observation_error(exc: InvalidTeachingExecutionObservationError) -> Exception:
    message = str(exc)
    lowered = message.lower()
    if "observation_kind" in lowered or "frozen" in lowered:
        return UnsupportedObservationKind(message)
    if "immutable" in lowered or "completed" in lowered or "cancelled" in lowered:
        return TeachingExecutionNotInProgress(message)
    return InvalidTeachingExecutionRequest(message)


class CreateTeachingExecutionObservationService:
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
        *,
        execution_id: ExecutionId,
        command: CreateTeachingExecutionObservationCommand,
        idempotency_key: str,
        event_context: MutationEventContext,
        audit_provenance: MutationAuditProvenance,
        now: datetime | None = None,
    ) -> TeachingExecutionObservationReadModel:
        fingerprint = _create_fingerprint(execution_id, command)
        scope = IdempotencyScope(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            operation=TEACHING_EXECUTION_OBSERVATION_CREATE_V1,
            key_sha256=hash_idempotency_key(idempotency_key),
        )
        recorded_at = _now(now)
        with self._uow_factory(execution_tenant_id) as uow:
            uow.idempotency.acquire_scope(scope)
            established = uow.idempotency.get(scope)
            if established is not None:
                if established.request_fingerprint_sha256 != fingerprint:
                    raise IdempotencyKeyReused("idempotency key already bound")
                replayed = uow.executions.get_observation(
                    ObservationId(established.result_content_id)
                )
                if replayed is None:
                    raise PersistenceInvariantViolation(
                        "idempotent observation create outcome is not visible"
                    )
                return teaching_execution_observation_read_model(replayed)

            parent = uow.executions.get_for_update(execution_id)
            if parent is None:
                raise TeachingExecutionNotFound(
                    "TeachingExecution is not visible in the execution tenant"
                )
            if parent.teacher_principal_id != principal_id:
                raise TeachingExecutionForbidden(
                    "TeachingExecution is owned by a different teacher"
                )
            self._class_authority.require_assignable_class_ref(
                execution_tenant_id, principal_id, parent.class_ref
            )
            try:
                observation = parent.create_observation(
                    observation_kind=command.observation_kind,
                    body=command.body,
                    recorded_at=recorded_at,
                )
            except InvalidTeachingExecutionObservationError as exc:
                raise _map_observation_error(exc) from exc

            uow.executions.insert_observation(observation)
            insert_required_teaching_execution_audit(
                uow,
                tenant_id=execution_tenant_id,
                action=SecurityAuditAction.TEACHING_EXECUTION_OBSERVATION_CREATE,
                primary_resource_ref=observation_primary_ref(
                    observation.observation_id.value,
                    int(observation.revision),
                ),
                resource_revision_before=None,
                resource_revision_after=int(observation.revision),
                related_resource_refs=(
                    execution_primary_ref(
                        parent.execution_id.value,
                        int(parent.aggregate_revision),
                    ),
                ),
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
                    result_content_id=observation.observation_id.value,
                    result_version_id=None,
                    result_review_decision_id=None,
                    result_publication_id=None,
                    result_aggregate_revision=int(observation.revision),
                    created_at=recorded_at,
                    expires_at=recorded_at + self._idempotency_retention,
                )
            )
            uow.commit()
        return teaching_execution_observation_read_model(observation)


class CorrectTeachingExecutionObservationService:
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

    def correct(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        *,
        execution_id: ExecutionId,
        observation_id: ObservationId,
        expected_revision: ObservationRevision,
        command: CorrectTeachingExecutionObservationCommand,
        idempotency_key: str,
        event_context: MutationEventContext,
        audit_provenance: MutationAuditProvenance,
        now: datetime | None = None,
    ) -> TeachingExecutionObservationReadModel:
        fingerprint = _correct_fingerprint(
            observation_id, expected_revision, command
        )
        scope = IdempotencyScope(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            operation=TEACHING_EXECUTION_OBSERVATION_CORRECT_V1,
            key_sha256=hash_idempotency_key(idempotency_key),
        )
        updated_at = _now(now)
        with self._uow_factory(execution_tenant_id) as uow:
            uow.idempotency.acquire_scope(scope)
            established = uow.idempotency.get(scope)
            if established is not None:
                if established.request_fingerprint_sha256 != fingerprint:
                    raise IdempotencyKeyReused("idempotency key already bound")
                replayed = uow.executions.get_observation(
                    ObservationId(established.result_content_id)
                )
                if replayed is None:
                    raise PersistenceInvariantViolation(
                        "idempotent observation correct outcome is not visible"
                    )
                return teaching_execution_observation_read_model(replayed)

            parent = uow.executions.get_for_update(execution_id)
            if parent is None:
                raise TeachingExecutionNotFound(
                    "TeachingExecution is not visible in the execution tenant"
                )
            if parent.teacher_principal_id != principal_id:
                raise TeachingExecutionForbidden(
                    "TeachingExecution is owned by a different teacher"
                )
            self._class_authority.require_assignable_class_ref(
                execution_tenant_id, principal_id, parent.class_ref
            )
            current = uow.executions.get_observation(observation_id)
            if current is None or current.execution_id != execution_id:
                raise ObservationNotFound(
                    "TeachingExecutionObservation is not visible for this execution"
                )
            if int(current.revision) != int(expected_revision):
                raise ObservationRevisionConflict(
                    "If-Match does not match the current observation revision"
                )
            try:
                corrected = parent.correct_observation(
                    current, body=command.body, updated_at=updated_at
                )
            except InvalidTeachingExecutionObservationError as exc:
                raise _map_observation_error(exc) from exc

            applied = uow.executions.update_observation(
                corrected, expected_revision=expected_revision
            )
            if not applied:
                raise ObservationRevisionConflict(
                    "If-Match does not match the current observation revision"
                )
            insert_required_teaching_execution_audit(
                uow,
                tenant_id=execution_tenant_id,
                action=SecurityAuditAction.TEACHING_EXECUTION_OBSERVATION_CORRECT,
                primary_resource_ref=observation_primary_ref(
                    corrected.observation_id.value,
                    int(corrected.revision),
                ),
                resource_revision_before=int(expected_revision),
                resource_revision_after=int(corrected.revision),
                related_resource_refs=(
                    execution_primary_ref(
                        parent.execution_id.value,
                        int(parent.aggregate_revision),
                    ),
                ),
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
                    result_content_id=corrected.observation_id.value,
                    result_version_id=None,
                    result_review_decision_id=None,
                    result_publication_id=None,
                    result_aggregate_revision=int(corrected.revision),
                    created_at=updated_at,
                    expires_at=updated_at + self._idempotency_retention,
                )
            )
            uow.commit()
        return teaching_execution_observation_read_model(corrected)
