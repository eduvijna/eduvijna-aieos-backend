"""Required Asset committed-mutation audit evidence helpers (PED-I10B6).

Uses MutationEventContext for trusted actor/correlation/causation provenance
only. Does not emit an integration event or outbox row.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from aieos.domains.asset.application.ports import AssetUnitOfWork
from aieos.domains.asset.domain.identities import AssetId, AssetRevisionNumber
from aieos.domains.asset.domain.resource_type import AssetResourceType
from aieos.platform.events.models import MutationEventContext
from aieos.platform.resources import ResourceRef
from aieos.platform.security.audit import (
    SecurityAuditAction,
    SecurityAuditExecutionChannel,
    build_security_mutation_audit_record,
)
from aieos.platform.security.audit.errors import InvalidSecurityAuditError

_PINNED_REVISION_ACTIONS = frozenset(
    {
        SecurityAuditAction.ASSET_REVISION_REGISTER,
        SecurityAuditAction.ASSET_REVISION_ACTIVATE,
        SecurityAuditAction.ASSET_SAFETY_PASS,
        SecurityAuditAction.ASSET_SAFETY_FAIL,
    }
)


@dataclass(frozen=True, slots=True)
class AssetMutationAuditProvenance:
    """Explicit execution provenance for required Asset mutation audit."""

    executing_principal_id: UUID
    execution_channel: SecurityAuditExecutionChannel
    delegation_id: UUID | None = None
    trace_id: str | None = None


def asset_stable_primary_ref(
    resource_type: AssetResourceType | str, asset_id: AssetId
) -> ResourceRef:
    """Stable Asset ResourceRef. resource_revision is always None (ADR-AIEOS-036R1)."""
    return ResourceRef(str(resource_type), asset_id.value, None)


def asset_pinned_revision_ref(
    resource_type: AssetResourceType | str,
    asset_id: AssetId,
    revision_number: AssetRevisionNumber,
) -> ResourceRef:
    """Same Asset identity pinned to an exact AssetRevisionNumber."""
    return ResourceRef(str(resource_type), asset_id.value, int(revision_number))


def insert_required_asset_audit(
    uow: AssetUnitOfWork,
    *,
    tenant_id: UUID,
    action: SecurityAuditAction,
    resource_type: AssetResourceType | str,
    asset_id: AssetId,
    resource_revision_before: int | None,
    resource_revision_after: int,
    mutation_event_context: MutationEventContext,
    audit_provenance: AssetMutationAuditProvenance,
    occurred_at: datetime,
    revision_number: AssetRevisionNumber | None = None,
) -> None:
    """Insert one required SecurityMutationAuditRecord via the Asset UoW."""
    if action in _PINNED_REVISION_ACTIONS:
        if revision_number is None:
            raise InvalidSecurityAuditError(
                "revision-specific asset audit requires a pinned related ResourceRef"
            )
        related = (
            asset_pinned_revision_ref(resource_type, asset_id, revision_number),
        )
    else:
        if revision_number is not None:
            raise InvalidSecurityAuditError(
                "this asset audit action must not include a pinned related ResourceRef"
            )
        related = ()
    record = build_security_mutation_audit_record(
        tenant_id=tenant_id,
        action=action,
        primary_resource_ref=asset_stable_primary_ref(resource_type, asset_id),
        resource_revision_before=resource_revision_before,
        resource_revision_after=resource_revision_after,
        related_resource_refs=related,
        mutation_event_context=mutation_event_context,
        executing_principal_id=audit_provenance.executing_principal_id,
        execution_channel=audit_provenance.execution_channel,
        occurred_at=occurred_at,
        delegation_id=audit_provenance.delegation_id,
        trace_id=audit_provenance.trace_id,
    )
    uow.audit.insert(record)
