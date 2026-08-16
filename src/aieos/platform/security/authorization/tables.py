"""SQLAlchemy table definitions for ADR-AIEOS-031 security authority SoR."""

from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKeyConstraint, MetaData, Table, Text
from sqlalchemy.dialects.postgresql import UUID

from aieos.platform.security.audit.persistence.metadata import SECURITY_SCHEMA

# Separate MetaData so authority tables do not collide with audit_records Table.
authority_metadata = MetaData(schema=SECURITY_SCHEMA)

principals_table = Table(
    "principals",
    authority_metadata,
    Column("principal_id", UUID(as_uuid=True), primary_key=True, nullable=False),
    Column("status", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

tenants_table = Table(
    "tenants",
    authority_metadata,
    Column("tenant_id", UUID(as_uuid=True), primary_key=True, nullable=False),
    Column("status", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

tenant_memberships_table = Table(
    "tenant_memberships",
    authority_metadata,
    Column("tenant_id", UUID(as_uuid=True), primary_key=True, nullable=False),
    Column("principal_id", UUID(as_uuid=True), primary_key=True, nullable=False),
    Column("status", Text, nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=True),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["tenant_id"],
        [f"{SECURITY_SCHEMA}.tenants.tenant_id"],
        name="fk_security_tenant_memberships_tenant",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["principal_id"],
        [f"{SECURITY_SCHEMA}.principals.principal_id"],
        name="fk_security_tenant_memberships_principal",
        ondelete="RESTRICT",
    ),
)

capability_grants_table = Table(
    "capability_grants",
    authority_metadata,
    Column("tenant_id", UUID(as_uuid=True), primary_key=True, nullable=False),
    Column("principal_id", UUID(as_uuid=True), primary_key=True, nullable=False),
    Column("capability", Text, primary_key=True, nullable=False),
    Column("status", Text, nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=True),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["tenant_id", "principal_id"],
        [
            f"{SECURITY_SCHEMA}.tenant_memberships.tenant_id",
            f"{SECURITY_SCHEMA}.tenant_memberships.principal_id",
        ],
        name="fk_security_capability_grants_membership",
        ondelete="RESTRICT",
    ),
)
