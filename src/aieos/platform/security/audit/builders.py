"""Canonical SecurityMutationAuditRecord builder. Pure validation only."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from aieos.platform.events.models import MutationEventContext
from aieos.platform.resources import ResourceRef
from aieos.platform.security.audit.actions import (
    SecurityAuditAction,
    SecurityAuditExecutionChannel,
)
from aieos.platform.security.audit.errors import InvalidSecurityAuditError
from aieos.platform.security.audit.identities import AuditRecordId
from aieos.platform.security.audit.models import (
    SecurityMutationAuditContext,
    SecurityMutationAuditRecord,
)


def build_security_mutation_audit_record(
    *,
    tenant_id: UUID,
    action: SecurityAuditAction,
    primary_resource_ref: ResourceRef,
    resource_revision_before: int | None,
    resource_revision_after: int,
    related_resource_refs: tuple[ResourceRef, ...],
    mutation_event_context: MutationEventContext,
    executing_principal_id: UUID,
    execution_channel: SecurityAuditExecutionChannel,
    occurred_at: datetime,
    delegation_id: UUID | None = None,
    trace_id: str | None = None,
    audit_record_id: AuditRecordId | None = None,
) -> SecurityMutationAuditRecord:
    """Build immutable committed-mutation audit evidence.

    Correlation and causation derive exclusively from MutationEventContext.
    Independent correlation_id/causation_id arguments are intentionally absent.
    """
    if not isinstance(action, SecurityAuditAction):
        raise InvalidSecurityAuditError("action must be a SecurityAuditAction")
    if not isinstance(execution_channel, SecurityAuditExecutionChannel):
        raise InvalidSecurityAuditError(
            "execution_channel must be a SecurityAuditExecutionChannel"
        )
    if not isinstance(mutation_event_context, MutationEventContext):
        raise InvalidSecurityAuditError(
            "mutation_event_context must be a MutationEventContext"
        )
    context = SecurityMutationAuditContext(
        mutation_event_context=mutation_event_context,
        executing_principal_id=executing_principal_id,
        execution_channel=execution_channel,
        delegation_id=delegation_id,
        trace_id=trace_id,
    )
    record_id = (
        audit_record_id if audit_record_id is not None else AuditRecordId.generate()
    )
    return SecurityMutationAuditRecord(
        audit_record_id=record_id,
        tenant_id=tenant_id,
        action=action,
        primary_resource_ref=primary_resource_ref,
        resource_revision_before=resource_revision_before,
        resource_revision_after=resource_revision_after,
        related_resource_refs=related_resource_refs,
        audit_context=context,
        occurred_at=occurred_at,
    )
