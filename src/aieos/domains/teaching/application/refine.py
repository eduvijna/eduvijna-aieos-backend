"""Refine an existing durable TeachingWork.

PATCH semantics over teacher-editable preparation fields only. Ownership and
If-Match preconditions are enforced before any write. No AI generation.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from aieos.domains.teaching.application.errors import (
    AggregateRevisionConflict,
    IdempotencyKeyReused,
    InvalidTeachingWorkRequest,
    PersistenceInvariantViolation,
    TeachingWorkForbidden,
    TeachingWorkNotFound,
)
from aieos.domains.teaching.application.models import (
    RefineTeachingWorkCommand,
    TeachingWorkReadModel,
    teaching_work_read_model,
)
from aieos.domains.teaching.application.ports import TeachingUnitOfWorkFactory
from aieos.domains.teaching.domain.errors import TeachingDomainError
from aieos.domains.teaching.domain.identities import AggregateRevision, WorkId
from aieos.domains.teaching.domain.work import UnsetType
from aieos.platform.idempotency.hashing import fingerprint_material, hash_idempotency_key
from aieos.platform.idempotency.models import (
    TEACHING_WORK_REFINE_V1,
    IdempotencyOutcome,
    IdempotencyScope,
)

_OMITTED = "\u0000omitted"


def _fingerprint_value(value: object) -> object:
    if isinstance(value, UnsetType):
        return _OMITTED
    if isinstance(value, date):
        return value.isoformat()
    return value


def _refine_fingerprint(
    work_id: WorkId,
    expected_aggregate_revision: AggregateRevision,
    command: RefineTeachingWorkCommand,
) -> str:
    return fingerprint_material(
        {
            "work_id": str(work_id),
            "expected_aggregate_revision": int(expected_aggregate_revision),
            "goal_text": _fingerprint_value(command.goal_text),
            "class_label": _fingerprint_value(command.class_label),
            "subject": _fingerprint_value(command.subject),
            "topic": _fingerprint_value(command.topic),
            "target_date": _fingerprint_value(command.target_date),
            "locale": _fingerprint_value(command.locale),
        }
    )


class RefineTeachingWorkService:
    """Owner-only partial update of a durable TeachingWork."""

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

    def refine(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        *,
        work_id: WorkId,
        expected_aggregate_revision: AggregateRevision,
        command: RefineTeachingWorkCommand,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> TeachingWorkReadModel:
        if not command.has_changes():
            raise InvalidTeachingWorkRequest(
                "refine request must change at least one field"
            )
        fingerprint = _refine_fingerprint(
            work_id, expected_aggregate_revision, command
        )
        scope = IdempotencyScope(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            operation=TEACHING_WORK_REFINE_V1,
            key_sha256=hash_idempotency_key(idempotency_key),
        )
        with self._uow_factory(execution_tenant_id) as uow:
            uow.idempotency.acquire_scope(scope)
            established = uow.idempotency.get(scope)
            if established is not None:
                if established.request_fingerprint_sha256 != fingerprint:
                    raise IdempotencyKeyReused("idempotency key already bound")
                # Replay returns the durable Work established by the original
                # refine. result_content_id holds the Work identity.
                replayed = uow.works.get(WorkId(established.result_content_id))
                if replayed is None:
                    raise PersistenceInvariantViolation(
                        "idempotent refine outcome is not visible"
                    )
                return teaching_work_read_model(replayed)

            locked = uow.works.get_for_update(work_id)
            if locked is None:
                raise TeachingWorkNotFound(
                    "TeachingWork is not visible in the execution tenant"
                )
            if locked.teacher_principal_id != principal_id:
                raise TeachingWorkForbidden(
                    "TeachingWork is owned by a different teacher"
                )
            if int(locked.aggregate_revision) != int(expected_aggregate_revision):
                raise AggregateRevisionConflict(
                    "If-Match does not match the current aggregate revision"
                )
            updated_at = now if now is not None else datetime.now(UTC)
            try:
                refined = locked.refine(
                    updated_at=updated_at,
                    goal_text=command.goal_text,
                    class_label=command.class_label,
                    subject=command.subject,
                    topic=command.topic,
                    target_date=command.target_date,
                    locale=command.locale,
                )
            except TeachingDomainError as exc:
                raise InvalidTeachingWorkRequest(
                    "teaching work refine request is invalid"
                ) from exc
            applied = uow.works.update(
                refined, expected_revision=expected_aggregate_revision
            )
            if not applied:
                raise AggregateRevisionConflict(
                    "If-Match does not match the current aggregate revision"
                )
            uow.idempotency.insert(
                IdempotencyOutcome(
                    tenant_id=scope.tenant_id,
                    principal_id=scope.principal_id,
                    operation=scope.operation,
                    key_sha256=scope.key_sha256,
                    request_fingerprint_sha256=fingerprint,
                    result_content_id=refined.work_id.value,
                    result_version_id=None,
                    result_review_decision_id=None,
                    result_publication_id=None,
                    result_aggregate_revision=int(refined.aggregate_revision),
                    created_at=updated_at,
                    expires_at=updated_at + self._idempotency_retention,
                )
            )
            uow.commit()
        return teaching_work_read_model(refined)
