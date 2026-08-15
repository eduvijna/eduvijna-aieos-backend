"""Insert-only SQLAlchemy security mutation-audit repository.

Participates in a caller-owned Unit of Work. Never commits or rolls back.
"""

from __future__ import annotations

from sqlalchemy.engine import Connection

from aieos.platform.resources import ResourceRef
from aieos.platform.security.audit.models import SecurityMutationAuditRecord
from aieos.platform.security.audit.persistence.errors import (
    reraise_as_audit_persistence_error,
)
from aieos.platform.security.audit.persistence.models import audit_records_table


def _related_refs_as_json(
    refs: tuple[ResourceRef, ...],
) -> list[dict[str, object]]:
    return [
        {
            "resource_type": ref.resource_type,
            "resource_id": str(ref.resource_id),
            "resource_revision": ref.resource_revision,
        }
        for ref in refs
    ]


class SqlAlchemySecurityMutationAuditRepository:
    """Implements SecurityMutationAuditRepository against security.audit_records."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: SecurityMutationAuditRecord) -> None:
        ctx = record.audit_context
        primary = record.primary_resource_ref
        values = {
            "audit_record_id": record.audit_record_id.value,
            "tenant_id": record.tenant_id,
            "action": record.action.value,
            "primary_resource_type": primary.resource_type,
            "primary_resource_id": primary.resource_id,
            "primary_resource_revision": primary.resource_revision,
            "resource_revision_before": record.resource_revision_before,
            "resource_revision_after": record.resource_revision_after,
            "related_resource_refs": _related_refs_as_json(
                record.related_resource_refs
            ),
            "initiating_principal_id": ctx.initiating_principal_id,
            "effective_actor_id": ctx.effective_actor_id,
            "executing_principal_id": ctx.executing_principal_id,
            "delegation_id": ctx.delegation_id,
            "execution_channel": ctx.execution_channel.value,
            "correlation_id": ctx.correlation_id,
            "causation_id": ctx.causation_id,
            "trace_id": ctx.trace_id,
            "occurred_at": record.occurred_at,
        }
        try:
            self._connection.execute(audit_records_table.insert().values(**values))
        except Exception as exc:  # noqa: BLE001 — boundary translation
            reraise_as_audit_persistence_error(exc)
