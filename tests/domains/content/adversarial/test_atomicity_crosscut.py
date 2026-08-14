"""GCI-I14 adversarial: cross-cutting outbox failure atomicity."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from aieos.domains.content.application.errors import PersistenceOperationFailed
from aieos.platform.events.persistence.repositories import SqlAlchemyOutboxRepository
from tests.domains.content.adversarial.helpers import (
    client,
    content_row,
    create_content,
    decide,
    decision_count,
    headers,
    in_review,
    publication_count,
    submit_review,
)
from tests.domains.content.application.test_gci_i13_import import (
    FIXED_NOW,
    _candidate,
    _counts,
    _event_context,
    _importer,
)
from tests.platform.events.helpers import outbox_rows
from tests.platform.workflows.helpers import append_version

pytestmark = pytest.mark.gci_i14


def _boom_outbox(monkeypatch) -> None:
    def boom(self, *args, **kwargs):  # noqa: ANN001
        raise PersistenceOperationFailed("outbox insert failed")

    monkeypatch.setattr(SqlAlchemyOutboxRepository, "insert", boom)


class TestOutboxFailureRollbackCrosscut:
    def test_create_outbox_failure_rolls_back(
        self, runtime_engine, bootstrap_engine, monkeypatch
    ) -> None:
        tenant_id = uuid.uuid7()
        c = client(runtime_engine, tenant_id)
        _boom_outbox(monkeypatch)
        response = c.post(
            "/api/v1/contents",
            json={
                "content_type": "test.generic",
                "title": "Title",
                "description": "Description",
                "locale": "en-IN",
            },
            headers=headers(tenant_id),
        )
        assert response.status_code == 503
        with bootstrap_engine.connect() as conn:
            count = conn.execute(
                text("SELECT count(*) FROM content.contents WHERE tenant_id = :tid"),
                {"tid": tenant_id},
            ).scalar_one()
        assert int(count) == 0

    def test_append_outbox_failure_rolls_back(
        self, runtime_engine, bootstrap_engine, monkeypatch
    ) -> None:
        tenant_id = uuid.uuid7()
        c = client(runtime_engine, tenant_id)
        created = create_content(c, tenant_id)
        content_id = created["content_id"]
        before = len(outbox_rows(bootstrap_engine, content_id=content_id))
        _boom_outbox(monkeypatch)
        response = append_version(c, tenant_id, content_id, etag='"r0"')
        assert response.status_code == 503
        row = content_row(bootstrap_engine, content_id)
        assert row.current_version_id is None
        assert int(row.aggregate_revision) == 0
        assert len(outbox_rows(bootstrap_engine, content_id=content_id)) == before

    def test_review_decide_outbox_failure_rolls_back(
        self, runtime_engine, bootstrap_engine, monkeypatch
    ) -> None:
        tenant_id = uuid.uuid7()
        c = client(runtime_engine, tenant_id)
        content_id, version_id, etag = in_review(c, tenant_id)
        _boom_outbox(monkeypatch)
        response = decide(
            c, tenant_id, content_id, version_id, action="approve", etag=etag
        )
        assert response.status_code == 503
        assert decision_count(bootstrap_engine, content_id) == 0
        assert content_row(bootstrap_engine, content_id).stewardship_state == "IN_REVIEW"

    def test_publish_outbox_failure_rolls_back(
        self, runtime_engine, bootstrap_engine, monkeypatch
    ) -> None:
        tenant_id = uuid.uuid7()
        c = client(runtime_engine, tenant_id)
        content_id, version_id, etag = in_review(c, tenant_id)
        approved = decide(
            c, tenant_id, content_id, version_id, action="approve", etag=etag
        )
        assert approved.status_code == 200
        _boom_outbox(monkeypatch)
        hdrs = headers(tenant_id)
        hdrs["If-Match"] = approved.headers["ETag"]
        response = c.post(
            f"/api/v1/contents/{content_id}/actions/publish",
            json={"version_id": version_id},
            headers=hdrs,
        )
        assert response.status_code == 503
        assert publication_count(bootstrap_engine, content_id) == 0
        assert content_row(bootstrap_engine, content_id).published_version_id is None

    def test_migration_import_outbox_failure_rolls_back(
        self, migration_runtime_engine, bootstrap_engine, monkeypatch
    ) -> None:
        tenant_id = uuid.uuid7()
        _boom_outbox(monkeypatch)
        with pytest.raises(PersistenceOperationFailed):
            _importer(migration_runtime_engine).import_content(
                tenant_id,
                uuid.uuid7(),
                _candidate(source_resource_id="i14-outbox-mig"),
                event_context=_event_context(),
                now=FIXED_NOW,
            )
        counts = _counts(bootstrap_engine, tenant_id=tenant_id)
        assert counts["contents"] == 0
        assert counts["versions"] == 0
        # FAILED evidence may still be recorded for GCI-G12 identity; target must not exist.
        assert counts["created"] == 0
        assert counts["versioned"] == 0
