"""Opaque aggregate-revision ETag encoding."""

from __future__ import annotations


def encode_revision_etag(aggregate_revision: int) -> str:
    """Deterministic opaque ETag. Clients must treat the value as opaque."""
    return f'"r{aggregate_revision}"'
