"""HTTP ContentVersion append orchestration. Reuses GCI-I03 append_version_in_uow."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from uuid import UUID

from aieos.domains.content.application.errors import (
    ContentNotFound,
    ContentPayloadInvalid,
    ContentSchemaMismatch,
    ContentSchemaNotFound,
    ContentVersionAppendNotAllowed,
    ContentVersionNotFound,
    IdempotencyKeyReused,
    PersistenceInvariantViolation,
)
from aieos.domains.content.application.models import (
    AppendContentVersionCommand,
    ContentVersionReadModel,
    content_version_read_model,
)
from aieos.domains.content.application.ports import ContentUnitOfWorkFactory
from aieos.domains.content.application.services import append_version_in_uow
from aieos.domains.content.domain.errors import InvalidPayloadError, SchemaNotFoundError
from aieos.domains.content.domain.identities import (
    AggregateRevision,
    ContentId,
    ContentVersionId,
    VersionNumber,
)
from aieos.domains.content.domain.origin import ContentOrigin
from aieos.domains.content.domain.schema import ContentSchemaRegistry, SchemaId, SchemaVersion
from aieos.domains.content.domain.states import StewardshipState
from aieos.domains.content.domain.version import ContentPayload, ContentVersion
from aieos.platform.idempotency.hashing import fingerprint_material, hash_idempotency_key
from aieos.platform.idempotency.models import (
    CONTENT_VERSION_APPEND_V1,
    IdempotencyOutcome,
    IdempotencyScope,
)

_APPEND_ALLOWED = frozenset({StewardshipState.DRAFT.value, StewardshipState.GENERATED.value})


class HttpAppendContentVersionService:
    def __init__(
        self,
        uow_factory: ContentUnitOfWorkFactory,
        schema_registry: ContentSchemaRegistry,
        *,
        idempotency_retention: timedelta,
    ) -> None:
        if idempotency_retention.total_seconds() <= 0:
            raise ValueError("idempotency_retention must be a positive duration")
        self._uow_factory = uow_factory
        self._schema_registry = schema_registry
        self._idempotency_retention = idempotency_retention

    def append(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        *,
        content_id: ContentId,
        expected_aggregate_revision: AggregateRevision,
        schema_id: str,
        schema_version: int,
        payload: Mapping[str, object],
        idempotency_key: str,
        now: datetime | None = None,
    ) -> tuple[ContentVersionReadModel, AggregateRevision]:
        created_at = now if now is not None else datetime.now(UTC)
        fingerprint = fingerprint_material(
            {
                "content_id": str(content_id),
                "expected_aggregate_revision": int(expected_aggregate_revision),
                "schema_id": schema_id,
                "schema_version": schema_version,
                "payload": dict(payload),
            }
        )
        scope = IdempotencyScope(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            operation=CONTENT_VERSION_APPEND_V1,
            key_sha256=hash_idempotency_key(idempotency_key),
        )
        with self._uow_factory(execution_tenant_id) as uow:
            uow.idempotency.acquire_scope(scope)
            existing = uow.idempotency.get(scope)
            if existing is not None:
                if existing.request_fingerprint_sha256 != fingerprint:
                    raise IdempotencyKeyReused("idempotency key already bound")
                if existing.result_version_id is None:
                    raise PersistenceInvariantViolation(
                        "idempotent append outcome is missing version identity"
                    )
                stored = uow.versions.get(ContentVersionId(existing.result_version_id))
                if stored is None or stored.content_id != content_id:
                    raise PersistenceInvariantViolation(
                        "idempotent append outcome is not visible"
                    )
                return (
                    content_version_read_model(stored),
                    AggregateRevision(existing.result_aggregate_revision),
                )

            head = uow.contents.get_head_for_update(content_id)
            if head is None or head.tenant_id != execution_tenant_id:
                raise ContentNotFound("Content is not visible in the execution tenant")
            if head.stewardship_state not in _APPEND_ALLOWED:
                raise ContentVersionAppendNotAllowed(
                    "ContentVersion append is not allowed in the current stewardship state"
                )
            try:
                registered = self._schema_registry.get(schema_id, schema_version)
            except SchemaNotFoundError as exc:
                raise ContentSchemaNotFound("schema is not registered") from exc
            if registered.content_type != head.content_type:
                raise ContentSchemaMismatch(
                    "registered schema content_type does not match Content"
                )
            try:
                registered.validate(payload)
                domain_payload = ContentPayload.from_mapping(payload)
            except ContentPayloadInvalid:
                raise
            except (InvalidPayloadError, Exception) as exc:
                raise ContentPayloadInvalid("payload failed schema validation") from exc

            if head.current_version_id is None:
                version_number = VersionNumber(1)
                parent = None
            else:
                if head.current_version_number is None:
                    raise PersistenceInvariantViolation(
                        "locked head is missing current version_number"
                    )
                version_number = VersionNumber(head.current_version_number.value + 1)
                parent = head.current_version_id

            version = ContentVersion(
                version_id=ContentVersionId.generate(),
                tenant_id=execution_tenant_id,
                content_id=content_id,
                version_number=version_number,
                parent_version_id=parent,
                schema_id=SchemaId(schema_id),
                schema_version=SchemaVersion(schema_version),
                payload=domain_payload,
                origin=ContentOrigin.HUMAN,
                created_at=created_at,
                created_by_principal_id=principal_id,
            )
            result = append_version_in_uow(
                uow,
                execution_tenant_id,
                AppendContentVersionCommand(
                    expected_aggregate_revision=expected_aggregate_revision,
                    version=version,
                    provenance=None,
                ),
                now=created_at,
                head=head,
            )
            uow.idempotency.insert(
                IdempotencyOutcome(
                    tenant_id=scope.tenant_id,
                    principal_id=scope.principal_id,
                    operation=scope.operation,
                    key_sha256=scope.key_sha256,
                    request_fingerprint_sha256=fingerprint,
                    result_content_id=content_id.value,
                    result_version_id=result.version_id.value,
                    result_aggregate_revision=int(result.aggregate_revision),
                    created_at=created_at,
                    expires_at=created_at + self._idempotency_retention,
                )
            )
            uow.commit()
            return content_version_read_model(version), result.aggregate_revision


class GetContentVersionService:
    def __init__(self, uow_factory: ContentUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def get(
        self,
        execution_tenant_id: UUID,
        content_id: ContentId,
        version_id: ContentVersionId,
    ) -> ContentVersionReadModel:
        with self._uow_factory(execution_tenant_id) as uow:
            found = uow.versions.get(version_id)
        if found is None or found.content_id != content_id:
            raise ContentVersionNotFound(
                "ContentVersion is not visible for the requested Content"
            )
        return content_version_read_model(found)
