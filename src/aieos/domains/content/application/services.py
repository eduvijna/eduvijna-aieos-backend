"""Authoritative ContentVersion append. Not a product-facing API entrypoint."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from aieos.domains.content.application.asset_refs import (
    ensure_unique_asset_slots,
    validate_asset_bindings,
)
from aieos.domains.content.application.errors import (
    AggregateRevisionConflict,
    AIProvenanceInvalid,
    ContentNotFound,
    ContentVersionAppendNotAllowed,
    MigrationImportProvenanceInvalid,
    PersistenceInvariantViolation,
    TenantContextMismatch,
    VersionLineageConflict,
)
from aieos.domains.content.application.models import (
    AppendContentVersionCommand,
    AppendContentVersionResult,
    LockedContentHead,
)
from aieos.domains.content.application.ports import (
    AssetReferenceValidationPort,
    ContentUnitOfWork,
    ContentUnitOfWorkFactory,
)
from aieos.domains.content.domain.errors import (
    InvalidAIGenerationProvenanceError,
    InvalidMigrationImportProvenanceError,
)
from aieos.domains.content.domain.migration_provenance import (
    MigrationImportProvenanceV1,
    migration_import_provenance_as_json,
    migration_import_provenance_from_json,
)
from aieos.domains.content.domain.origin import ContentOrigin
from aieos.domains.content.domain.provenance import (
    AIGenerationProvenanceV1,
    ai_generation_provenance_as_json,
    ai_generation_provenance_from_json,
)
from aieos.domains.content.domain.states import StewardshipState
from aieos.domains.content.domain.version import ContentVersion
from aieos.platform.events.content_events import version_created_outbox
from aieos.platform.events.models import MutationEventContext

_APPEND_ALLOWED = frozenset(
    {
        StewardshipState.DRAFT.value,
        StewardshipState.GENERATED.value,
        StewardshipState.APPROVED.value,
    }
)


def _require_object_mapping(value: Mapping[str, object] | None, *, label: str) -> None:
    if value is None:
        return
    if not isinstance(value, Mapping):
        raise PersistenceInvariantViolation(f"{label} must be a JSON object")
    if any(not isinstance(key, str) for key in value):
        raise PersistenceInvariantViolation(f"{label} object keys must be strings")


def _normalize_ai_provenance(
    value: AIGenerationProvenanceV1
    | MigrationImportProvenanceV1
    | Mapping[str, object]
    | None,
) -> tuple[AIGenerationProvenanceV1, dict[str, object]]:
    if isinstance(value, MigrationImportProvenanceV1):
        raise PersistenceInvariantViolation(
            "MigrationImportProvenanceV1 is only valid for origin IMPORT"
        )
    if value is None:
        raise AIProvenanceInvalid("origin AI requires typed AIGenerationProvenanceV1")
    try:
        if isinstance(value, AIGenerationProvenanceV1):
            typed = value
        elif isinstance(value, Mapping):
            typed = ai_generation_provenance_from_json(value)
        else:
            raise AIProvenanceInvalid(
                "origin AI requires typed AIGenerationProvenanceV1"
            )
    except InvalidAIGenerationProvenanceError as exc:
        raise AIProvenanceInvalid(str(exc)) from exc
    return typed, ai_generation_provenance_as_json(typed)


def _normalize_migration_provenance(
    value: AIGenerationProvenanceV1
    | MigrationImportProvenanceV1
    | Mapping[str, object]
    | None,
) -> tuple[MigrationImportProvenanceV1, dict[str, object]]:
    if isinstance(value, AIGenerationProvenanceV1):
        raise PersistenceInvariantViolation(
            "AIGenerationProvenanceV1 is only valid for origin AI"
        )
    if value is None:
        raise MigrationImportProvenanceInvalid(
            "origin IMPORT requires typed MigrationImportProvenanceV1"
        )
    try:
        if isinstance(value, MigrationImportProvenanceV1):
            typed = value
        elif isinstance(value, Mapping):
            typed = migration_import_provenance_from_json(value)
        else:
            raise MigrationImportProvenanceInvalid(
                "origin IMPORT requires typed MigrationImportProvenanceV1"
            )
    except InvalidMigrationImportProvenanceError as exc:
        raise MigrationImportProvenanceInvalid(str(exc)) from exc
    return typed, migration_import_provenance_as_json(typed)


def _assert_linear_append(head: LockedContentHead, version: ContentVersion) -> None:
    if head.current_version_id is None:
        if version.version_number.value != 1 or version.parent_version_id is not None:
            raise VersionLineageConflict(
                "first ContentVersion must have version_number 1 and no parent"
            )
        return
    if version.parent_version_id != head.current_version_id:
        raise VersionLineageConflict(
            "parent_version_id must equal the aggregate current_version_id"
        )
    if head.current_version_number is None:
        raise VersionLineageConflict("locked head is missing current version_number")
    if version.version_number.value != head.current_version_number.value + 1:
        raise VersionLineageConflict(
            "linear history requires version_number == current version_number + 1"
        )


def append_version_in_uow(
    uow: ContentUnitOfWork,
    execution_tenant_id: UUID,
    command: AppendContentVersionCommand,
    *,
    now: datetime,
    event_context: MutationEventContext,
    head: LockedContentHead | None = None,
) -> AppendContentVersionResult:
    """Transaction-scoped append. Caller owns commit/rollback."""
    version = command.version
    if execution_tenant_id != version.tenant_id:
        raise TenantContextMismatch(
            "execution tenant does not match ContentVersion.tenant_id"
        )
    provenance_payload: Mapping[str, Any] | None
    if version.origin is ContentOrigin.AI:
        _typed, provenance_payload = _normalize_ai_provenance(command.provenance)
    elif version.origin is ContentOrigin.IMPORT:
        _typed, provenance_payload = _normalize_migration_provenance(command.provenance)
    else:
        if isinstance(command.provenance, AIGenerationProvenanceV1):
            raise PersistenceInvariantViolation(
                "AIGenerationProvenanceV1 is only valid for origin AI"
            )
        if isinstance(command.provenance, MigrationImportProvenanceV1):
            raise PersistenceInvariantViolation(
                "MigrationImportProvenanceV1 is only valid for origin IMPORT"
            )
        _require_object_mapping(command.provenance, label="provenance")
        provenance_payload = (
            None if command.provenance is None else dict(command.provenance)
        )
    if now.tzinfo is None or now.utcoffset() is None:
        raise PersistenceInvariantViolation("updated_at must be timezone-aware")
    if head is None:
        head = uow.contents.get_head_for_update(version.content_id)
    if head is None:
        raise ContentNotFound("Content is not visible in the execution tenant")
    if head.tenant_id != execution_tenant_id:
        raise ContentNotFound("Content is not visible in the execution tenant")
    if head.aggregate_revision != command.expected_aggregate_revision:
        raise AggregateRevisionConflict(
            "expected aggregate_revision does not match stored head"
        )
    if head.stewardship_state not in _APPEND_ALLOWED:
        raise ContentVersionAppendNotAllowed(
            "ContentVersion append is not allowed in the current stewardship state"
        )
    _assert_linear_append(head, version)
    for ref in command.asset_refs:
        if (
            ref.content_id != version.content_id
            or ref.version_id != version.version_id
            or ref.tenant_id != version.tenant_id
        ):
            raise PersistenceInvariantViolation(
                "VersionAssetRef must match ContentVersion identity"
            )
    uow.versions.insert(version, provenance_payload)
    if command.asset_refs:
        uow.version_asset_refs.insert_many(command.asset_refs)
    resulting = uow.contents.advance_current_version(
        content_id=version.content_id,
        tenant_id=execution_tenant_id,
        expected_revision=command.expected_aggregate_revision,
        expected_current_version_id=head.current_version_id,
        expected_state=head.stewardship_state,
        new_version_id=version.version_id,
        updated_at=now,
    )
    if resulting is None:
        raise AggregateRevisionConflict(
            "aggregate head changed before append could commit"
        )
    uow.outbox.insert(
        version_created_outbox(
            tenant_id=execution_tenant_id,
            content_id=version.content_id.value,
            version_id=version.version_id.value,
            version_number=int(version.version_number),
            origin=version.origin.value,
            aggregate_revision=int(resulting),
            context=event_context,
            created_at=now,
        )
    )
    return AppendContentVersionResult(
        content_id=version.content_id,
        version_id=version.version_id,
        version_number=version.version_number,
        aggregate_revision=resulting,
    )


class AppendContentVersionService:
    """Authoritative transactional append. Not a product-facing API entrypoint."""

    def __init__(
        self,
        uow_factory: ContentUnitOfWorkFactory,
        asset_reference_validation: AssetReferenceValidationPort,
    ) -> None:
        self._uow_factory = uow_factory
        self._asset_reference_validation = asset_reference_validation

    def append(
        self,
        execution_tenant_id: UUID,
        command: AppendContentVersionCommand,
        *,
        event_context: MutationEventContext,
        principal_id: UUID | None = None,
        now: datetime | None = None,
    ) -> AppendContentVersionResult:
        updated_at = now if now is not None else datetime.now(UTC)
        actor_id = (
            principal_id
            if principal_id is not None
            else command.version.created_by_principal_id
        )
        ensure_unique_asset_slots(command.asset_refs)
        if command.asset_refs:
            validate_asset_bindings(
                self._asset_reference_validation,
                execution_tenant_id,
                actor_id,
                tuple(ref.resource_ref for ref in command.asset_refs),
            )
        with self._uow_factory(execution_tenant_id) as uow:
            result = append_version_in_uow(
                uow,
                execution_tenant_id,
                command,
                now=updated_at,
                event_context=event_context,
            )
            uow.commit()
            return result
