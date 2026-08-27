"""SQLAlchemy metadata for the AI PostgreSQL schema."""

from __future__ import annotations

from sqlalchemy import MetaData

AI_SCHEMA = "ai"

ai_metadata = MetaData(schema=AI_SCHEMA)
