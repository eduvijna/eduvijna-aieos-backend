"""GCI-I14 adversarial: workflow/events architecture and outage invariants."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from aieos.platform.events.constants import OUTBOX_PENDING
from tests.dbutil import REPO_ROOT
from tests.domains.content.adversarial.helpers import (
    assert_problem,
    client,
    content_row,
    decide,
    decision_count,
    in_review,
    submit_review,
)
from tests.fakes import AllowReviewAuthorization
from tests.platform.events.helpers import assert_no_sensitive_material, outbox_rows
from tests.platform.workflows.helpers import (
    command_intent_rows,
    create_content,
    generated_version,
    start_intent_rows,
)

pytestmark = pytest.mark.gci_i14

CONTENT_SRC = REPO_ROOT / "src" / "aieos" / "domains" / "content"


class TestWorkflowEventsArchitecture:
    def test_domain_application_have_no_temporal_nats(self) -> None:
        hits: list[str] = []
        for root_name in ("domain", "application"):
            root = CONTENT_SRC / root_name
            for path in root.rglob("*.py"):
                text_src = path.read_text(encoding="utf-8")
                for needle in ("temporalio", "import nats", "nats.", "from nats"):
                    if needle in text_src:
                        hits.append(f"{path.relative_to(CONTENT_SRC)}:{needle}")
        assert hits == []

    def test_event_payloads_exclude_sensitive_material(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        c = client(runtime_engine, tenant_id)
        content_id, version_id, etag = in_review(c, tenant_id)
        approved = decide(
            c,
            tenant_id,
            content_id,
            version_id,
            action="approve",
            etag=etag,
            body={"comment": "ok"},
        )
        assert approved.status_code == 200
        for row in outbox_rows(bootstrap_engine, content_id=content_id):
            assert_no_sensitive_material(dict(row["envelope"]))
            blob = str(row["envelope"])
            assert "api_key" not in blob
            assert "Bearer " not in blob
            assert '"marker"' not in blob

    def test_nats_outage_business_committed_outbox_pending(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        """NATS need not be up: mutation commits with PENDING outbox intent."""
        tenant_id = uuid.uuid7()
        c = client(runtime_engine, tenant_id)
        created = create_content(c, tenant_id)
        content_id = created["content_id"]
        assert content_row(bootstrap_engine, content_id).stewardship_state == "DRAFT"
        rows = outbox_rows(bootstrap_engine, content_id=content_id)
        assert len(rows) == 1
        assert rows[0]["status"] == OUTBOX_PENDING
        assert rows[0]["event_type"] == "io.eduvijna.aieos.content.content.created.v1"

    def test_duplicate_submit_does_not_duplicate_start_intent(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        c = client(runtime_engine, tenant_id)
        content_id, version_id, etag = generated_version(c, tenant_id)
        key = f"submit-{uuid.uuid7()}"
        first = submit_review(
            c,
            tenant_id,
            content_id,
            version_id,
            etag=etag,
            **{"Idempotency-Key": key},
        )
        assert first.status_code == 200
        replay = submit_review(
            c,
            tenant_id,
            content_id,
            version_id,
            etag=etag,
            **{"Idempotency-Key": key},
        )
        assert replay.status_code == 200
        starts = start_intent_rows(bootstrap_engine, content_id)
        assert len(starts) == 1

    def test_authorization_revoke_while_in_review_before_decide(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        auth = AllowReviewAuthorization()
        c = client(runtime_engine, tenant_id, principal_id, authorization=auth)
        content_id, version_id, etag = in_review(c, tenant_id)
        assert content_row(bootstrap_engine, content_id).stewardship_state == "IN_REVIEW"
        auth.allow_decide = False
        denied = client(
            runtime_engine, tenant_id, principal_id, authorization=auth
        )
        response = decide(
            denied, tenant_id, content_id, version_id, action="approve", etag=etag
        )
        assert_problem(response, status=403, code="forbidden")
        assert decision_count(bootstrap_engine, content_id) == 0
        assert command_intent_rows(bootstrap_engine, content_id) == []
        assert content_row(bootstrap_engine, content_id).stewardship_state == "IN_REVIEW"
