"""Required Assessment committed-mutation audit evidence helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from aieos.domains.assessment.application.ports import AssessmentUnitOfWork
from aieos.platform.events.models import MutationEventContext
from aieos.platform.resources import ResourceRef
from aieos.platform.security.audit import (
    SecurityAuditAction,
    SecurityAuditExecutionChannel,
    build_security_mutation_audit_record,
)

RESOURCE_ASSESSMENT_CLASSROOM = "assessment.classroom"


@dataclass(frozen=True, slots=True)
class MutationAuditProvenance:
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


def assessment_primary_ref(assessment_id: UUID, revision_after: int) -> ResourceRef:
    return ResourceRef(RESOURCE_ASSESSMENT_CLASSROOM, assessment_id, revision_after)


def insert_required_assessment_audit(
    uow: AssessmentUnitOfWork,
    *,
    tenant_id: UUID,
    action: SecurityAuditAction,
    assessment_id: UUID,
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
        primary_resource_ref=assessment_primary_ref(
            assessment_id, resource_revision_after
        ),
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
