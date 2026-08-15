"""Required Content committed-mutation audit evidence helpers (SAI-I03)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from aieos.domains.content.application.ports import ContentUnitOfWork
from aieos.platform.events.models import MutationEventContext
from aieos.platform.resources import ResourceRef
from aieos.platform.security.audit import (
    SecurityAuditAction,
    SecurityAuditExecutionChannel,
    build_security_mutation_audit_record,
)

RESOURCE_CONTENT = "content.content"
RESOURCE_CONTENT_VERSION = "content.content_version"
RESOURCE_REVIEW_DECISION = "content.review_decision"
RESOURCE_PUBLICATION = "content.publication"


@dataclass(frozen=True, slots=True)
class MutationAuditProvenance:
    """Explicit execution provenance for required Content mutation audit."""

    executing_principal_id: UUID
    execution_channel: SecurityAuditExecutionChannel
    delegation_id: UUID | None = None
    trace_id: str | None = None


def api_mutation_audit_provenance(principal_id: UUID) -> MutationAuditProvenance:
    """API-origin provenance. Not client-controlled."""
    return MutationAuditProvenance(
        executing_principal_id=principal_id,
        execution_channel=SecurityAuditExecutionChannel.API,
        delegation_id=None,
        trace_id=None,
    )


def content_primary_ref(content_id: UUID, revision_after: int) -> ResourceRef:
    return ResourceRef(RESOURCE_CONTENT, content_id, revision_after)


def content_version_ref(version_id: UUID) -> ResourceRef:
    return ResourceRef(RESOURCE_CONTENT_VERSION, version_id, None)


def review_decision_ref(review_decision_id: UUID) -> ResourceRef:
    return ResourceRef(RESOURCE_REVIEW_DECISION, review_decision_id, None)


def publication_ref(publication_id: UUID) -> ResourceRef:
    return ResourceRef(RESOURCE_PUBLICATION, publication_id, None)


def insert_required_content_audit(
    uow: ContentUnitOfWork,
    *,
    tenant_id: UUID,
    action: SecurityAuditAction,
    content_id: UUID,
    resource_revision_before: int | None,
    resource_revision_after: int,
    related_resource_refs: tuple[ResourceRef, ...],
    mutation_event_context: MutationEventContext,
    audit_provenance: MutationAuditProvenance,
    occurred_at: datetime,
) -> None:
    """Insert one required SecurityMutationAuditRecord via the Content UoW."""
    record = build_security_mutation_audit_record(
        tenant_id=tenant_id,
        action=action,
        primary_resource_ref=content_primary_ref(content_id, resource_revision_after),
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
