"""Immutable security mutation-audit models. Historical evidence only."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from aieos.platform.events.models import MutationEventContext
from aieos.platform.resources import ResourceRef
from aieos.platform.security.audit.actions import (
    SecurityAuditAction,
    SecurityAuditExecutionChannel,
    is_assessment_create_action,
    is_assessment_increment_action,
    is_asset_create_action,
    is_asset_increment_action,
    is_asset_stable_registration_action,
    is_content_audit_action,
    is_content_create_action,
    is_content_increment_action,
    is_content_migration_import_action,
    is_teaching_create_action,
    is_teaching_increment_action,
)
from aieos.platform.security.audit.errors import InvalidSecurityAuditError
from aieos.platform.security.audit.identities import AuditRecordId

_TRACE_ID_RE_LEN = 32
_MAX_RELATED_REFS = 16


def _require_uuid(value: object, *, label: str) -> UUID:
    if not isinstance(value, UUID):
        raise InvalidSecurityAuditError(f"{label} must be a UUID")
    return value


def _require_optional_uuid(value: object, *, label: str) -> UUID | None:
    if value is None:
        return None
    return _require_uuid(value, label=label)


def _require_non_negative_int(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidSecurityAuditError(f"{label} must be a non-negative integer")
    if value < 0:
        raise InvalidSecurityAuditError(f"{label} must be a non-negative integer")
    return value


def _require_optional_non_negative_int(value: object, *, label: str) -> int | None:
    if value is None:
        return None
    return _require_non_negative_int(value, label=label)


def _normalize_trace_id(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidSecurityAuditError("trace_id must be a string when present")
    if len(value) != _TRACE_ID_RE_LEN:
        raise InvalidSecurityAuditError(
            "trace_id must be 32 lowercase hexadecimal characters"
        )
    if value != value.lower() or any(ch not in "0123456789abcdef" for ch in value):
        raise InvalidSecurityAuditError(
            "trace_id must be 32 lowercase hexadecimal characters"
        )
    if value == "0" * _TRACE_ID_RE_LEN:
        raise InvalidSecurityAuditError("trace_id must not be all zeroes")
    return value


def _require_aware_utc(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise InvalidSecurityAuditError("occurred_at must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidSecurityAuditError("occurred_at must be timezone-aware")
    return value.astimezone(UTC)


def _validate_revision_pair(
    action: SecurityAuditAction,
    before: int | None,
    after: int,
) -> None:
    if is_content_create_action(action):
        if before is not None or after != 0:
            raise InvalidSecurityAuditError(
                "content.create requires before=None and after=0"
            )
        return
    if is_content_migration_import_action(action):
        if before is not None or after != 1:
            raise InvalidSecurityAuditError(
                "content.migration.import requires before=None and after=1"
            )
        return
    if is_content_increment_action(action):
        if before is None:
            raise InvalidSecurityAuditError(
                f"{action.value} requires a non-null resource_revision_before"
            )
        if after != before + 1:
            raise InvalidSecurityAuditError(
                f"{action.value} requires resource_revision_after == before + 1"
            )
        return
    if is_asset_create_action(action):
        if before is not None or after != 0:
            raise InvalidSecurityAuditError(
                "asset.create requires before=None and after=0"
            )
        return
    if is_asset_stable_registration_action(action):
        if before is None:
            raise InvalidSecurityAuditError(
                "asset.revision.register requires a non-null resource_revision_before"
            )
        if after != before:
            raise InvalidSecurityAuditError(
                "asset.revision.register requires resource_revision_after == before"
            )
        return
    if is_asset_increment_action(action):
        if before is None:
            raise InvalidSecurityAuditError(
                f"{action.value} requires a non-null resource_revision_before"
            )
        if after != before + 1:
            raise InvalidSecurityAuditError(
                f"{action.value} requires resource_revision_after == before + 1"
            )
        return
    if is_teaching_create_action(action):
        if before is not None or after != 0:
            raise InvalidSecurityAuditError(
                f"{action.value} requires before=None and after=0"
            )
        return
    if is_teaching_increment_action(action):
        if before is None:
            raise InvalidSecurityAuditError(
                f"{action.value} requires a non-null resource_revision_before"
            )
        if after != before + 1:
            raise InvalidSecurityAuditError(
                f"{action.value} requires resource_revision_after == before + 1"
            )
        return
    if is_assessment_create_action(action):
        if before is not None or after != 0:
            raise InvalidSecurityAuditError(
                f"{action.value} requires before=None and after=0"
            )
        return
    if is_assessment_increment_action(action):
        if before is None:
            raise InvalidSecurityAuditError(
                f"{action.value} requires a non-null resource_revision_before"
            )
        if after != before + 1:
            raise InvalidSecurityAuditError(
                f"{action.value} requires resource_revision_after == before + 1"
            )
        return
    raise InvalidSecurityAuditError(f"unsupported audit action: {action!r}")


def _validate_primary_resource_revision(
    action: SecurityAuditAction, primary: ResourceRef, after: int
) -> None:
    if (
        is_content_audit_action(action)
        or is_teaching_create_action(action)
        or is_teaching_increment_action(action)
        or is_assessment_create_action(action)
        or is_assessment_increment_action(action)
    ):
        if primary.resource_revision != after:
            raise InvalidSecurityAuditError(
                "primary_resource_ref.resource_revision must equal "
                "resource_revision_after"
            )
        return
    if primary.resource_revision is not None:
        raise InvalidSecurityAuditError(
            "asset primary_resource_ref.resource_revision must be None"
        )


def _validate_related_refs(
    primary: ResourceRef,
    related: tuple[ResourceRef, ...],
) -> tuple[ResourceRef, ...]:
    if not isinstance(related, tuple):
        raise InvalidSecurityAuditError("related_resource_refs must be a tuple")
    if len(related) > _MAX_RELATED_REFS:
        raise InvalidSecurityAuditError(
            f"related_resource_refs must contain at most {_MAX_RELATED_REFS} entries"
        )
    seen: set[tuple[str, UUID, int | None]] = set()
    primary_key = (
        primary.resource_type,
        primary.resource_id,
        primary.resource_revision,
    )
    for ref in related:
        if not isinstance(ref, ResourceRef):
            raise InvalidSecurityAuditError(
                "related_resource_refs entries must be ResourceRef instances"
            )
        key = (ref.resource_type, ref.resource_id, ref.resource_revision)
        if key == primary_key:
            raise InvalidSecurityAuditError(
                "related_resource_refs must not duplicate primary_resource_ref"
            )
        if key in seen:
            raise InvalidSecurityAuditError(
                "related_resource_refs must not contain duplicates"
            )
        seen.add(key)
    return related


@dataclass(frozen=True, slots=True)
class SecurityMutationAuditContext:
    """Trusted mutation provenance for audit. Not authorization truth."""

    mutation_event_context: MutationEventContext
    executing_principal_id: UUID
    execution_channel: SecurityAuditExecutionChannel
    delegation_id: UUID | None = None
    trace_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.mutation_event_context, MutationEventContext):
            raise InvalidSecurityAuditError(
                "mutation_event_context must be a MutationEventContext"
            )
        object.__setattr__(
            self,
            "executing_principal_id",
            _require_uuid(self.executing_principal_id, label="executing_principal_id"),
        )
        object.__setattr__(
            self,
            "delegation_id",
            _require_optional_uuid(self.delegation_id, label="delegation_id"),
        )
        if not isinstance(self.execution_channel, SecurityAuditExecutionChannel):
            raise InvalidSecurityAuditError(
                "execution_channel must be a SecurityAuditExecutionChannel"
            )
        object.__setattr__(self, "trace_id", _normalize_trace_id(self.trace_id))

    @property
    def initiating_principal_id(self) -> UUID:
        return self.mutation_event_context.actor_principal_id

    @property
    def effective_actor_id(self) -> UUID:
        return self.mutation_event_context.effective_actor_id

    @property
    def correlation_id(self) -> UUID:
        return self.mutation_event_context.correlation_id

    @property
    def causation_id(self) -> UUID:
        return self.mutation_event_context.causation_id


@dataclass(frozen=True, slots=True)
class SecurityMutationAuditRecord:
    """Historical committed-mutation audit evidence.

    Not authorization evidence, SecurityContext, permission snapshot,
    delegation authority, business state, OutboxMessage/CloudEvent, or
    Temporal workflow history.
    """

    audit_record_id: AuditRecordId
    tenant_id: UUID
    action: SecurityAuditAction
    primary_resource_ref: ResourceRef
    resource_revision_before: int | None
    resource_revision_after: int
    related_resource_refs: tuple[ResourceRef, ...]
    audit_context: SecurityMutationAuditContext
    occurred_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.audit_record_id, AuditRecordId):
            raise InvalidSecurityAuditError(
                "audit_record_id must be an AuditRecordId"
            )
        object.__setattr__(
            self, "tenant_id", _require_uuid(self.tenant_id, label="tenant_id")
        )
        if not isinstance(self.action, SecurityAuditAction):
            raise InvalidSecurityAuditError(
                "action must be a SecurityAuditAction"
            )
        if not isinstance(self.primary_resource_ref, ResourceRef):
            raise InvalidSecurityAuditError(
                "primary_resource_ref must be a ResourceRef"
            )
        before = _require_optional_non_negative_int(
            self.resource_revision_before, label="resource_revision_before"
        )
        after = _require_non_negative_int(
            self.resource_revision_after, label="resource_revision_after"
        )
        object.__setattr__(self, "resource_revision_before", before)
        object.__setattr__(self, "resource_revision_after", after)
        _validate_revision_pair(self.action, before, after)
        _validate_primary_resource_revision(
            self.action, self.primary_resource_ref, after
        )
        object.__setattr__(
            self,
            "related_resource_refs",
            _validate_related_refs(
                self.primary_resource_ref, self.related_resource_refs
            ),
        )
        if not isinstance(self.audit_context, SecurityMutationAuditContext):
            raise InvalidSecurityAuditError(
                "audit_context must be a SecurityMutationAuditContext"
            )
        object.__setattr__(self, "occurred_at", _require_aware_utc(self.occurred_at))
