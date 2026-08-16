"""PED-I09 Authorization Kernel allow/deny matrix and current-authority proofs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine

from aieos.domains.content.domain.identities import ContentId, ContentVersionId
from aieos.domains.content.application.errors import (
    AIGenerationForbidden,
    MigrationForbidden,
    PublicationForbidden,
    ReviewForbidden,
)
from aieos.domains.content.application.ports import (
    CONTENT_MIGRATE_IMPORT,
    CONTENT_PUBLISH,
    CONTENT_REVIEW_DECIDE,
    CONTENT_REVIEW_SUBMIT,
    CONTENT_VERSION_CREATE,
)
from aieos.platform.security.authorization import (
    AIEOS_CONTENT_CAPABILITIES,
    AuthorizationKernel,
    AuthorityDecision,
    KernelAIGenerationAuthorization,
    KernelContentMigrationAuthorization,
    KernelCurrentTenantAccessAuthority,
    KernelPublicationAuthorization,
    KernelReviewAuthorization,
)
from aieos.platform.security.authorization.decisions import (
    GrantStatus,
    MembershipStatus,
    PrincipalStatus,
    TenantStatus,
)
from aieos.platform.security.authorization.kernel import validate_known_capabilities
from aieos.platform.security.context import (
    AuthorizationUnavailableError,
    UnauthorizedError,
)
from tests.platform.security.authorization.helpers import (
    revoke_grant,
    revoke_membership,
    seed_active_authority,
    seed_grant,
    seed_membership,
    seed_principal,
    seed_tenant,
)

pytestmark = pytest.mark.ped_i09


def _kernel(engine) -> AuthorizationKernel:
    return AuthorizationKernel(engine, known_capabilities=AIEOS_CONTENT_CAPABILITIES)


class TestTenantAccess:
    def test_allow_active_membership(
        self, bootstrap_engine, runtime_engine
    ) -> None:
        tenant = uuid.uuid7()
        principal = uuid.uuid7()
        seed_active_authority(
            bootstrap_engine, tenant_id=tenant, principal_id=principal
        )
        assert (
            _kernel(runtime_engine).decide_tenant_access(
                principal_id=principal, tenant_id=tenant
            )
            is AuthorityDecision.ALLOW
        )
        KernelCurrentTenantAccessAuthority(_kernel(runtime_engine)).authorize_tenant(
            principal_id=principal, tenant_id=tenant
        )

    @pytest.mark.parametrize(
        "setup",
        [
            "unknown_principal",
            "suspended_principal",
            "disabled_principal",
            "unknown_tenant",
            "suspended_tenant",
            "disabled_tenant",
            "missing_membership",
            "suspended_membership",
            "revoked_membership",
            "expired_membership",
        ],
    )
    def test_deny_matrix(
        self, bootstrap_engine, runtime_engine, setup: str
    ) -> None:
        tenant = uuid.uuid7()
        principal = uuid.uuid7()
        if setup == "unknown_principal":
            seed_tenant(bootstrap_engine, tenant)
        elif setup == "suspended_principal":
            seed_principal(
                bootstrap_engine, principal, status=PrincipalStatus.SUSPENDED
            )
            seed_tenant(bootstrap_engine, tenant)
            seed_membership(
                bootstrap_engine, tenant_id=tenant, principal_id=principal
            )
        elif setup == "disabled_principal":
            seed_principal(
                bootstrap_engine, principal, status=PrincipalStatus.DISABLED
            )
            seed_tenant(bootstrap_engine, tenant)
            seed_membership(
                bootstrap_engine, tenant_id=tenant, principal_id=principal
            )
        elif setup == "unknown_tenant":
            seed_principal(bootstrap_engine, principal)
        elif setup == "suspended_tenant":
            seed_principal(bootstrap_engine, principal)
            seed_tenant(bootstrap_engine, tenant, status=TenantStatus.SUSPENDED)
            seed_membership(
                bootstrap_engine, tenant_id=tenant, principal_id=principal
            )
        elif setup == "disabled_tenant":
            seed_principal(bootstrap_engine, principal)
            seed_tenant(bootstrap_engine, tenant, status=TenantStatus.DISABLED)
            seed_membership(
                bootstrap_engine, tenant_id=tenant, principal_id=principal
            )
        elif setup == "missing_membership":
            seed_principal(bootstrap_engine, principal)
            seed_tenant(bootstrap_engine, tenant)
        elif setup == "suspended_membership":
            seed_principal(bootstrap_engine, principal)
            seed_tenant(bootstrap_engine, tenant)
            seed_membership(
                bootstrap_engine,
                tenant_id=tenant,
                principal_id=principal,
                status=MembershipStatus.SUSPENDED,
            )
        elif setup == "revoked_membership":
            seed_principal(bootstrap_engine, principal)
            seed_tenant(bootstrap_engine, tenant)
            seed_membership(
                bootstrap_engine,
                tenant_id=tenant,
                principal_id=principal,
                status=MembershipStatus.REVOKED,
                revoked_at=datetime.now(UTC),
            )
        elif setup == "expired_membership":
            seed_principal(bootstrap_engine, principal)
            seed_tenant(bootstrap_engine, tenant)
            seed_membership(
                bootstrap_engine,
                tenant_id=tenant,
                principal_id=principal,
                expires_at=datetime.now(UTC) - timedelta(minutes=5),
            )
        decision = _kernel(runtime_engine).decide_tenant_access(
            principal_id=principal, tenant_id=tenant
        )
        assert decision is AuthorityDecision.DENY
        with pytest.raises(UnauthorizedError):
            KernelCurrentTenantAccessAuthority(
                _kernel(runtime_engine)
            ).authorize_tenant(principal_id=principal, tenant_id=tenant)


class TestCapability:
    def test_allow_exact_grant(self, bootstrap_engine, runtime_engine) -> None:
        tenant = uuid.uuid7()
        principal = uuid.uuid7()
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant,
            principal_id=principal,
            capabilities=(CONTENT_PUBLISH,),
        )
        assert (
            _kernel(runtime_engine).decide_capability(
                principal_id=principal,
                tenant_id=tenant,
                capability=CONTENT_PUBLISH,
            )
            is AuthorityDecision.ALLOW
        )

    @pytest.mark.parametrize(
        "setup",
        [
            "unknown_capability",
            "wrong_capability",
            "missing_grant",
            "revoked_grant",
            "expired_grant",
            "inactive_principal",
            "inactive_tenant",
            "invalid_membership",
        ],
    )
    def test_deny_matrix(
        self, bootstrap_engine, runtime_engine, setup: str
    ) -> None:
        tenant = uuid.uuid7()
        principal = uuid.uuid7()
        if setup == "unknown_capability":
            seed_active_authority(
                bootstrap_engine, tenant_id=tenant, principal_id=principal
            )
            capability = "content.unknown.capability"
        elif setup == "wrong_capability":
            seed_active_authority(
                bootstrap_engine,
                tenant_id=tenant,
                principal_id=principal,
                capabilities=(CONTENT_PUBLISH,),
            )
            capability = CONTENT_REVIEW_DECIDE
        elif setup == "missing_grant":
            seed_active_authority(
                bootstrap_engine, tenant_id=tenant, principal_id=principal
            )
            capability = CONTENT_PUBLISH
        elif setup == "revoked_grant":
            seed_active_authority(
                bootstrap_engine, tenant_id=tenant, principal_id=principal
            )
            seed_grant(
                bootstrap_engine,
                tenant_id=tenant,
                principal_id=principal,
                capability=CONTENT_PUBLISH,
                status=GrantStatus.REVOKED,
                revoked_at=datetime.now(UTC),
            )
            capability = CONTENT_PUBLISH
        elif setup == "expired_grant":
            seed_active_authority(
                bootstrap_engine, tenant_id=tenant, principal_id=principal
            )
            seed_grant(
                bootstrap_engine,
                tenant_id=tenant,
                principal_id=principal,
                capability=CONTENT_PUBLISH,
                expires_at=datetime.now(UTC) - timedelta(minutes=1),
            )
            capability = CONTENT_PUBLISH
        elif setup == "inactive_principal":
            seed_principal(
                bootstrap_engine, principal, status=PrincipalStatus.SUSPENDED
            )
            seed_tenant(bootstrap_engine, tenant)
            seed_membership(
                bootstrap_engine, tenant_id=tenant, principal_id=principal
            )
            seed_grant(
                bootstrap_engine,
                tenant_id=tenant,
                principal_id=principal,
                capability=CONTENT_PUBLISH,
            )
            capability = CONTENT_PUBLISH
        elif setup == "inactive_tenant":
            seed_principal(bootstrap_engine, principal)
            seed_tenant(bootstrap_engine, tenant, status=TenantStatus.DISABLED)
            seed_membership(
                bootstrap_engine, tenant_id=tenant, principal_id=principal
            )
            seed_grant(
                bootstrap_engine,
                tenant_id=tenant,
                principal_id=principal,
                capability=CONTENT_PUBLISH,
            )
            capability = CONTENT_PUBLISH
        else:  # invalid_membership
            seed_principal(bootstrap_engine, principal)
            seed_tenant(bootstrap_engine, tenant)
            seed_membership(
                bootstrap_engine,
                tenant_id=tenant,
                principal_id=principal,
                status=MembershipStatus.SUSPENDED,
            )
            # Grant FK requires membership row; status suspended still blocks.
            seed_grant(
                bootstrap_engine,
                tenant_id=tenant,
                principal_id=principal,
                capability=CONTENT_PUBLISH,
            )
            capability = CONTENT_PUBLISH
        assert (
            _kernel(runtime_engine).decide_capability(
                principal_id=principal,
                tenant_id=tenant,
                capability=capability,
            )
            is AuthorityDecision.DENY
        )

    def test_publish_grant_does_not_authorize_review_decide(
        self, bootstrap_engine, runtime_engine
    ) -> None:
        tenant = uuid.uuid7()
        principal = uuid.uuid7()
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant,
            principal_id=principal,
            capabilities=(CONTENT_PUBLISH,),
        )
        assert (
            _kernel(runtime_engine).decide_capability(
                principal_id=principal,
                tenant_id=tenant,
                capability=CONTENT_REVIEW_DECIDE,
            )
            is AuthorityDecision.DENY
        )


class TestCurrentAuthorityNoCache:
    def test_membership_revocation_takes_effect_next_evaluation(
        self, bootstrap_engine, runtime_engine
    ) -> None:
        tenant = uuid.uuid7()
        principal = uuid.uuid7()
        seed_active_authority(
            bootstrap_engine, tenant_id=tenant, principal_id=principal
        )
        kernel = _kernel(runtime_engine)
        assert (
            kernel.decide_tenant_access(principal_id=principal, tenant_id=tenant)
            is AuthorityDecision.ALLOW
        )
        revoke_membership(
            bootstrap_engine, tenant_id=tenant, principal_id=principal
        )
        assert (
            kernel.decide_tenant_access(principal_id=principal, tenant_id=tenant)
            is AuthorityDecision.DENY
        )

    def test_capability_revocation_takes_effect_next_evaluation(
        self, bootstrap_engine, runtime_engine
    ) -> None:
        tenant = uuid.uuid7()
        principal = uuid.uuid7()
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant,
            principal_id=principal,
            capabilities=(CONTENT_PUBLISH,),
        )
        kernel = _kernel(runtime_engine)
        assert (
            kernel.decide_capability(
                principal_id=principal,
                tenant_id=tenant,
                capability=CONTENT_PUBLISH,
            )
            is AuthorityDecision.ALLOW
        )
        revoke_grant(
            bootstrap_engine,
            tenant_id=tenant,
            principal_id=principal,
            capability=CONTENT_PUBLISH,
        )
        assert (
            kernel.decide_capability(
                principal_id=principal,
                tenant_id=tenant,
                capability=CONTENT_PUBLISH,
            )
            is AuthorityDecision.DENY
        )


class TestWildcardFailClosed:
    def test_known_capabilities_rejects_star(self) -> None:
        with pytest.raises(ValueError, match="wildcard"):
            validate_known_capabilities({"content.publish", "*"})
        with pytest.raises(ValueError, match="wildcard"):
            AuthorizationKernel(
                create_engine("postgresql+psycopg://unused/unused"),
                known_capabilities=AIEOS_CONTENT_CAPABILITIES | {"*"},
            )

    def test_known_capabilities_rejects_prefix_wildcard(self) -> None:
        with pytest.raises(ValueError, match="wildcard"):
            validate_known_capabilities({"content.*"})
        with pytest.raises(ValueError, match="wildcard"):
            AuthorizationKernel(
                create_engine("postgresql+psycopg://unused/unused"),
                known_capabilities={"content.review.*"},
            )

    def test_decide_capability_star_never_allow(
        self, bootstrap_engine, runtime_engine
    ) -> None:
        tenant = uuid.uuid7()
        principal = uuid.uuid7()
        seed_active_authority(
            bootstrap_engine, tenant_id=tenant, principal_id=principal
        )
        kernel = _kernel(runtime_engine)
        assert (
            kernel.decide_capability(
                principal_id=principal, tenant_id=tenant, capability="*"
            )
            is AuthorityDecision.DENY
        )
        assert (
            kernel.decide_capability(
                principal_id=principal,
                tenant_id=tenant,
                capability="content.*",
            )
            is AuthorityDecision.DENY
        )


class TestCorruptAuthorityState:
    def test_coerce_rejects_unknown_statuses(self) -> None:
        from aieos.platform.security.authorization.repository import (
            coerce_authority_status,
        )

        with pytest.raises(AuthorizationUnavailableError):
            coerce_authority_status(PrincipalStatus, "CORRUPT")
        with pytest.raises(AuthorizationUnavailableError):
            coerce_authority_status(TenantStatus, "BOGUS")
        with pytest.raises(AuthorizationUnavailableError):
            coerce_authority_status(MembershipStatus, "UNKNOWN")
        with pytest.raises(AuthorizationUnavailableError):
            coerce_authority_status(GrantStatus, "INVALID")

    def test_row_materialization_rejects_corrupt_statuses(self) -> None:
        from aieos.platform.security.authorization.repository import (
            GrantAuthorityRow,
            MembershipAuthorityRow,
            PrincipalAuthorityRow,
            TenantAuthorityRow,
        )

        pid = uuid.uuid7()
        tid = uuid.uuid7()
        with pytest.raises(AuthorizationUnavailableError):
            PrincipalAuthorityRow(principal_id=pid, status="CORRUPT")  # type: ignore[arg-type]
        with pytest.raises(AuthorizationUnavailableError):
            TenantAuthorityRow(tenant_id=tid, status="BOGUS")  # type: ignore[arg-type]
        with pytest.raises(AuthorizationUnavailableError):
            MembershipAuthorityRow(
                tenant_id=tid,
                principal_id=pid,
                status="UNKNOWN",  # type: ignore[arg-type]
                expires_at=None,
                revoked_at=None,
            )
        with pytest.raises(AuthorizationUnavailableError):
            GrantAuthorityRow(
                tenant_id=tid,
                principal_id=pid,
                capability=CONTENT_PUBLISH,
                status="INVALID",  # type: ignore[arg-type]
                expires_at=None,
                revoked_at=None,
            )

    def test_kernel_propagates_corrupt_principal_as_unavailable(self) -> None:
        from datetime import UTC, datetime

        from aieos.platform.security.authorization.repository import (
            PrincipalAuthorityRow,
            TenantAccessBundle,
            TenantAuthorityRow,
            MembershipAuthorityRow,
        )

        principal = uuid.uuid7()
        tenant = uuid.uuid7()
        now = datetime.now(UTC)

        class _CorruptPrincipalRepo:
            def load_tenant_access_bundle(self, *, principal_id, tenant_id):
                return TenantAccessBundle(
                    principal=PrincipalAuthorityRow(
                        principal_id=principal_id, status="CORRUPT"  # type: ignore[arg-type]
                    ),
                    tenant=TenantAuthorityRow(
                        tenant_id=tenant_id, status=TenantStatus.ACTIVE
                    ),
                    membership=MembershipAuthorityRow(
                        tenant_id=tenant_id,
                        principal_id=principal_id,
                        status=MembershipStatus.ACTIVE,
                        expires_at=None,
                        revoked_at=None,
                    ),
                    evaluated_at=now,
                )

            def load_capability_bundle(self, *, principal_id, tenant_id, capability):
                raise AssertionError("not used")

        kernel = AuthorizationKernel(
            create_engine("postgresql+psycopg://unused/unused"),
            known_capabilities=AIEOS_CONTENT_CAPABILITIES,
            repository=_CorruptPrincipalRepo(),  # type: ignore[arg-type]
        )
        with pytest.raises(AuthorizationUnavailableError):
            kernel.decide_tenant_access(principal_id=principal, tenant_id=tenant)

    def test_kernel_propagates_corrupt_grant_as_unavailable(self) -> None:
        from datetime import UTC, datetime

        from aieos.platform.security.authorization.repository import (
            CapabilityBundle,
            GrantAuthorityRow,
            MembershipAuthorityRow,
            PrincipalAuthorityRow,
            TenantAuthorityRow,
        )

        principal = uuid.uuid7()
        tenant = uuid.uuid7()
        now = datetime.now(UTC)

        class _CorruptGrantRepo:
            def load_tenant_access_bundle(self, *, principal_id, tenant_id):
                raise AssertionError("not used")

            def load_capability_bundle(self, *, principal_id, tenant_id, capability):
                return CapabilityBundle(
                    principal=PrincipalAuthorityRow(
                        principal_id=principal_id, status=PrincipalStatus.ACTIVE
                    ),
                    tenant=TenantAuthorityRow(
                        tenant_id=tenant_id, status=TenantStatus.ACTIVE
                    ),
                    membership=MembershipAuthorityRow(
                        tenant_id=tenant_id,
                        principal_id=principal_id,
                        status=MembershipStatus.ACTIVE,
                        expires_at=None,
                        revoked_at=None,
                    ),
                    grant=GrantAuthorityRow(
                        tenant_id=tenant_id,
                        principal_id=principal_id,
                        capability=capability,
                        status="CORRUPT",  # type: ignore[arg-type]
                        expires_at=None,
                        revoked_at=None,
                    ),
                    evaluated_at=now,
                )

        kernel = AuthorizationKernel(
            create_engine("postgresql+psycopg://unused/unused"),
            known_capabilities=AIEOS_CONTENT_CAPABILITIES,
            repository=_CorruptGrantRepo(),  # type: ignore[arg-type]
        )
        with pytest.raises(AuthorizationUnavailableError):
            kernel.decide_capability(
                principal_id=principal,
                tenant_id=tenant,
                capability=CONTENT_PUBLISH,
            )


class TestUnavailable:
    def test_database_unavailable_is_not_deny(self) -> None:
        engine = create_engine(
            "postgresql+psycopg://nobody:bad@127.0.0.1:1/none",
            pool_pre_ping=False,
            connect_args={"connect_timeout": 1},
        )
        kernel = _kernel(engine)
        with pytest.raises(AuthorizationUnavailableError):
            kernel.decide_tenant_access(
                principal_id=uuid.uuid7(), tenant_id=uuid.uuid7()
            )
        with pytest.raises(AuthorizationUnavailableError):
            kernel.decide_capability(
                principal_id=uuid.uuid7(),
                tenant_id=uuid.uuid7(),
                capability=CONTENT_PUBLISH,
            )


class TestContentAdapters:
    def test_review_publication_ai_migration_adapters(
        self, bootstrap_engine, runtime_engine
    ) -> None:
        tenant = uuid.uuid7()
        principal = uuid.uuid7()
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant,
            principal_id=principal,
            capabilities=(
                CONTENT_REVIEW_SUBMIT,
                CONTENT_PUBLISH,
                CONTENT_VERSION_CREATE,
                CONTENT_MIGRATE_IMPORT,
            ),
        )
        kernel = _kernel(runtime_engine)
        content_id = ContentId(uuid.uuid7())
        version_id = ContentVersionId(uuid.uuid7())
        KernelReviewAuthorization(kernel).authorize(
            tenant_id=tenant,
            principal_id=principal,
            content_id=content_id,
            version_id=version_id,
            capability=CONTENT_REVIEW_SUBMIT,
        )
        with pytest.raises(ReviewForbidden):
            KernelReviewAuthorization(kernel).authorize(
                tenant_id=tenant,
                principal_id=principal,
                content_id=content_id,
                version_id=version_id,
                capability=CONTENT_REVIEW_DECIDE,
            )
        with pytest.raises(ReviewForbidden):
            KernelReviewAuthorization(kernel).authorize(
                tenant_id=tenant,
                principal_id=principal,
                content_id=content_id,
                version_id=version_id,
                capability="content.review.admin",
            )
        KernelPublicationAuthorization(kernel).authorize(
            tenant_id=tenant,
            principal_id=principal,
            content_id=content_id,
            version_id=version_id,
            capability=CONTENT_PUBLISH,
        )
        with pytest.raises(PublicationForbidden):
            KernelPublicationAuthorization(kernel).authorize(
                tenant_id=tenant,
                principal_id=principal,
                content_id=content_id,
                version_id=version_id,
                capability=CONTENT_REVIEW_SUBMIT,
            )
        KernelAIGenerationAuthorization(kernel).authorize(
            tenant_id=tenant,
            principal_id=principal,
            content_id=content_id,
            capability=CONTENT_VERSION_CREATE,
        )
        with pytest.raises(AIGenerationForbidden):
            KernelAIGenerationAuthorization(kernel).authorize(
                tenant_id=tenant,
                principal_id=principal,
                content_id=content_id,
                capability=CONTENT_PUBLISH,
            )
        KernelContentMigrationAuthorization(kernel).authorize(
            tenant_id=tenant,
            principal_id=principal,
            capability=CONTENT_MIGRATE_IMPORT,
        )
        with pytest.raises(MigrationForbidden):
            KernelContentMigrationAuthorization(kernel).authorize(
                tenant_id=tenant,
                principal_id=principal,
                capability=CONTENT_PUBLISH,
            )
