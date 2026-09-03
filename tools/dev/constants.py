"""Shared local-development PostgreSQL constants.

LOCAL DEVELOPMENT ONLY — NEVER PRODUCTION.
"""

from __future__ import annotations

CONTAINER_NAME = "aieos-local-postgres"
VOLUME_NAME = "aieos-local-postgres-data"
POSTGRES_IMAGE = "postgres:18"
BOOTSTRAP_USER = "aieos_bootstrap"
SCHEMA_OWNER_ROLE = "aieos_content_owner"
SECURITY_SCHEMA_OWNER_ROLE = "aieos_security_owner"
ASSET_SCHEMA_OWNER_ROLE = "aieos_asset_owner"
MIGRATOR_USER = "aieos_migrator"
RUNTIME_USER = "aieos_runtime"
MIGRATION_RUNTIME_USER = "aieos_content_migration_runtime"
WORKFLOW_DISPATCHER_USER = "aieos_workflow_dispatcher"
EVENT_DISPATCHER_USER = "aieos_event_dispatcher"
EVENT_CANDIDATE_READER_ROLE = "aieos_event_candidate_reader"
WORKFLOW_CANDIDATE_READER_ROLE = "aieos_workflow_candidate_reader"
DB_NAME = "aieos"
DB_PASSWORD = "aieos_test"
HOST = "127.0.0.1"
HOST_PORT = "55432"
EXPECTED_ALEMBIC_HEAD = "tosd070002"

ALLOWED_DB_HOSTS = frozenset({"127.0.0.1", "localhost"})

# Obvious production / managed-service host substrings — local tooling must reject these.
FORBIDDEN_DB_HOST_FRAGMENTS = (
    ".rds.amazonaws.com",
    ".postgres.database.azure.com",
    ".cloudsql",
    ".neon.tech",
    ".supabase.co",
    ".elephantsql.com",
    ".cockroachlabs.cloud",
)
