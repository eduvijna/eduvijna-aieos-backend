"""Create one durable TeachingWork from a Teaching Intent request.

The Teaching Intent is the inbound request. It is not stored as its own
aggregate and there is no teaching_intents table: the intent's only durable
trace is the resulting Work (including its intent_type discriminator).

No AI generation happens here. Creating a Work never produces content.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from aieos.domains.teaching.application.errors import (
    IdempotencyKeyReused,
    InvalidTeachingWorkRequest,
    PersistenceInvariantViolation,
)
from aieos.domains.teaching.application.models import (
    CreateTeachingWorkCommand,
    TeachingWorkReadModel,
    teaching_work_read_model,
)
from aieos.domains.teaching.application.ports import TeachingUnitOfWorkFactory
from aieos.domains.teaching.domain.errors import TeachingDomainError
from aieos.domains.teaching.domain.identities import WorkId
from aieos.domains.teaching.domain.intent_type import IntentType, parse_intent_type
from aieos.domains.teaching.domain.work import TeachingWork
from aieos.platform.idempotency.hashing import fingerprint_material, hash_idempotency_key
from aieos.platform.idempotency.models import (
    TEACHING_WORK_CREATE_V1,
    IdempotencyOutcome,
    IdempotencyScope,
)


def _create_fingerprint(command: CreateTeachingWorkCommand) -> str:
    return fingerprint_material(
        {
            "intent_type": command.intent_type,
            "goal_text": command.goal_text,
            "class_label": command.class_label,
            "subject": command.subject,
            "topic": command.topic,
            "target_date": command.target_date.isoformat(),
            "locale": command.locale,
        }
    )


class CreateTeachingWorkService:
    """Authoritative TeachingWork insert driven by a Teaching Intent request."""

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

    def create(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        command: CreateTeachingWorkCommand,
        *,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> TeachingWorkReadModel:
        # Generic create must never materialize remediate_class without the
        # Assessment-origin command (DEV09-I02). Reject before persistence and
        # before any idempotency outcome is committed.
        try:
            intent_type = parse_intent_type(command.intent_type)
        except TeachingDomainError as exc:
            raise InvalidTeachingWorkRequest(
                "teaching work create request is invalid"
            ) from exc
        if intent_type is IntentType.REMEDIATE_CLASS:
            raise InvalidTeachingWorkRequest(
                "remediate_class requires Assessment-origin create; "
                "generic TeachingWork create is not authorized"
            )

        fingerprint = _create_fingerprint(command)
        scope = IdempotencyScope(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            operation=TEACHING_WORK_CREATE_V1,
            key_sha256=hash_idempotency_key(idempotency_key),
        )
        with self._uow_factory(execution_tenant_id) as uow:
            uow.idempotency.acquire_scope(scope)
            existing = uow.idempotency.get(scope)
            if existing is not None:
                if existing.request_fingerprint_sha256 != fingerprint:
                    raise IdempotencyKeyReused("idempotency key already bound")
                # result_content_id carries the established Work identity for
                # this operation; the platform column name is content-oriented.
                found = uow.works.get(WorkId(existing.result_content_id))
                if found is None:
                    raise PersistenceInvariantViolation(
                        "idempotent create outcome is not visible"
                    )
                return teaching_work_read_model(found)

            created_at = now if now is not None else datetime.now(UTC)
            try:
                work = TeachingWork.create_from_intent(
                    tenant_id=execution_tenant_id,
                    teacher_principal_id=principal_id,
                    intent_type=intent_type,
                    goal_text=command.goal_text,
                    class_label=command.class_label,
                    subject=command.subject,
                    topic=command.topic,
                    target_date=command.target_date,
                    locale=command.locale,
                    created_at=created_at,
                )
            except TeachingDomainError as exc:
                raise InvalidTeachingWorkRequest(
                    "teaching work create request is invalid"
                ) from exc
            uow.works.insert(work)
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
                    result_aggregate_revision=int(work.aggregate_revision),
                    created_at=created_at,
                    expires_at=created_at + self._idempotency_retention,
                )
            )
            uow.commit()
        return teaching_work_read_model(work)
