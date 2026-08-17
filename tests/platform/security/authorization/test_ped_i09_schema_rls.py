"""PED-I09 security authority schema, constraints, and RLS proofs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from aieos.domains.content.application.ports import CONTENT_PUBLISH
from aieos.platform.security.authorization import (
    AIEOS_CONTENT_CAPABILITIES,
    AuthorizationKernel,
    AuthorityDecision,
)
from tests.conftest import alembic_config, provision_runtime_grants
from tests.dbutil import set_tenant
from tests.platform.security.authorization.helpers import (
    seed_active_authority,
    seed_grant,
    seed_membership,
    seed_principal,
    seed_tenant,
)

pytestmark = pytest.mark.ped_i09

AUTHORITY_TABLES = (
    "principals",
    "tenants",
    "tenant_memberships",
    "capability_grants",
)
TENANT_OWNED = ("tenants", "tenant_memberships", "capability_grants")


class TestMigrationHeadAndSchema:
    def test_alembic_head_is_pedi090001(self, bootstrap_engine) -> None:
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "pedi10b2001"
            )
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM pg_namespace WHERE nspname = 'security'"
                    )
                ).scalar_one()
                == 1
            )
            tables = {
                row[0]
                for row in conn.execute(
                    text(
                        """
                        SELECT table_name FROM information_schema.tables
                        WHERE table_schema = 'security'
                          AND table_type = 'BASE TABLE'
                        """
                    )
                )
            }
            for name in AUTHORITY_TABLES:
                assert name in tables
            assert "roles" not in tables
            assert "role_capabilities" not in tables
            assert "permissions" not in tables
            assert "delegations" not in tables
            assert "break_glass_grants" not in tables

    def test_downgrade_to_saii_then_reupgrade(
        self, postgres18, bootstrap_engine
    ) -> None:
        cfg = alembic_config(postgres18["migrator_url"])
        command.downgrade(cfg, "saii020001")
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "saii020001"
            )
            remaining = {
                row[0]
                for row in conn.execute(
                    text(
                        """
                        SELECT table_name FROM information_schema.tables
                        WHERE table_schema = 'security'
                          AND table_type = 'BASE TABLE'
                        """
                    )
                )
            }
            assert "principals" not in remaining
            assert "audit_records" in remaining
        command.upgrade(cfg, "head")
        provision_runtime_grants(bootstrap_engine)
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "pedi10b2001"
            )


class TestConstraints:
    def test_status_check_constraints(self, bootstrap_engine) -> None:
        principal = uuid.uuid7()
        tenant = uuid.uuid7()
        with bootstrap_engine.begin() as conn:
            with pytest.raises((IntegrityError, DBAPIError)):
                conn.execute(
                    text(
                        """
                        INSERT INTO security.principals
                        (principal_id, status, created_at, updated_at)
                        VALUES (:id, 'BOGUS', clock_timestamp(), clock_timestamp())
                        """
                    ),
                    {"id": principal},
                )
        seed_principal(bootstrap_engine, principal)
        with bootstrap_engine.begin() as conn:
            with pytest.raises((IntegrityError, DBAPIError)):
                conn.execute(
                    text(
                        """
                        INSERT INTO security.tenants
                        (tenant_id, status, created_at, updated_at)
                        VALUES (:id, 'BOGUS', clock_timestamp(), clock_timestamp())
                        """
                    ),
                    {"id": tenant},
                )
        seed_tenant(bootstrap_engine, tenant)
        with bootstrap_engine.begin() as conn:
            with pytest.raises((IntegrityError, DBAPIError)):
                conn.execute(
                    text(
                        """
                        INSERT INTO security.tenant_memberships (
                            tenant_id, principal_id, status,
                            created_at, updated_at
                        ) VALUES (
                            :tenant_id, :principal_id, 'BOGUS',
                            clock_timestamp(), clock_timestamp()
                        )
                        """
                    ),
                    {"tenant_id": tenant, "principal_id": principal},
                )
        seed_membership(
            bootstrap_engine, tenant_id=tenant, principal_id=principal
        )
        with bootstrap_engine.begin() as conn:
            with pytest.raises((IntegrityError, DBAPIError)):
                conn.execute(
                    text(
                        """
                        INSERT INTO security.capability_grants (
                            tenant_id, principal_id, capability, status,
                            created_at, updated_at
                        ) VALUES (
                            :tenant_id, :principal_id, 'content.publish', 'BOGUS',
                            clock_timestamp(), clock_timestamp()
                        )
                        """
                    ),
                    {"tenant_id": tenant, "principal_id": principal},
                )
            with pytest.raises((IntegrityError, DBAPIError)):
                conn.execute(
                    text(
                        """
                        INSERT INTO security.capability_grants (
                            tenant_id, principal_id, capability, status,
                            created_at, updated_at
                        ) VALUES (
                            :tenant_id, :principal_id, '   ', 'ACTIVE',
                            clock_timestamp(), clock_timestamp()
                        )
                        """
                    ),
                    {"tenant_id": tenant, "principal_id": principal},
                )

    def test_fk_restrict(self, bootstrap_engine) -> None:
        principal = uuid.uuid7()
        tenant = uuid.uuid7()
        seed_principal(bootstrap_engine, principal)
        seed_tenant(bootstrap_engine, tenant)
        seed_membership(
            bootstrap_engine, tenant_id=tenant, principal_id=principal
        )
        with bootstrap_engine.begin() as conn:
            with pytest.raises((IntegrityError, DBAPIError)):
                conn.execute(
                    text("DELETE FROM security.principals WHERE principal_id = :id"),
                    {"id": principal},
                )
            with pytest.raises((IntegrityError, DBAPIError)):
                conn.execute(
                    text("DELETE FROM security.tenants WHERE tenant_id = :id"),
                    {"id": tenant},
                )


class TestRls:
    def test_enable_force_and_policies(self, bootstrap_engine) -> None:
        with bootstrap_engine.connect() as conn:
            for table in TENANT_OWNED:
                row = conn.execute(
                    text(
                        """
                        SELECT c.relrowsecurity, c.relforcerowsecurity
                        FROM pg_class c
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = 'security' AND c.relname = :table
                        """
                    ),
                    {"table": table},
                ).one()
                assert row == (True, True)
                policies = conn.execute(
                    text(
                        """
                        SELECT polname FROM pg_policy p
                        JOIN pg_class c ON c.oid = p.polrelid
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = 'security' AND c.relname = :table
                        """
                    ),
                    {"table": table},
                ).scalars().all()
                assert policies
            principal_rls = conn.execute(
                text(
                    """
                    SELECT c.relrowsecurity, c.relforcerowsecurity
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'security' AND c.relname = 'principals'
                    """
                )
            ).one()
            assert principal_rls == (False, False)

    def test_missing_tenant_context_fails_closed_runtime(
        self, bootstrap_engine, runtime_engine
    ) -> None:
        tenant = uuid.uuid7()
        principal = uuid.uuid7()
        seed_active_authority(
            bootstrap_engine, tenant_id=tenant, principal_id=principal
        )
        for table in (
            "security.tenants",
            "security.tenant_memberships",
            "security.capability_grants",
        ):
            with runtime_engine.connect() as conn:
                with conn.begin():
                    with pytest.raises(DBAPIError, match="aieos.tenant_id"):
                        conn.execute(text(f"SELECT * FROM {table}")).fetchall()

    def test_tenant_isolation(self, bootstrap_engine, runtime_engine) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        principal_a = uuid.uuid7()
        principal_b = uuid.uuid7()
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_a,
            principal_id=principal_a,
            capabilities=(CONTENT_PUBLISH,),
        )
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_b,
            principal_id=principal_b,
            capabilities=(CONTENT_PUBLISH,),
        )
        with runtime_engine.connect() as conn:
            with conn.begin():
                set_tenant(conn, tenant_a)
                memberships = conn.execute(
                    text("SELECT tenant_id FROM security.tenant_memberships")
                ).scalars().all()
                grants = conn.execute(
                    text("SELECT tenant_id FROM security.capability_grants")
                ).scalars().all()
                tenants = conn.execute(
                    text("SELECT tenant_id FROM security.tenants")
                ).scalars().all()
        assert memberships == [tenant_a]
        assert grants == [tenant_a]
        assert tenants == [tenant_a]

    def test_pooled_connection_transaction_local_isolation(
        self, bootstrap_engine, runtime_engine
    ) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        principal = uuid.uuid7()
        seed_principal(bootstrap_engine, principal)
        seed_tenant(bootstrap_engine, tenant_a)
        seed_tenant(bootstrap_engine, tenant_b)
        seed_membership(
            bootstrap_engine, tenant_id=tenant_a, principal_id=principal
        )
        seed_membership(
            bootstrap_engine, tenant_id=tenant_b, principal_id=principal
        )
        with runtime_engine.connect() as conn:
            with conn.begin():
                set_tenant(conn, tenant_a)
                assert (
                    conn.execute(
                        text("SELECT count(*) FROM security.tenant_memberships")
                    ).scalar_one()
                    == 1
                )
            # After transaction ends, GUC must not leak into next transaction.
            with conn.begin():
                set_tenant(conn, tenant_b)
                rows = conn.execute(
                    text("SELECT tenant_id FROM security.tenant_memberships")
                ).scalars().all()
                assert rows == [tenant_b]

    def test_principals_global_readable(self, bootstrap_engine, runtime_engine) -> None:
        principal = uuid.uuid7()
        seed_principal(bootstrap_engine, principal)
        with runtime_engine.connect() as conn:
            with conn.begin():
                found = conn.execute(
                    text(
                        "SELECT principal_id FROM security.principals "
                        "WHERE principal_id = :id"
                    ),
                    {"id": principal},
                ).scalar_one()
                assert found == principal

    def test_db_rejects_wildcard_capabilities(self, bootstrap_engine) -> None:
        tenant = uuid.uuid7()
        principal = uuid.uuid7()
        seed_active_authority(
            bootstrap_engine, tenant_id=tenant, principal_id=principal
        )
        for capability in ("*", "content.*", "*.publish", "content.review.*"):
            with bootstrap_engine.begin() as conn:
                with pytest.raises((IntegrityError, DBAPIError)):
                    conn.execute(
                        text(
                            """
                            INSERT INTO security.capability_grants (
                                tenant_id, principal_id, capability, status,
                                created_at, updated_at
                            ) VALUES (
                                :tenant_id, :principal_id, :capability, 'ACTIVE',
                                clock_timestamp(), clock_timestamp()
                            )
                            """
                        ),
                        {
                            "tenant_id": tenant,
                            "principal_id": principal,
                            "capability": capability,
                        },
                    )

    def test_stored_wildcard_cannot_authorize_publish_even_if_constraint_bypassed(
        self, bootstrap_engine, runtime_engine
    ) -> None:
        """Corrupt-state simulation: drop CHECK, insert '*', prove no authority."""
        tenant = uuid.uuid7()
        principal = uuid.uuid7()
        seed_active_authority(
            bootstrap_engine, tenant_id=tenant, principal_id=principal
        )
        with bootstrap_engine.begin() as conn:
            conn.execute(
                text(
                    "ALTER TABLE security.capability_grants DROP CONSTRAINT "
                    "ck_security_capability_grants_capability_no_wildcard"
                )
            )
            conn.execute(
                text(
                    """
                    INSERT INTO security.capability_grants (
                        tenant_id, principal_id, capability, status,
                        created_at, updated_at
                    ) VALUES (
                        :tenant_id, :principal_id, '*', 'ACTIVE',
                        clock_timestamp(), clock_timestamp()
                    )
                    """
                ),
                {"tenant_id": tenant, "principal_id": principal},
            )
        try:
            kernel = AuthorizationKernel(
                runtime_engine, known_capabilities=AIEOS_CONTENT_CAPABILITIES
            )
            assert (
                kernel.decide_capability(
                    principal_id=principal,
                    tenant_id=tenant,
                    capability=CONTENT_PUBLISH,
                )
                is AuthorityDecision.DENY
            )
            assert (
                kernel.decide_capability(
                    principal_id=principal, tenant_id=tenant, capability="*"
                )
                is AuthorityDecision.DENY
            )
        finally:
            with bootstrap_engine.begin() as conn:
                conn.execute(
                    text(
                        "DELETE FROM security.capability_grants "
                        "WHERE capability LIKE '%*%'"
                    )
                )
                conn.execute(
                    text(
                        "ALTER TABLE security.capability_grants ADD CONSTRAINT "
                        "ck_security_capability_grants_capability_no_wildcard "
                        "CHECK (position('*' in capability) = 0)"
                    )
                )

    def test_exact_publish_grant_still_allows(
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
        kernel = AuthorizationKernel(
            runtime_engine, known_capabilities=AIEOS_CONTENT_CAPABILITIES
        )
        assert (
            kernel.decide_capability(
                principal_id=principal,
                tenant_id=tenant,
                capability=CONTENT_PUBLISH,
            )
            is AuthorityDecision.ALLOW
        )


class TestExpiryUsesDatabaseTime:
    def test_expired_membership_denied(
        self, bootstrap_engine, runtime_engine
    ) -> None:
        tenant = uuid.uuid7()
        principal = uuid.uuid7()
        past = datetime.now(UTC) - timedelta(hours=1)
        seed_principal(bootstrap_engine, principal)
        seed_tenant(bootstrap_engine, tenant)
        seed_membership(
            bootstrap_engine,
            tenant_id=tenant,
            principal_id=principal,
            expires_at=past,
        )
        kernel = AuthorizationKernel(
            runtime_engine, known_capabilities=AIEOS_CONTENT_CAPABILITIES
        )
        assert (
            kernel.decide_tenant_access(principal_id=principal, tenant_id=tenant)
            is AuthorityDecision.DENY
        )
