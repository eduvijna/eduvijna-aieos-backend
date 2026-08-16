"""SAI-I04 AI materialization + controlled migration transactional audit."""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.application.ai_materialization import (
    MaterializeAIGeneratedContentVersionService,
)
from aieos.domains.content.application.audit import (
    MutationAuditProvenance,
    ai_materialization_audit_provenance,
    api_mutation_audit_provenance,
    migration_audit_provenance,
)
from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.application.errors import (
    AggregateRevisionConflict,
    PersistenceOperationFailed,
)
from aieos.domains.content.application.migration_import import ImportMigratedContentService
from aieos.domains.content.application.models import AIGeneratedVersionMaterializationCommand
from aieos.domains.content.domain.identities import AggregateRevision, ContentId
from aieos.domains.content.domain.provenance import AIGenerationProvenanceV1
from aieos.domains.content.infrastructure.persistence.audit_repository import (
    ContentSecurityMutationAuditRepository,
)
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWork,
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.platform.api.app import create_app
from aieos.platform.events.constants import (
    EVENT_CONTENT_CREATED_V1,
    EVENT_CONTENT_VERSION_CREATED_V1,
)
from aieos.platform.events.models import MutationEventContext
from aieos.platform.resources import ResourceRef
from aieos.platform.security.audit import SecurityAuditExecutionChannel
from tests.domains.content.application.test_gci_i11_materialization import (
    FIXED_NOW as AI_NOW,
    _materializer,
    _provenance,
    _seed_content,
)
from tests.domains.content.application.test_gci_i13_import import (
    DIGEST_A,
    DIGEST_B,
    FIXED_NOW as MIG_NOW,
    _candidate,
    _counts,
    _importer,
    _mig_row,
)
from tests.fakes import (
    AllowAIGenerationAuthorization,
    AllowAssetCurrentGovernance,
    AllowAssetReferenceValidation,
    AllowPublicationAuthorization,
    AllowPublicationGovernance,
    AllowReviewAuthorization,
    AllowReviewCommentPolicy,
    IDEMPOTENCY_RETENTION,
    FixedPrincipalAuthenticator,
    StubSecurityContextResolver,
    make_test_schema_registry,
)
from tests.platform.events.helpers import outbox_rows
from tests.platform.workflows.helpers import (
    create_content,
    decide,
    headers,
    submit_review,
)

pytestmark = pytest.mark.sai_i04

CURSOR_KEY = b"sai-i04-test-cursor-signing-key"


def _audit_rows(bootstrap_engine: Engine, *, content_id: str | UUID | None = None) -> list[dict]:
    sql = "SELECT * FROM security.audit_records"
    params: dict[str, object] = {}
    if content_id is not None:
        sql += " WHERE primary_resource_id = :cid"
        params["cid"] = UUID(str(content_id))
    sql += (
        " ORDER BY resource_revision_after NULLS FIRST,"
        " occurred_at, audit_record_id"
    )
    with bootstrap_engine.connect() as conn:
        return [dict(r) for r in conn.execute(text(sql), params).mappings().all()]


def _related(row: dict) -> list[dict]:
    refs = row["related_resource_refs"]
    if isinstance(refs, str):
        return json.loads(refs)
    return list(refs)


def _event(*, actor: UUID, effective: UUID, correlation: UUID | None = None) -> MutationEventContext:
    return MutationEventContext(
        correlation_id=correlation or uuid.uuid7(),
        causation_id=uuid.uuid7(),
        actor_principal_id=actor,
        effective_actor_id=effective,
    )


def _client(runtime_engine: Engine, tenant_id: UUID, principal_id: UUID) -> TestClient:
    return TestClient(
        create_app(
            uow_factory=SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
            request_identity_authenticator=FixedPrincipalAuthenticator(principal_id),
            security_resolver=StubSecurityContextResolver(tenant_id, principal_id),
            content_types=StaticContentTypeCatalog({"test.generic"}),
            cursor_signing_key=CURSOR_KEY,
            schema_registry=make_test_schema_registry(),
            idempotency_retention=IDEMPOTENCY_RETENTION,
            review_authorization=AllowReviewAuthorization(),
            review_comment_policy=AllowReviewCommentPolicy(),
            publication_authorization=AllowPublicationAuthorization(),
            publication_governance=AllowPublicationGovernance(),
            asset_reference_validation=AllowAssetReferenceValidation(),
            asset_current_governance=AllowAssetCurrentGovernance(),
        ),
        raise_server_exceptions=False,
    )


def _content_row(bootstrap_engine: Engine, content_id: UUID | ContentId):
    cid = content_id.value if isinstance(content_id, ContentId) else content_id
    with bootstrap_engine.connect() as conn:
        return conn.execute(
            text(
                """
                SELECT stewardship_state, aggregate_revision, current_version_id,
                       published_version_id, updated_at
                FROM content.contents WHERE content_id = :cid
                """
            ),
            {"cid": cid},
        ).one()


class TestAIAudit:
    def test_shape_actors_channel_correlation_and_minimization(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        initiating = uuid.uuid7()
        effective = uuid.uuid7()
        executing = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id)
        correlation = uuid.uuid7()
        ctx = _event(actor=initiating, effective=effective, correlation=correlation)
        result = _materializer(runtime_engine).materialize(
            tenant_id,
            executing,
            AIGeneratedVersionMaterializationCommand(
                content_id=content_id,
                expected_aggregate_revision=AggregateRevision(0),
                schema_id="test.generic",
                schema_version=1,
                payload={"marker": "ai-v1"},
                provenance=_provenance(correlation),
            ),
            event_context=ctx,
            audit_provenance=MutationAuditProvenance(
                executing_principal_id=executing,
                execution_channel=SecurityAuditExecutionChannel.AI_MATERIALIZATION,
            ),
            now=AI_NOW,
        )
        assert int(result.aggregate_revision) == 1
        rows = [
            r
            for r in _audit_rows(bootstrap_engine, content_id=content_id.value)
            if r["action"] == "content.ai.materialize"
        ]
        assert len(rows) == 1
        row = rows[0]
        assert row["primary_resource_type"] == "content.content"
        assert row["resource_revision_before"] == 0
        assert row["resource_revision_after"] == 1
        assert row["primary_resource_revision"] == 1
        related = _related(row)
        assert len(related) == 1
        assert related[0]["resource_type"] == "content.content_version"
        assert related[0]["resource_id"] == str(result.version_id.value)
        assert related[0]["resource_revision"] is None
        assert related[0]["resource_revision"] != 1
        assert row["execution_channel"] == "AI_MATERIALIZATION"
        assert row["initiating_principal_id"] == initiating
        assert row["effective_actor_id"] == effective
        assert row["executing_principal_id"] == executing
        assert row["delegation_id"] is None
        assert row["trace_id"] is None
        assert row["occurred_at"] == AI_NOW
        blob = json.dumps(row, default=str)
        for needle in (
            "generation_run_ref",
            "provider_id",
            "model_id",
            "capability_id",
            "prompt",
            "neutral-model",
        ):
            assert needle not in blob
        events = [
            e
            for e in outbox_rows(bootstrap_engine, content_id=str(content_id.value))
            if e["event_type"] == EVENT_CONTENT_VERSION_CREATED_V1
        ]
        assert len(events) == 1
        env = dict(events[0]["envelope"])
        assert row["correlation_id"] == UUID(env["correlationid"]) == correlation
        assert row["causation_id"] == UUID(env["causationid"]) == ctx.causation_id
        assert row["audit_record_id"] != events[0]["event_id"]
        assert not any(r["action"] == "content.version.create" for r in _audit_rows(bootstrap_engine, content_id=content_id.value))
        assert not any(r["action"] == "content.publish" for r in _audit_rows(bootstrap_engine, content_id=content_id.value))

    def test_audit_failure_and_late_commit_failure_roll_back(
        self, runtime_engine, bootstrap_engine, monkeypatch
    ) -> None:
        tenant_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id)
        correlation = uuid.uuid7()
        ctx = _event(actor=uuid.uuid7(), effective=uuid.uuid7(), correlation=correlation)

        def boom_audit(self, record) -> None:
            raise PersistenceOperationFailed("content persistence operation failed")

        monkeypatch.setattr(ContentSecurityMutationAuditRepository, "insert", boom_audit)
        with pytest.raises(PersistenceOperationFailed):
            _materializer(runtime_engine).materialize(
                tenant_id,
                uuid.uuid7(),
                AIGeneratedVersionMaterializationCommand(
                    content_id=content_id,
                    expected_aggregate_revision=AggregateRevision(0),
                    schema_id="test.generic",
                    schema_version=1,
                    payload={"marker": "ai"},
                    provenance=_provenance(correlation),
                ),
                event_context=ctx,
                audit_provenance=ai_materialization_audit_provenance(uuid.uuid7()),
                now=AI_NOW,
            )
        head = _content_row(bootstrap_engine, content_id)
        assert head.current_version_id is None
        assert int(head.aggregate_revision) == 0
        assert outbox_rows(bootstrap_engine, content_id=str(content_id.value)) == []
        assert _audit_rows(bootstrap_engine, content_id=content_id.value) == []

        monkeypatch.undo()
        content_id2 = _seed_content(bootstrap_engine, tenant_id)
        correlation2 = uuid.uuid7()
        ctx2 = _event(actor=uuid.uuid7(), effective=uuid.uuid7(), correlation=correlation2)

        def boom_commit(self) -> None:
            raise PersistenceOperationFailed("content persistence operation failed")

        monkeypatch.setattr(SqlAlchemyContentUnitOfWork, "commit", boom_commit)
        with pytest.raises(PersistenceOperationFailed):
            _materializer(runtime_engine).materialize(
                tenant_id,
                uuid.uuid7(),
                AIGeneratedVersionMaterializationCommand(
                    content_id=content_id2,
                    expected_aggregate_revision=AggregateRevision(0),
                    schema_id="test.generic",
                    schema_version=1,
                    payload={"marker": "ai2"},
                    provenance=_provenance(correlation2),
                ),
                event_context=ctx2,
                audit_provenance=ai_materialization_audit_provenance(uuid.uuid7()),
                now=AI_NOW,
            )
        head2 = _content_row(bootstrap_engine, content_id2)
        assert head2.current_version_id is None
        assert int(head2.aggregate_revision) == 0
        assert _audit_rows(bootstrap_engine, content_id=content_id2.value) == []

    def test_stale_retry_and_concurrency_one_audit(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id)
        principal = uuid.uuid7()
        correlation = uuid.uuid7()
        ctx = _event(actor=principal, effective=principal, correlation=correlation)
        first = _materializer(runtime_engine).materialize(
            tenant_id,
            principal,
            AIGeneratedVersionMaterializationCommand(
                content_id=content_id,
                expected_aggregate_revision=AggregateRevision(0),
                schema_id="test.generic",
                schema_version=1,
                payload={"marker": "ai-v1"},
                provenance=_provenance(correlation),
            ),
            event_context=ctx,
            audit_provenance=ai_materialization_audit_provenance(principal),
            now=AI_NOW,
        )
        assert int(first.aggregate_revision) == 1
        with pytest.raises(AggregateRevisionConflict):
            stale_corr = uuid.uuid7()
            _materializer(runtime_engine).materialize(
                tenant_id,
                principal,
                AIGeneratedVersionMaterializationCommand(
                    content_id=content_id,
                    expected_aggregate_revision=AggregateRevision(0),
                    schema_id="test.generic",
                    schema_version=1,
                    payload={"marker": "ai-v2"},
                    provenance=_provenance(stale_corr),
                ),
                event_context=_event(
                    actor=principal, effective=principal, correlation=stale_corr
                ),
                audit_provenance=ai_materialization_audit_provenance(principal),
                now=AI_NOW,
            )
        assert (
            len(
                [
                    r
                    for r in _audit_rows(bootstrap_engine, content_id=content_id.value)
                    if r["action"] == "content.ai.materialize"
                ]
            )
            == 1
        )

        content_id2 = _seed_content(bootstrap_engine, tenant_id)
        barrier = threading.Barrier(2)
        results: list[object] = []

        def worker(marker: str) -> None:
            barrier.wait()
            try:
                corr = uuid.uuid7()
                results.append(
                    _materializer(runtime_engine).materialize(
                        tenant_id,
                        principal,
                        AIGeneratedVersionMaterializationCommand(
                            content_id=content_id2,
                            expected_aggregate_revision=AggregateRevision(0),
                            schema_id="test.generic",
                            schema_version=1,
                            payload={"marker": marker},
                            provenance=_provenance(corr),
                        ),
                        event_context=_event(
                            actor=principal, effective=principal, correlation=corr
                        ),
                        audit_provenance=ai_materialization_audit_provenance(principal),
                        now=AI_NOW,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                results.append(exc)

        threads = [
            threading.Thread(target=worker, args=("left",)),
            threading.Thread(target=worker, args=("right",)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        successes = [r for r in results if not isinstance(r, Exception)]
        failures = [r for r in results if isinstance(r, Exception)]
        assert len(successes) == 1
        assert len(failures) == 1
        # Loser is a governed concurrency conflict (revision / unique / lineage).
        assert failures[0].__class__.__name__ in {
            "AggregateRevisionConflict",
            "VersionAlreadyExists",
            "VersionLineageConflict",
            "PersistenceOperationFailed",
            "PersistenceInvariantViolation",
        }
        assert (
            len(
                [
                    r
                    for r in _audit_rows(bootstrap_engine, content_id=content_id2.value)
                    if r["action"] == "content.ai.materialize"
                ]
            )
            == 1
        )
        assert (
            len(
                [
                    e
                    for e in outbox_rows(bootstrap_engine, content_id=str(content_id2.value))
                    if e["event_type"] == EVENT_CONTENT_VERSION_CREATED_V1
                ]
            )
            == 1
        )

    def test_older_publication_preserved_no_publish_audit(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, principal)
        created = create_content(client, tenant_id)
        content_id = ContentId(UUID(created["content_id"]))
        # human append + submit + approve + publish V1
        from tests.platform.workflows.helpers import append_version

        appended = append_version(client, tenant_id, str(content_id.value), etag='"r0"')
        assert appended.status_code == 201
        v1 = appended.json()["version_id"]
        submitted = submit_review(
            client, tenant_id, str(content_id.value), v1, etag=appended.headers["ETag"]
        )
        assert submitted.status_code == 200
        approved = decide(
            client,
            tenant_id,
            str(content_id.value),
            v1,
            action="approve",
            etag=submitted.headers["ETag"],
        )
        assert approved.status_code == 200
        published = client.post(
            f"/api/v1/contents/{content_id.value}/actions/publish",
            json={"version_id": v1},
            headers={**headers(tenant_id), "If-Match": approved.headers["ETag"]},
        )
        assert published.status_code == 200
        before = _content_row(bootstrap_engine, content_id)
        assert str(before.published_version_id) == v1
        corr = uuid.uuid7()
        result = _materializer(runtime_engine).materialize(
            tenant_id,
            principal,
            AIGeneratedVersionMaterializationCommand(
                content_id=content_id,
                expected_aggregate_revision=AggregateRevision(4),
                schema_id="test.generic",
                schema_version=1,
                payload={"marker": "ai-v2"},
                provenance=_provenance(corr),
            ),
            event_context=_event(actor=principal, effective=principal, correlation=corr),
            audit_provenance=ai_materialization_audit_provenance(principal),
            now=AI_NOW,
        )
        after = _content_row(bootstrap_engine, content_id)
        assert str(after.published_version_id) == v1
        assert after.current_version_id == result.version_id.value
        actions = [r["action"] for r in _audit_rows(bootstrap_engine, content_id=content_id.value)]
        assert actions.count("content.ai.materialize") == 1
        assert actions.count("content.publish") == 1
        assert "content.version.create" in actions  # human append only


class TestMigrationAudit:
    def test_shape_actors_owner_separation_and_dual_event_correlation(
        self, migration_runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        initiating = uuid.uuid7()
        effective = uuid.uuid7()
        executing = uuid.uuid7()
        owner = uuid.uuid7()
        ctx = _event(actor=initiating, effective=effective)
        candidate = replace(
            _candidate(source_resource_id="sai-i04-shape"),
            target_owner_principal_id=owner,
        )
        result = _importer(migration_runtime_engine).import_content(
            tenant_id,
            executing,
            candidate,
            event_context=ctx,
            audit_provenance=MutationAuditProvenance(
                executing_principal_id=executing,
                execution_channel=SecurityAuditExecutionChannel.MIGRATION,
            ),
            now=MIG_NOW,
        )
        rows = [
            r
            for r in _audit_rows(bootstrap_engine, content_id=result.content_id.value)
            if r["action"] == "content.migration.import"
        ]
        assert len(rows) == 1
        row = rows[0]
        assert row["primary_resource_type"] == "content.content"
        assert row["resource_revision_before"] is None
        assert row["resource_revision_after"] == 1
        assert row["primary_resource_revision"] == 1
        related = _related(row)
        assert len(related) == 1
        assert related[0]["resource_type"] == "content.content_version"
        assert related[0]["resource_id"] == str(result.version_id.value)
        assert related[0]["resource_revision"] is None
        assert row["execution_channel"] == "MIGRATION"
        assert row["initiating_principal_id"] == initiating
        assert row["effective_actor_id"] == effective
        assert row["executing_principal_id"] == executing
        assert owner not in (
            row["initiating_principal_id"],
            row["effective_actor_id"],
            row["executing_principal_id"],
        )
        assert row["occurred_at"] == MIG_NOW
        blob = json.dumps(row, default=str)
        for needle in (
            "legacy.edu",
            "source_digest",
            DIGEST_A,
            "mapping_id",
            "migration_batch",
            "edu.lesson",
        ):
            assert needle not in blob
        assert "migration_import" not in [r["resource_type"] for r in related]
        events = outbox_rows(bootstrap_engine, content_id=str(result.content_id.value))
        created = [e for e in events if e["event_type"] == EVENT_CONTENT_CREATED_V1]
        versioned = [e for e in events if e["event_type"] == EVENT_CONTENT_VERSION_CREATED_V1]
        assert len(created) == 1 and len(versioned) == 1
        for env_row in (created[0], versioned[0]):
            env = dict(env_row["envelope"])
            assert row["correlation_id"] == UUID(env["correlationid"])
            assert row["causation_id"] == UUID(env["causationid"])
            assert row["tenant_id"] == env_row["tenant_id"] == tenant_id
            assert row["audit_record_id"] != env_row["event_id"]
        assert not any(
            r["action"] in ("content.create", "content.version.create")
            for r in _audit_rows(bootstrap_engine, content_id=result.content_id.value)
        )

    def test_replay_changed_digest_and_exact_one_audit(
        self, migration_runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal = uuid.uuid7()
        first = _importer(migration_runtime_engine).import_content(
            tenant_id,
            principal,
            _candidate(source_resource_id="sai-i04-replay"),
            event_context=_event(actor=principal, effective=principal),
            audit_provenance=migration_audit_provenance(principal),
            now=MIG_NOW,
        )
        before = len(
            [
                r
                for r in _audit_rows(bootstrap_engine, content_id=first.content_id.value)
                if r["action"] == "content.migration.import"
            ]
        )
        assert before == 1
        replay = _importer(migration_runtime_engine).import_content(
            tenant_id,
            principal,
            _candidate(source_resource_id="sai-i04-replay"),
            event_context=_event(actor=principal, effective=principal),
            audit_provenance=migration_audit_provenance(principal),
            now=MIG_NOW,
        )
        assert replay.replayed is True
        assert (
            len(
                [
                    r
                    for r in _audit_rows(bootstrap_engine, content_id=first.content_id.value)
                    if r["action"] == "content.migration.import"
                ]
            )
            == 1
        )
        from aieos.domains.content.application.errors import MigrationSourceConflict

        with pytest.raises(MigrationSourceConflict):
            _importer(migration_runtime_engine).import_content(
                tenant_id,
                principal,
                _candidate(source_resource_id="sai-i04-replay", digest=DIGEST_B),
                event_context=_event(actor=principal, effective=principal),
                audit_provenance=migration_audit_provenance(principal),
                now=MIG_NOW,
            )
        assert (
            len(
                [
                    r
                    for r in _audit_rows(bootstrap_engine, content_id=first.content_id.value)
                    if r["action"] == "content.migration.import"
                ]
            )
            == 1
        )

    def test_audit_failure_rolls_back_target_and_records_failed(
        self, migration_runtime_engine, bootstrap_engine, monkeypatch
    ) -> None:
        tenant_id = uuid.uuid7()
        principal = uuid.uuid7()

        def boom(self, record) -> None:
            raise PersistenceOperationFailed("content persistence operation failed")

        monkeypatch.setattr(ContentSecurityMutationAuditRepository, "insert", boom)
        with pytest.raises(PersistenceOperationFailed):
            _importer(migration_runtime_engine).import_content(
                tenant_id,
                principal,
                _candidate(source_resource_id="sai-i04-audit-fail"),
                event_context=_event(actor=principal, effective=principal),
                audit_provenance=migration_audit_provenance(principal),
                now=MIG_NOW,
            )
        counts = _counts(bootstrap_engine, tenant_id=tenant_id)
        assert counts["contents"] == 0
        assert counts["versions"] == 0
        assert counts["created"] == 0
        assert counts["versioned"] == 0
        row = _mig_row(bootstrap_engine, tenant_id, "sai-i04-audit-fail")
        assert row.outcome == "FAILED"
        assert int(row.attempt_count) == 1
        assert _audit_rows(bootstrap_engine) == [] or all(
            r["action"] != "content.migration.import" or r["tenant_id"] != tenant_id
            for r in _audit_rows(bootstrap_engine)
        )

        monkeypatch.undo()
        recovered = _importer(migration_runtime_engine).import_content(
            tenant_id,
            principal,
            _candidate(source_resource_id="sai-i04-audit-fail"),
            event_context=_event(actor=principal, effective=principal),
            audit_provenance=migration_audit_provenance(principal),
            now=MIG_NOW,
        )
        assert recovered.replayed is False
        after = _mig_row(bootstrap_engine, tenant_id, "sai-i04-audit-fail")
        assert after.outcome == "IMPORTED"
        assert int(after.attempt_count) >= 1
        assert (
            len(
                [
                    r
                    for r in _audit_rows(
                        bootstrap_engine, content_id=recovered.content_id.value
                    )
                    if r["action"] == "content.migration.import"
                ]
            )
            == 1
        )

    def test_channel_mislabel_rejected(self, migration_runtime_engine) -> None:
        from aieos.domains.content.application.errors import InvalidContentRequest

        tenant_id = uuid.uuid7()
        principal = uuid.uuid7()
        with pytest.raises(InvalidContentRequest):
            _importer(migration_runtime_engine).import_content(
                tenant_id,
                principal,
                _candidate(source_resource_id="sai-i04-bad-channel"),
                event_context=_event(actor=principal, effective=principal),
                audit_provenance=api_mutation_audit_provenance(principal),
                now=MIG_NOW,
            )


class TestMixedLifecycleAndApiRegression:
    def test_api_create_ai_submit_approve_publish_actions(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, principal)
        created = create_content(client, tenant_id)
        content_id = ContentId(UUID(created["content_id"]))
        corr = uuid.uuid7()
        ai = _materializer(runtime_engine).materialize(
            tenant_id,
            principal,
            AIGeneratedVersionMaterializationCommand(
                content_id=content_id,
                expected_aggregate_revision=AggregateRevision(0),
                schema_id="test.generic",
                schema_version=1,
                payload={"marker": "ai-life"},
                provenance=_provenance(corr),
            ),
            event_context=_event(actor=principal, effective=principal, correlation=corr),
            audit_provenance=ai_materialization_audit_provenance(principal),
            now=AI_NOW,
        )
        submitted = submit_review(
            client,
            tenant_id,
            str(content_id.value),
            str(ai.version_id.value),
            etag='"r1"',
        )
        assert submitted.status_code == 200, submitted.text
        approved = decide(
            client,
            tenant_id,
            str(content_id.value),
            str(ai.version_id.value),
            action="approve",
            etag=submitted.headers["ETag"],
        )
        assert approved.status_code == 200
        published = client.post(
            f"/api/v1/contents/{content_id.value}/actions/publish",
            json={"version_id": str(ai.version_id.value)},
            headers={**headers(tenant_id), "If-Match": approved.headers["ETag"]},
        )
        assert published.status_code == 200
        actions = [r["action"] for r in _audit_rows(bootstrap_engine, content_id=content_id.value)]
        assert actions == [
            "content.create",
            "content.ai.materialize",
            "content.review.submit",
            "content.review.approve",
            "content.publish",
        ]
        assert "content.version.create" not in actions
