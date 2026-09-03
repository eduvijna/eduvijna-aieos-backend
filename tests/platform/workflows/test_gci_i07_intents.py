"""GCI-I07 workflow intent persistence, RLS, privileges, and atomicity."""

from __future__ import annotations

from aieos.domains.content.application.audit import api_mutation_audit_provenance

import io
import uuid
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from aieos.domains.content.application.errors import PersistenceOperationFailed
from aieos.domains.content.application.review import ReviewCommandService
from aieos.domains.content.domain.identities import AggregateRevision, ContentId, ContentVersionId
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.platform.events.models import MutationEventContext
from aieos.platform.workflows.constants import (
    CONTENT_REVIEW_TASK_QUEUE,
    CONTENT_REVIEW_WORKFLOW_TYPE,
    INTENT_CLAIMED,
    INTENT_DELIVERED,
    INTENT_PENDING,
    INTENT_QUARANTINED,
    content_review_temporal_workflow_id,
)
from aieos.platform.workflows.persistence.repositories import (
    SqlAlchemyWorkflowDispatcherRepository,
    SqlAlchemyWorkflowIntentRepository,
)
from tests.conftest import SCHEMA_OWNER_ROLE, alembic_config, provision_runtime_grants
from tests.dbutil import REPO_ROOT, clear_asset_audit_rows_for_schema_downgrade, set_tenant
from tests.fakes import (
    AllowReviewAuthorization,
    AllowReviewCommentPolicy,
    IDEMPOTENCY_RETENTION,
    MarkerReviewCommentPolicy,
    SENSITIVE_TEST_COMMENT,
)
from tests.platform.workflows.helpers import (
    client_for,
    command_intent_rows,
    content_row,
    decide,
    decision_count,
    generated_version,
    headers,
    in_review,
    start_intent_rows,
    submit_review,
)

pytestmark = pytest.mark.gci_i07


def _is_uuid7(value: UUID) -> bool:
    return value.version == 7


class TestIntentPersistence:
    def test_submit_inserts_one_start_intent(
        self, runtime_engine, bootstrap_engine, postgres18
    ) -> None:
        assert postgres18["server_version"].startswith("18.")
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = client_for(runtime_engine, tenant_id, principal_id)
        content_id, version_id, etag = generated_version(client, tenant_id)
        submitted = submit_review(client, tenant_id, content_id, version_id, etag=etag)
        assert submitted.status_code == 200, submitted.text
        rows = start_intent_rows(bootstrap_engine, content_id)
        assert len(rows) == 1
        row = rows[0]
        assert row["status"] == INTENT_PENDING
        assert row["workflow_type"] == CONTENT_REVIEW_WORKFLOW_TYPE
        assert row["task_queue"] == CONTENT_REVIEW_TASK_QUEUE
        assert _is_uuid7(row["workflow_instance_id"])
        assert _is_uuid7(row["workflow_start_intent_id"])
        assert row["temporal_workflow_id"] == content_review_temporal_workflow_id(
            str(row["workflow_instance_id"])
        )
        assert row["business_key"] == f"content-review:v1:{content_id}:{version_id}"
        payload = dict(row["input"])
        assert set(payload) == {
            "workflow_instance_id",
            "tenant_id",
            "content_id",
            "version_id",
            "correlation_id",
        }
        assert "comment" not in payload
        assert "Title" not in str(payload)
        assert SENSITIVE_TEST_COMMENT not in str(rows)

    def test_submit_retry_no_second_intent(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = client_for(runtime_engine, tenant_id, principal_id)
        content_id, version_id, etag = generated_version(client, tenant_id)
        key = f"submit-{uuid.uuid7()}"
        first = submit_review(
            client, tenant_id, content_id, version_id, etag=etag, **{"Idempotency-Key": key}
        )
        assert first.status_code == 200
        replay = submit_review(
            client, tenant_id, content_id, version_id, etag=etag, **{"Idempotency-Key": key}
        )
        assert replay.status_code == 200
        assert replay.headers["ETag"] == first.headers["ETag"]
        assert len(start_intent_rows(bootstrap_engine, content_id)) == 1
        assert content_row(bootstrap_engine, content_id).stewardship_state == "IN_REVIEW"

    def test_decision_inserts_one_command_intent_and_replays(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = client_for(runtime_engine, tenant_id, principal_id)
        content_id, version_id, etag = in_review(client, tenant_id)
        key = f"approve-{uuid.uuid7()}"
        body = {"reason_code": "reason_code_should_not_leak", "comment": "ok"}
        first = decide(
            client,
            tenant_id,
            content_id,
            version_id,
            action="approve",
            etag=etag,
            body=body,
            **{"Idempotency-Key": key},
        )
        assert first.status_code == 200, first.text
        rows = command_intent_rows(bootstrap_engine, content_id)
        assert len(rows) == 1
        payload = dict(rows[0]["payload"])
        assert set(payload) == {
            "command_id",
            "workflow_instance_id",
            "review_decision_id",
            "content_id",
            "version_id",
            "decision",
            "correlation_id",
        }
        assert payload["decision"] == "APPROVE"
        assert "comment" not in payload
        assert "reason_code" not in payload
        assert "reason_code_should_not_leak" not in str(rows)
        assert rows[0]["business_key"] == f"review-decision:{payload['review_decision_id']}"
        assert _is_uuid7(rows[0]["command_id"])
        replay = decide(
            client,
            tenant_id,
            content_id,
            version_id,
            action="approve",
            etag=etag,
            body=body,
            **{"Idempotency-Key": key},
        )
        assert replay.status_code == 200
        assert len(command_intent_rows(bootstrap_engine, content_id)) == 1

    def test_request_changes_and_reject_command_intents(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        for action, body in (
            ("request-changes", {"comment": "needs work"}),
            ("reject", {"comment": "no"}),
        ):
            tenant_id = uuid.uuid7()
            principal_id = uuid.uuid7()
            client = client_for(runtime_engine, tenant_id, principal_id)
            content_id, version_id, etag = in_review(client, tenant_id)
            key = f"{action}-{uuid.uuid7()}"
            first = decide(
                client,
                tenant_id,
                content_id,
                version_id,
                action=action,
                etag=etag,
                body=body,
                **{"Idempotency-Key": key},
            )
            assert first.status_code == 200, first.text
            rows = command_intent_rows(bootstrap_engine, content_id)
            assert len(rows) == 1
            replay = decide(
                client,
                tenant_id,
                content_id,
                version_id,
                action=action,
                etag=etag,
                body=body,
                **{"Idempotency-Key": key},
            )
            assert replay.status_code == 200
            assert len(command_intent_rows(bootstrap_engine, content_id)) == 1

    def test_comment_governance_creates_no_command_intent(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = client_for(
            runtime_engine,
            tenant_id,
            principal_id,
            comment_policy=MarkerReviewCommentPolicy(),
        )
        content_id, version_id, etag = in_review(client, tenant_id)
        blocked = decide(
            client,
            tenant_id,
            content_id,
            version_id,
            action="approve",
            etag=etag,
            body={"comment": SENSITIVE_TEST_COMMENT},
        )
        assert blocked.status_code == 422
        assert decision_count(bootstrap_engine, content_id) == 0
        assert command_intent_rows(bootstrap_engine, content_id) == []
        assert content_row(bootstrap_engine, content_id).stewardship_state == "IN_REVIEW"


class TestAtomicity:
    def test_failure_before_start_intent_rolls_back(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id, etag = generated_version(
            client_for(runtime_engine, tenant_id, principal_id), tenant_id
        )
        before = content_row(bootstrap_engine, content_id)
        service = ReviewCommandService(
            SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
            AllowReviewAuthorization(),
            AllowReviewCommentPolicy(),
            idempotency_retention=IDEMPOTENCY_RETENTION,
        )
        original = SqlAlchemyWorkflowIntentRepository.insert_start_intent

        def boom(self, intent):
            raise PersistenceOperationFailed("inject start intent failure")

        SqlAlchemyWorkflowIntentRepository.insert_start_intent = boom  # type: ignore[method-assign]
        try:
            with pytest.raises(PersistenceOperationFailed):
                service.submit(
                    tenant_id,
                    principal_id,
                    content_id=ContentId(UUID(content_id)),
                    version_id=ContentVersionId(UUID(version_id)),
                    expected_aggregate_revision=AggregateRevision(
                        int(etag.strip('"').lstrip("r"))
                    ),
                    idempotency_key=f"fail-start-{uuid.uuid7()}",
                    event_context=MutationEventContext(
                        correlation_id=uuid.uuid7(),
                        causation_id=uuid.uuid7(),
                        actor_principal_id=principal_id,
                        effective_actor_id=principal_id,
                    ),
                    audit_provenance=api_mutation_audit_provenance(principal_id),
                )
        finally:
            SqlAlchemyWorkflowIntentRepository.insert_start_intent = original  # type: ignore[method-assign]
        after = content_row(bootstrap_engine, content_id)
        assert after.stewardship_state == "GENERATED"
        assert int(after.aggregate_revision) == int(before.aggregate_revision)
        assert start_intent_rows(bootstrap_engine, content_id) == []

    def test_failure_before_command_intent_rolls_back(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id, etag = in_review(
            client_for(runtime_engine, tenant_id, principal_id), tenant_id
        )
        before = content_row(bootstrap_engine, content_id)
        service = ReviewCommandService(
            SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
            AllowReviewAuthorization(),
            AllowReviewCommentPolicy(),
            idempotency_retention=IDEMPOTENCY_RETENTION,
        )
        original = SqlAlchemyWorkflowIntentRepository.insert_command_intent

        def boom(self, intent):
            raise PersistenceOperationFailed("inject command intent failure")

        SqlAlchemyWorkflowIntentRepository.insert_command_intent = boom  # type: ignore[method-assign]
        try:
            with pytest.raises(PersistenceOperationFailed):
                service.approve(
                    tenant_id,
                    principal_id,
                    content_id=ContentId(UUID(content_id)),
                    version_id=ContentVersionId(UUID(version_id)),
                    expected_aggregate_revision=AggregateRevision(
                        int(etag.strip('"').lstrip("r"))
                    ),
                    reason_code=None,
                    comment=None,
                    idempotency_key=f"fail-cmd-{uuid.uuid7()}",
                    event_context=MutationEventContext(
                        correlation_id=uuid.uuid7(),
                        causation_id=uuid.uuid7(),
                        actor_principal_id=principal_id,
                        effective_actor_id=principal_id,
                    ),
                    audit_provenance=api_mutation_audit_provenance(principal_id),
                )
        finally:
            SqlAlchemyWorkflowIntentRepository.insert_command_intent = original  # type: ignore[method-assign]
        after = content_row(bootstrap_engine, content_id)
        assert after.stewardship_state == "IN_REVIEW"
        assert int(after.aggregate_revision) == int(before.aggregate_revision)
        assert decision_count(bootstrap_engine, content_id) == 0
        assert command_intent_rows(bootstrap_engine, content_id) == []


class TestTenantIsolationAndPrivilege:
    def test_runtime_cannot_update_or_delete_intents(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = client_for(runtime_engine, tenant_id, principal_id)
        content_id, version_id, etag = generated_version(client, tenant_id)
        submit_review(client, tenant_id, content_id, version_id, etag=etag)
        row = start_intent_rows(bootstrap_engine, content_id)[0]
        with runtime_engine.connect() as conn:
            set_tenant(conn, tenant_id)
            with pytest.raises(ProgrammingError):
                conn.execute(
                    text(
                        "UPDATE workflow.workflow_start_intents SET status = 'DELIVERED' "
                        "WHERE workflow_start_intent_id = :id"
                    ),
                    {"id": row["workflow_start_intent_id"]},
                )
                conn.commit()
        with runtime_engine.connect() as conn:
            set_tenant(conn, tenant_id)
            with pytest.raises(ProgrammingError):
                conn.execute(
                    text(
                        "DELETE FROM workflow.workflow_start_intents "
                        "WHERE workflow_start_intent_id = :id"
                    ),
                    {"id": row["workflow_start_intent_id"]},
                )
                conn.commit()

    def test_dispatcher_cannot_insert_and_tenant_isolation(
        self, runtime_engine, workflow_dispatcher_engine, bootstrap_engine
    ) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        client = client_for(runtime_engine, tenant_a, uuid.uuid7())
        content_id, version_id, etag = generated_version(client, tenant_a)
        submit_review(client, tenant_a, content_id, version_id, etag=etag)
        with workflow_dispatcher_engine.connect() as conn:
            set_tenant(conn, tenant_a)
            with pytest.raises(ProgrammingError):
                conn.execute(
                    text(
                        """
                        INSERT INTO workflow.workflow_start_intents (
                            workflow_start_intent_id, tenant_id, workflow_instance_id,
                            workflow_type, workflow_major_version, temporal_workflow_id,
                            task_queue, business_key, input, status, attempt_count,
                            available_at, created_at
                        ) VALUES (
                            :id, :tenant, :wid, 'ContentReviewWorkflowV1', 1, 'x',
                            'aieos.content.review', 'bk', '{}'::jsonb, 'PENDING', 0,
                            now(), now()
                        )
                        """
                    ),
                    {
                        "id": uuid.uuid7(),
                        "tenant": tenant_a,
                        "wid": uuid.uuid7(),
                    },
                )
                conn.commit()
        with workflow_dispatcher_engine.connect() as conn:
            set_tenant(conn, tenant_b)
            count = conn.execute(
                text("SELECT count(*) FROM workflow.workflow_start_intents")
            ).scalar_one()
            assert int(count) == 0
        with runtime_engine.connect() as conn:
            set_tenant(conn, tenant_b)
            count = conn.execute(
                text("SELECT count(*) FROM workflow.workflow_start_intents")
            ).scalar_one()
            assert int(count) == 0
        with runtime_engine.connect() as conn:
            with pytest.raises(Exception):
                conn.execute(text("SELECT count(*) FROM workflow.workflow_start_intents")).scalar_one()

    def test_two_dispatchers_claim_once_and_expired_reclaim(
        self, runtime_engine, workflow_dispatcher_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = client_for(runtime_engine, tenant_id, uuid.uuid7())
        content_id, version_id, etag = generated_version(client, tenant_id)
        submit_review(client, tenant_id, content_id, version_id, etag=etag)
        repo = SqlAlchemyWorkflowDispatcherRepository(workflow_dispatcher_engine)
        now = datetime.now(UTC)
        first = repo.claim_start_intent(
            tenant_id=tenant_id,
            claimed_by="d1",
            now=now,
            claim_until=now + timedelta(seconds=30),
        )
        second = repo.claim_start_intent(
            tenant_id=tenant_id,
            claimed_by="d2",
            now=now,
            claim_until=now + timedelta(seconds=30),
        )
        assert first is not None
        assert second is None
        assert first.status == INTENT_CLAIMED
        expired = repo.claim_start_intent(
            tenant_id=tenant_id,
            claimed_by="d2",
            now=now + timedelta(seconds=31),
            claim_until=now + timedelta(seconds=61),
        )
        assert expired is not None
        assert expired.claimed_by == "d2"
        assert expired.attempt_count == 2


class TestAlembicOwnership:
    def test_offline_sql_assumes_owner_before_workflow_ddl(self, postgres18) -> None:
        cfg = alembic_config(postgres18["migrator_url"])
        output = io.StringIO()
        with redirect_stdout(output):
            command.upgrade(cfg, "base:head", sql=True)
        sql_text = output.getvalue()
        role_stmt = f"SET LOCAL ROLE {SCHEMA_OWNER_ROLE}"
        create_schema = "CREATE SCHEMA workflow"
        role_at = sql_text.find(role_stmt)
        schema_at = sql_text.find(create_schema)
        assert role_at != -1
        assert schema_at != -1
        assert role_at < schema_at

    def test_upgrade_downgrade_cycle(self, postgres18, bootstrap_engine) -> None:
        cfg = alembic_config(postgres18["migrator_url"])
        clear_asset_audit_rows_for_schema_downgrade(bootstrap_engine)
        command.downgrade(cfg, "gcii060001")
        with bootstrap_engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
                "gcii060001"
            )
            schemas = {
                row[0]
                for row in conn.execute(text("SELECT nspname FROM pg_namespace"))
            }
            assert "workflow" not in schemas
        command.upgrade(cfg, "head")
        clear_asset_audit_rows_for_schema_downgrade(bootstrap_engine)
        command.downgrade(cfg, "base")
        command.upgrade(cfg, "head")
        provision_runtime_grants(bootstrap_engine)
        with bootstrap_engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == ("tosd070002")
