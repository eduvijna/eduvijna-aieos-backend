"""SQLAlchemy metadata for the Assessment PostgreSQL schema."""

from __future__ import annotations

from sqlalchemy import MetaData

ASSESSMENT_SCHEMA = "assessment"

assessment_metadata = MetaData(schema=ASSESSMENT_SCHEMA)
