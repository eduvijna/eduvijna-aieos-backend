"""PED-I10B6 Asset transactional security audit semantics."""

from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from uuid import uuid7

import pytest

from aieos.domains.asset.application.audit import (
    AssetMutationAuditProvenance,
    insert_required_asset_audit,
)
from aieos.domains.asset.application.ingest import PreparedBlob
from aieos.domains.asset.application.mutation_errors import (
    AssetConflict,
    AssetForbidden,
    AssetPersistenceFailed,
    AssetTransitionRejected,
)
from aieos.domains.asset.application.mutations import AssetMutationService
from aieos.domains.asset.domain.identities import (
    AssetAggregateRevision,
    AssetId,
    AssetRevisionId,
    AssetRevisionNumber,
)
from aieos.domains.asset.domain.resource_type import AssetResourceType
from aieos.platform.events.models import MutationEventContext
from aieos.platform.resources import ResourceRef
from aieos.platform.security.audit import (
    InvalidSecurityAuditError,
    SecurityAuditAction,
    SecurityAuditExecutionChannel,
    build_security_mutation_audit_record,
)
from aieos.platform.security.context import AuthorizationUnavailableError
from tests.domains.asset.application.fakes import InMemoryBlobStore
from tests.domains.asset.application.mutation_fakes import (
    AllowAssetMutationAuthorization,
    DenyAssetMutationAuthorization,
    InMemoryAssetUnitOfWorkFactory,
    UnavailableAssetMutationAuthorization,
    asset_audit_kwargs,
)
from tests.domains.asset.application.test_ped_i10b5_mutations import InspectProbe

pytestmark = pytest.mark.ped_i10b6

FIXED = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
PAYLOAD = b"asset-bytes-v1"
ZERO = AssetAggregateRevision(0)


def _clock() -> datetime:
    return FIXED


def _service(factory=None, blobs=None, auth=None):
    factory = factory or InMemoryAssetUnitOfWorkFactory()
    blobs = blobs or InMemoryBlobStore()
    auth = auth or AllowAssetMutationAuthorization()
    return (
        AssetMutationService(factory, blobs, auth, clock=_clock),
        factory,
        blobs,
    )


def _prepared(blobs: InMemoryBlobStore) -> PreparedBlob:
    info = blobs.create(storage_key=uuid7().hex, source=BytesIO(PAYLOAD))
    return PreparedBlob(
        storage_key=info.storage_key,
        byte_size=info.byte_size,
        sha256=info.sha256,
    )


def _create(service):
    tenant, principal, asset_id = uuid7(), uuid7(), AssetId.generate()
    asset = service.create_asset(
        tenant_id=tenant,
        principal_id=principal,
        asset_id=asset_id,
        resource_type=AssetResourceType.IMAGE,
        **asset_audit_kwargs(principal),
    )
    return asset, tenant, principal


def _register(service, blobs, tenant, principal, asset_id):
    return service.register_revision(
        tenant_id=tenant,
        principal_id=principal,
        asset_id=asset_id,
        asset_revision_id=AssetRevisionId.generate(),
        prepared=_prepared(blobs),
        media_type="image/png",
        **asset_audit_kwargs(principal),
    )


class TestSuccessfulMutationAudit:
    def test_create_register_activate_lifecycle_quarantine_safety(self) -> None:
        service, factory, blobs = _service()
        tenant, principal = uuid7(), uuid7()
        audit = asset_audit_kwargs(principal)
        asset = service.create_asset(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=AssetId.generate(),
            resource_type=AssetResourceType.IMAGE,
            **audit,
        )
        registered = service.register_revision(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            asset_revision_id=AssetRevisionId.generate(),
            prepared=_prepared(blobs),
            media_type="image/png",
            **audit,
        )
        service.mark_safety_passed(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            asset_revision_id=registered.revision.asset_revision_id,
            expected_aggregate_revision=ZERO,
            **audit,
        )
        service.activate_revision(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            resource_type=AssetResourceType.IMAGE,
            revision_number=registered.revision.revision_number,
            expected_aggregate_revision=AssetAggregateRevision(1),
            **audit,
        )
        service.withdraw_asset(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            expected_aggregate_revision=AssetAggregateRevision(2),
            **audit,
        )
        service.restore_asset(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            expected_aggregate_revision=AssetAggregateRevision(3),
            **audit,
        )
        service.quarantine_asset(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            expected_aggregate_revision=AssetAggregateRevision(4),
            **audit,
        )
        service.clear_quarantine(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            expected_aggregate_revision=AssetAggregateRevision(5),
            **audit,
        )
        second = service.register_revision(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            asset_revision_id=AssetRevisionId.generate(),
            prepared=_prepared(blobs),
            media_type="image/png",
            **audit,
        )
        service.delete_asset(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            expected_aggregate_revision=AssetAggregateRevision(6),
            **audit,
        )
        service.mark_safety_failed(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            asset_revision_id=second.revision.asset_revision_id,
            expected_aggregate_revision=AssetAggregateRevision(7),
            **audit,
        )
        records = factory.catalog.audit_records
        actions = [row.action.value for row in records]
        assert actions == [
            "asset.create",
            "asset.revision.register",
            "asset.safety.pass",
            "asset.revision.activate",
            "asset.lifecycle.withdraw",
            "asset.lifecycle.restore",
            "asset.quarantine.set",
            "asset.quarantine.clear",
            "asset.revision.register",
            "asset.lifecycle.delete",
            "asset.safety.fail",
        ]
        create = records[0]
        assert create.tenant_id == tenant
        assert create.primary_resource_ref.resource_type == "asset.image"
        assert create.primary_resource_ref.resource_id == asset.asset_id.value
        assert create.primary_resource_ref.resource_revision is None
        assert create.resource_revision_before is None
        assert create.resource_revision_after == 0
        assert create.related_resource_refs == ()
        assert create.occurred_at == FIXED
        assert create.occurred_at.tzinfo is UTC
        assert create.audit_context.executing_principal_id == principal
        assert create.audit_context.initiating_principal_id == principal
        assert create.audit_context.correlation_id == audit["mutation_event_context"].correlation_id

        register = records[1]
        assert register.resource_revision_before == 0
        assert register.resource_revision_after == 0
        assert register.primary_resource_ref.resource_revision is None
        assert len(register.related_resource_refs) == 1
        related = register.related_resource_refs[0]
        assert related.resource_type == "asset.image"
        assert related.resource_id == asset.asset_id.value
        assert related.resource_revision == int(registered.revision.revision_number)

        activate = records[3]
        assert activate.resource_revision_before == 1
        assert activate.resource_revision_after == 2
        assert activate.related_resource_refs[0].resource_revision == int(
            registered.revision.revision_number
        )

        withdraw = records[4]
        assert withdraw.related_resource_refs == ()
        assert withdraw.resource_revision_before == 2
        assert withdraw.resource_revision_after == 3


class TestNoAuditOnNonMutation:
    def test_create_and_register_replay(self) -> None:
        service, factory, blobs = _service()
        asset, tenant, principal = _create(service)
        audit = asset_audit_kwargs(principal)
        replayed = service.create_asset(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            resource_type=AssetResourceType.IMAGE,
            **audit,
        )
        assert replayed.asset_id == asset.asset_id
        prepared = _prepared(blobs)
        revision_id = AssetRevisionId.generate()
        first = service.register_revision(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            asset_revision_id=revision_id,
            prepared=prepared,
            media_type="image/png",
            **audit,
        )
        second = service.register_revision(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            asset_revision_id=revision_id,
            prepared=prepared,
            media_type="image/png",
            **audit,
        )
        assert first.revision.asset_revision_id == second.revision.asset_revision_id
        assert [row.action.value for row in factory.catalog.audit_records] == [
            "asset.create",
            "asset.revision.register",
        ]

    def test_stale_invalid_auth_and_activation_failures(self) -> None:
        service, factory, blobs = _service()
        asset, tenant, principal = _create(service)
        audit = asset_audit_kwargs(principal)
        registered = _register(service, blobs, tenant, principal, asset.asset_id)
        service.mark_safety_passed(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            asset_revision_id=registered.revision.asset_revision_id,
            expected_aggregate_revision=ZERO,
            **audit,
        )
        before = len(factory.catalog.audit_records)
        with pytest.raises(AssetConflict):
            service.withdraw_asset(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset.asset_id,
                expected_aggregate_revision=AssetAggregateRevision(9),
                **audit,
            )
        with pytest.raises(AssetTransitionRejected):
            service.restore_asset(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset.asset_id,
                expected_aggregate_revision=AssetAggregateRevision(1),
                **audit,
            )
        with pytest.raises(AssetTransitionRejected):
            service.clear_quarantine(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset.asset_id,
                expected_aggregate_revision=AssetAggregateRevision(1),
                **audit,
            )
        with pytest.raises(AssetTransitionRejected):
            service.mark_safety_passed(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset.asset_id,
                asset_revision_id=registered.revision.asset_revision_id,
                expected_aggregate_revision=AssetAggregateRevision(1),
                **audit,
            )
        denied = AssetMutationService(
            factory, blobs, DenyAssetMutationAuthorization(), clock=_clock
        )
        with pytest.raises(AssetForbidden):
            denied.withdraw_asset(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset.asset_id,
                expected_aggregate_revision=ZERO,
                **audit,
            )
        unavailable = AssetMutationService(
            factory, blobs, UnavailableAssetMutationAuthorization(), clock=_clock
        )
        with pytest.raises(AuthorizationUnavailableError):
            unavailable.withdraw_asset(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset.asset_id,
                expected_aggregate_revision=ZERO,
                **audit,
            )
        hidden = AssetMutationService(
            InMemoryAssetUnitOfWorkFactory(),
            blobs,
            AllowAssetMutationAuthorization(),
            clock=_clock,
        )
        other_tenant = uuid7()
        with pytest.raises(Exception):
            hidden.withdraw_asset(
                tenant_id=other_tenant,
                principal_id=principal,
                asset_id=asset.asset_id,
                expected_aggregate_revision=ZERO,
                **asset_audit_kwargs(principal),
            )
        assert len(factory.catalog.audit_records) == before

    def test_activation_failures_do_not_audit(self) -> None:
        writer_factory = InMemoryAssetUnitOfWorkFactory()
        blobs = InMemoryBlobStore()
        writer = AssetMutationService(
            writer_factory, blobs, AllowAssetMutationAuthorization(), clock=_clock
        )
        asset, tenant, principal = _create(writer)
        registered = _register(writer, blobs, tenant, principal, asset.asset_id)
        writer.mark_safety_passed(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset.asset_id,
            asset_revision_id=registered.revision.asset_revision_id,
            expected_aggregate_revision=ZERO,
            **asset_audit_kwargs(principal),
        )
        before = len(writer_factory.catalog.audit_records)
        probe = InspectProbe(blobs)
        service = AssetMutationService(
            writer_factory, probe, AllowAssetMutationAuthorization(), clock=_clock
        )
        with pytest.raises(AssetConflict):
            service.activate_revision(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset.asset_id,
                resource_type=AssetResourceType.IMAGE,
                revision_number=registered.revision.revision_number,
                expected_aggregate_revision=AssetAggregateRevision(9),
                **asset_audit_kwargs(principal),
            )
        assert probe.calls == []
        assert len(writer_factory.catalog.audit_records) == before


class TestAuditInsertFailureRollsBack:
    def test_create_and_register_rollback(self) -> None:
        factory = InMemoryAssetUnitOfWorkFactory()
        blobs = InMemoryBlobStore()
        service = AssetMutationService(
            factory, blobs, AllowAssetMutationAuthorization(), clock=_clock
        )
        tenant, principal, asset_id = uuid7(), uuid7(), AssetId.generate()
        factory.catalog.audit_insert_error = AssetPersistenceFailed(
            "asset persistence operation failed"
        )
        with pytest.raises(AssetPersistenceFailed):
            service.create_asset(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset_id,
                resource_type=AssetResourceType.IMAGE,
                **asset_audit_kwargs(principal),
            )
        assert factory.catalog.assets == {}
        assert factory.catalog.audit_records == []
        factory.catalog.audit_insert_error = None
        asset = service.create_asset(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset_id,
            resource_type=AssetResourceType.IMAGE,
            **asset_audit_kwargs(principal),
        )
        factory.catalog.audit_insert_error = AssetPersistenceFailed(
            "asset persistence operation failed"
        )
        with pytest.raises(AssetPersistenceFailed):
            service.register_revision(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=asset.asset_id,
                asset_revision_id=AssetRevisionId.generate(),
                prepared=_prepared(blobs),
                media_type="image/png",
                **asset_audit_kwargs(principal),
            )
        assert factory.catalog.revisions == {}
        assert [row.action.value for row in factory.catalog.audit_records] == [
            "asset.create"
        ]


class TestPlatformAuditModel:
    def test_content_semantics_unchanged(self) -> None:
        event = MutationEventContext(
            correlation_id=uuid7(),
            causation_id=uuid7(),
            actor_principal_id=uuid7(),
            effective_actor_id=uuid7(),
        )
        record = build_security_mutation_audit_record(
            tenant_id=uuid7(),
            action=SecurityAuditAction.CONTENT_CREATE,
            primary_resource_ref=ResourceRef("content.content", uuid7(), 0),
            resource_revision_before=None,
            resource_revision_after=0,
            related_resource_refs=(),
            mutation_event_context=event,
            executing_principal_id=uuid7(),
            execution_channel=SecurityAuditExecutionChannel.API,
            occurred_at=FIXED,
        )
        assert record.primary_resource_ref.resource_revision == 0
        with pytest.raises(InvalidSecurityAuditError):
            build_security_mutation_audit_record(
                tenant_id=uuid7(),
                action=SecurityAuditAction.CONTENT_CREATE,
                primary_resource_ref=ResourceRef("content.content", uuid7(), None),
                resource_revision_before=None,
                resource_revision_after=0,
                related_resource_refs=(),
                mutation_event_context=event,
                executing_principal_id=uuid7(),
                execution_channel=SecurityAuditExecutionChannel.API,
                occurred_at=FIXED,
            )

    def test_asset_primary_revision_none_required(self) -> None:
        event = MutationEventContext(
            correlation_id=uuid7(),
            causation_id=uuid7(),
            actor_principal_id=uuid7(),
            effective_actor_id=uuid7(),
        )
        asset_id = uuid7()
        with pytest.raises(InvalidSecurityAuditError):
            build_security_mutation_audit_record(
                tenant_id=uuid7(),
                action=SecurityAuditAction.ASSET_CREATE,
                primary_resource_ref=ResourceRef("asset.image", asset_id, 0),
                resource_revision_before=None,
                resource_revision_after=0,
                related_resource_refs=(),
                mutation_event_context=event,
                executing_principal_id=uuid7(),
                execution_channel=SecurityAuditExecutionChannel.API,
                occurred_at=FIXED,
            )
        accepted = build_security_mutation_audit_record(
            tenant_id=uuid7(),
            action=SecurityAuditAction.ASSET_CREATE,
            primary_resource_ref=ResourceRef("asset.image", asset_id, None),
            resource_revision_before=None,
            resource_revision_after=0,
            related_resource_refs=(),
            mutation_event_context=event,
            executing_principal_id=uuid7(),
            execution_channel=SecurityAuditExecutionChannel.API,
            occurred_at=FIXED,
        )
        assert accepted.primary_resource_ref.resource_revision is None

    def test_asset_register_and_increment_pairs(self) -> None:
        event = MutationEventContext(
            correlation_id=uuid7(),
            causation_id=uuid7(),
            actor_principal_id=uuid7(),
            effective_actor_id=uuid7(),
        )
        asset_id = uuid7()
        primary = ResourceRef("asset.image", asset_id, None)
        related = (ResourceRef("asset.image", asset_id, 1),)
        assert build_security_mutation_audit_record(
            tenant_id=uuid7(),
            action=SecurityAuditAction.ASSET_REVISION_REGISTER,
            primary_resource_ref=primary,
            resource_revision_before=3,
            resource_revision_after=3,
            related_resource_refs=related,
            mutation_event_context=event,
            executing_principal_id=uuid7(),
            execution_channel=SecurityAuditExecutionChannel.API,
            occurred_at=FIXED,
        )
        with pytest.raises(InvalidSecurityAuditError):
            build_security_mutation_audit_record(
                tenant_id=uuid7(),
                action=SecurityAuditAction.ASSET_REVISION_REGISTER,
                primary_resource_ref=primary,
                resource_revision_before=3,
                resource_revision_after=4,
                related_resource_refs=related,
                mutation_event_context=event,
                executing_principal_id=uuid7(),
                execution_channel=SecurityAuditExecutionChannel.API,
                occurred_at=FIXED,
            )
        assert build_security_mutation_audit_record(
            tenant_id=uuid7(),
            action=SecurityAuditAction.ASSET_REVISION_ACTIVATE,
            primary_resource_ref=primary,
            resource_revision_before=3,
            resource_revision_after=4,
            related_resource_refs=related,
            mutation_event_context=event,
            executing_principal_id=uuid7(),
            execution_channel=SecurityAuditExecutionChannel.API,
            occurred_at=FIXED,
        )
        with pytest.raises(InvalidSecurityAuditError):
            build_security_mutation_audit_record(
                tenant_id=uuid7(),
                action=SecurityAuditAction.ASSET_REVISION_ACTIVATE,
                primary_resource_ref=primary,
                resource_revision_before=3,
                resource_revision_after=3,
                related_resource_refs=related,
                mutation_event_context=event,
                executing_principal_id=uuid7(),
                execution_channel=SecurityAuditExecutionChannel.API,
                occurred_at=FIXED,
            )

    def test_helper_requires_pinned_related_revision(self) -> None:
        factory = InMemoryAssetUnitOfWorkFactory()
        uow = factory(uuid7())
        with uow:
            with pytest.raises(InvalidSecurityAuditError):
                insert_required_asset_audit(
                    uow,
                    tenant_id=uuid7(),
                    action=SecurityAuditAction.ASSET_REVISION_REGISTER,
                    resource_type=AssetResourceType.IMAGE,
                    asset_id=AssetId.generate(),
                    resource_revision_before=0,
                    resource_revision_after=0,
                    mutation_event_context=MutationEventContext(
                        correlation_id=uuid7(),
                        causation_id=uuid7(),
                        actor_principal_id=uuid7(),
                        effective_actor_id=uuid7(),
                    ),
                    audit_provenance=AssetMutationAuditProvenance(
                        executing_principal_id=uuid7(),
                        execution_channel=SecurityAuditExecutionChannel.API,
                    ),
                    occurred_at=FIXED,
                )
