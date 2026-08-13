"""Append one immutable ContentVersion onto a Content aggregate."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

from aieos.domains.content.application.errors import (
    AggregateRevisionConflict,
    ContentNotFound,
    PersistenceInvariantViolation,
    TenantContextMismatch,
    VersionLineageConflict,
)
from aieos.domains.content.application.models import (
    AppendContentVersionCommand,
    AppendContentVersionResult,
    LockedContentHead,
)
from aieos.domains.content.application.ports import ContentUnitOfWorkFactory
from aieos.domains.content.domain.origin import ContentOrigin
from aieos.domains.content.domain.version import ContentVersion


def _require_object_mapping(value: Mapping[str, object] | None, *, label: str) -> None:
    if value is None:
        return
    if not isinstance(value, Mapping):
        raise PersistenceInvariantViolation(f"{label} must be a JSON object")
    if any(not isinstance(key, str) for key in value):
        raise PersistenceInvariantViolation(f"{label} object keys must be strings")


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


class AppendContentVersionService:
    """Authoritative transactional append. Not a product-facing API entrypoint."""

    def __init__(self, uow_factory: ContentUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def append(
        self,
        execution_tenant_id: UUID,
        command: AppendContentVersionCommand,
        *,
        now: datetime | None = None,
    ) -> AppendContentVersionResult:
        version = command.version
        if execution_tenant_id != version.tenant_id:
            raise TenantContextMismatch(
                "execution tenant does not match ContentVersion.tenant_id"
            )
        _require_object_mapping(command.provenance, label="provenance")
        if version.origin is ContentOrigin.AI and command.provenance is None:
            raise PersistenceInvariantViolation(
                "origin AI requires a provenance JSON object"
            )
        updated_at = now if now is not None else datetime.now(UTC)
        if updated_at.tzinfo is None or updated_at.utcoffset() is None:
            raise PersistenceInvariantViolation("updated_at must be timezone-aware")

        with self._uow_factory(execution_tenant_id) as uow:
            head = uow.contents.get_head_for_update(version.content_id)
            if head is None:
                raise ContentNotFound("Content is not visible in the execution tenant")
            if head.tenant_id != execution_tenant_id:
                raise ContentNotFound("Content is not visible in the execution tenant")
            if head.aggregate_revision != command.expected_aggregate_revision:
                raise AggregateRevisionConflict(
                    "expected aggregate_revision does not match stored head"
                )
            _assert_linear_append(head, version)
            uow.versions.insert(version, command.provenance)
            resulting = uow.contents.advance_current_version(
                content_id=version.content_id,
                tenant_id=execution_tenant_id,
                expected_revision=command.expected_aggregate_revision,
                expected_current_version_id=head.current_version_id,
                new_version_id=version.version_id,
                updated_at=updated_at,
            )
            if resulting is None:
                raise AggregateRevisionConflict(
                    "aggregate head changed before append could commit"
                )
            uow.commit()
            return AppendContentVersionResult(
                content_id=version.content_id,
                version_id=version.version_id,
                version_number=version.version_number,
                aggregate_revision=resulting,
            )
