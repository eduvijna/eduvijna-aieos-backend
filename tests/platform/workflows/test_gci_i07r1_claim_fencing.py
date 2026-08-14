"""GCI-I07R1 claim fencing and terminal non-regression."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from aieos.platform.workflows.constants import INTENT_DELIVERED
from aieos.platform.workflows.persistence.repositories import (
    SqlAlchemyWorkflowDispatcherRepository,
)
from tests.platform.workflows.helpers import (
    client_for,
    command_intent_rows,
    decide,
    generated_version,
    in_review,
    start_intent_rows,
    submit_review,
)

pytestmark = pytest.mark.gci_i07


class TestClaimFencing:
    def test_stale_start_claim_cannot_mutate_delivered_row(
        self, runtime_engine, workflow_dispatcher_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = client_for(runtime_engine, tenant_id, uuid.uuid7())
        content_id, version_id, etag = generated_version(client, tenant_id)
        submit_review(client, tenant_id, content_id, version_id, etag=etag)
        repo = SqlAlchemyWorkflowDispatcherRepository(workflow_dispatcher_engine)
        now = datetime.now(UTC)
        claim_a = repo.claim_start_intent(
            tenant_id=tenant_id,
            claimed_by="A",
            now=now,
            claim_until=now + timedelta(seconds=1),
        )
        assert claim_a is not None
        attempt_a = claim_a.attempt_count
        claim_b = repo.claim_start_intent(
            tenant_id=tenant_id,
            claimed_by="B",
            now=now + timedelta(seconds=2),
            claim_until=now + timedelta(seconds=32),
        )
        assert claim_b is not None
        assert claim_b.attempt_count == attempt_a + 1
        assert repo.mark_start_delivered(
            tenant_id=tenant_id,
            workflow_start_intent_id=claim_b.workflow_start_intent_id.value,
            claimed_by="B",
            attempt_count=claim_b.attempt_count,
            delivered_at=now + timedelta(seconds=3),
        )
        row = start_intent_rows(bootstrap_engine, content_id)[0]
        assert row["status"] == INTENT_DELIVERED
        assert int(row["attempt_count"]) == claim_b.attempt_count

        assert not repo.release_start_for_retry(
            tenant_id=tenant_id,
            workflow_start_intent_id=claim_a.workflow_start_intent_id.value,
            claimed_by="A",
            attempt_count=attempt_a,
            available_at=now + timedelta(seconds=10),
            error_code="temporal_unavailable",
            quarantine=False,
        )
        assert not repo.release_start_for_retry(
            tenant_id=tenant_id,
            workflow_start_intent_id=claim_a.workflow_start_intent_id.value,
            claimed_by="A",
            attempt_count=attempt_a,
            available_at=now + timedelta(seconds=10),
            error_code="workflow_identity_conflict",
            quarantine=True,
        )
        assert not repo.mark_start_delivered(
            tenant_id=tenant_id,
            workflow_start_intent_id=claim_a.workflow_start_intent_id.value,
            claimed_by="A",
            attempt_count=attempt_a,
            delivered_at=now + timedelta(seconds=11),
        )
        final = start_intent_rows(bootstrap_engine, content_id)[0]
        assert final["status"] == INTENT_DELIVERED
        assert int(final["attempt_count"]) == claim_b.attempt_count
        assert final["last_error_code"] is None

    def test_stale_command_claim_cannot_mutate_delivered_row(
        self, runtime_engine, workflow_dispatcher_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = client_for(runtime_engine, tenant_id, uuid.uuid7())
        content_id, version_id, etag = in_review(client, tenant_id)
        # Mark start delivered so command can be claimed.
        start = start_intent_rows(bootstrap_engine, content_id)[0]
        repo = SqlAlchemyWorkflowDispatcherRepository(workflow_dispatcher_engine)
        now = datetime.now(UTC)
        start_claim = repo.claim_start_intent(
            tenant_id=tenant_id,
            claimed_by="S",
            now=now,
            claim_until=now + timedelta(seconds=30),
        )
        assert start_claim is not None
        assert repo.mark_start_delivered(
            tenant_id=tenant_id,
            workflow_start_intent_id=start_claim.workflow_start_intent_id.value,
            claimed_by="S",
            attempt_count=start_claim.attempt_count,
            delivered_at=now,
        )
        assert start["workflow_start_intent_id"] == start_claim.workflow_start_intent_id.value
        approved = decide(
            client,
            tenant_id,
            content_id,
            version_id,
            action="approve",
            etag=etag,
        )
        assert approved.status_code == 200, approved.text
        now = datetime.now(UTC)
        claim_a = repo.claim_command_intent(
            tenant_id=tenant_id,
            claimed_by="A",
            now=now,
            claim_until=now + timedelta(seconds=1),
        )
        assert claim_a is not None
        attempt_a = claim_a.attempt_count
        claim_b = repo.claim_command_intent(
            tenant_id=tenant_id,
            claimed_by="B",
            now=now + timedelta(seconds=2),
            claim_until=now + timedelta(seconds=32),
        )
        assert claim_b is not None
        assert claim_b.attempt_count == attempt_a + 1
        assert repo.mark_command_delivered(
            tenant_id=tenant_id,
            workflow_command_intent_id=claim_b.workflow_command_intent_id.value,
            claimed_by="B",
            attempt_count=claim_b.attempt_count,
            delivered_at=now + timedelta(seconds=3),
        )
        assert not repo.release_command_for_retry(
            tenant_id=tenant_id,
            workflow_command_intent_id=claim_a.workflow_command_intent_id.value,
            claimed_by="A",
            attempt_count=attempt_a,
            available_at=now + timedelta(seconds=10),
            error_code="temporal_unavailable",
            quarantine=False,
        )
        assert not repo.release_command_for_retry(
            tenant_id=tenant_id,
            workflow_command_intent_id=claim_a.workflow_command_intent_id.value,
            claimed_by="A",
            attempt_count=attempt_a,
            available_at=now + timedelta(seconds=10),
            error_code="workflow_terminal_mismatch",
            quarantine=True,
        )
        assert not repo.mark_command_delivered(
            tenant_id=tenant_id,
            workflow_command_intent_id=claim_a.workflow_command_intent_id.value,
            claimed_by="A",
            attempt_count=attempt_a,
            delivered_at=now + timedelta(seconds=11),
        )
        final = command_intent_rows(bootstrap_engine, content_id)[0]
        assert final["status"] == INTENT_DELIVERED
        assert int(final["attempt_count"]) == claim_b.attempt_count
        assert final["last_error_code"] is None
