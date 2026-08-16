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
from aieos.platform.security.identity import TrustedRequestIdentity
from tests.domains.content.domain.fakes import TEST_GENERIC_V1, TestFixtureSchema

IDEMPOTENCY_RETENTION = timedelta(hours=24)


class FixedPrincipalAuthenticator:
    """Test-only authenticator. Not a production IdP adapter."""

    def __init__(
        self,
        principal_id: UUID,
        *,
        unauthenticated: bool = False,
        unavailable: bool = False,
        unavailable_secret: str | None = None,
    ) -> None:
        self.principal_id = principal_id
        self.unauthenticated = unauthenticated
        self.unavailable = unavailable
        self.unavailable_secret = unavailable_secret

    def authenticate(self, request) -> TrustedRequestIdentity:
        if self.unavailable:
            if self.unavailable_secret is not None:
                raise RuntimeError(self.unavailable_secret)
            from aieos.platform.security.context import AuthenticationUnavailableError

            raise AuthenticationUnavailableError("authentication unavailable")
        if self.unauthenticated:
            raise UnauthenticatedError("not authenticated")
        return TrustedRequestIdentity(principal_id=self.principal_id)


class StubSecurityContextResolver:
    """Authorized tenant is independent of the caller-supplied tenant header.

    Consumes TrustedRequestIdentity explicitly. Does not read HTTP headers.
    """

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

    def resolve(
        self,
        *,
        identity: TrustedRequestIdentity,
        requested_tenant_id: UUID | None,
    ) -> TrustedSecurityContext:
        if self.unauthenticated:
            raise UnauthenticatedError("not authenticated")
        if requested_tenant_id is None:
            raise UnauthenticatedError("tenant header required")
        if requested_tenant_id != self.authorized_tenant_id:
            raise UnauthorizedError("not authorized for requested tenant")
        return TrustedSecurityContext(
            tenant_id=self.authorized_tenant_id,
            principal_id=identity.principal_id,
        )


class MutableCurrentTenantAccessAuthority:
    """Test-only current tenant-access authority. Supports revoke/suspend."""

    def __init__(
        self,
        allowed: set[tuple[UUID, UUID]] | None = None,
        *,
        suspended_tenants: set[UUID] | None = None,
        unavailable: bool = False,
        unavailable_secret: str | None = None,
    ) -> None:
        self.allowed = allowed or set()
        self.suspended_tenants = suspended_tenants or set()
        self.unavailable = unavailable
        self.unavailable_secret = unavailable_secret
        self.calls: list[tuple[UUID, UUID]] = []

    def grant(self, principal_id: UUID, tenant_id: UUID) -> None:
        self.allowed.add((principal_id, tenant_id))
        self.suspended_tenants.discard(tenant_id)

    def revoke(self, principal_id: UUID, tenant_id: UUID) -> None:
        self.allowed.discard((principal_id, tenant_id))

    def suspend(self, tenant_id: UUID) -> None:
        self.suspended_tenants.add(tenant_id)

    def authorize_tenant(self, *, principal_id: UUID, tenant_id: UUID) -> None:
        self.calls.append((principal_id, tenant_id))
        if self.unavailable:
            if self.unavailable_secret is not None:
                raise RuntimeError(self.unavailable_secret)
            from aieos.platform.security.context import AuthorizationUnavailableError

            raise AuthorizationUnavailableError("tenant authority unavailable")
        if tenant_id in self.suspended_tenants:
            raise UnauthorizedError("tenant suspended")
        if (principal_id, tenant_id) not in self.allowed:
            raise UnauthorizedError("not authorized for requested tenant")


class RecordingUowFactory:
    """Counts UoW factory invocations for zero-persistence failure proofs."""

    def __init__(self, inner=None) -> None:
        self.inner = inner
        self.calls = 0
        self.tenants: list[UUID] = []

    def __call__(self, execution_tenant_id):
        self.calls += 1
        self.tenants.append(execution_tenant_id)
        if self.inner is None:
            raise RuntimeError("recording-uow-stop")
        return self.inner(execution_tenant_id)


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


class AllowAIGenerationAuthorization:
    def __init__(self, *, allow: bool = True) -> None:
        self.allow = allow
        self.calls: list[tuple[UUID, str]] = []

    def authorize(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        content_id,
        capability: str,
    ) -> None:
        self.calls.append((principal_id, capability))
        if not self.allow:
            from aieos.domains.content.application.errors import AIGenerationForbidden

            raise AIGenerationForbidden("AI generation materialization forbidden")


class AllowMigrationAuthorization:
    def __init__(self, *, allow: bool = True) -> None:
        self.allow = allow
        self.calls: list[tuple[UUID, str]] = []

    def authorize(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        capability: str,
    ) -> None:
        from aieos.domains.content.application.errors import MigrationForbidden
        from aieos.domains.content.application.ports import CONTENT_MIGRATE_IMPORT

        self.calls.append((principal_id, capability))
        if not self.allow or capability != CONTENT_MIGRATE_IMPORT:
            raise MigrationForbidden("content.migrate.import denied")


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
