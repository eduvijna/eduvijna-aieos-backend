"""PED-I10A Asset authority Content adapter unit tests."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4, uuid7

import pytest

from aieos.domains.content.application.asset_authority_adapters import (
    AssetAuthorityCurrentGovernanceAdapter,
    AssetAuthorityReferenceValidationAdapter,
)
from aieos.domains.content.application.errors import (
    AssetReferenceValidationFailed,
    PublicationAssetValidationFailed,
)
from aieos.domains.content.domain.identities import ContentId, ContentVersionId
from aieos.domains.content.domain.version_asset_ref import VersionAssetRef
from aieos.platform.governance.errors import GovernanceUnavailableError
from aieos.platform.resources import ResourceRef
from aieos.platform.resources.asset_use import (
    AssetUseAssessment,
    AssetUseRejectionReason,
)
from tests.platform.governance.helpers import RecordingAssetUseAuthority

pytestmark = pytest.mark.ped_i10a

HANDLED = frozenset({"asset.image", "asset.document"})


def _ref(
    *,
    resource_type: str = "asset.image",
    resource_id=None,
    revision: int | None = None,
) -> ResourceRef:
    return ResourceRef(
        resource_type=resource_type,
        resource_id=resource_id or uuid4(),
        resource_revision=revision,
    )


def _varef(
    resource_ref: ResourceRef, *, required: bool = True, ordinal: int = 0
) -> VersionAssetRef:
    return VersionAssetRef(
        tenant_id=uuid4(),
        content_id=ContentId(uuid7()),
        version_id=ContentVersionId(uuid7()),
        resource_ref=resource_ref,
        role="primary",
        ordinal=ordinal,
        required=required,
        created_at=datetime.now(tz=UTC),
    )


class TestBindingAdapter:
    def test_supported_usable_passes(self) -> None:
        authority = RecordingAssetUseAuthority()
        adapter = AssetAuthorityReferenceValidationAdapter(
            authority, handled_resource_types=HANDLED
        )
        tenant = uuid4()
        principal = uuid4()
        ref = _ref(revision=3)
        adapter.validate_binding(
            tenant_id=tenant, principal_id=principal, resource_ref=ref
        )
        assert authority.calls == [(tenant, principal, ref)]

    def test_unsupported_type_and_no_wildcard(self) -> None:
        authority = RecordingAssetUseAuthority()
        adapter = AssetAuthorityReferenceValidationAdapter(
            authority, handled_resource_types=HANDLED
        )
        with pytest.raises(AssetReferenceValidationFailed):
            adapter.validate_binding(
                tenant_id=uuid4(),
                principal_id=uuid4(),
                resource_ref=_ref(resource_type="asset.video"),
            )
        assert authority.calls == []
        with pytest.raises(ValueError, match="wildcard"):
            AssetAuthorityReferenceValidationAdapter(
                authority, handled_resource_types={"asset.*"}
            )
        with pytest.raises(ValueError, match="wildcard"):
            AssetAuthorityReferenceValidationAdapter(
                authority, handled_resource_types={"*"}
            )

    @pytest.mark.parametrize("reason", list(AssetUseRejectionReason))
    def test_each_unusable_reason_rejects(self, reason: AssetUseRejectionReason) -> None:
        resource_id = uuid4()
        authority = RecordingAssetUseAuthority(
            assessments={
                resource_id: AssetUseAssessment(usable=False, reason_code=reason)
            }
        )
        adapter = AssetAuthorityReferenceValidationAdapter(
            authority, handled_resource_types=HANDLED
        )
        with pytest.raises(AssetReferenceValidationFailed):
            adapter.validate_binding(
                tenant_id=uuid4(),
                principal_id=uuid4(),
                resource_ref=_ref(resource_id=resource_id),
            )

    def test_pinned_and_unpinned_revision_preserved(self) -> None:
        authority = RecordingAssetUseAuthority()
        adapter = AssetAuthorityReferenceValidationAdapter(
            authority, handled_resource_types=HANDLED
        )
        pinned = _ref(revision=7)
        unpinned = _ref(revision=None)
        tenant = uuid4()
        principal = uuid4()
        adapter.validate_binding(
            tenant_id=tenant, principal_id=principal, resource_ref=pinned
        )
        adapter.validate_binding(
            tenant_id=tenant, principal_id=principal, resource_ref=unpinned
        )
        assert authority.calls[0][2] is pinned
        assert authority.calls[0][2].resource_revision == 7
        assert authority.calls[1][2] is unpinned
        assert authority.calls[1][2].resource_revision is None

    def test_unavailable_malformed_and_runtime(self) -> None:
        tenant = uuid4()
        principal = uuid4()
        ref = _ref()
        unavailable = RecordingAssetUseAuthority(unavailable=True)
        adapter = AssetAuthorityReferenceValidationAdapter(
            unavailable, handled_resource_types=HANDLED
        )
        with pytest.raises(GovernanceUnavailableError):
            adapter.validate_binding(
                tenant_id=tenant, principal_id=principal, resource_ref=ref
            )

        malformed = RecordingAssetUseAuthority(malformed={"usable": True})
        adapter_m = AssetAuthorityReferenceValidationAdapter(
            malformed, handled_resource_types=HANDLED
        )
        with pytest.raises(GovernanceUnavailableError):
            adapter_m.validate_binding(
                tenant_id=tenant, principal_id=principal, resource_ref=ref
            )

        buggy = RecordingAssetUseAuthority(raise_runtime=True)
        adapter_b = AssetAuthorityReferenceValidationAdapter(
            buggy, handled_resource_types=HANDLED
        )
        with pytest.raises(RuntimeError, match="SECRET_ASSET_AUTHORITY_BUG"):
            adapter_b.validate_binding(
                tenant_id=tenant, principal_id=principal, resource_ref=ref
            )


class TestCurrentUseAdapter:
    def test_every_ref_including_optional_evaluated(self) -> None:
        a = _ref()
        b = _ref()
        authority = RecordingAssetUseAuthority(
            assessments={
                b.resource_id: AssetUseAssessment(
                    usable=False, reason_code=AssetUseRejectionReason.QUARANTINED
                )
            }
        )
        adapter = AssetAuthorityCurrentGovernanceAdapter(
            authority, handled_resource_types=HANDLED
        )
        with pytest.raises(PublicationAssetValidationFailed):
            adapter.validate_current_use(
                tenant_id=uuid4(),
                principal_id=uuid4(),
                content_id=ContentId(uuid7()),
                version_id=ContentVersionId(uuid7()),
                asset_refs=[
                    _varef(a, required=True, ordinal=0),
                    _varef(b, required=False, ordinal=1),
                ],
            )
        assert len(authority.calls) == 2

    def test_unsupported_type_rejects(self) -> None:
        authority = RecordingAssetUseAuthority()
        adapter = AssetAuthorityCurrentGovernanceAdapter(
            authority, handled_resource_types=HANDLED
        )
        with pytest.raises(PublicationAssetValidationFailed):
            adapter.validate_current_use(
                tenant_id=uuid4(),
                principal_id=uuid4(),
                content_id=ContentId(uuid7()),
                version_id=ContentVersionId(uuid7()),
                asset_refs=[_varef(_ref(resource_type="asset.video"))],
            )
        assert authority.calls == []

    def test_memoization_within_operation_only(self) -> None:
        shared = _ref(revision=2)
        authority = RecordingAssetUseAuthority()
        adapter = AssetAuthorityCurrentGovernanceAdapter(
            authority, handled_resource_types=HANDLED
        )
        adapter.validate_current_use(
            tenant_id=uuid4(),
            principal_id=uuid4(),
            content_id=ContentId(uuid7()),
            version_id=ContentVersionId(uuid7()),
            asset_refs=[
                _varef(shared, required=True, ordinal=0),
                _varef(shared, required=False, ordinal=1),
            ],
        )
        assert len(authority.calls) == 1
        adapter.validate_current_use(
            tenant_id=uuid4(),
            principal_id=uuid4(),
            content_id=ContentId(uuid7()),
            version_id=ContentVersionId(uuid7()),
            asset_refs=[_varef(shared, required=True)],
        )
        assert len(authority.calls) == 2

    def test_unavailable_malformed_runtime(self) -> None:
        ref = _varef(_ref())
        tenant = uuid4()
        principal = uuid4()
        content_id = ContentId(uuid7())
        version_id = ContentVersionId(uuid7())
        unavailable = RecordingAssetUseAuthority(unavailable=True)
        adapter = AssetAuthorityCurrentGovernanceAdapter(
            unavailable, handled_resource_types=HANDLED
        )
        with pytest.raises(GovernanceUnavailableError):
            adapter.validate_current_use(
                tenant_id=tenant,
                principal_id=principal,
                content_id=content_id,
                version_id=version_id,
                asset_refs=[ref],
            )

        malformed = RecordingAssetUseAuthority(malformed=object())
        with pytest.raises(GovernanceUnavailableError):
            AssetAuthorityCurrentGovernanceAdapter(
                malformed, handled_resource_types=HANDLED
            ).validate_current_use(
                tenant_id=tenant,
                principal_id=principal,
                content_id=content_id,
                version_id=version_id,
                asset_refs=[ref],
            )

        with pytest.raises(RuntimeError, match="SECRET_ASSET_AUTHORITY_BUG"):
            AssetAuthorityCurrentGovernanceAdapter(
                RecordingAssetUseAuthority(raise_runtime=True),
                handled_resource_types=HANDLED,
            ).validate_current_use(
                tenant_id=tenant,
                principal_id=principal,
                content_id=content_id,
                version_id=version_id,
                asset_refs=[ref],
            )
