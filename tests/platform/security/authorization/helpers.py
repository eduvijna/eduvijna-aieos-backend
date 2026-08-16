"""TEST-ONLY security authority seed helpers (not production control-plane)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from aieos.platform.security.authorization.decisions import (
    GrantStatus,
    MembershipStatus,
    PrincipalStatus,
    TenantStatus,
)


def seed_principal(
    engine: Engine,
    principal_id: UUID,
    *,
    status: str = PrincipalStatus.ACTIVE,
    now: datetime | None = None,
) -> None:
    with engine.begin() as conn:
        _upsert_principal(conn, principal_id, status=status, now=now)


def seed_tenant(
    engine: Engine,
    tenant_id: UUID,
    *,
    status: str = TenantStatus.ACTIVE,
    now: datetime | None = None,
) -> None:
    with engine.begin() as conn:
        _upsert_tenant(conn, tenant_id, status=status, now=now)


def seed_membership(
    engine: Engine,
    *,
    tenant_id: UUID,
    principal_id: UUID,
    status: str = MembershipStatus.ACTIVE,
    expires_at: datetime | None = None,
    revoked_at: datetime | None = None,
    now: datetime | None = None,
) -> None:
    with engine.begin() as conn:
        _upsert_membership(
            conn,
            tenant_id=tenant_id,
            principal_id=principal_id,
            status=status,
            expires_at=expires_at,
            revoked_at=revoked_at,
            now=now,
        )


def seed_grant(
    engine: Engine,
    *,
    tenant_id: UUID,
    principal_id: UUID,
    capability: str,
    status: str = GrantStatus.ACTIVE,
    expires_at: datetime | None = None,
    revoked_at: datetime | None = None,
    now: datetime | None = None,
) -> None:
    with engine.begin() as conn:
        _upsert_grant(
            conn,
            tenant_id=tenant_id,
            principal_id=principal_id,
            capability=capability,
            status=status,
            expires_at=expires_at,
            revoked_at=revoked_at,
            now=now,
        )


def seed_active_authority(
    engine: Engine,
    *,
    tenant_id: UUID,
    principal_id: UUID,
    capabilities: tuple[str, ...] = (),
) -> None:
    """Seed ACTIVE principal + tenant + membership (+ optional ACTIVE grants)."""
    with engine.begin() as conn:
        _upsert_principal(conn, principal_id)
        _upsert_tenant(conn, tenant_id)
        _upsert_membership(
            conn, tenant_id=tenant_id, principal_id=principal_id
        )
        for capability in capabilities:
            _upsert_grant(
                conn,
                tenant_id=tenant_id,
                principal_id=principal_id,
                capability=capability,
            )


def revoke_membership(
    engine: Engine, *, tenant_id: UUID, principal_id: UUID
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE security.tenant_memberships
                SET status = :status,
                    revoked_at = clock_timestamp(),
                    updated_at = clock_timestamp()
                WHERE tenant_id = :tenant_id AND principal_id = :principal_id
                """
            ),
            {
                "status": MembershipStatus.REVOKED,
                "tenant_id": tenant_id,
                "principal_id": principal_id,
            },
        )


def revoke_grant(
    engine: Engine,
    *,
    tenant_id: UUID,
    principal_id: UUID,
    capability: str,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE security.capability_grants
                SET status = :status,
                    revoked_at = clock_timestamp(),
                    updated_at = clock_timestamp()
                WHERE tenant_id = :tenant_id
                  AND principal_id = :principal_id
                  AND capability = :capability
                """
            ),
            {
                "status": GrantStatus.REVOKED,
                "tenant_id": tenant_id,
                "principal_id": principal_id,
                "capability": capability,
            },
        )


def _ts(now: datetime | None) -> datetime | None:
    return now


def _upsert_principal(
    conn: Connection,
    principal_id: UUID,
    *,
    status: str = PrincipalStatus.ACTIVE,
    now: datetime | None = None,
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO security.principals (
                principal_id, status, created_at, updated_at
            ) VALUES (
                :principal_id, :status,
                COALESCE(:now, clock_timestamp()),
                COALESCE(:now, clock_timestamp())
            )
            ON CONFLICT (principal_id) DO UPDATE SET
                status = EXCLUDED.status,
                updated_at = EXCLUDED.updated_at
            """
        ),
        {"principal_id": principal_id, "status": status, "now": _ts(now)},
    )


def _upsert_tenant(
    conn: Connection,
    tenant_id: UUID,
    *,
    status: str = TenantStatus.ACTIVE,
    now: datetime | None = None,
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO security.tenants (
                tenant_id, status, created_at, updated_at
            ) VALUES (
                :tenant_id, :status,
                COALESCE(:now, clock_timestamp()),
                COALESCE(:now, clock_timestamp())
            )
            ON CONFLICT (tenant_id) DO UPDATE SET
                status = EXCLUDED.status,
                updated_at = EXCLUDED.updated_at
            """
        ),
        {"tenant_id": tenant_id, "status": status, "now": _ts(now)},
    )


def _upsert_membership(
    conn: Connection,
    *,
    tenant_id: UUID,
    principal_id: UUID,
    status: str = MembershipStatus.ACTIVE,
    expires_at: datetime | None = None,
    revoked_at: datetime | None = None,
    now: datetime | None = None,
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO security.tenant_memberships (
                tenant_id, principal_id, status,
                expires_at, revoked_at, created_at, updated_at
            ) VALUES (
                :tenant_id, :principal_id, :status,
                :expires_at, :revoked_at,
                COALESCE(:now, clock_timestamp()),
                COALESCE(:now, clock_timestamp())
            )
            ON CONFLICT (tenant_id, principal_id) DO UPDATE SET
                status = EXCLUDED.status,
                expires_at = EXCLUDED.expires_at,
                revoked_at = EXCLUDED.revoked_at,
                updated_at = EXCLUDED.updated_at
            """
        ),
        {
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "status": status,
            "expires_at": expires_at,
            "revoked_at": revoked_at,
            "now": _ts(now),
        },
    )


def _upsert_grant(
    conn: Connection,
    *,
    tenant_id: UUID,
    principal_id: UUID,
    capability: str,
    status: str = GrantStatus.ACTIVE,
    expires_at: datetime | None = None,
    revoked_at: datetime | None = None,
    now: datetime | None = None,
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO security.capability_grants (
                tenant_id, principal_id, capability, status,
                expires_at, revoked_at, created_at, updated_at
            ) VALUES (
                :tenant_id, :principal_id, :capability, :status,
                :expires_at, :revoked_at,
                COALESCE(:now, clock_timestamp()),
                COALESCE(:now, clock_timestamp())
            )
            ON CONFLICT (tenant_id, principal_id, capability) DO UPDATE SET
                status = EXCLUDED.status,
                expires_at = EXCLUDED.expires_at,
                revoked_at = EXCLUDED.revoked_at,
                updated_at = EXCLUDED.updated_at
            """
        ),
        {
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "capability": capability,
            "status": status,
            "expires_at": expires_at,
            "revoked_at": revoked_at,
            "now": _ts(now),
        },
    )
