"""SQLAlchemy metadata for the platform security PostgreSQL schema."""

from __future__ import annotations

from sqlalchemy import MetaData

SECURITY_SCHEMA = "security"

security_metadata = MetaData(schema=SECURITY_SCHEMA)
