"""GCI-I14 adversarial: tenancy, RLS pooled isolation, HTTP non-disclosure."""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from aieos.domains.content.application.audit import migration_audit_provenance
from sqlalchemy import text

from tests.dbutil import set_tenant
from tests.domains.content.adversarial.helpers import (
    assert_problem,
    client,
    create_content,
    decide,
    expect_dbapi,
    headers,
    in_review,
)
from tests.platform.workflows.helpers import append_version, submit_review

pytestmark = pytest.mark.gci_i14


class TestPooledTenantIsolation:
    def test_pooled_connection_tenant_a_then_b_no_visibility(
        self, runtime_engine, migration_runtime_engine, bootstrap_engine
    ) -> None:
        from tests.domains.content.application.test_gci_i13_import import (
            FIXED_NOW,
            _candidate,
            _event_context,
            _importer,
        )

        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        client_a = client(runtime_engine, tenant_a)
        client_b = client(runtime_engine, tenant_b)

        content_a, version_a, etag_a = in_review(client_a, tenant_a)
        approved_a = decide(
            client_a, tenant_a, content_a, version_a, action="approve", etag=etag_a
        )
        assert approved_a.status_code == 200
        hdrs = headers(tenant_a)
        hdrs["If-Match"] = approved_a.headers["ETag"]
        pub_a = client_a.post(
            f"/api/v1/contents/{content_a}/actions/publish",
            json={"version_id": version_a},
            headers=hdrs,
        )
        assert pub_a.status_code == 200, pub_a.text

        content_b, version_b, etag_b = in_review(client_b, tenant_b)
        approved_b = decide(
            client_b, tenant_b, content_b, version_b, action="approve", etag=etag_b
        )
        assert approved_b.status_code == 200

        principal_id = uuid.uuid7()
        _importer(migration_runtime_engine).import_content(
            tenant_a,
            principal_id,
            _candidate(source_resource_id="i14-tenant-a"),
            event_context=_event_context(),
            audit_provenance=migration_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        _importer(migration_runtime_engine).import_content(
            tenant_b,
            principal_id,
            _candidate(source_resource_id="i14-tenant-b"),
            event_context=_event_context(),
            audit_provenance=migration_audit_provenance(principal_id),
            now=FIXED_NOW,
        )

        with runtime_engine.connect() as conn:
            with conn.begin():
                set_tenant(conn, tenant_a)
                a_contents = {
                    row[0]
                    for row in conn.execute(text("SELECT content_id FROM content.contents"))
                }
                assert UUID(content_a) in a_contents
                assert UUID(content_b) not in a_contents
            # commit clears SET LOCAL tenant
            with conn.begin():
                expect_dbapi(
                    conn,
                    lambda: conn.execute(
                        text("SELECT content_id FROM content.contents")
                    ).fetchall(),
                    match="aieos.tenant_id is not set",
                )
            with conn.begin():
                set_tenant(conn, tenant_b)
                b_contents = {
                    row[0]
                    for row in conn.execute(text("SELECT content_id FROM content.contents"))
                }
                assert UUID(content_b) in b_contents
                assert UUID(content_a) not in b_contents

                b_versions = {
                    row[0]
                    for row in conn.execute(
                        text("SELECT version_id FROM content.content_versions")
                    )
                }
                assert UUID(version_b) in b_versions
                assert UUID(version_a) not in b_versions

                b_reviews = {
                    row[0]
                    for row in conn.execute(
                        text("SELECT content_id FROM content.review_decisions")
                    )
                }
                assert UUID(content_b) in b_reviews
                assert UUID(content_a) not in b_reviews

                b_pubs = {
                    row[0]
                    for row in conn.execute(
                        text("SELECT content_id FROM content.publications")
                    )
                }
                assert UUID(content_a) not in b_pubs

                b_idem = {
                    str(row[0])
                    for row in conn.execute(
                        text("SELECT tenant_id FROM api.idempotency_records")
                    )
                }
                assert str(tenant_a) not in b_idem

                b_starts = {
                    row[0]
                    for row in conn.execute(
                        text(
                            "SELECT input->>'content_id' FROM workflow.workflow_start_intents"
                        )
                    )
                }
                assert content_b in b_starts
                assert content_a not in b_starts

                mig = {
                    row[0]
                    for row in conn.execute(
                        text(
                            "SELECT source_resource_id FROM content.migration_import_records"
                        )
                    )
                }
                assert "i14-tenant-b" in mig
                assert "i14-tenant-a" not in mig

        # Outbox SELECT is withheld from ordinary runtime; verify via bootstrap + tenant filter.
        with bootstrap_engine.connect() as conn:
            a_outbox = conn.execute(
                text(
                    "SELECT count(*) FROM integration.outbox_messages WHERE tenant_id = :tid"
                ),
                {"tid": tenant_a},
            ).scalar_one()
            b_outbox = conn.execute(
                text(
                    "SELECT count(*) FROM integration.outbox_messages WHERE tenant_id = :tid"
                ),
                {"tid": tenant_b},
            ).scalar_one()
            assert int(a_outbox) >= 1
            assert int(b_outbox) >= 1


class TestHttpTenantBoundary:
    def test_body_query_tenant_id_cannot_override_trusted_context(
        self, runtime_engine
    ) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        client_a = client(runtime_engine, tenant_a)
        client_b = client(runtime_engine, tenant_b)
        created_b = create_content(client_b, tenant_b)
        content_b = created_b["content_id"]

        # Extra body/query tenant_id fields must not disclose Tenant B to Tenant A.
        leaked = client_a.get(
            f"/api/v1/contents/{content_b}",
            params={"tenant_id": str(tenant_b)},
            headers=headers(tenant_a),
        )
        assert_problem(leaked, status=404, code="content_not_found")

        create_attempt = client_a.post(
            "/api/v1/contents",
            json={
                "content_type": "test.generic",
                "title": "Title",
                "description": "Description",
                "locale": "en-IN",
                "tenant_id": str(tenant_b),
            },
            headers=headers(tenant_a),
        )
        # Extra field ignored or rejected; must not create under B.
        assert create_attempt.status_code in {201, 422}
        if create_attempt.status_code == 201:
            owned = client_b.get(
                f"/api/v1/contents/{create_attempt.json()['content_id']}",
                headers=headers(tenant_b),
            )
            assert_problem(owned, status=404, code="content_not_found")

    def test_cross_tenant_review_queue_cursor_and_detail(self, runtime_engine) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        client_a = client(runtime_engine, tenant_a)
        client_b = client(runtime_engine, tenant_b)
        content_a, version_a, _ = in_review(client_a, tenant_a)
        in_review(client_a, tenant_a)  # second item so cursor is issued
        page = client_a.get(
            "/api/v1/teacher-os/review-queue",
            params={"limit": 1},
            headers=headers(tenant_a),
        )
        assert page.status_code == 200
        next_cursor = page.json()["next_cursor"]
        assert next_cursor
        bad_cursor = client_b.get(
            "/api/v1/teacher-os/review-queue",
            params={"cursor": next_cursor},
            headers=headers(tenant_b),
        )
        assert_problem(bad_cursor, status=400, code="invalid_cursor")

        content_b, version_b, _ = in_review(client_b, tenant_b)
        detail = client_a.get(
            f"/api/v1/teacher-os/review-queue/{content_b}/versions/{version_b}",
            headers=headers(tenant_a),
        )
        assert_problem(detail, status=404, code="review_queue_item_not_found")
        _ = (content_a, version_a)
