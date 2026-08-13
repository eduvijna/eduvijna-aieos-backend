"""SQLAlchemy metadata for the platform API PostgreSQL schema."""

from __future__ import annotations

from sqlalchemy import MetaData

API_SCHEMA = "api"

api_metadata = MetaData(schema=API_SCHEMA)
