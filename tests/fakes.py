"""Test doubles. Not production security or catalog implementations."""

from __future__ import annotations

from uuid import UUID

from datetime import timedelta

from aieos.domains.content.domain.schema import ContentSchemaRegistry
from aieos.platform.security.context import (
    TrustedSecurityContext,
    UnauthenticatedError,
    UnauthorizedError,
)
from tests.domains.content.domain.fakes import TEST_GENERIC_V1, TestFixtureSchema
from aieos.domains.content.domain.schema import SchemaId, SchemaVersion

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
