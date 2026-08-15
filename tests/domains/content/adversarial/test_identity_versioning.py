"""GCI-I14 adversarial: identity, immutability, lineage, concurrency."""

from __future__ import annotations

import threading
import uuid
from uuid import UUID

import pytest
from aieos.domains.content.application.audit import migration_audit_provenance
from sqlalchemy import text

from tests.dbutil import set_tenant
from tests.domains.content.adversarial.helpers import (
    assert_problem,
    client,
    content_row,
    create_content,
    decide,
    expect_dbapi,
    headers,
    in_review,
    version_row,
)
from tests.platform.events.helpers import outbox_rows
from tests.platform.workflows.helpers import append_version

pytestmark = pytest.mark.gci_i14


def _seed_version_via_http(runtime_engine, tenant_id: UUID) -> tuple[str, str]:
    c = client(runtime_engine, tenant_id)
    created = create_content(c, tenant_id)
    content_id = created["content_id"]
    appended = append_version(c, tenant_id, content_id, etag='"r0"')
    assert appended.status_code == 201, appended.text
    return content_id, appended.json()["version_id"]


class TestImmutableFactsDenied:
    def test_content_version_update_delete_denied_runtime(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        content_id, version_id = _seed_version_via_http(runtime_engine, tenant_id)
        with runtime_engine.connect() as conn:
            with conn.begin():
                set_tenant(conn, tenant_id)
                expect_dbapi(
                    conn,
                    lambda: conn.execute(
                        text(
                            "UPDATE content.content_versions SET origin = 'SYSTEM' "
                            "WHERE version_id = :vid"
                        ),
                        {"vid": version_id},
                    ),
                    match="permission denied",
                )
                expect_dbapi(
                    conn,
                    lambda: conn.execute(
                        text(
                            "DELETE FROM content.content_versions WHERE version_id = :vid"
                        ),
                        {"vid": version_id},
                    ),
                    match="permission denied",
                )
        before = version_row(bootstrap_engine, version_id)
        assert before.origin == "HUMAN"
        assert content_row(bootstrap_engine, content_id).current_version_id == UUID(
            version_id
        )

    def test_review_publication_asset_update_delete_denied(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        c = client(runtime_engine, tenant_id)
        content_id, version_id, etag = in_review(c, tenant_id)
        approved = decide(
            c, tenant_id, content_id, version_id, action="approve", etag=etag
        )
        assert approved.status_code == 200, approved.text
        hdrs = headers(tenant_id)
        hdrs["If-Match"] = approved.headers["ETag"]
        published = c.post(
            f"/api/v1/contents/{content_id}/actions/publish",
            json={"version_id": version_id},
            headers=hdrs,
        )
        assert published.status_code == 200, published.text
        with bootstrap_engine.connect() as conn:
            review_id = conn.execute(
                text(
                    "SELECT review_decision_id FROM content.review_decisions "
                    "WHERE content_id = :cid"
                ),
                {"cid": content_id},
            ).scalar_one()
            pub_id = conn.execute(
                text(
                    "SELECT publication_id FROM content.publications "
                    "WHERE content_id = :cid"
                ),
                {"cid": content_id},
            ).scalar_one()
        with runtime_engine.connect() as conn:
            with conn.begin():
                set_tenant(conn, tenant_id)
                expect_dbapi(
                    conn,
                    lambda: conn.execute(
                        text(
                            "UPDATE content.review_decisions SET comment = 'x' "
                            "WHERE review_decision_id = :rid"
                        ),
                        {"rid": review_id},
                    ),
                    match="permission denied",
                )
                expect_dbapi(
                    conn,
                    lambda: conn.execute(
                        text(
                            "DELETE FROM content.review_decisions "
                            "WHERE review_decision_id = :rid"
                        ),
                        {"rid": review_id},
                    ),
                    match="permission denied",
                )
                expect_dbapi(
                    conn,
                    lambda: conn.execute(
                        text(
                            "UPDATE content.publications SET published_by_principal_id = :p "
                            "WHERE publication_id = :pid"
                        ),
                        {"pid": pub_id, "p": uuid.uuid7()},
                    ),
                    match="permission denied",
                )
                expect_dbapi(
                    conn,
                    lambda: conn.execute(
                        text(
                            "DELETE FROM content.publications WHERE publication_id = :pid"
                        ),
                        {"pid": pub_id},
                    ),
                    match="permission denied",
                )
                # version_asset_refs may be empty; privilege still denied on UPDATE/DELETE.
                expect_dbapi(
                    conn,
                    lambda: conn.execute(
                        text(
                            "UPDATE content.version_asset_refs SET role = 'hijack' "
                            "WHERE content_id = :cid"
                        ),
                        {"cid": content_id},
                    ),
                    match="permission denied",
                )
                expect_dbapi(
                    conn,
                    lambda: conn.execute(
                        text(
                            "DELETE FROM content.version_asset_refs WHERE content_id = :cid"
                        ),
                        {"cid": content_id},
                    ),
                    match="permission denied",
                )

    def test_imported_migration_record_rewrite_denied(
        self, migration_runtime_engine, bootstrap_engine
    ) -> None:
        from tests.domains.content.application.test_gci_i13_import import (
            FIXED_NOW,
            _candidate,
            _event_context,
            _importer,
            _mig_row,
        )

        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        result = _importer(migration_runtime_engine).import_content(
            tenant_id,
            principal_id,
            _candidate(source_resource_id="i14-rewrite"),
            event_context=_event_context(),
            audit_provenance=migration_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        assert result.replayed is False
        row = _mig_row(bootstrap_engine, tenant_id, "i14-rewrite")
        assert row.outcome == "IMPORTED"
        with migration_runtime_engine.connect() as conn:
            with conn.begin():
                set_tenant(conn, tenant_id)
                expect_dbapi(
                    conn,
                    lambda: conn.execute(
                        text(
                            """
                            UPDATE content.migration_import_records
                            SET outcome = 'FAILED', failure_code = 'hijack'
                            WHERE source_resource_id = 'i14-rewrite'
                            """
                        )
                    ),
                )
                expect_dbapi(
                    conn,
                    lambda: conn.execute(
                        text(
                            """
                            UPDATE content.migration_import_records
                            SET source_digest_sha256 = :d
                            WHERE source_resource_id = 'i14-rewrite'
                            """
                        ),
                        {"d": "d" * 64},
                    ),
                )
                expect_dbapi(
                    conn,
                    lambda: conn.execute(
                        text(
                            "DELETE FROM content.migration_import_records "
                            "WHERE source_resource_id = 'i14-rewrite'"
                        )
                    ),
                )
        after = _mig_row(bootstrap_engine, tenant_id, "i14-rewrite")
        assert after.outcome == "IMPORTED"
        assert after.source_digest_sha256 == row.source_digest_sha256
        assert after.target_content_id == result.content_id.value


class TestLineageAndRevision:
    def test_cross_content_parent_lineage_rejected(self, bootstrap_engine) -> None:
        from sqlalchemy.exc import IntegrityError

        now = __import__("datetime").datetime(2026, 8, 14, 23, 0, tzinfo=__import__("datetime").UTC)
        sha = "a" * 64
        with bootstrap_engine.begin() as conn:
            tenant_id = uuid.uuid7()
            content_a = uuid.uuid7()
            content_b = uuid.uuid7()
            version_a = uuid.uuid7()
            owner = uuid.uuid7()
            for cid in (content_a, content_b):
                conn.execute(
                    text(
                        """
                        INSERT INTO content.contents (
                            content_id, tenant_id, owner_principal_id, content_type, title,
                            description, locale, stewardship_state, current_version_id,
                            published_version_id, aggregate_revision, created_at,
                            created_by_principal_id, updated_at, archived_at
                        ) VALUES (
                            :cid, :tid, :owner, 'test.generic', 'T', 'D', 'en-IN', 'DRAFT',
                            NULL, NULL, 0, :now, :owner, :now, NULL
                        )
                        """
                    ),
                    {"cid": cid, "tid": tenant_id, "owner": owner, "now": now},
                )
            conn.execute(
                text(
                    """
                    INSERT INTO content.content_versions (
                        version_id, tenant_id, content_id, version_number, parent_version_id,
                        schema_id, schema_version, payload, payload_sha256, origin,
                        provenance, created_at, created_by_principal_id
                    ) VALUES (
                        :vid, :tid, :cid, 1, NULL, 'test.generic', 1,
                        '{"marker":"v1"}'::jsonb, :sha, 'HUMAN', NULL, :now, :owner
                    )
                    """
                ),
                {
                    "vid": version_a,
                    "tid": tenant_id,
                    "cid": content_a,
                    "sha": sha,
                    "now": now,
                    "owner": owner,
                },
            )
            with pytest.raises(IntegrityError):
                with conn.begin_nested():
                    conn.execute(
                        text(
                            """
                            INSERT INTO content.content_versions (
                                version_id, tenant_id, content_id, version_number,
                                parent_version_id, schema_id, schema_version, payload,
                                payload_sha256, origin, provenance, created_at,
                                created_by_principal_id
                            ) VALUES (
                                :vid, :tid, :cid, 2, :parent, 'test.generic', 1,
                                '{"marker":"v2"}'::jsonb, :sha, 'HUMAN', NULL, :now, :owner
                            )
                            """
                        ),
                        {
                            "vid": uuid.uuid7(),
                            "tid": tenant_id,
                            "cid": content_b,
                            "parent": version_a,
                            "sha": "b" * 64,
                            "now": now,
                            "owner": owner,
                        },
                    )

    def test_version_number_differs_from_aggregate_revision_after_review(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        c = client(runtime_engine, tenant_id)
        content_id, version_id, etag = in_review(c, tenant_id)
        row = content_row(bootstrap_engine, content_id)
        ver = version_row(bootstrap_engine, version_id)
        assert int(ver.version_number) == 1
        assert int(row.aggregate_revision) == 2
        assert int(ver.version_number) != int(row.aggregate_revision)
        # If-Match uses aggregate revision, not version_number.
        bad = decide(
            c,
            tenant_id,
            content_id,
            version_id,
            action="approve",
            etag='"r1"',
        )
        assert_problem(bad, status=412, code="resource_revision_conflict")
        ok = decide(
            c,
            tenant_id,
            content_id,
            version_id,
            action="approve",
            etag=etag,
        )
        assert ok.status_code == 200, ok.text
        assert ok.headers["ETag"] == '"r3"'

    def test_update_payload_sha256_schema_origin_provenance_rejected(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        _, version_id = _seed_version_via_http(runtime_engine, tenant_id)
        before = version_row(bootstrap_engine, version_id)
        with runtime_engine.connect() as conn:
            with conn.begin():
                set_tenant(conn, tenant_id)
                for sql in (
                    "UPDATE content.content_versions SET payload = '{}'::jsonb "
                    "WHERE version_id = :vid",
                    "UPDATE content.content_versions SET payload_sha256 = :sha "
                    "WHERE version_id = :vid",
                    "UPDATE content.content_versions SET schema_id = 'hijack' "
                    "WHERE version_id = :vid",
                    "UPDATE content.content_versions SET schema_version = 99 "
                    "WHERE version_id = :vid",
                    "UPDATE content.content_versions SET origin = 'AI' WHERE version_id = :vid",
                    "UPDATE content.content_versions SET provenance = '{}'::jsonb "
                    "WHERE version_id = :vid",
                ):
                    expect_dbapi(
                        conn,
                        lambda s=sql: conn.execute(
                            text(s), {"vid": version_id, "sha": "c" * 64}
                        ),
                        match="permission denied",
                    )
        after = version_row(bootstrap_engine, version_id)
        assert after.payload == before.payload
        assert after.payload_sha256 == before.payload_sha256
        assert after.schema_id == before.schema_id
        assert after.schema_version == before.schema_version
        assert after.origin == before.origin
        assert after.provenance == before.provenance


class TestConcurrentAppend:
    def test_concurrent_append_one_winner_one_conflict_one_version_created(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        c = client(runtime_engine, tenant_id)
        created = create_content(c, tenant_id)
        content_id = created["content_id"]
        barrier = threading.Barrier(2)
        results: list[int] = []
        lock = threading.Lock()

        def worker(marker: str) -> None:
            local = client(runtime_engine, tenant_id)
            barrier.wait(timeout=10)
            hdrs = headers(tenant_id)
            hdrs["If-Match"] = '"r0"'
            response = local.post(
                f"/api/v1/contents/{content_id}/versions",
                json={
                    "schema_id": "test.generic",
                    "schema_version": 1,
                    "payload": {"marker": marker},
                },
                headers=hdrs,
            )
            with lock:
                results.append(response.status_code)

        threads = [
            threading.Thread(target=worker, args=("left",)),
            threading.Thread(target=worker, args=("right",)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(results) == [201, 412]
        with bootstrap_engine.connect() as conn:
            versions = int(
                conn.execute(
                    text(
                        "SELECT count(*) FROM content.content_versions "
                        "WHERE content_id = :cid"
                    ),
                    {"cid": content_id},
                ).scalar_one()
            )
        assert versions == 1
        created_events = [
            row
            for row in outbox_rows(bootstrap_engine, content_id=content_id)
            if row["event_type"]
            == "io.eduvijna.aieos.content.content.version_created.v1"
        ]
        assert len(created_events) == 1
        row = content_row(bootstrap_engine, content_id)
        assert int(row.aggregate_revision) == 1
