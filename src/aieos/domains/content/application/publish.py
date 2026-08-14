"""Authoritative Content publish orchestration."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from aieos.domains.content.application.errors import (
    AggregateRevisionConflict,
    ContentNotFound,
    ContentVersionAlreadyPublished,
    ContentVersionNotFound,
    IdempotencyKeyReused,
    PersistenceInvariantViolation,
    PublicationApprovalRequired,
    PublicationAssetValidationFailed,
    PublicationGovernanceRejected,
    PublicationNotAllowed,
    PublicationPayloadInvalid,
    PublicationSchemaUnavailable,
    PublicationVersionNotCurrent,
)
from aieos.domains.content.application.models import PublicationResult
from aieos.domains.content.application.ports import (
    CONTENT_PUBLISH,
    ContentUnitOfWork,
    ContentUnitOfWorkFactory,
    PublicationAssetValidationPort,
    PublicationAuthorizationPort,
    PublicationGovernancePort,
)
from aieos.domains.content.domain.errors import InvalidPayloadError, SchemaNotFoundError
from aieos.domains.content.domain.identities import (
    AggregateRevision,
    ContentId,
    ContentVersionId,
    PublicationId,
)
from aieos.domains.content.domain.publication import Publication
from aieos.domains.content.domain.review import ReviewDecisionCode
from aieos.domains.content.domain.schema import ContentSchemaRegistry
from aieos.domains.content.domain.states import StewardshipState
from aieos.platform.events.content_events import published_outbox
from aieos.platform.events.models import MutationEventContext
from aieos.platform.idempotency.hashing import fingerprint_material, hash_idempotency_key
from aieos.platform.idempotency.models import CONTENT_PUBLISH_V1, IdempotencyOutcome, IdempotencyScope


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(UTC)


def _publish_fingerprint(
    *,
    content_id: ContentId,
    version_id: ContentVersionId,
    expected_aggregate_revision: AggregateRevision,
) -> str:
    return fingerprint_material(
        {
            "content_id": str(content_id),
            "version_id": str(version_id),
            "expected_aggregate_revision": int(expected_aggregate_revision),
        }
    )


class PublishContentService:
    def __init__(
        self,
        uow_factory: ContentUnitOfWorkFactory,
        authorization: PublicationAuthorizationPort,
        governance: PublicationGovernancePort,
        asset_validation: PublicationAssetValidationPort,
        schema_registry: ContentSchemaRegistry,
        *,
        idempotency_retention: timedelta,
    ) -> None:
        if idempotency_retention.total_seconds() <= 0:
            raise ValueError("idempotency_retention must be a positive duration")
        self._uow_factory = uow_factory
        self._authorization = authorization
        self._governance = governance
        self._asset_validation = asset_validation
        self._schema_registry = schema_registry
        self._idempotency_retention = idempotency_retention

    def publish(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        *,
        content_id: ContentId,
        version_id: ContentVersionId,
        expected_aggregate_revision: AggregateRevision,
        idempotency_key: str,
        event_context: MutationEventContext,
        now: datetime | None = None,
    ) -> PublicationResult:
        self._authorization.authorize(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            content_id=content_id,
            version_id=version_id,
            capability=CONTENT_PUBLISH,
        )
        published_at = _now(now)
        fingerprint = _publish_fingerprint(
            content_id=content_id,
            version_id=version_id,
            expected_aggregate_revision=expected_aggregate_revision,
        )
        scope = IdempotencyScope(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            operation=CONTENT_PUBLISH_V1,
            key_sha256=hash_idempotency_key(idempotency_key),
        )
        with self._uow_factory(execution_tenant_id) as uow:
            uow.idempotency.acquire_scope(scope)
            existing = uow.idempotency.get(scope)
            if existing is not None:
                if existing.request_fingerprint_sha256 != fingerprint:
                    raise IdempotencyKeyReused("idempotency key already bound")
                return self._replay(uow, existing)
            result = self._publish_new(
                uow,
                execution_tenant_id,
                principal_id=principal_id,
                content_id=content_id,
                version_id=version_id,
                expected_aggregate_revision=expected_aggregate_revision,
                event_context=event_context,
                published_at=published_at,
            )
            uow.idempotency.insert(
                IdempotencyOutcome(
                    tenant_id=scope.tenant_id,
                    principal_id=scope.principal_id,
                    operation=scope.operation,
                    key_sha256=scope.key_sha256,
                    request_fingerprint_sha256=fingerprint,
                    result_content_id=content_id.value,
                    result_version_id=version_id.value,
                    result_review_decision_id=None,
                    result_publication_id=result.publication_id.value,
                    result_aggregate_revision=int(result.aggregate_revision),
                    created_at=published_at,
                    expires_at=published_at + self._idempotency_retention,
                )
            )
            uow.commit()
        return result

    def _replay(
        self, uow: ContentUnitOfWork, existing: IdempotencyOutcome
    ) -> PublicationResult:
        if existing.result_publication_id is None:
            raise PersistenceInvariantViolation(
                "idempotent publish outcome is missing Publication identity"
            )
        stored = uow.publications.get(PublicationId(existing.result_publication_id))
        if stored is None:
            raise PersistenceInvariantViolation(
                "idempotent publish outcome is not visible"
            )
        return PublicationResult(
            publication_id=stored.publication_id,
            content_id=stored.content_id,
            version_id=stored.version_id,
            approval_decision_id=stored.approval_decision_id,
            published_at=stored.published_at,
            published_version_id=stored.version_id,
            aggregate_revision=AggregateRevision(existing.result_aggregate_revision),
        )

    def _publish_new(
        self,
        uow: ContentUnitOfWork,
        execution_tenant_id: UUID,
        *,
        principal_id: UUID,
        content_id: ContentId,
        version_id: ContentVersionId,
        expected_aggregate_revision: AggregateRevision,
        event_context: MutationEventContext,
        published_at: datetime,
    ) -> PublicationResult:
        head = uow.contents.get_head_for_update(content_id)
        if head is None or head.tenant_id != execution_tenant_id:
            raise ContentNotFound("Content is not visible in the execution tenant")
        version = uow.versions.get(version_id)
        if version is None or version.content_id != content_id:
            raise ContentVersionNotFound(
                "ContentVersion is not visible for the requested Content"
            )
        if head.aggregate_revision != expected_aggregate_revision:
            raise AggregateRevisionConflict(
                "expected aggregate_revision does not match stored head"
            )
        if head.current_version_id is None or head.current_version_id != version_id:
            raise PublicationVersionNotCurrent(
                "requested version is not the current ContentVersion"
            )
        if head.stewardship_state == StewardshipState.ARCHIVED.value:
            raise PublicationNotAllowed(
                "publish is not allowed for archived Content"
            )
        if head.stewardship_state != StewardshipState.APPROVED.value:
            raise PublicationNotAllowed(
                "publish requires APPROVED stewardship state"
            )
        decision = uow.reviews.get_for_version(content_id, version_id)
        if decision is None or decision.decision is not ReviewDecisionCode.APPROVE:
            raise PublicationApprovalRequired(
                "exact-version APPROVE ReviewDecision is required"
            )
        if uow.publications.get_for_version(content_id, version_id) is not None:
            raise ContentVersionAlreadyPublished(
                "this ContentVersion already has a Publication"
            )
        if head.published_version_id == version_id:
            raise ContentVersionAlreadyPublished(
                "this ContentVersion is already the published pointer"
            )
        try:
            registered = self._schema_registry.get(
                str(version.schema_id), int(version.schema_version)
            )
        except SchemaNotFoundError as exc:
            raise PublicationSchemaUnavailable(
                "stored schema reader is unavailable"
            ) from exc
        if registered.content_type != head.content_type:
            raise PublicationSchemaUnavailable(
                "stored schema reader does not match Content content_type"
            )
        try:
            registered.validate(dict(version.payload.body))
        except InvalidPayloadError as exc:
            raise PublicationPayloadInvalid(
                "stored ContentVersion payload failed schema validation"
            ) from exc
        try:
            self._asset_validation.validate(
                tenant_id=execution_tenant_id,
                content_id=content_id,
                version_id=version_id,
            )
        except PublicationAssetValidationFailed:
            raise
        try:
            self._governance.evaluate(
                tenant_id=execution_tenant_id,
                content_id=content_id,
                version_id=version_id,
            )
        except PublicationGovernanceRejected:
            raise
        publication = Publication(
            publication_id=PublicationId.generate(),
            tenant_id=execution_tenant_id,
            content_id=content_id,
            version_id=version_id,
            approval_decision_id=decision.review_decision_id,
            published_by_principal_id=principal_id,
            effective_actor_id=principal_id,
            published_at=published_at,
            correlation_id=event_context.correlation_id,
        )
        uow.publications.insert(publication)
        resulting = uow.contents.set_published_version(
            content_id=content_id,
            tenant_id=execution_tenant_id,
            version_id=version_id,
            expected_revision=expected_aggregate_revision,
            updated_at=published_at,
        )
        if resulting is None:
            raise AggregateRevisionConflict(
                "aggregate head changed before publish could commit"
            )
        uow.outbox.insert(
            published_outbox(
                tenant_id=execution_tenant_id,
                content_id=content_id.value,
                published_version_id=version_id.value,
                publication_id=publication.publication_id.value,
                aggregate_revision=int(resulting),
                context=event_context,
                created_at=published_at,
            )
        )
        return PublicationResult(
            publication_id=publication.publication_id,
            content_id=content_id,
            version_id=version_id,
            approval_decision_id=decision.review_decision_id,
            published_at=published_at,
            published_version_id=version_id,
            aggregate_revision=resulting,
        )
