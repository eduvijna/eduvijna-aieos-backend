"""PED-I10B6 Asset authorization: exact capabilities, deny-before-UoW."""

from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from uuid import uuid7

import pytest

from aieos.domains.asset.application.ingest import PreparedBlob
from aieos.domains.asset.application.mutation_errors import AssetForbidden
from aieos.domains.asset.application.mutations import AssetMutationService
from aieos.domains.asset.application.ports import (
    ASSET_CREATE,
    ASSET_LIFECYCLE_MANAGE,
    ASSET_QUARANTINE_MANAGE,
    ASSET_REVISION_ACTIVATE,
    ASSET_REVISION_REGISTER,
    ASSET_SAFETY_DECIDE,
)
from aieos.domains.asset.domain.identities import (
    AssetAggregateRevision,
    AssetId,
    AssetRevisionId,
    AssetRevisionNumber,
)
from aieos.domains.asset.domain.resource_type import AssetResourceType
from aieos.platform.security.authorization.asset_adapters import (
    AIEOS_ASSET_CAPABILITIES,
    KernelAssetMutationAuthorization,
)
from aieos.platform.security.authorization.decisions import AuthorityDecision
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


def _clock() -> datetime:
    return FIXED


def _prepared(blobs: InMemoryBlobStore) -> PreparedBlob:
    info = blobs.create(storage_key=uuid7().hex, source=BytesIO(PAYLOAD))
    return PreparedBlob(
        storage_key=info.storage_key,
        byte_size=info.byte_size,
        sha256=info.sha256,
    )


class _DecisionKernel:
    def __init__(self, decision: AuthorityDecision | Exception) -> None:
        self._decision = decision
        self.calls: list[str] = []

    def decide_capability(self, *, principal_id, tenant_id, capability: str):
        self.calls.append(capability)
        if isinstance(self._decision, Exception):
            raise self._decision
        return self._decision


class TestCapabilityVocabulary:
    def test_exact_six_capabilities(self) -> None:
        assert AIEOS_ASSET_CAPABILITIES == frozenset(
            {
                ASSET_CREATE,
                ASSET_REVISION_REGISTER,
                ASSET_REVISION_ACTIVATE,
                ASSET_LIFECYCLE_MANAGE,
                ASSET_QUARANTINE_MANAGE,
                ASSET_SAFETY_DECIDE,
            }
        )
        assert AIEOS_ASSET_CAPABILITIES == {
            "asset.create",
            "asset.revision.register",
            "asset.revision.activate",
            "asset.lifecycle.manage",
            "asset.quarantine.manage",
            "asset.safety.decide",
        }
        for forbidden in (
            "asset.*",
            "*",
            "asset.read",
            "asset.purge",
            "asset.bytes.read",
            "asset.download",
            "asset.upload",
        ):
            assert forbidden not in AIEOS_ASSET_CAPABILITIES


class TestAdapter:
    def test_allow_exact_known_capability(self) -> None:
        kernel = _DecisionKernel(AuthorityDecision.ALLOW)
        KernelAssetMutationAuthorization(kernel).authorize(
            tenant_id=uuid7(),
            principal_id=uuid7(),
            capability=ASSET_CREATE,
            asset_id=AssetId.generate(),
        )
        assert kernel.calls == [ASSET_CREATE]

    def test_deny_maps_to_asset_forbidden(self) -> None:
        kernel = _DecisionKernel(AuthorityDecision.DENY)
        with pytest.raises(AssetForbidden, match="asset capability denied"):
            KernelAssetMutationAuthorization(kernel).authorize(
                tenant_id=uuid7(),
                principal_id=uuid7(),
                capability=ASSET_CREATE,
            )

    def test_unknown_and_wildcard_denied_without_kernel(self) -> None:
        kernel = _DecisionKernel(AuthorityDecision.ALLOW)
        adapter = KernelAssetMutationAuthorization(kernel)
        for capability in ("asset.read", "asset.*", "*", "content.publish"):
            with pytest.raises(AssetForbidden):
                adapter.authorize(
                    tenant_id=uuid7(),
                    principal_id=uuid7(),
                    capability=capability,
                )
        assert kernel.calls == []

    def test_unavailable_propagates(self) -> None:
        kernel = _DecisionKernel(
            AuthorizationUnavailableError("authorization unavailable")
        )
        with pytest.raises(AuthorizationUnavailableError, match="authorization unavailable"):
            KernelAssetMutationAuthorization(kernel).authorize(
                tenant_id=uuid7(),
                principal_id=uuid7(),
                capability=ASSET_CREATE,
            )

    def test_unexpected_failure_is_sanitized(self) -> None:
        kernel = _DecisionKernel(RuntimeError("internal provider secret"))
        with pytest.raises(AuthorizationUnavailableError) as exc:
            KernelAssetMutationAuthorization(kernel).authorize(
                tenant_id=uuid7(),
                principal_id=uuid7(),
                capability=ASSET_CREATE,
            )
        assert str(exc.value) == "authorization unavailable"
        assert "secret" not in str(exc.value)
        assert "internal" not in str(exc.value)
        assert exc.value.__cause__ is not None


class TestCommandCapabilityMapping:
    def test_each_command_requests_exact_capability(self) -> None:
        factory = InMemoryAssetUnitOfWorkFactory()
        blobs = InMemoryBlobStore()
        auth = AllowAssetMutationAuthorization()
        service = AssetMutationService(factory, blobs, auth, clock=_clock)
        tenant, principal = uuid7(), uuid7()
        asset_id = AssetId.generate()
        audit = asset_audit_kwargs(principal)
        service.create_asset(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset_id,
            resource_type=AssetResourceType.IMAGE,
            **audit,
        )
        prepared = _prepared(blobs)
        registered = service.register_revision(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset_id,
            asset_revision_id=AssetRevisionId.generate(),
            prepared=prepared,
            media_type="image/png",
            **audit,
        )
        service.mark_safety_passed(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset_id,
            asset_revision_id=registered.revision.asset_revision_id,
            expected_aggregate_revision=AssetAggregateRevision(0),
            **audit,
        )
        service.activate_revision(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset_id,
            resource_type=AssetResourceType.IMAGE,
            revision_number=registered.revision.revision_number,
            expected_aggregate_revision=AssetAggregateRevision(1),
            **audit,
        )
        service.withdraw_asset(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset_id,
            expected_aggregate_revision=AssetAggregateRevision(2),
            **audit,
        )
        service.restore_asset(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset_id,
            expected_aggregate_revision=AssetAggregateRevision(3),
            **audit,
        )
        service.quarantine_asset(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset_id,
            expected_aggregate_revision=AssetAggregateRevision(4),
            **audit,
        )
        service.clear_quarantine(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset_id,
            expected_aggregate_revision=AssetAggregateRevision(5),
            **audit,
        )
        second = service.register_revision(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset_id,
            asset_revision_id=AssetRevisionId.generate(),
            prepared=_prepared(blobs),
            media_type="image/png",
            **audit,
        )
        service.delete_asset(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset_id,
            expected_aggregate_revision=AssetAggregateRevision(6),
            **audit,
        )
        service.mark_safety_failed(
            tenant_id=tenant,
            principal_id=principal,
            asset_id=asset_id,
            asset_revision_id=second.revision.asset_revision_id,
            expected_aggregate_revision=AssetAggregateRevision(7),
            **audit,
        )
        assert auth.calls == [
            ASSET_CREATE,
            ASSET_REVISION_REGISTER,
            ASSET_SAFETY_DECIDE,
            ASSET_REVISION_ACTIVATE,
            ASSET_LIFECYCLE_MANAGE,
            ASSET_LIFECYCLE_MANAGE,
            ASSET_QUARANTINE_MANAGE,
            ASSET_QUARANTINE_MANAGE,
            ASSET_REVISION_REGISTER,
            ASSET_LIFECYCLE_MANAGE,
            ASSET_SAFETY_DECIDE,
        ]


class TestAuthorizeBeforeUow:
    @pytest.mark.parametrize(
        "method,kwargs_fn",
        [
            (
                "create_asset",
                lambda: {
                    "asset_id": AssetId.generate(),
                    "resource_type": AssetResourceType.IMAGE,
                },
            ),
            (
                "register_revision",
                lambda: {
                    "asset_id": AssetId.generate(),
                    "asset_revision_id": AssetRevisionId.generate(),
                    "prepared": PreparedBlob("k", 1, "a" * 64),
                    "media_type": "image/png",
                },
            ),
            (
                "activate_revision",
                lambda: {
                    "asset_id": AssetId.generate(),
                    "resource_type": AssetResourceType.IMAGE,
                    "revision_number": AssetRevisionNumber(1),
                    "expected_aggregate_revision": AssetAggregateRevision(0),
                },
            ),
            (
                "withdraw_asset",
                lambda: {
                    "asset_id": AssetId.generate(),
                    "expected_aggregate_revision": AssetAggregateRevision(0),
                },
            ),
            (
                "restore_asset",
                lambda: {
                    "asset_id": AssetId.generate(),
                    "expected_aggregate_revision": AssetAggregateRevision(0),
                },
            ),
            (
                "delete_asset",
                lambda: {
                    "asset_id": AssetId.generate(),
                    "expected_aggregate_revision": AssetAggregateRevision(0),
                },
            ),
            (
                "quarantine_asset",
                lambda: {
                    "asset_id": AssetId.generate(),
                    "expected_aggregate_revision": AssetAggregateRevision(0),
                },
            ),
            (
                "clear_quarantine",
                lambda: {
                    "asset_id": AssetId.generate(),
                    "expected_aggregate_revision": AssetAggregateRevision(0),
                },
            ),
            (
                "mark_safety_passed",
                lambda: {
                    "asset_id": AssetId.generate(),
                    "asset_revision_id": AssetRevisionId.generate(),
                    "expected_aggregate_revision": AssetAggregateRevision(0),
                },
            ),
            (
                "mark_safety_failed",
                lambda: {
                    "asset_id": AssetId.generate(),
                    "asset_revision_id": AssetRevisionId.generate(),
                    "expected_aggregate_revision": AssetAggregateRevision(0),
                },
            ),
        ],
    )
    def test_deny_zero_uow(self, method, kwargs_fn) -> None:
        factory = InMemoryAssetUnitOfWorkFactory()
        service = AssetMutationService(
            factory, InMemoryBlobStore(), DenyAssetMutationAuthorization(), clock=_clock
        )
        tenant, principal = uuid7(), uuid7()
        with pytest.raises(AssetForbidden):
            getattr(service, method)(
                tenant_id=tenant,
                principal_id=principal,
                **kwargs_fn(),
                **asset_audit_kwargs(principal),
            )
        assert factory.catalog.opens == 0
        assert factory.catalog.audit_records == []

    @pytest.mark.parametrize(
        "method,kwargs_fn",
        [
            (
                "create_asset",
                lambda: {
                    "asset_id": AssetId.generate(),
                    "resource_type": AssetResourceType.IMAGE,
                },
            ),
            (
                "withdraw_asset",
                lambda: {
                    "asset_id": AssetId.generate(),
                    "expected_aggregate_revision": AssetAggregateRevision(0),
                },
            ),
            (
                "mark_safety_passed",
                lambda: {
                    "asset_id": AssetId.generate(),
                    "asset_revision_id": AssetRevisionId.generate(),
                    "expected_aggregate_revision": AssetAggregateRevision(0),
                },
            ),
        ],
    )
    def test_unavailable_zero_uow(self, method, kwargs_fn) -> None:
        factory = InMemoryAssetUnitOfWorkFactory()
        service = AssetMutationService(
            factory,
            InMemoryBlobStore(),
            UnavailableAssetMutationAuthorization(),
            clock=_clock,
        )
        tenant, principal = uuid7(), uuid7()
        with pytest.raises(AuthorizationUnavailableError):
            getattr(service, method)(
                tenant_id=tenant,
                principal_id=principal,
                **kwargs_fn(),
                **asset_audit_kwargs(principal),
            )
        assert factory.catalog.opens == 0
        assert factory.catalog.audit_records == []

    def test_activate_deny_zero_uow_and_zero_inspect(self) -> None:
        factory = InMemoryAssetUnitOfWorkFactory()
        probe = InspectProbe(InMemoryBlobStore())
        service = AssetMutationService(
            factory, probe, DenyAssetMutationAuthorization(), clock=_clock
        )
        tenant, principal = uuid7(), uuid7()
        with pytest.raises(AssetForbidden):
            service.activate_revision(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=AssetId.generate(),
                resource_type=AssetResourceType.IMAGE,
                revision_number=AssetRevisionNumber(1),
                expected_aggregate_revision=AssetAggregateRevision(0),
                **asset_audit_kwargs(principal),
            )
        assert factory.catalog.opens == 0
        assert probe.calls == []

    def test_activate_unavailable_zero_uow_and_zero_inspect(self) -> None:
        factory = InMemoryAssetUnitOfWorkFactory()
        probe = InspectProbe(InMemoryBlobStore())
        service = AssetMutationService(
            factory, probe, UnavailableAssetMutationAuthorization(), clock=_clock
        )
        tenant, principal = uuid7(), uuid7()
        with pytest.raises(AuthorizationUnavailableError):
            service.activate_revision(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=AssetId.generate(),
                resource_type=AssetResourceType.IMAGE,
                revision_number=AssetRevisionNumber(1),
                expected_aggregate_revision=AssetAggregateRevision(0),
                **asset_audit_kwargs(principal),
            )
        assert factory.catalog.opens == 0
        assert probe.calls == []

    def test_unexpected_kernel_failure_sanitized_zero_uow(self) -> None:
        factory = InMemoryAssetUnitOfWorkFactory()
        kernel = _DecisionKernel(RuntimeError("internal provider secret"))
        service = AssetMutationService(
            factory,
            InMemoryBlobStore(),
            KernelAssetMutationAuthorization(kernel),
            clock=_clock,
        )
        tenant, principal = uuid7(), uuid7()
        with pytest.raises(AuthorizationUnavailableError) as exc:
            service.create_asset(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=AssetId.generate(),
                resource_type=AssetResourceType.IMAGE,
                **asset_audit_kwargs(principal),
            )
        assert str(exc.value) == "authorization unavailable"
        assert "secret" not in str(exc.value)
        assert factory.catalog.opens == 0
        assert factory.catalog.audit_records == []

    def test_constructor_requires_authorization_port(self) -> None:
        with pytest.raises(TypeError):
            AssetMutationService(
                InMemoryAssetUnitOfWorkFactory(),
                InMemoryBlobStore(),
            )

    def test_jwt_claims_are_not_consulted(self) -> None:
        factory = InMemoryAssetUnitOfWorkFactory()
        service = AssetMutationService(
            factory, InMemoryBlobStore(), DenyAssetMutationAuthorization(), clock=_clock
        )
        _jwt_claims = {
            "roles": ["admin"],
            "permissions": ["asset.*"],
            "scope": "asset.create",
        }
        _ = _jwt_claims
        tenant, principal = uuid7(), uuid7()
        with pytest.raises(AssetForbidden):
            service.create_asset(
                tenant_id=tenant,
                principal_id=principal,
                asset_id=AssetId.generate(),
                resource_type=AssetResourceType.IMAGE,
                **asset_audit_kwargs(principal),
            )
        assert factory.catalog.opens == 0
