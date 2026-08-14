"""Controlled migration import into new AIEOS Content (GCI-I13). No legacy connectors."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Mapping
from uuid import UUID

from aieos.domains.content.application.asset_refs import (
    build_version_asset_refs,
    ensure_unique_asset_slots,
    validate_asset_bindings,
)
from aieos.domains.content.application.errors import (
    AssetReferenceValidationFailed,
    ContentPayloadInvalid,
    ContentSchemaMismatch,
    ContentSchemaNotFound,
    MigrationCandidateInvalid,
    MigrationForbidden,
    MigrationImportProvenanceInvalid,
    MigrationInvariantViolation,
    MigrationSourceConflict,
    UnknownContentType,
)
from aieos.domains.content.application.migration_models import (
    MIGRATION_OUTCOME_FAILED,
    MIGRATION_OUTCOME_IMPORTED,
    ImportMigratedContentResult,
    MigrationContentCandidate,
    MigrationImportRecord,
)
from aieos.domains.content.application.models import (
    AppendContentVersionCommand,
    LockedContentHead,
)
from aieos.domains.content.application.ports import (
    CONTENT_MIGRATE_IMPORT,
    AssetReferenceValidationPort,
    ContentMigrationAuthorizationPort,
    ContentTypeCatalog,
    ContentUnitOfWorkFactory,
    MigrationSourceSerializationGate,
)
from aieos.domains.content.application.services import append_version_in_uow
from aieos.domains.content.domain.content import Content, ContentType
from aieos.domains.content.domain.errors import (
    ContentDomainError,
    InvalidMigrationImportProvenanceError,
    InvalidMigrationSourceIdentityError,
    InvalidPayloadError,
    SchemaNotFoundError,
)
from aieos.domains.content.domain.identities import (
    AggregateRevision,
    ContentId,
    ContentVersionId,
    VersionNumber,
)
from aieos.domains.content.domain.migration import (
    require_mapping_version,
    require_migration_identifier,
    require_optional_source_version,
    require_source_digest_sha256,
)
from aieos.domains.content.domain.migration_provenance import (
    MigrationImportProvenanceV1,
)
from aieos.domains.content.domain.origin import ContentOrigin
from aieos.domains.content.domain.schema import (
    ContentSchemaRegistry,
    SchemaId,
    SchemaVersion,
)
from aieos.domains.content.domain.states import StewardshipState
from aieos.domains.content.domain.version import ContentPayload, ContentVersion
from aieos.platform.events.content_events import content_created_outbox
from aieos.platform.events.models import MutationEventContext

_FAILURE_SCHEMA = "schema_validation_failed"
_FAILURE_ASSET = "asset_binding_failed"
_FAILURE_ASSET_SLOT = "duplicate_asset_slot"
_FAILURE_CONTENT_TYPE = "unknown_content_type"
_FAILURE_PERSISTENCE = "persistence_failed"


def _fingerprints_match(
    record: MigrationImportRecord, candidate: MigrationContentCandidate
) -> bool:
    return (
        record.source_version == candidate.source_version
        and record.source_digest_sha256 == candidate.source_digest_sha256
        and record.mapping_id == candidate.mapping_id
        and record.mapping_version == candidate.mapping_version
    )


def _assert_no_source_conflict(
    record: MigrationImportRecord, candidate: MigrationContentCandidate
) -> None:
    if record.source_digest_sha256 != candidate.source_digest_sha256:
        raise MigrationSourceConflict("source digest conflicts with migration evidence")
    if record.source_version != candidate.source_version:
        raise MigrationSourceConflict("source version conflicts with migration evidence")
    if (
        record.mapping_id != candidate.mapping_id
        or record.mapping_version != candidate.mapping_version
    ):
        raise MigrationSourceConflict(
            "mapping identity conflicts with migration evidence"
        )


def _normalize_candidate(candidate: MigrationContentCandidate) -> MigrationContentCandidate:
    try:
        require_source_digest_sha256(candidate.source_digest_sha256)
        require_optional_source_version(candidate.source_version)
        require_migration_identifier(candidate.mapping_id, label="mapping_id")
        require_mapping_version(candidate.mapping_version)
    except InvalidMigrationSourceIdentityError as exc:
        raise MigrationCandidateInvalid(str(exc)) from exc
    if not isinstance(candidate.migration_batch_id, UUID):
        raise MigrationCandidateInvalid("migration_batch_id must be a UUID")
    if not isinstance(candidate.target_owner_principal_id, UUID):
        raise MigrationCandidateInvalid("target_owner_principal_id must be a UUID")
    if not isinstance(candidate.payload, Mapping):
        raise MigrationCandidateInvalid("payload must be a mapping")
    return candidate


class ImportMigratedContentService:
    """Import a canonical MigrationContentCandidate as new Content + IMPORT v1."""

    def __init__(
        self,
        uow_factory: ContentUnitOfWorkFactory,
        catalog: ContentTypeCatalog,
        schema_registry: ContentSchemaRegistry,
        asset_reference_validation: AssetReferenceValidationPort,
        migration_authorization: ContentMigrationAuthorizationPort,
        source_serialization: MigrationSourceSerializationGate,
        *,
        after_target_failure: object | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._catalog = catalog
        self._schema_registry = schema_registry
        self._asset_reference_validation = asset_reference_validation
        self._migration_authorization = migration_authorization
        self._source_serialization = source_serialization
        # Optional test hook invoked after target rollback, before FAILED finalization.
        self._after_target_failure = after_target_failure

    def import_content(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        candidate: MigrationContentCandidate,
        *,
        event_context: MutationEventContext,
        now: datetime | None = None,
    ) -> ImportMigratedContentResult:
        created_at = now if now is not None else datetime.now(UTC)
        candidate = _normalize_candidate(candidate)
        self._migration_authorization.authorize(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            capability=CONTENT_MIGRATE_IMPORT,
        )

        identity = candidate.source_identity
        with self._source_serialization.hold(
            execution_tenant_id,
            identity.source_system,
            identity.source_resource_type,
            identity.source_resource_id,
        ):
            failure_code: str | None = None
            try:
                with self._uow_factory(execution_tenant_id) as uow:
                    existing = uow.migration_imports.get(
                        identity.source_system,
                        identity.source_resource_type,
                        identity.source_resource_id,
                    )
                    if existing is not None:
                        _assert_no_source_conflict(existing, candidate)
                        if existing.outcome == MIGRATION_OUTCOME_IMPORTED:
                            return self._replay_imported(
                                uow, existing, execution_tenant_id
                            )
                    try:
                        validated = self._validate_target_inputs(
                            execution_tenant_id, principal_id, candidate
                        )
                    except UnknownContentType:
                        failure_code = _FAILURE_CONTENT_TYPE
                        raise
                    except (
                        ContentSchemaNotFound,
                        ContentSchemaMismatch,
                        ContentPayloadInvalid,
                    ):
                        failure_code = _FAILURE_SCHEMA
                        raise
                    except AssetReferenceValidationFailed as exc:
                        msg = str(exc).lower()
                        failure_code = (
                            _FAILURE_ASSET_SLOT
                            if "duplicate" in msg
                            else _FAILURE_ASSET
                        )
                        raise
                    result = self._materialize_new(
                        uow,
                        execution_tenant_id,
                        principal_id,
                        candidate,
                        validated,
                        event_context=event_context,
                        created_at=created_at,
                        prior=existing,
                    )
                    uow.commit()
                    return result
            except (
                MigrationSourceConflict,
                MigrationForbidden,
                MigrationInvariantViolation,
                MigrationCandidateInvalid,
                MigrationImportProvenanceInvalid,
            ):
                raise
            except (
                UnknownContentType,
                ContentSchemaNotFound,
                ContentSchemaMismatch,
                ContentPayloadInvalid,
                AssetReferenceValidationFailed,
            ):
                self._notify_after_target_failure()
                self._record_failure(
                    execution_tenant_id,
                    candidate,
                    failure_code=failure_code or _FAILURE_PERSISTENCE,
                    now=created_at,
                )
                raise
            except Exception:
                self._notify_after_target_failure()
                self._record_failure(
                    execution_tenant_id,
                    candidate,
                    failure_code=_FAILURE_PERSISTENCE,
                    now=created_at,
                )
                raise

    def _notify_after_target_failure(self) -> None:
        hook = self._after_target_failure
        if hook is None:
            return
        if callable(hook):
            hook()

    def _replay_imported(
        self,
        uow,
        existing: MigrationImportRecord,
        execution_tenant_id: UUID,
    ) -> ImportMigratedContentResult:
        if existing.target_content_id is None or existing.target_version_id is None:
            raise MigrationInvariantViolation(
                "IMPORTED migration record is missing target identity"
            )
        content_id = ContentId(existing.target_content_id)
        version_id = ContentVersionId(existing.target_version_id)
        found = uow.contents.get(content_id)
        version = uow.versions.get(version_id)
        if (
            found is None
            or found.tenant_id != execution_tenant_id
            or version is None
            or version.content_id != content_id
        ):
            raise MigrationInvariantViolation(
                "established migration target is no longer visible"
            )
        return ImportMigratedContentResult(
            content_id=content_id,
            version_id=version_id,
            replayed=True,
        )

    def _validate_target_inputs(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        candidate: MigrationContentCandidate,
    ) -> tuple[ContentPayload, list[dict[str, object]]]:
        if not self._catalog.contains(candidate.content_type):
            raise UnknownContentType("content_type is not registered")
        try:
            registered = self._schema_registry.get(
                candidate.schema_id, candidate.schema_version
            )
        except SchemaNotFoundError as exc:
            raise ContentSchemaNotFound("schema is not registered") from exc
        if registered.content_type != candidate.content_type:
            raise ContentSchemaMismatch(
                "registered schema content_type does not match candidate"
            )
        try:
            registered.validate(candidate.payload)
            domain_payload = ContentPayload.from_mapping(candidate.payload)
        except InvalidPayloadError as exc:
            raise ContentPayloadInvalid("payload failed schema validation") from exc

        association_items: list[dict[str, object]] = [
            {
                "resource_ref": {
                    "resource_type": spec.resource_ref.resource_type,
                    "resource_id": spec.resource_ref.resource_id,
                    "resource_revision": spec.resource_ref.resource_revision,
                },
                "role": spec.role,
                "ordinal": spec.ordinal,
                "required": spec.required,
            }
            for spec in candidate.asset_refs
        ]
        provisional_content = ContentId.generate()
        provisional_version = ContentVersionId.generate()
        built_refs = build_version_asset_refs(
            tenant_id=execution_tenant_id,
            content_id=provisional_content,
            version_id=provisional_version,
            created_at=datetime.now(UTC),
            items=association_items,
        )
        ensure_unique_asset_slots(built_refs)
        if built_refs:
            validate_asset_bindings(
                self._asset_reference_validation,
                execution_tenant_id,
                principal_id,
                tuple(ref.resource_ref for ref in built_refs),
            )
        return domain_payload, association_items

    def _materialize_new(
        self,
        uow,
        execution_tenant_id: UUID,
        principal_id: UUID,
        candidate: MigrationContentCandidate,
        validated: tuple[ContentPayload, list[dict[str, object]]],
        *,
        event_context: MutationEventContext,
        created_at: datetime,
        prior: MigrationImportRecord | None,
    ) -> ImportMigratedContentResult:
        domain_payload, association_items = validated
        content_id = ContentId.generate()
        version_id = ContentVersionId.generate()
        try:
            content = Content(
                content_id=content_id,
                tenant_id=execution_tenant_id,
                owner_principal_id=candidate.target_owner_principal_id,
                content_type=ContentType(candidate.content_type),
                title=candidate.title,
                description=candidate.description,
                locale=candidate.locale,
                stewardship_state=StewardshipState.DRAFT,
                current_version_id=None,
                published_version_id=None,
                aggregate_revision=AggregateRevision(0),
                created_at=created_at,
                created_by_principal_id=principal_id,
                updated_at=created_at,
                archived_at=None,
            )
        except ContentDomainError as exc:
            raise MigrationCandidateInvalid("content create request is invalid") from exc

        try:
            provenance = MigrationImportProvenanceV1(
                migration_batch_id=candidate.migration_batch_id,
                source_system=candidate.source_identity.source_system,
                source_resource_type=candidate.source_identity.source_resource_type,
                source_resource_id=candidate.source_identity.source_resource_id,
                source_version=candidate.source_version,
                source_digest_sha256=candidate.source_digest_sha256,
                mapping_id=candidate.mapping_id,
                mapping_version=candidate.mapping_version,
            )
        except InvalidMigrationImportProvenanceError as exc:
            raise MigrationImportProvenanceInvalid(str(exc)) from exc

        version = ContentVersion(
            version_id=version_id,
            tenant_id=execution_tenant_id,
            content_id=content_id,
            version_number=VersionNumber(1),
            parent_version_id=None,
            schema_id=SchemaId(candidate.schema_id),
            schema_version=SchemaVersion(candidate.schema_version),
            payload=domain_payload,
            origin=ContentOrigin.IMPORT,
            created_at=created_at,
            created_by_principal_id=principal_id,
        )
        built_refs = build_version_asset_refs(
            tenant_id=execution_tenant_id,
            content_id=content_id,
            version_id=version_id,
            created_at=created_at,
            items=association_items,
        )
        ensure_unique_asset_slots(built_refs)

        uow.contents.insert(content)
        uow.outbox.insert(
            content_created_outbox(
                tenant_id=execution_tenant_id,
                content_id=content_id.value,
                content_type=candidate.content_type,
                context=event_context,
                created_at=created_at,
            )
        )
        head = LockedContentHead(
            tenant_id=execution_tenant_id,
            content_id=content_id,
            aggregate_revision=AggregateRevision(0),
            current_version_id=None,
            current_version_number=None,
            published_version_id=None,
            stewardship_state=StewardshipState.DRAFT.value,
            content_type=candidate.content_type,
        )
        append_version_in_uow(
            uow,
            execution_tenant_id,
            AppendContentVersionCommand(
                expected_aggregate_revision=AggregateRevision(0),
                version=version,
                provenance=provenance,
                asset_refs=built_refs,
            ),
            now=created_at,
            event_context=event_context,
            head=head,
        )
        if prior is None:
            uow.migration_imports.insert_imported(
                MigrationImportRecord(
                    tenant_id=execution_tenant_id,
                    source_system=candidate.source_identity.source_system,
                    source_resource_type=candidate.source_identity.source_resource_type,
                    source_resource_id=candidate.source_identity.source_resource_id,
                    source_version=candidate.source_version,
                    source_digest_sha256=candidate.source_digest_sha256,
                    mapping_id=candidate.mapping_id,
                    mapping_version=candidate.mapping_version,
                    first_migration_batch_id=candidate.migration_batch_id,
                    last_migration_batch_id=candidate.migration_batch_id,
                    outcome=MIGRATION_OUTCOME_IMPORTED,
                    target_content_id=content_id.value,
                    target_version_id=version_id.value,
                    attempt_count=1,
                    first_attempt_at=created_at,
                    last_attempt_at=created_at,
                    completed_at=created_at,
                    failure_code=None,
                )
            )
        else:
            uow.migration_imports.mark_imported_from_failed(
                prior,
                target_content_id=content_id.value,
                target_version_id=version_id.value,
                migration_batch_id=candidate.migration_batch_id,
                completed_at=created_at,
            )
        return ImportMigratedContentResult(
            content_id=content_id,
            version_id=version_id,
            replayed=False,
        )

    def _record_failure(
        self,
        execution_tenant_id: UUID,
        candidate: MigrationContentCandidate,
        *,
        failure_code: str,
        now: datetime | None = None,
    ) -> None:
        """Persist FAILED evidence while outer source serialization remains held."""
        attempted_at = now if now is not None else datetime.now(UTC)
        identity = candidate.source_identity
        with self._uow_factory(execution_tenant_id) as uow:
            existing = uow.migration_imports.get(
                identity.source_system,
                identity.source_resource_type,
                identity.source_resource_id,
            )
            if existing is not None and existing.outcome == MIGRATION_OUTCOME_IMPORTED:
                return
            if existing is not None:
                if not _fingerprints_match(existing, candidate):
                    raise MigrationSourceConflict(
                        "source fingerprint conflicts with migration evidence"
                    )
                uow.migration_imports.update_failed_retry(
                    existing,
                    migration_batch_id=candidate.migration_batch_id,
                    failure_code=failure_code,
                    attempted_at=attempted_at,
                )
            else:
                uow.migration_imports.insert_failed(
                    MigrationImportRecord(
                        tenant_id=execution_tenant_id,
                        source_system=identity.source_system,
                        source_resource_type=identity.source_resource_type,
                        source_resource_id=identity.source_resource_id,
                        source_version=candidate.source_version,
                        source_digest_sha256=candidate.source_digest_sha256,
                        mapping_id=candidate.mapping_id,
                        mapping_version=candidate.mapping_version,
                        first_migration_batch_id=candidate.migration_batch_id,
                        last_migration_batch_id=candidate.migration_batch_id,
                        outcome=MIGRATION_OUTCOME_FAILED,
                        target_content_id=None,
                        target_version_id=None,
                        attempt_count=1,
                        first_attempt_at=attempted_at,
                        last_attempt_at=attempted_at,
                        completed_at=None,
                        failure_code=failure_code,
                    )
                )
            uow.commit()
