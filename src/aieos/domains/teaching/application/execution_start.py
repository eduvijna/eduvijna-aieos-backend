"""Start one durable TeachingExecution with ClassRef and content-binding gates."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from aieos.domains.content.domain.identities import ContentId, ContentVersionId
from aieos.domains.teaching.application.artifacts import ListTeachingWorkArtifactsService
from aieos.domains.teaching.application.audit import (
    MutationAuditProvenance,
    content_version_ref,
    execution_primary_ref,
    insert_required_teaching_execution_audit,
    source_work_ref,
)
from aieos.domains.teaching.application.errors import (
    ExecutionContentBindingRejected,
    GenerationServiceUnavailable,
    IdempotencyKeyReused,
    InvalidTeachingExecutionRequest,
    PersistenceInvariantViolation,
    TeachingWorkForbidden,
    TeachingWorkNotFound,
)
from aieos.domains.teaching.application.models import (
    StartTeachingExecutionCommand,
    TeachingExecutionReadModel,
    teaching_execution_read_model,
)
from aieos.domains.teaching.application.ports import TeachingUnitOfWorkFactory
from aieos.domains.teaching.application.school_context import (
    SchoolContextClassAuthority,
)
from aieos.domains.teaching.domain.errors import InvalidTeachingExecutionError
from aieos.domains.teaching.domain.execution import TeachingExecution
from aieos.domains.teaching.domain.execution_content_binding import ContentBindingSpec
from aieos.domains.teaching.domain.identities import ExecutionId, WorkId
from aieos.platform.events.models import MutationEventContext
from aieos.platform.events.teaching_events import execution_started_outbox
from aieos.platform.idempotency.hashing import fingerprint_material, hash_idempotency_key
from aieos.platform.idempotency.models import (
    TEACHING_EXECUTION_START_V1,
    IdempotencyOutcome,
    IdempotencyScope,
)
from aieos.platform.security.audit import SecurityAuditAction


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(UTC)


def _canonical_bindings(
    command: StartTeachingExecutionCommand,
) -> list[dict[str, str]]:
    items = [
        {
            "content_id": str(binding.content_id),
            "content_version_id": str(binding.content_version_id),
            "artifact_kind": binding.artifact_kind.strip(),
        }
        for binding in command.bindings
    ]
    items.sort(
        key=lambda item: (
            item["content_id"],
            item["content_version_id"],
            item["artifact_kind"],
        )
    )
    return items


def _start_fingerprint(command: StartTeachingExecutionCommand) -> str:
    return fingerprint_material(
        {
            "work_id": str(command.work_id),
            "class_ref": command.class_ref.strip(),
            "bindings": _canonical_bindings(command),
        }
    )


def _allowed_artifact_keys(
    artifacts,
) -> set[tuple[UUID, UUID, str]]:
    allowed: set[tuple[UUID, UUID, str]] = set()
    for item in artifacts.items:
        kind = item.artifact_kind if item.artifact_kind is not None else item.content_type
        allowed.add((item.content_id, item.version_id, kind))
    return allowed


class StartTeachingExecutionService:
    def __init__(
        self,
        uow_factory: TeachingUnitOfWorkFactory,
        class_authority: SchoolContextClassAuthority,
        artifacts: ListTeachingWorkArtifactsService | None,
        *,
        idempotency_retention: timedelta,
    ) -> None:
        if idempotency_retention.total_seconds() <= 0:
            raise ValueError("idempotency_retention must be a positive duration")
        self._uow_factory = uow_factory
        self._class_authority = class_authority
        self._artifacts = artifacts
        self._idempotency_retention = idempotency_retention

    def start(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        command: StartTeachingExecutionCommand,
        *,
        idempotency_key: str,
        event_context: MutationEventContext,
        audit_provenance: MutationAuditProvenance,
        now: datetime | None = None,
    ) -> TeachingExecutionReadModel:
        started_at = _now(now)
        fingerprint = _start_fingerprint(command)
        scope = IdempotencyScope(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            operation=TEACHING_EXECUTION_START_V1,
            key_sha256=hash_idempotency_key(idempotency_key),
        )
        work_id = WorkId(command.work_id)
        with self._uow_factory(execution_tenant_id) as uow:
            uow.idempotency.acquire_scope(scope)
            existing = uow.idempotency.get(scope)
            if existing is not None:
                if existing.request_fingerprint_sha256 != fingerprint:
                    raise IdempotencyKeyReused("idempotency key already bound")
                replayed = uow.executions.get(ExecutionId(existing.result_content_id))
                if replayed is None:
                    raise PersistenceInvariantViolation(
                        "idempotent start outcome is not visible"
                    )
                return teaching_execution_read_model(replayed)

            class_target = self._class_authority.require_assignable_class_ref(
                execution_tenant_id, principal_id, command.class_ref
            )
            work = uow.works.get(work_id)
            if work is None:
                raise TeachingWorkNotFound(
                    "TeachingWork is not visible in the execution tenant"
                )
            if work.teacher_principal_id != principal_id:
                raise TeachingWorkForbidden(
                    "TeachingWork is owned by a different teacher"
                )

            binding_specs: list[ContentBindingSpec] = []
            if command.bindings:
                if self._artifacts is None:
                    raise GenerationServiceUnavailable(
                        "Teaching Work artifacts are not composed in this runtime"
                    )
                artifacts = self._artifacts.list(
                    execution_tenant_id, principal_id, work_id
                )
                allowed = _allowed_artifact_keys(artifacts)
                for binding in command.bindings:
                    content_id = ContentId(binding.content_id)
                    content_version_id = ContentVersionId(binding.content_version_id)
                    uow.content_eligibility.verify_execution_content_version_with_lock(
                        content_id=content_id,
                        content_version_id=content_version_id,
                    )
                    kind = binding.artifact_kind.strip()
                    key = (binding.content_id, binding.content_version_id, kind)
                    if key not in allowed:
                        raise ExecutionContentBindingRejected(
                            "binding does not match the Work artifact projection"
                        )
                    binding_specs.append(
                        ContentBindingSpec(
                            content_id=binding.content_id,
                            content_version_id=binding.content_version_id,
                            artifact_kind=kind,
                        )
                    )

            try:
                execution = TeachingExecution.start(
                    tenant_id=execution_tenant_id,
                    teacher_principal_id=principal_id,
                    work_id=work_id,
                    class_ref=class_target.class_ref,
                    started_at=started_at,
                    bindings=binding_specs,
                )
            except InvalidTeachingExecutionError as exc:
                raise InvalidTeachingExecutionRequest(
                    "teaching execution start request is invalid"
                ) from exc

            uow.executions.insert(execution)
            uow.outbox.insert(
                execution_started_outbox(
                    tenant_id=execution_tenant_id,
                    execution_id=execution.execution_id.value,
                    teacher_principal_id=execution.teacher_principal_id,
                    work_id=execution.work_id.value,
                    class_ref=execution.class_ref,
                    lifecycle_state=execution.lifecycle_state.value,
                    started_at=execution.started_at,
                    bindings=tuple(
                        {
                            "content_id": str(binding.content_id),
                            "content_version_id": str(binding.content_version_id),
                            "artifact_kind": binding.artifact_kind,
                        }
                        for binding in execution.bindings
                    ),
                    aggregate_revision=int(execution.aggregate_revision),
                    context=event_context,
                    created_at=started_at,
                )
            )
            related = (source_work_ref(work_id.value),) + tuple(
                content_version_ref(binding.content_version_id)
                for binding in execution.bindings
            )
            insert_required_teaching_execution_audit(
                uow,
                tenant_id=execution_tenant_id,
                action=SecurityAuditAction.TEACHING_EXECUTION_START,
                primary_resource_ref=execution_primary_ref(
                    execution.execution_id.value,
                    int(execution.aggregate_revision),
                ),
                resource_revision_before=None,
                resource_revision_after=int(execution.aggregate_revision),
                related_resource_refs=related,
                mutation_event_context=event_context,
                audit_provenance=audit_provenance,
                occurred_at=started_at,
            )
            uow.idempotency.insert(
                IdempotencyOutcome(
                    tenant_id=scope.tenant_id,
                    principal_id=scope.principal_id,
                    operation=scope.operation,
                    key_sha256=scope.key_sha256,
                    request_fingerprint_sha256=fingerprint,
                    result_content_id=execution.execution_id.value,
                    result_version_id=None,
                    result_review_decision_id=None,
                    result_publication_id=None,
                    result_aggregate_revision=int(execution.aggregate_revision),
                    created_at=started_at,
                    expires_at=started_at + self._idempotency_retention,
                )
            )
            uow.commit()
        return teaching_execution_read_model(execution)
