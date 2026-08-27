"""SQLAlchemy metadata for the Teaching PostgreSQL schema."""

from __future__ import annotations

from sqlalchemy import MetaData

TEACHING_SCHEMA = "teaching"

teaching_metadata = MetaData(schema=TEACHING_SCHEMA)
