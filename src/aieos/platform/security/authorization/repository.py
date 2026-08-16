"""SQLAlchemy read adapter for ADR-AIEOS-031 security authority SoR."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, select, text
from sqlalchemy.engine import Connection, Engine

from aieos.platform.security.authorization.session import security_authority_read
from aieos.platform.security.authorization.tables import (
    capability_grants_table,
    principals_table,
    tenant_memberships_table,
    tenants_table,
)
from aieos.platform.security.context import AuthorizationUnavailableError


@dataclass(frozen=True, slots=True)
class PrincipalAuthorityRow:
    principal_id: UUID
    status: str


@dataclass(frozen=True, slots=True)
class TenantAuthorityRow:
    tenant_id: UUID
    status: str


@dataclass(frozen=True, slots=True)
class MembershipAuthorityRow:
    tenant_id: UUID
    principal_id: UUID
    status: str
    expires_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True, slots=True)
class GrantAuthorityRow:
    tenant_id: UUID
    principal_id: UUID
    capability: str
    status: str
    expires_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True, slots=True)
class TenantAccessBundle:
    principal: PrincipalAuthorityRow | None
    tenant: TenantAuthorityRow | None
    membership: MembershipAuthorityRow | None
    evaluated_at: datetime


@dataclass(frozen=True, slots=True)
class CapabilityBundle:
    principal: PrincipalAuthorityRow | None
    tenant: TenantAuthorityRow | None
    membership: MembershipAuthorityRow | None
    grant: GrantAuthorityRow | None
    evaluated_at: datetime


class SqlAlchemySecurityAuthorityRepository:
    """Current-authority reads. No decision cache. No Content UoW."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def load_tenant_access_bundle(
        self, *, principal_id: UUID, tenant_id: UUID
    ) -> TenantAccessBundle:
        try:
            with security_authority_read(
                self._engine, query_tenant_id=tenant_id
            ) as conn:
                now = self._fetch_now(conn)
                return TenantAccessBundle(
                    principal=self._fetch_principal(conn, principal_id),
                    tenant=self._fetch_tenant(conn, tenant_id),
                    membership=self._fetch_membership(
                        conn, principal_id=principal_id, tenant_id=tenant_id
                    ),
                    evaluated_at=now,
                )
        except AuthorizationUnavailableError:
            raise
        except Exception as exc:
            raise AuthorizationUnavailableError(
                "authorization unavailable"
            ) from exc

    def load_capability_bundle(
        self, *, principal_id: UUID, tenant_id: UUID, capability: str
    ) -> CapabilityBundle:
        try:
            with security_authority_read(
                self._engine, query_tenant_id=tenant_id
            ) as conn:
                now = self._fetch_now(conn)
                return CapabilityBundle(
                    principal=self._fetch_principal(conn, principal_id),
                    tenant=self._fetch_tenant(conn, tenant_id),
                    membership=self._fetch_membership(
                        conn, principal_id=principal_id, tenant_id=tenant_id
                    ),
                    grant=self._fetch_grant(
                        conn,
                        principal_id=principal_id,
                        tenant_id=tenant_id,
                        capability=capability,
                    ),
                    evaluated_at=now,
                )
        except AuthorizationUnavailableError:
            raise
        except Exception as exc:
            raise AuthorizationUnavailableError(
                "authorization unavailable"
            ) from exc

    def _fetch_now(self, conn: Connection) -> datetime:
        value = conn.execute(text("SELECT clock_timestamp()")).scalar_one()
        if not isinstance(value, datetime):
            raise AuthorizationUnavailableError("authorization unavailable")
        return value

    def _fetch_principal(
        self, conn: Connection, principal_id: UUID
    ) -> PrincipalAuthorityRow | None:
        row = (
            conn.execute(
                select(
                    principals_table.c.principal_id,
                    principals_table.c.status,
                ).where(principals_table.c.principal_id == principal_id)
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        status = row["status"]
        if not isinstance(status, str):
            raise AuthorizationUnavailableError("authorization unavailable")
        return PrincipalAuthorityRow(
            principal_id=row["principal_id"], status=status
        )

    def _fetch_tenant(
        self, conn: Connection, tenant_id: UUID
    ) -> TenantAuthorityRow | None:
        row = (
            conn.execute(
                select(tenants_table.c.tenant_id, tenants_table.c.status).where(
                    tenants_table.c.tenant_id == tenant_id
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        status = row["status"]
        if not isinstance(status, str):
            raise AuthorizationUnavailableError("authorization unavailable")
        return TenantAuthorityRow(tenant_id=row["tenant_id"], status=status)

    def _fetch_membership(
        self, conn: Connection, *, principal_id: UUID, tenant_id: UUID
    ) -> MembershipAuthorityRow | None:
        row = (
            conn.execute(
                select(
                    tenant_memberships_table.c.tenant_id,
                    tenant_memberships_table.c.principal_id,
                    tenant_memberships_table.c.status,
                    tenant_memberships_table.c.expires_at,
                    tenant_memberships_table.c.revoked_at,
                ).where(
                    and_(
                        tenant_memberships_table.c.tenant_id == tenant_id,
                        tenant_memberships_table.c.principal_id == principal_id,
                    )
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        status = row["status"]
        if not isinstance(status, str):
            raise AuthorizationUnavailableError("authorization unavailable")
        return MembershipAuthorityRow(
            tenant_id=row["tenant_id"],
            principal_id=row["principal_id"],
            status=status,
            expires_at=row["expires_at"],
            revoked_at=row["revoked_at"],
        )

    def _fetch_grant(
        self,
        conn: Connection,
        *,
        principal_id: UUID,
        tenant_id: UUID,
        capability: str,
    ) -> GrantAuthorityRow | None:
        row = (
            conn.execute(
                select(
                    capability_grants_table.c.tenant_id,
                    capability_grants_table.c.principal_id,
                    capability_grants_table.c.capability,
                    capability_grants_table.c.status,
                    capability_grants_table.c.expires_at,
                    capability_grants_table.c.revoked_at,
                ).where(
                    and_(
                        capability_grants_table.c.tenant_id == tenant_id,
                        capability_grants_table.c.principal_id == principal_id,
                        capability_grants_table.c.capability == capability,
                    )
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        status = row["status"]
        cap = row["capability"]
        if not isinstance(status, str) or not isinstance(cap, str):
            raise AuthorizationUnavailableError("authorization unavailable")
        return GrantAuthorityRow(
            tenant_id=row["tenant_id"],
            principal_id=row["principal_id"],
            capability=cap,
            status=status,
            expires_at=row["expires_at"],
            revoked_at=row["revoked_at"],
        )


def membership_is_currently_valid(
    membership: MembershipAuthorityRow, *, now: datetime
) -> bool:
    from aieos.platform.security.authorization.decisions import MembershipStatus

    if membership.status != MembershipStatus.ACTIVE:
        return False
    if membership.revoked_at is not None:
        return False
    if membership.expires_at is not None and membership.expires_at <= now:
        return False
    return True


def grant_is_currently_valid(
    grant: GrantAuthorityRow, *, now: datetime
) -> bool:
    from aieos.platform.security.authorization.decisions import GrantStatus

    if grant.status != GrantStatus.ACTIVE:
        return False
    if grant.revoked_at is not None:
        return False
    if grant.expires_at is not None and grant.expires_at <= now:
        return False
    return True
