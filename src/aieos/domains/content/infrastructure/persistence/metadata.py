"""SQLAlchemy metadata for the Generic Content PostgreSQL schema."""

from __future__ import annotations

from sqlalchemy import MetaData

CONTENT_SCHEMA = "content"

content_metadata = MetaData(schema=CONTENT_SCHEMA)
