"""GCI-I14 adversarial: migration import trust boundary and GCI-G12 conflicts."""

from __future__ import annotations

import threading
import uuid

import pytest
from sqlalchemy import text

from aieos.domains.content.application.audit import migration_audit_provenance
from aieos.domains.content.application.errors import (
    MigrationForbidden,
    MigrationSourceConflict,
)
from aieos.domains.content.domain.errors import InvalidMigrationImportProvenanceError
from aieos.domains.content.domain.migration_provenance import (
    migration_import_provenance_as_json,
    migration_import_provenance_from_json,
)
from tests.dbutil import set_tenant
from tests.domains.content.application.test_gci_i13_import import (
    DIGEST_A,
    DIGEST_B,
    FIXED_NOW,
    _candidate,
    _counts,
    _event_context,
    _head,
    _importer,
    _mig_row,
)
from tests.domains.content.domain.test_gci_i13_provenance import _valid_provenance
from tests.fakes import AllowMigrationAuthorization

pytestmark = pytest.mark.gci_i14


class TestMigrationConflictsAndTrust:
    def test_i13r1_no_gap_race_reassert(
        self, migration_runtime_engine, bootstrap_engine, monkeypatch
    ) -> None:
        """Thin wrap of I13R1: gap finalization blocks concurrent changed digest."""
        from aieos.domains.content.application.errors import PersistenceOperationFailed
        from aieos.domains.content.infrastructure.persistence.repositories import (
            SqlAlchemyContentRepository,
        )

        tenant_id = uuid.uuid7()
        in_gap = threading.Event()
        release_gap = threading.Event()
        b_outcomes: list[str] = []

        def after_failure() -> None:
            in_gap.set()
            assert release_gap.wait(timeout=15)

        original_insert = SqlAlchemyContentRepository.insert

        def boom(self, content):  # noqa: ANN001
            raise PersistenceOperationFailed("forced content insert failure")

        monkeypatch.setattr(SqlAlchemyContentRepository, "insert", boom)
        importer_a = _importer(
            migration_runtime_engine, after_target_failure=after_failure
        )

        def run_a() -> None:
            with pytest.raises(PersistenceOperationFailed):
                principal_id = uuid.uuid7()
                importer_a.import_content(
                    tenant_id,
                    principal_id,
                    _candidate(source_resource_id="i14-gap", digest=DIGEST_A),
                    event_context=_event_context(),
                    audit_provenance=migration_audit_provenance(principal_id),
                    now=FIXED_NOW,
                )

        thread_a = threading.Thread(target=run_a)
        thread_a.start()
        assert in_gap.wait(timeout=15)
        assert _counts(bootstrap_engine, tenant_id=tenant_id)["contents"] == 0
        assert _mig_row(bootstrap_engine, tenant_id, "i14-gap") is None

        def run_b() -> None:
            try:
                principal_id = uuid.uuid7()
                _importer(migration_runtime_engine).import_content(
                    tenant_id,
                    principal_id,
                    _candidate(source_resource_id="i14-gap", digest=DIGEST_B),
                    event_context=_event_context(),
                    audit_provenance=migration_audit_provenance(principal_id),
                    now=FIXED_NOW,
                )
                b_outcomes.append("ok")
            except MigrationSourceConflict:
                b_outcomes.append("conflict")

        thread_b = threading.Thread(target=run_b)
        thread_b.start()
        threading.Event().wait(0.5)
        assert _counts(bootstrap_engine, tenant_id=tenant_id)["contents"] == 0

        monkeypatch.setattr(SqlAlchemyContentRepository, "insert", original_insert)
        release_gap.set()
        thread_a.join(timeout=15)
        thread_b.join(timeout=15)
        assert not thread_a.is_alive()
        assert not thread_b.is_alive()

        row = _mig_row(bootstrap_engine, tenant_id, "i14-gap")
        assert row is not None
        assert row.outcome == "FAILED"
        assert row.source_digest_sha256 == DIGEST_A
        assert b_outcomes == ["conflict"]
        assert _counts(bootstrap_engine, tenant_id=tenant_id)["contents"] == 0

    def test_same_source_digest_version_mapping_conflicts(
        self, migration_runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        service = _importer(migration_runtime_engine)
        principal_id = uuid.uuid7()
        service.import_content(
            tenant_id,
            principal_id,
            _candidate(source_resource_id="i14-conflict"),
            event_context=_event_context(),
            audit_provenance=migration_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        with pytest.raises(MigrationSourceConflict):
            service.import_content(
                tenant_id,
                principal_id,
                _candidate(source_resource_id="i14-conflict", digest=DIGEST_B),
                event_context=_event_context(),
                audit_provenance=migration_audit_provenance(principal_id),
                now=FIXED_NOW,
            )
        with pytest.raises(MigrationSourceConflict):
            principal_id = uuid.uuid7()
            service.import_content(
                tenant_id,
                principal_id,
                _candidate(source_resource_id="i14-conflict", source_version="2"),
                event_context=_event_context(),
                audit_provenance=migration_audit_provenance(principal_id),
                now=FIXED_NOW,
            )
        with pytest.raises(MigrationSourceConflict):
            service.import_content(
                tenant_id,
                principal_id,
                _candidate(source_resource_id="i14-conflict", mapping_version=2),
                event_context=_event_context(),
                audit_provenance=migration_audit_provenance(principal_id),
                now=FIXED_NOW,
            )
        assert _counts(bootstrap_engine, tenant_id=tenant_id)["contents"] == 1

    def test_legacy_approved_published_fixture_stays_generated(
        self, migration_runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        result = _importer(migration_runtime_engine).import_content(
            tenant_id,
            principal_id,
            _candidate(source_resource_id="i14-legacy-trust"),
            event_context=_event_context(),
            audit_provenance=migration_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        head = _head(bootstrap_engine, result.content_id.value)
        assert head.stewardship_state == "GENERATED"
        assert head.published_version_id is None
        counts = _counts(bootstrap_engine, tenant_id=tenant_id)
        assert counts["reviews"] == 0
        assert counts["pubs"] == 0

    def test_revoke_migration_auth_on_replay_forbidden(
        self, migration_runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        auth = AllowMigrationAuthorization(allow=True)
        service = _importer(migration_runtime_engine, auth=auth)
        principal_id = uuid.uuid7()
        first = service.import_content(
            tenant_id,
            principal_id,
            _candidate(source_resource_id="i14-auth-replay"),
            event_context=_event_context(),
            audit_provenance=migration_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        assert first.replayed is False
        auth.allow = False
        with pytest.raises(MigrationForbidden):
            service.import_content(
                tenant_id,
                principal_id,
                _candidate(source_resource_id="i14-auth-replay"),
                event_context=_event_context(),
                audit_provenance=migration_audit_provenance(principal_id),
                now=FIXED_NOW,
            )
        assert _counts(bootstrap_engine, tenant_id=tenant_id)["contents"] == 1

    def test_import_provenance_secret_and_schema_version_attacks(self) -> None:
        base = migration_import_provenance_as_json(_valid_provenance())
        attacks = [
            {**base, "api_key": "SECRET"},
            {**base, "secret": "nope"},
            {**base, "schema_version": True},
            {**base, "schema_version": 1.0},
            {**base, "schema_version": "1"},
        ]
        for payload in attacks:
            with pytest.raises(InvalidMigrationImportProvenanceError):
                migration_import_provenance_from_json(payload)

    def test_migration_runtime_cannot_insert_review_or_publication(
        self, migration_runtime_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        with migration_runtime_engine.connect() as conn:
            with conn.begin():
                set_tenant(conn, tenant_id)
                with pytest.raises(Exception):
                    conn.execute(
                        text(
                            """
                            INSERT INTO content.review_decisions (
                                review_decision_id, tenant_id, content_id, version_id,
                                decision, reason_code, comment, reviewer_principal_id,
                                effective_actor_id, delegation_id, decided_at,
                                correlation_id
                            ) VALUES (
                                :id, :tid, :cid, :vid, 'APPROVE', NULL, NULL, :p, :p,
                                NULL, :now, :corr
                            )
                            """
                        ),
                        {
                            "id": uuid.uuid7(),
                            "tid": tenant_id,
                            "cid": uuid.uuid7(),
                            "vid": uuid.uuid7(),
                            "p": uuid.uuid7(),
                            "now": FIXED_NOW,
                            "corr": uuid.uuid7(),
                        },
                    )
                with pytest.raises(Exception):
                    conn.execute(
                        text(
                            """
                            INSERT INTO content.publications (
                                publication_id, tenant_id, content_id, version_id,
                                approval_decision_id, published_by_principal_id,
                                effective_actor_id, published_at, correlation_id
                            ) VALUES (
                                :id, :tid, :cid, :vid, :rid, :p, :p, :now, :corr
                            )
                            """
                        ),
                        {
                            "id": uuid.uuid7(),
                            "tid": tenant_id,
                            "cid": uuid.uuid7(),
                            "vid": uuid.uuid7(),
                            "rid": uuid.uuid7(),
                            "p": uuid.uuid7(),
                            "now": FIXED_NOW,
                            "corr": uuid.uuid7(),
                        },
                    )
