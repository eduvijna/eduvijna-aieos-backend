"""SQLAlchemy metadata for the Asset-owned PostgreSQL schema."""

from __future__ import annotations

from sqlalchemy import MetaData

ASSET_SCHEMA = "asset"

asset_metadata = MetaData(schema=ASSET_SCHEMA)
