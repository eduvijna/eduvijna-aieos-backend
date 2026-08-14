"""Test doubles. Not production security or catalog implementations."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from aieos.domains.content.application.errors import (
    AssetReferenceValidationFailed,
    PublicationAssetValidationFailed,
    PublicationForbidden,
    PublicationGovernanceRejected,
    ReviewCommentRejected,
    ReviewForbidden,
)
from aieos.domains.content.application.ports import (
    CONTENT_PUBLISH,
    CONTENT_REVIEW_DECIDE,
    CONTENT_REVIEW_SUBMIT,
)
from aieos.domains.content.domain.schema import ContentSchemaRegistry, SchemaId, SchemaVersion
from aieos.platform.security.context import (
    TrustedSecurityContext,
    UnauthenticatedError,
    UnauthorizedError,
)
from tests.domains.content.domain.fakes import TEST_GENERIC_V1, TestFixtureSchema

IDEMPOTENCY_RETENTION = timedelta(hours=24)


class StubSecurityContextResolver:
    """Authorized tenant is independent of the caller-supplied tenant header."""

    def __init__(
        self,
        authorized_tenant_id: UUID,
        principal_id: UUID,
        *,
        unauthenticated: bool = False,
    ) -> None:
        self.authorized_tenant_id = authorized_tenant_id
        self.principal_id = principal_id
        self.unauthenticated = unauthenticated

    def resolve(self, requested_tenant_id: UUID | None) -> TrustedSecurityContext:
        if self.unauthenticated:
            raise UnauthenticatedError("not authenticated")
        if requested_tenant_id is None:
            raise UnauthenticatedError("tenant header required")
        if requested_tenant_id != self.authorized_tenant_id:
            raise UnauthorizedError("not authorized for requested tenant")
        return TrustedSecurityContext(
            tenant_id=self.authorized_tenant_id,
            principal_id=self.principal_id,
        )


SENSITIVE_TEST_COMMENT = "SENSITIVE_TEST_COMMENT"


class AllowReviewAuthorization:
    def __init__(self, *, allow_submit: bool = True, allow_decide: bool = True) -> None:
        self.allow_submit = allow_submit
        self.allow_decide = allow_decide
        self.calls: list[tuple[UUID, str]] = []

    def authorize(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        content_id,
        version_id,
        capability: str,
    ) -> None:
        self.calls.append((principal_id, capability))
        allowed = {
            CONTENT_REVIEW_SUBMIT: self.allow_submit,
            CONTENT_REVIEW_DECIDE: self.allow_decide,
        }
        if not allowed.get(capability, False):
            raise ReviewForbidden("review capability denied")


class AllowReviewCommentPolicy:
    def evaluate(self, comment: str | None) -> None:
        return None


class MarkerReviewCommentPolicy:
    def evaluate(self, comment: str | None) -> None:
        if comment is not None and SENSITIVE_TEST_COMMENT in comment:
            raise ReviewCommentRejected("review comment rejected")


class AllowPublicationAuthorization:
    def __init__(self, *, allow: bool = True) -> None:
        self.allow = allow
        self.calls: list[tuple[UUID, str]] = []

    def authorize(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        content_id,
        version_id,
        capability: str,
    ) -> None:
        self.calls.append((principal_id, capability))
        if capability != CONTENT_PUBLISH or not self.allow:
            raise PublicationForbidden("content.publish denied")


class AllowPublicationGovernance:
    def __init__(self, *, allow: bool = True) -> None:
        self.allow = allow
        self.calls: list[tuple[UUID, UUID]] = []

    def evaluate(
        self,
        *,
        tenant_id: UUID,
        content_id,
        version_id,
    ) -> None:
        self.calls.append((content_id.value, version_id.value))
        if not self.allow:
            raise PublicationGovernanceRejected("publication governance rejected")


class AllowAssetReferenceValidation:
    def __init__(
        self, *, deny_ids: set[UUID] | None = None, raise_runtime: bool = False
    ) -> None:
        self.deny_ids = deny_ids or set()
        self.raise_runtime = raise_runtime
        self.calls: list[UUID] = []

    def validate_binding(
        self, *, tenant_id: UUID, principal_id: UUID, resource_ref
    ) -> None:
        self.calls.append(resource_ref.resource_id)
        if self.raise_runtime:
            raise RuntimeError("SECRET_ASSET_VALIDATOR_BUG")
        if resource_ref.resource_id in self.deny_ids:
            raise AssetReferenceValidationFailed("asset reference invalid")


class AllowAssetCurrentGovernance:
    def __init__(
        self, *, deny: bool = False, quarantined_ids: set[UUID] | None = None
    ) -> None:
        self.deny = deny
        self.quarantined_ids = quarantined_ids or set()
        self.calls: list[tuple[UUID, UUID]] = []

    def validate_current_use(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        content_id,
        version_id,
        asset_refs,
    ) -> None:
        self.calls.append((content_id.value, version_id.value))
        if self.deny or any(
            ref.resource_ref.resource_id in self.quarantined_ids for ref in asset_refs
        ):
            raise PublicationAssetValidationFailed(
                "publication asset validation failed"
            )


def make_test_schema_registry() -> ContentSchemaRegistry:
    registry = ContentSchemaRegistry()
    registry.register(TEST_GENERIC_V1)
    registry.register(
        TestFixtureSchema(
            content_type="other.type",
            schema_id=SchemaId("test.other"),
            schema_version=SchemaVersion(1),
            required_keys=("marker",),
        )
    )
    return registry
