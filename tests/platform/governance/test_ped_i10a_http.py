"""PED-I10A HTTP / mutation safety evidence."""

from __future__ import annotations

import json
import uuid
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.application.asset_authority_adapters import (
    AssetAuthorityCurrentGovernanceAdapter,
    AssetAuthorityReferenceValidationAdapter,
)
from aieos.platform.governance import (
    BaselinePublicationGovernanceV1,
    DeterministicReviewCommentPolicyV1,
)
from aieos.platform.resources.asset_use import (
    AssetUseAssessment,
    AssetUseRejectionReason,
)
from tests.domains.content.adversarial.helpers import (
    assert_problem,
    client,
    content_row,
    create_content,
    decide,
    decision_count,
    headers,
    idempotency_count,
    in_review,
    outbox_count_for_content,
    publication_count,
    submit_review,
)
from tests.fakes import (
    AllowAssetCurrentGovernance,
    AllowAssetReferenceValidation,
    AllowReviewCommentPolicy,
)
from tests.platform.governance.helpers import RecordingAssetUseAuthority

pytestmark = pytest.mark.ped_i10a

HANDLED = frozenset({"asset.image", "asset.document"})
_SECRET = "sk_live_SUPERSECRETVALUE99"
_PRIVATE_KEY_SNIPPET = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA"


def _asset_ref_body(
    *,
    resource_id: UUID | None = None,
    resource_type: str = "asset.image",
    revision: int | None = None,
    required: bool = True,
) -> dict:
    return {
        "role": "primary",
        "ordinal": 0,
        "required": required,
        "resource_ref": {
            "resource_type": resource_type,
            "resource_id": str(resource_id or uuid.uuid7()),
            "resource_revision": revision,
        },
    }


def _version_count(engine: Engine, content_id: str | UUID) -> int:
    with engine.connect() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT count(*) FROM content.content_versions WHERE content_id = :cid"
                ),
                {"cid": UUID(str(content_id))},
            ).scalar_one()
        )


def _asset_ref_count(engine: Engine, content_id: str | UUID) -> int:
    with engine.connect() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT count(*) FROM content.version_asset_refs WHERE content_id = :cid"
                ),
                {"cid": UUID(str(content_id))},
            ).scalar_one()
        )


def _audit_count(engine: Engine, tenant_id: UUID) -> int:
    with engine.connect() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT count(*) FROM security.audit_records WHERE tenant_id = :tid"
                ),
                {"tid": tenant_id},
            ).scalar_one()
        )


def _append_with_refs(
    c: TestClient,
    tenant_id: UUID,
    content_id: str,
    *,
    etag: str,
    refs: list[dict],
    key: str | None = None,
):
    hdrs = headers(tenant_id)
    if key is not None:
        hdrs["Idempotency-Key"] = key
    hdrs["If-Match"] = etag
    body = {
        "schema_id": "test.generic",
        "schema_version": 1,
        "payload": {"marker": "v1"},
        "asset_refs": refs,
    }
    return c.post(f"/api/v1/contents/{content_id}/versions", json=body, headers=hdrs)


def _publish(c, tenant_id, content_id, version_id, etag, **extra):
    hdrs = headers(tenant_id, **extra)
    hdrs["If-Match"] = etag
    return c.post(
        f"/api/v1/contents/{content_id}/actions/publish",
        json={"version_id": version_id},
        headers=hdrs,
    )


def _binding_client(runtime_engine, tenant_id, principal_id, authority):
    return client(
        runtime_engine,
        tenant_id,
        principal_id,
        asset_reference_validation=AssetAuthorityReferenceValidationAdapter(
            authority, handled_resource_types=HANDLED
        ),
        asset_current_governance=AssetAuthorityCurrentGovernanceAdapter(
            authority, handled_resource_types=HANDLED
        ),
        publication_governance=BaselinePublicationGovernanceV1(),
        comment_policy=DeterministicReviewCommentPolicyV1(),
    )


class TestReviewCommentHttp:
    def test_rejected_comment_no_persistence(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        c = client(
            runtime_engine,
            tenant_id,
            comment_policy=DeterministicReviewCommentPolicyV1(),
        )
        content_id, version_id, etag = in_review(c, tenant_id)
        before_outbox = outbox_count_for_content(bootstrap_engine, content_id)
        before_idem = idempotency_count(bootstrap_engine, tenant_id)
        before_audit = _audit_count(bootstrap_engine, tenant_id)
        denied = decide(
            c,
            tenant_id,
            content_id,
            version_id,
            action="approve",
            etag=etag,
            body={"comment": f"note {_PRIVATE_KEY_SNIPPET}"},
        )
        body = assert_problem(denied, status=422, code="review_comment_rejected")
        blob = json.dumps(body) + denied.text
        assert "PRIVATE KEY" not in blob
        assert "MIIEowIBAAKCAQEA" not in blob
        assert decision_count(bootstrap_engine, content_id) == 0
        assert outbox_count_for_content(bootstrap_engine, content_id) == before_outbox
        assert idempotency_count(bootstrap_engine, tenant_id) == before_idem
        assert _audit_count(bootstrap_engine, tenant_id) == before_audit
        assert content_row(bootstrap_engine, content_id).stewardship_state == "IN_REVIEW"


class TestAssetBindingHttp:
    def test_unusable_asset_no_commit(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        asset_id = uuid.uuid7()
        authority = RecordingAssetUseAuthority(
            assessments={
                asset_id: AssetUseAssessment(
                    usable=False, reason_code=AssetUseRejectionReason.QUARANTINED
                )
            }
        )
        c = _binding_client(runtime_engine, tenant_id, principal_id, authority)
        content_id = create_content(c, tenant_id)["content_id"]
        before_versions = _version_count(bootstrap_engine, content_id)
        before_refs = _asset_ref_count(bootstrap_engine, content_id)
        before_idem = idempotency_count(bootstrap_engine, tenant_id)
        before_audit = _audit_count(bootstrap_engine, tenant_id)
        before_outbox = outbox_count_for_content(bootstrap_engine, content_id)
        denied = _append_with_refs(
            c,
            tenant_id,
            content_id,
            etag='"r0"',
            refs=[_asset_ref_body(resource_id=asset_id)],
        )
        body = assert_problem(denied, status=422, code="asset_reference_invalid")
        assert "QUARANTINED" not in json.dumps(body)
        assert _version_count(bootstrap_engine, content_id) == before_versions
        assert _asset_ref_count(bootstrap_engine, content_id) == before_refs
        assert idempotency_count(bootstrap_engine, tenant_id) == before_idem
        assert _audit_count(bootstrap_engine, tenant_id) == before_audit
        assert outbox_count_for_content(bootstrap_engine, content_id) == before_outbox

    def test_governance_unavailable_binding(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        authority = RecordingAssetUseAuthority(unavailable=True)
        c = _binding_client(runtime_engine, tenant_id, principal_id, authority)
        content_id = create_content(c, tenant_id)["content_id"]
        before_versions = _version_count(bootstrap_engine, content_id)
        response = _append_with_refs(
            c,
            tenant_id,
            content_id,
            etag='"r0"',
            refs=[_asset_ref_body()],
        )
        body = assert_problem(response, status=503, code="governance_unavailable")
        assert body["title"] == "Governance unavailable"
        assert body["detail"] == "Governance is temporarily unavailable"
        assert body["type"] == "urn:aieos:problem:governance_unavailable"
        assert _version_count(bootstrap_engine, content_id) == before_versions
        assert _asset_ref_count(bootstrap_engine, content_id) == 0

    def test_runtime_error_is_internal_error(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        authority = RecordingAssetUseAuthority(raise_runtime=True)
        c = _binding_client(runtime_engine, tenant_id, principal_id, authority)
        content_id = create_content(c, tenant_id)["content_id"]
        response = _append_with_refs(
            c,
            tenant_id,
            content_id,
            etag='"r0"',
            refs=[_asset_ref_body()],
        )
        body = assert_problem(response, status=500, code="internal_error")
        assert "SECRET_ASSET_AUTHORITY_BUG" not in response.text
        assert body["code"] != "governance_unavailable"
        assert _version_count(bootstrap_engine, content_id) == 0


class TestPublishCurrentUseHttp:
    def _approve_with_asset(self, runtime_engine, tenant_id, principal_id, authority):
        c = _binding_client(runtime_engine, tenant_id, principal_id, authority)
        content_id = create_content(c, tenant_id)["content_id"]
        asset_id = uuid.uuid7()
        authority.assessments[asset_id] = AssetUseAssessment(usable=True)
        appended = _append_with_refs(
            c,
            tenant_id,
            content_id,
            etag='"r0"',
            refs=[_asset_ref_body(resource_id=asset_id)],
        )
        assert appended.status_code == 201, appended.text
        version_id = appended.json()["version_id"]
        submitted = submit_review(
            c, tenant_id, content_id, version_id, etag=appended.headers["ETag"]
        )
        assert submitted.status_code == 200, submitted.text
        approved = decide(
            c,
            tenant_id,
            content_id,
            version_id,
            action="approve",
            etag=submitted.headers["ETag"],
        )
        assert approved.status_code == 200, approved.text
        return c, content_id, version_id, approved.headers["ETag"], asset_id

    def test_unusable_current_asset_no_publication(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        authority = RecordingAssetUseAuthority()
        c, content_id, version_id, etag, asset_id = self._approve_with_asset(
            runtime_engine, tenant_id, principal_id, authority
        )
        authority.assessments[asset_id] = AssetUseAssessment(
            usable=False, reason_code=AssetUseRejectionReason.WITHDRAWN
        )
        before_pub = publication_count(bootstrap_engine, content_id)
        before_pointer = content_row(bootstrap_engine, content_id).published_version_id
        before_idem = idempotency_count(bootstrap_engine, tenant_id)
        before_audit = _audit_count(bootstrap_engine, tenant_id)
        before_outbox = outbox_count_for_content(bootstrap_engine, content_id)
        denied = _publish(c, tenant_id, content_id, version_id, etag)
        body = assert_problem(
            denied, status=409, code="publication_asset_validation_failed"
        )
        assert "WITHDRAWN" not in json.dumps(body)
        assert publication_count(bootstrap_engine, content_id) == before_pub
        assert (
            content_row(bootstrap_engine, content_id).published_version_id
            == before_pointer
        )
        assert idempotency_count(bootstrap_engine, tenant_id) == before_idem
        assert _audit_count(bootstrap_engine, tenant_id) == before_audit
        assert outbox_count_for_content(bootstrap_engine, content_id) == before_outbox

    def test_governance_unavailable_on_publish(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        authority = RecordingAssetUseAuthority()
        c, content_id, version_id, etag, _asset_id = self._approve_with_asset(
            runtime_engine, tenant_id, principal_id, authority
        )
        authority.unavailable = True
        denied = _publish(c, tenant_id, content_id, version_id, etag)
        body = assert_problem(denied, status=503, code="governance_unavailable")
        assert body["detail"] == "Governance is temporarily unavailable"
        assert publication_count(bootstrap_engine, content_id) == 0


class TestIdempotentReplayStability:
    def test_append_replay_stable_after_governance_hardening(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        asset_id = uuid.uuid7()
        authority = RecordingAssetUseAuthority()
        c = _binding_client(runtime_engine, tenant_id, principal_id, authority)
        content_id = create_content(c, tenant_id)["content_id"]
        key = f"append-replay-{uuid.uuid7()}"
        first = _append_with_refs(
            c,
            tenant_id,
            content_id,
            etag='"r0"',
            refs=[_asset_ref_body(resource_id=asset_id)],
            key=key,
        )
        assert first.status_code == 201, first.text
        authority.assessments[asset_id] = AssetUseAssessment(
            usable=False, reason_code=AssetUseRejectionReason.DELETED
        )
        replay = _append_with_refs(
            c,
            tenant_id,
            content_id,
            etag='"r0"',
            refs=[_asset_ref_body(resource_id=asset_id)],
            key=key,
        )
        assert replay.status_code == 201, replay.text
        assert replay.json()["version_id"] == first.json()["version_id"]
        assert _version_count(bootstrap_engine, content_id) == 1

    def test_review_replay_stable_after_comment_policy_hardening(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        allowing = client(
            runtime_engine,
            tenant_id,
            principal_id,
            comment_policy=AllowReviewCommentPolicy(),
        )
        content_id, version_id, etag = in_review(allowing, tenant_id)
        key = f"review-replay-{uuid.uuid7()}"
        first = decide(
            allowing,
            tenant_id,
            content_id,
            version_id,
            action="approve",
            etag=etag,
            body={"comment": f"ok {_SECRET}"},
            **{"Idempotency-Key": key},
        )
        assert first.status_code == 200, first.text
        hardened = client(
            runtime_engine,
            tenant_id,
            principal_id,
            comment_policy=DeterministicReviewCommentPolicyV1(),
        )
        replay = decide(
            hardened,
            tenant_id,
            content_id,
            version_id,
            action="approve",
            etag=etag,
            body={"comment": f"ok {_SECRET}"},
            **{"Idempotency-Key": key},
        )
        assert replay.status_code == 200, replay.text
        assert decision_count(bootstrap_engine, content_id) == 1

    def test_publish_replay_stable_after_asset_quarantine(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        authority = RecordingAssetUseAuthority()
        c, content_id, version_id, etag, asset_id = (
            TestPublishCurrentUseHttp()._approve_with_asset(
                runtime_engine, tenant_id, principal_id, authority
            )
        )
        key = f"pub-replay-{uuid.uuid7()}"
        first = _publish(
            c, tenant_id, content_id, version_id, etag, **{"Idempotency-Key": key}
        )
        assert first.status_code == 200, first.text
        authority.assessments[asset_id] = AssetUseAssessment(
            usable=False, reason_code=AssetUseRejectionReason.QUARANTINED
        )
        replay = _publish(
            c, tenant_id, content_id, version_id, etag, **{"Idempotency-Key": key}
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["publication_id"] == first.json()["publication_id"]
        assert publication_count(bootstrap_engine, content_id) == 1


class TestProductionBaselineWiring:
    def test_baseline_publication_governance_succeeds(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        c = client(
            runtime_engine,
            tenant_id,
            publication_governance=BaselinePublicationGovernanceV1(),
            asset_reference_validation=AllowAssetReferenceValidation(),
            asset_current_governance=AllowAssetCurrentGovernance(),
            comment_policy=AllowReviewCommentPolicy(),
        )
        content_id, version_id, etag = in_review(c, tenant_id)
        approved = decide(
            c, tenant_id, content_id, version_id, action="approve", etag=etag
        )
        assert approved.status_code == 200
        published = _publish(
            c, tenant_id, content_id, version_id, approved.headers["ETag"]
        )
        assert published.status_code == 200, published.text
