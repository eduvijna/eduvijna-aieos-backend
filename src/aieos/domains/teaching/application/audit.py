"""Required Teaching committed-mutation audit evidence helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from aieos.domains.content.application.audit import content_version_ref
from aieos.domains.teaching.application.ports import TeachingUnitOfWork
from aieos.platform.events.models import MutationEventContext
from aieos.platform.resources import ResourceRef
from aieos.platform.security.audit import (
    SecurityAuditAction,
    SecurityAuditExecutionChannel,
    build_security_mutation_audit_record,
)

RESOURCE_TEACHING_ASSIGNMENT = "teaching.assignment"
RESOURCE_TEACHING_EXECUTION = "teaching.execution"
RESOURCE_TEACHING_EXECUTION_OBSERVATION = "teaching.execution.observation"


@dataclass(frozen=True, slots=True)
class MutationAuditProvenance:
    """Explicit execution provenance for required Teaching mutation audit."""

    executing_principal_id: UUID
    execution_channel: SecurityAuditExecutionChannel
    delegation_id: UUID | None = None
    trace_id: str | None = None


def api_mutation_audit_provenance(principal_id: UUID) -> MutationAuditProvenance:
    return MutationAuditProvenance(
        executing_principal_id=principal_id,
        execution_channel=SecurityAuditExecutionChannel.API,
        delegation_id=None,
        trace_id=None,
    )


def assignment_primary_ref(assignment_id: UUID, revision_after: int) -> ResourceRef:
    return ResourceRef(RESOURCE_TEACHING_ASSIGNMENT, assignment_id, revision_after)


def execution_primary_ref(execution_id: UUID, revision_after: int) -> ResourceRef:
    return ResourceRef(RESOURCE_TEACHING_EXECUTION, execution_id, revision_after)


def observation_primary_ref(
    observation_id: UUID, revision_after: int
) -> ResourceRef:
    return ResourceRef(
        RESOURCE_TEACHING_EXECUTION_OBSERVATION, observation_id, revision_after
    )


def source_work_ref(work_id: UUID) -> ResourceRef:
    return ResourceRef("teaching.work", work_id, None)


def insert_required_teaching_audit(
    uow: TeachingUnitOfWork,
    *,
    tenant_id: UUID,
    action: SecurityAuditAction,
    assignment_id: UUID,
    resource_revision_before: int | None,
    resource_revision_after: int,
    related_resource_refs: tuple[ResourceRef, ...],
    mutation_event_context: MutationEventContext,
    audit_provenance: MutationAuditProvenance,
    occurred_at: datetime,
) -> None:
    insert_required_teaching_execution_audit(
        uow,
        tenant_id=tenant_id,
        action=action,
        primary_resource_ref=assignment_primary_ref(
            assignment_id, resource_revision_after
        ),
        resource_revision_before=resource_revision_before,
        resource_revision_after=resource_revision_after,
        related_resource_refs=related_resource_refs,
        mutation_event_context=mutation_event_context,
        audit_provenance=audit_provenance,
        occurred_at=occurred_at,
    )


def insert_required_teaching_execution_audit(
    uow: TeachingUnitOfWork,
    *,
    tenant_id: UUID,
    action: SecurityAuditAction,
    primary_resource_ref: ResourceRef,
    resource_revision_before: int | None,
    resource_revision_after: int,
    related_resource_refs: tuple[ResourceRef, ...],
    mutation_event_context: MutationEventContext,
    audit_provenance: MutationAuditProvenance,
    occurred_at: datetime,
) -> None:
    record = build_security_mutation_audit_record(
        tenant_id=tenant_id,
        action=action,
        primary_resource_ref=primary_resource_ref,
        resource_revision_before=resource_revision_before,
        resource_revision_after=resource_revision_after,
        related_resource_refs=related_resource_refs,
        mutation_event_context=mutation_event_context,
        executing_principal_id=audit_provenance.executing_principal_id,
        execution_channel=audit_provenance.execution_channel,
        occurred_at=occurred_at,
        delegation_id=audit_provenance.delegation_id,
        trace_id=audit_provenance.trace_id,
    )
    uow.audit.insert(record)


__all__ = [
    "MutationAuditProvenance",
    "RESOURCE_TEACHING_ASSIGNMENT",
    "RESOURCE_TEACHING_EXECUTION",
    "RESOURCE_TEACHING_EXECUTION_OBSERVATION",
    "api_mutation_audit_provenance",
    "assignment_primary_ref",
    "content_version_ref",
    "execution_primary_ref",
    "insert_required_teaching_audit",
    "insert_required_teaching_execution_audit",
    "observation_primary_ref",
    "source_work_ref",
]
