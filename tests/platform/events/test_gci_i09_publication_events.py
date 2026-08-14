"""GCI-I09 content.published.v1 outbox events."""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.application.errors import PersistenceOperationFailed
from aieos.platform.events.constants import EVENT_CONTENT_PUBLISHED_V1
from aieos.platform.events.persistence.repositories import SqlAlchemyOutboxRepository
from tests.platform.events.helpers import (
    assert_contract_compatible,
    assert_no_sensitive_material,
    client_for,
    outbox_rows,
)
from tests.platform.workflows.helpers import (
    append_version,
    create_content,
    decide,
    headers,
    in_review,
    submit_review,
)

pytestmark = pytest.mark.gci_i09


def _is_uuid7(value: UUID | str) -> bool:
    parsed = value if isinstance(value, UUID) else UUID(str(value))
    return parsed.version == 7


def _publish(
    client,
    tenant_id: UUID,
    content_id: str,
    version_id: str,
    *,
    etag: str,
    **extra: str,
):
    hdrs = headers(tenant_id, **extra)
    hdrs["If-Match"] = etag
    return client.post(
        f"/api/v1/contents/{content_id}/actions/publish",
        json={"version_id": version_id},
        headers=hdrs,
    )


def _approved(client, tenant_id: UUID) -> tuple[str, str, str]:
    content_id, version_id, etag = in_review(client, tenant_id)
    approved = decide(
        client, tenant_id, content_id, version_id, action="approve", etag=etag
    )
    assert approved.status_code == 200, approved.text
    return content_id, version_id, approved.headers["ETag"]


def _publication_count(bootstrap_engine: Engine, content_id: str) -> int:
    with bootstrap_engine.connect() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT count(*) FROM content.publications WHERE content_id = :cid"
                ),
                {"cid": content_id},
            ).scalar_one()
        )


def _content_row(bootstrap_engine: Engine, content_id: str):
    with bootstrap_engine.connect() as conn:
        return conn.execute(
            text(
                """
                SELECT stewardship_state, aggregate_revision, published_version_id
                FROM content.contents WHERE content_id = :cid
                """
            ),
            {"cid": content_id},
        ).one()


def _idempotency_count(bootstrap_engine: Engine, tenant_id: UUID) -> int:
    with bootstrap_engine.connect() as conn:
        return int(
            conn.execute(
                text(
                    """
                    SELECT count(*) FROM api.idempotency_records
                    WHERE tenant_id = :tid AND operation = 'content_publish.v1'
                    """
                ),
                {"tid": tenant_id},
            ).scalar_one()
        )


class TestPublishedEvents:
    def test_publish_emits_contract_safe_event(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = client_for(runtime_engine, tenant_id, uuid.uuid7())
        content_id, version_id, etag = _approved(client, tenant_id)
        response = _publish(client, tenant_id, content_id, version_id, etag=etag)
        assert response.status_code == 200, response.text
        rows = outbox_rows(bootstrap_engine, content_id=content_id)
        published = [row for row in rows if row["event_type"] == EVENT_CONTENT_PUBLISHED_V1]
        assert len(published) == 1
        row = published[0]
        assert int(row["aggregate_revision"]) == 4
        assert int(row["aggregate_revision"]) == response.json()["aggregate_revision"]
        envelope = dict(row["envelope"])
        assert envelope["aggregaterevision"] == 4
        assert set(envelope["data"]) == {
            "content_id",
            "published_version_id",
            "publication_id",
        }
        assert envelope["data"]["content_id"] == content_id
        assert envelope["data"]["published_version_id"] == version_id
        assert envelope["data"]["publication_id"] == response.json()["publication_id"]
        assert _is_uuid7(envelope["data"]["publication_id"])
        assert_contract_compatible(envelope, event_type=EVENT_CONTENT_PUBLISHED_V1)
        assert_no_sensitive_material(envelope)
        blob = str(envelope)
        for needle in ("marker", "Title", "Description", "payload", "SENSITIVE"):
            assert needle not in blob

    def test_idempotent_replay_no_second_event(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = client_for(runtime_engine, tenant_id, uuid.uuid7())
        content_id, version_id, etag = _approved(client, tenant_id)
        key = f"publish-{uuid.uuid7()}"
        first = _publish(
            client,
            tenant_id,
            content_id,
            version_id,
            etag=etag,
            **{"Idempotency-Key": key},
        )
        assert first.status_code == 200, first.text
        replay = _publish(
            client,
            tenant_id,
            content_id,
            version_id,
            etag=etag,
            **{"Idempotency-Key": key},
        )
        assert replay.status_code == 200
        published = [
            row
            for row in outbox_rows(bootstrap_engine, content_id=content_id)
            if row["event_type"] == EVENT_CONTENT_PUBLISHED_V1
        ]
        assert len(published) == 1

    def test_outbox_failure_full_rollback(
        self, runtime_engine, bootstrap_engine, monkeypatch
    ) -> None:
        tenant_id = uuid.uuid7()
        client = client_for(runtime_engine, tenant_id, uuid.uuid7())
        content_id, version_id, etag = _approved(client, tenant_id)
        before = _content_row(bootstrap_engine, content_id)
        before_outbox = len(outbox_rows(bootstrap_engine, content_id=content_id))

        def boom(self, message) -> None:
            raise PersistenceOperationFailed("inject outbox insert failure")

        monkeypatch.setattr(SqlAlchemyOutboxRepository, "insert", boom)
        response = _publish(client, tenant_id, content_id, version_id, etag=etag)
        assert response.status_code == 503, response.text
        after = _content_row(bootstrap_engine, content_id)
        assert after.published_version_id is None
        assert int(after.aggregate_revision) == int(before.aggregate_revision)
        assert _publication_count(bootstrap_engine, content_id) == 0
        assert _idempotency_count(bootstrap_engine, tenant_id) == 0
        assert len(outbox_rows(bootstrap_engine, content_id=content_id)) == before_outbox

    def test_revision_sequence_includes_publish(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = client_for(runtime_engine, tenant_id, uuid.uuid7())
        created = create_content(client, tenant_id)
        content_id = created["content_id"]
        appended = append_version(client, tenant_id, content_id, etag='"r0"')
        assert appended.status_code == 201, appended.text
        version_id = appended.json()["version_id"]
        submitted = submit_review(
            client, tenant_id, content_id, version_id, etag=appended.headers["ETag"]
        )
        assert submitted.status_code == 200, submitted.text
        approved = decide(
            client,
            tenant_id,
            content_id,
            version_id,
            action="approve",
            etag=submitted.headers["ETag"],
        )
        assert approved.status_code == 200, approved.text
        published = _publish(
            client,
            tenant_id,
            content_id,
            version_id,
            etag=approved.headers["ETag"],
        )
        assert published.status_code == 200, published.text
        rows = outbox_rows(bootstrap_engine, content_id=content_id)
        assert [int(row["aggregate_revision"]) for row in rows] == [0, 1, 2, 3, 4]
        assert rows[-1]["event_type"] == EVENT_CONTENT_PUBLISHED_V1
        event_ids = [row["event_id"] for row in rows]
        assert len(set(event_ids)) == 5
        assert all(_is_uuid7(event_id) for event_id in event_ids)
