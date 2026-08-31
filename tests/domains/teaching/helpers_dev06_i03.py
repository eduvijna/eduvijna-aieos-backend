"""Shared fixtures for TOS-DEV06-I03 TeachingAssignment application tests."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.domain.version import ContentPayload, canonical_payload_json
from aieos.domains.education.schema import WORKSHEET_CONTENT_TYPE

FIXED_NOW = datetime(2026, 8, 31, 14, 0, tzinfo=UTC)
IDEMPOTENCY_RETENTION = timedelta(hours=24)


def seed_published_worksheet(
    bootstrap_engine: Engine,
    *,
    tenant_id: UUID,
    owner_id: UUID | None = None,
) -> tuple[UUID, UUID]:
    """Insert APPROVED+published worksheet Content head and return (content_id, version_id)."""
    content_id = uuid.uuid7()
    version_id = uuid.uuid7()
    owner = owner_id or uuid.uuid7()
    payload = ContentPayload.from_mapping({"marker": "i03-assignment"})
    with bootstrap_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO content.contents (
                    content_id, tenant_id, owner_principal_id, content_type, title,
                    description, locale, stewardship_state, current_version_id,
                    published_version_id, aggregate_revision, created_at,
                    created_by_principal_id, updated_at, archived_at
                ) VALUES (
                    :content_id, :tenant_id, :owner, :content_type, 'Worksheet',
                    'Description', 'en-IN', 'APPROVED', :version_id,
                    :version_id, 1, :now, :owner, :now, NULL
                )
                """
            ),
            {
                "content_id": content_id,
                "tenant_id": tenant_id,
                "owner": owner,
                "content_type": WORKSHEET_CONTENT_TYPE,
                "version_id": version_id,
                "now": FIXED_NOW,
            },
        )
        conn.execute(
            text(
                """
                INSERT INTO content.content_versions (
                    version_id, tenant_id, content_id, version_number, parent_version_id,
                    schema_id, schema_version, payload, payload_sha256, origin,
                    provenance, created_at, created_by_principal_id
                ) VALUES (
                    :vid, :tid, :cid, 1, NULL,
                    'education.worksheet', 1, CAST(:payload AS jsonb),
                    :sha, 'HUMAN',
                    CAST(:prov AS jsonb), :now, :actor
                )
                """
            ),
            {
                "vid": version_id,
                "tid": tenant_id,
                "cid": content_id,
                "payload": canonical_payload_json(payload.body),
                "sha": payload.sha256.value,
                "prov": json.dumps({}),
                "now": FIXED_NOW,
                "actor": owner,
            },
        )
        decision_id = uuid.uuid7()
        conn.execute(
            text(
                """
                INSERT INTO content.review_decisions (
                    review_decision_id, tenant_id, content_id, version_id,
                    decision, comment, decided_at, reviewer_principal_id,
                    effective_actor_id, correlation_id
                ) VALUES (
                    :did, :tid, :cid, :vid, 'APPROVE', NULL, :now, :actor, :actor, :corr
                )
                """
            ),
            {
                "did": decision_id,
                "tid": tenant_id,
                "cid": content_id,
                "vid": version_id,
                "now": FIXED_NOW,
                "actor": owner,
                "corr": uuid.uuid7(),
            },
        )
        pub_id = uuid.uuid7()
        conn.execute(
            text(
                """
                INSERT INTO content.publications (
                    publication_id, tenant_id, content_id, version_id,
                    approval_decision_id, published_by_principal_id,
                    effective_actor_id, published_at, correlation_id
                ) VALUES (
                    :pid, :tid, :cid, :vid, :did, :actor, :actor, :now, :corr
                )
                """
            ),
            {
                "pid": pub_id,
                "tid": tenant_id,
                "cid": content_id,
                "vid": version_id,
                "did": decision_id,
                "now": FIXED_NOW,
                "actor": owner,
                "corr": uuid.uuid7(),
            },
        )
    return content_id, version_id


def republish_content_to_new_version(
    bootstrap_engine: Engine,
    *,
    tenant_id: UUID,
    content_id: UUID,
    owner_id: UUID,
) -> UUID:
    """Insert version 2 and move published pointer off version 1 (race CASE A)."""
    version_v2 = uuid.uuid7()
    payload = ContentPayload.from_mapping({"marker": "i03-assignment-v2"})
    with bootstrap_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO content.content_versions (
                    version_id, tenant_id, content_id, version_number, parent_version_id,
                    schema_id, schema_version, payload, payload_sha256, origin,
                    provenance, created_at, created_by_principal_id
                ) VALUES (
                    :vid, :tid, :cid, 2, NULL,
                    'education.worksheet', 1, CAST(:payload AS jsonb),
                    :sha, 'HUMAN',
                    CAST(:prov AS jsonb), :now, :actor
                )
                """
            ),
            {
                "vid": version_v2,
                "tid": tenant_id,
                "cid": content_id,
                "payload": canonical_payload_json(payload.body),
                "sha": payload.sha256.value,
                "prov": json.dumps({}),
                "now": FIXED_NOW,
                "actor": owner_id,
            },
        )
        conn.execute(
            text(
                """
                UPDATE content.contents
                SET published_version_id = :v2,
                    current_version_id = :v2,
                    aggregate_revision = aggregate_revision + 1,
                    updated_at = :now
                WHERE tenant_id = :tid AND content_id = :cid
                """
            ),
            {
                "v2": version_v2,
                "tid": tenant_id,
                "cid": content_id,
                "now": FIXED_NOW,
            },
        )
    return version_v2
