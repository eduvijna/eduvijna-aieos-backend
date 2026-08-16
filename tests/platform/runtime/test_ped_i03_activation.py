"""PED-I03 fail-closed API mutation activation tests."""

from __future__ import annotations

import base64
import uuid
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.platform.api.app import create_app
from aieos.platform.api.etag import encode_revision_etag
from aieos.platform.runtime import (
    ApiRuntimeDependencies,
    MutationActivationDecision,
    MutationActivationStatus,
    ReadinessCode,
    ReadinessResult,
    compose_api_application,
    load_api_mutation_activation_gate,
    load_api_runtime_config,
)
from aieos.platform.runtime.activation import (
    ENV_API_MUTATION_ACTIVATION,
    ENV_API_MUTATION_AUTHORIZED_ARTIFACT_DIGEST,
    ENV_API_MUTATION_AUTHORIZED_GIT_SHA,
)
from aieos.platform.runtime.config import (
    ENV_ARTIFACT_DIGEST,
    ENV_BUILD_ID,
    ENV_CURSOR_SIGNING_KEY_B64,
    ENV_DEPLOYMENT_ENVIRONMENT,
    ENV_GIT_SHA,
    ENV_IDEMPOTENCY_RETENTION_SECONDS,
    ENV_MIGRATOR_ROLE,
    ENV_RELEASE_VERSION,
    ENV_RUNTIME_DATABASE_CONNECT_TIMEOUT_SECONDS,
    ENV_RUNTIME_DATABASE_ROLE,
    ENV_RUNTIME_DATABASE_URL,
    ENV_SCHEMA_OWNER_ROLE,
    ENV_SECURITY_SCHEMA_OWNER_ROLE,
)
from tests.conftest import (
    RUNTIME_USER,
    SCHEMA_OWNER_ROLE,
    SECURITY_SCHEMA_OWNER_ROLE,
)
from tests.fakes import (
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

pytestmark = pytest.mark.ped_i03

VALID_GIT_SHA = "e" * 40
OTHER_GIT_SHA = "f" * 40
VALID_DIGEST = "sha256:" + ("a" * 64)
OTHER_DIGEST = "sha256:" + ("b" * 64)
SECRET_DB_PASSWORD = "SUPER_SECRET_DB_PASSWORD_PED_I03"
SECRET_CURSOR = b"SUPER_SECRET_CURSOR_KEY_PED_I03"
CURSOR_B64 = base64.b64encode(SECRET_CURSOR).decode("ascii")
CREATE_BODY = {
    "content_type": "test.generic",
    "title": "Title",
    "description": "Description",
    "locale": "en-IN",
}


class _ReadyProbe:
    def check(self) -> ReadinessResult:
        return ReadinessResult(True, ReadinessCode.READY)


class _DisabledGate:
    def check(self) -> MutationActivationDecision:
        return MutationActivationDecision(
            False, MutationActivationStatus.DISABLED
        )


class _BoomGate:
    def check(self) -> MutationActivationDecision:
        raise RuntimeError("SUPER_SECRET activation boom")


class _CountingUowFactory:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, execution_tenant_id: Any) -> Any:
        self.calls += 1
        raise AssertionError("UoW must not open when mutations are blocked")


def _config(runtime_url: str, *, git_sha: str = VALID_GIT_SHA, digest: str = VALID_DIGEST):
    return load_api_runtime_config(
        {
            ENV_DEPLOYMENT_ENVIRONMENT: "STAGING",
            ENV_RELEASE_VERSION: "0.1.0",
            ENV_GIT_SHA: git_sha,
            ENV_BUILD_ID: "build-ped-i03",
            ENV_ARTIFACT_DIGEST: digest,
            ENV_RUNTIME_DATABASE_URL: runtime_url,
            ENV_RUNTIME_DATABASE_ROLE: RUNTIME_USER,
            ENV_SCHEMA_OWNER_ROLE: SCHEMA_OWNER_ROLE,
            ENV_SECURITY_SCHEMA_OWNER_ROLE: SECURITY_SCHEMA_OWNER_ROLE,
            ENV_MIGRATOR_ROLE: "aieos_migrator",
            ENV_CURSOR_SIGNING_KEY_B64: CURSOR_B64,
            ENV_IDEMPOTENCY_RETENTION_SECONDS: "86400",
            ENV_RUNTIME_DATABASE_CONNECT_TIMEOUT_SECONDS: "5",
        }
    )


def _compose(
    config,
    *,
    gate,
    uow_factory,
    request_identity_authenticator=None,
    security_resolver=None,
    review_authorization=None,
    publication_authorization=None,
):
    tenant = uuid4()
    principal = uuid4()
    return (
        compose_api_application(
            config,
            ApiRuntimeDependencies(
                uow_factory=uow_factory,
                request_identity_authenticator=request_identity_authenticator
                or FixedPrincipalAuthenticator(principal),
                security_resolver=security_resolver
                or StubSecurityContextResolver(tenant, principal),
                content_types=StaticContentTypeCatalog({"test.generic"}),
                schema_registry=make_test_schema_registry(),
                review_authorization=review_authorization
                or AllowReviewAuthorization(),
                review_comment_policy=AllowReviewCommentPolicy(),
                publication_authorization=publication_authorization
                or AllowPublicationAuthorization(),
                publication_governance=AllowPublicationGovernance(),
                asset_reference_validation=AllowAssetReferenceValidation(),
                asset_current_governance=AllowAssetCurrentGovernance(),
                readiness_probe=_ReadyProbe(),
                mutation_activation_gate=gate,
            ),
        ),
        tenant,
        principal,
    )


def _ungated_app(runtime_engine: Engine, tenant_id: UUID, principal_id: UUID):
    return create_app(
        uow_factory=SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
        request_identity_authenticator=FixedPrincipalAuthenticator(principal_id),
        security_resolver=StubSecurityContextResolver(tenant_id, principal_id),
        content_types=StaticContentTypeCatalog({"test.generic"}),
        cursor_signing_key=SECRET_CURSOR,
        schema_registry=make_test_schema_registry(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
        review_authorization=AllowReviewAuthorization(),
        review_comment_policy=AllowReviewCommentPolicy(),
        publication_authorization=AllowPublicationAuthorization(),
        publication_governance=AllowPublicationGovernance(),
        asset_reference_validation=AllowAssetReferenceValidation(),
        asset_current_governance=AllowAssetCurrentGovernance(),
    )


def _headers(tenant_id: UUID, **extra: str) -> dict[str, str]:
    headers = {"X-AIEOS-Tenant-ID": str(tenant_id), **extra}
    if "Idempotency-Key" not in headers:
        headers["Idempotency-Key"] = f"test-{uuid.uuid7()}"
    return headers


def _assert_blocked(response) -> dict:
    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["code"] == "mutations_not_activated"
    assert body["title"] == "Mutations not activated"
    assert "not activated" in body["detail"].lower()
    assert body["instance"]
    assert body["request_id"]
    assert body["correlation_id"]
    assert SECRET_DB_PASSWORD not in response.text
    assert SECRET_CURSOR.decode("ascii") not in response.text
    assert "SUPER_SECRET" not in response.text
    assert "Traceback" not in response.text
    assert ENV_API_MUTATION_ACTIVATION not in response.text
    return body


def _counts(bootstrap_engine: Engine, tenant_id: UUID) -> dict[str, int]:
    with bootstrap_engine.connect() as conn:
        conn.execute(
            text("SELECT set_config('aieos.tenant_id', :t, true)"),
            {"t": str(tenant_id)},
        )
        return {
            "contents": int(
                conn.execute(text("SELECT count(*) FROM content.contents")).scalar_one()
            ),
            "versions": int(
                conn.execute(
                    text("SELECT count(*) FROM content.content_versions")
                ).scalar_one()
            ),
            "reviews": int(
                conn.execute(
                    text("SELECT count(*) FROM content.review_decisions")
                ).scalar_one()
            ),
            "publications": int(
                conn.execute(
                    text("SELECT count(*) FROM content.publications")
                ).scalar_one()
            ),
            "idempotency": int(
                conn.execute(
                    text("SELECT count(*) FROM api.idempotency_records")
                ).scalar_one()
            ),
            "workflow_start": int(
                conn.execute(
                    text("SELECT count(*) FROM workflow.workflow_start_intents")
                ).scalar_one()
            ),
            "workflow_command": int(
                conn.execute(
                    text("SELECT count(*) FROM workflow.workflow_command_intents")
                ).scalar_one()
            ),
            "outbox": int(
                conn.execute(
                    text("SELECT count(*) FROM integration.outbox_messages")
                ).scalar_one()
            ),
            "audit": int(
                conn.execute(
                    text("SELECT count(*) FROM security.audit_records")
                ).scalar_one()
            ),
        }


class TestActivationLoader:
    def test_missing_and_invalid_and_disabled(self, postgres18) -> None:
        config = _config(postgres18["runtime_url"])
        release = config.release_identity
        assert (
            load_api_mutation_activation_gate({}, release).check().enabled is False
        )
        assert (
            load_api_mutation_activation_gate(
                {ENV_API_MUTATION_ACTIVATION: "DISABLED"}, release
            )
            .check()
            .enabled
            is False
        )
        for bad in ("enabled", "true", "TRUE", "1", "yes", "ON", "unknown"):
            decision = load_api_mutation_activation_gate(
                {ENV_API_MUTATION_ACTIVATION: bad}, release
            ).check()
            assert decision.enabled is False
            assert decision.status is MutationActivationStatus.INVALID_CONFIGURATION

    def test_enabled_requires_exact_release_binding(self, postgres18) -> None:
        config = _config(postgres18["runtime_url"])
        release = config.release_identity
        base = {ENV_API_MUTATION_ACTIVATION: "ENABLED"}
        assert (
            load_api_mutation_activation_gate(base, release).check().enabled is False
        )
        assert (
            load_api_mutation_activation_gate(
                {
                    **base,
                    ENV_API_MUTATION_AUTHORIZED_GIT_SHA: VALID_GIT_SHA,
                },
                release,
            )
            .check()
            .enabled
            is False
        )
        assert (
            load_api_mutation_activation_gate(
                {
                    **base,
                    ENV_API_MUTATION_AUTHORIZED_GIT_SHA: VALID_GIT_SHA,
                    ENV_API_MUTATION_AUTHORIZED_ARTIFACT_DIGEST: OTHER_DIGEST,
                },
                release,
            )
            .check()
            .status
            is MutationActivationStatus.RELEASE_MISMATCH
        )
        assert (
            load_api_mutation_activation_gate(
                {
                    **base,
                    ENV_API_MUTATION_AUTHORIZED_GIT_SHA: OTHER_GIT_SHA,
                    ENV_API_MUTATION_AUTHORIZED_ARTIFACT_DIGEST: VALID_DIGEST,
                },
                release,
            )
            .check()
            .status
            is MutationActivationStatus.RELEASE_MISMATCH
        )
        assert (
            load_api_mutation_activation_gate(
                {
                    **base,
                    ENV_API_MUTATION_AUTHORIZED_GIT_SHA: VALID_GIT_SHA.upper(),
                    ENV_API_MUTATION_AUTHORIZED_ARTIFACT_DIGEST: VALID_DIGEST,
                },
                release,
            )
            .check()
            .enabled
            is False
        )
        enabled = load_api_mutation_activation_gate(
            {
                **base,
                ENV_API_MUTATION_AUTHORIZED_GIT_SHA: VALID_GIT_SHA,
                ENV_API_MUTATION_AUTHORIZED_ARTIFACT_DIGEST: VALID_DIGEST,
            },
            release,
        ).check()
        assert enabled.enabled is True
        assert enabled.status is MutationActivationStatus.ENABLED

    def test_stale_release_authorization(self, postgres18) -> None:
        release_b = _config(
            postgres18["runtime_url"], git_sha=OTHER_GIT_SHA, digest=OTHER_DIGEST
        ).release_identity
        decision = load_api_mutation_activation_gate(
            {
                ENV_API_MUTATION_ACTIVATION: "ENABLED",
                ENV_API_MUTATION_AUTHORIZED_GIT_SHA: VALID_GIT_SHA,
                ENV_API_MUTATION_AUTHORIZED_ARTIFACT_DIGEST: VALID_DIGEST,
            },
            release_b,
        ).check()
        assert decision.enabled is False
        assert decision.status is MutationActivationStatus.RELEASE_MISMATCH


class TestPreUowBlocking:
    @pytest.mark.parametrize(
        "method,path_builder,body",
        [
            ("POST", lambda cid, vid: "/api/v1/contents", CREATE_BODY),
            (
                "POST",
                lambda cid, vid: f"/api/v1/contents/{cid}/versions",
                {"schema_id": "test.generic", "schema_version": 1, "payload": {"marker": "x"}},
            ),
            (
                "POST",
                lambda cid, vid: (
                    f"/api/v1/contents/{cid}/versions/{vid}/actions/submit-for-review"
                ),
                {},
            ),
            (
                "POST",
                lambda cid, vid: (
                    f"/api/v1/contents/{cid}/versions/{vid}/actions/approve"
                ),
                {},
            ),
            (
                "POST",
                lambda cid, vid: (
                    f"/api/v1/contents/{cid}/versions/{vid}/actions/request-changes"
                ),
                {"comment": "n"},
            ),
            (
                "POST",
                lambda cid, vid: (
                    f"/api/v1/contents/{cid}/versions/{vid}/actions/reject"
                ),
                {"comment": "n"},
            ),
            (
                "POST",
                lambda cid, vid: f"/api/v1/contents/{cid}/actions/publish",
                {},
            ),
        ],
    )
    def test_disabled_blocks_before_uow(
        self, postgres18, method, path_builder, body
    ) -> None:
        config = _config(postgres18["runtime_url"])
        factory = _CountingUowFactory()
        app, tenant, _ = _compose(config, gate=_DisabledGate(), uow_factory=factory)
        client = TestClient(app, raise_server_exceptions=False)
        cid, vid = uuid4(), uuid4()
        path = path_builder(cid, vid)
        headers = _headers(tenant)
        if path != "/api/v1/contents":
            headers["If-Match"] = encode_revision_etag(0)
        response = client.request(method, path, json=body, headers=headers)
        _assert_blocked(response)
        assert factory.calls == 0

    def test_boom_gate_blocks_before_uow(self, postgres18) -> None:
        config = _config(postgres18["runtime_url"])
        factory = _CountingUowFactory()
        app, tenant, _ = _compose(config, gate=_BoomGate(), uow_factory=factory)
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/api/v1/contents", json=CREATE_BODY, headers=_headers(tenant)
        )
        _assert_blocked(response)
        assert factory.calls == 0
        assert "SUPER_SECRET" not in response.text


class TestBlockedSideEffects:
    def test_blocked_create_has_no_side_effects(
        self, postgres18, runtime_engine, bootstrap_engine
    ) -> None:
        config = _config(postgres18["runtime_url"])
        app, tenant, _ = _compose(
            config,
            gate=_DisabledGate(),
            uow_factory=SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
        )
        client = TestClient(app, raise_server_exceptions=False)
        before = _counts(bootstrap_engine, tenant)
        response = client.post(
            "/api/v1/contents", json=CREATE_BODY, headers=_headers(tenant)
        )
        _assert_blocked(response)
        after = _counts(bootstrap_engine, tenant)
        assert after == before

    def test_blocked_lifecycle_mutations_have_no_side_effects(
        self, postgres18, runtime_engine, bootstrap_engine
    ) -> None:
        seed_tenant, seed_principal = uuid4(), uuid4()
        seed = TestClient(
            _ungated_app(runtime_engine, seed_tenant, seed_principal),
            raise_server_exceptions=False,
        )
        created = seed.post(
            "/api/v1/contents", json=CREATE_BODY, headers=_headers(seed_tenant)
        )
        assert created.status_code == 201
        content_id = created.json()["content_id"]
        etag = created.headers["ETag"]
        appended = seed.post(
            f"/api/v1/contents/{content_id}/versions",
            json={
                "schema_id": "test.generic",
                "schema_version": 1,
                "payload": {"marker": "v"},
            },
            headers=_headers(seed_tenant, **{"If-Match": etag}),
        )
        assert appended.status_code == 201
        version_id = appended.json()["version_id"]
        etag = appended.headers["ETag"]

        config = _config(postgres18["runtime_url"])
        app, _, _ = _compose(
            config,
            gate=_DisabledGate(),
            uow_factory=SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
            request_identity_authenticator=FixedPrincipalAuthenticator(seed_principal),
            security_resolver=StubSecurityContextResolver(
                seed_tenant, seed_principal
            ),
        )
        client = TestClient(app, raise_server_exceptions=False)
        before = _counts(bootstrap_engine, seed_tenant)

        for path, body in (
            (
                f"/api/v1/contents/{content_id}/versions",
                {
                    "schema_id": "test.generic",
                    "schema_version": 1,
                    "payload": {"marker": "v2"},
                },
            ),
            (
                f"/api/v1/contents/{content_id}/versions/{version_id}/actions/submit-for-review",
                {},
            ),
            (
                f"/api/v1/contents/{content_id}/versions/{version_id}/actions/approve",
                {},
            ),
            (
                f"/api/v1/contents/{content_id}/versions/{version_id}/actions/request-changes",
                {"comment": "x"},
            ),
            (
                f"/api/v1/contents/{content_id}/versions/{version_id}/actions/reject",
                {"comment": "x"},
            ),
            (f"/api/v1/contents/{content_id}/actions/publish", {}),
        ):
            headers = _headers(seed_tenant, **{"If-Match": etag})
            response = client.post(path, json=body, headers=headers)
            _assert_blocked(response)

        after = _counts(bootstrap_engine, seed_tenant)
        assert after == before
        got = client.get(
            f"/api/v1/contents/{content_id}", headers=_headers(seed_tenant)
        )
        assert got.status_code == 200
        assert got.headers["ETag"] == etag


class TestReadsAndHealthIndependence:
    def test_reads_and_health_while_disabled(
        self, postgres18, runtime_engine
    ) -> None:
        seed_tenant, seed_principal = uuid4(), uuid4()
        seed = TestClient(
            _ungated_app(runtime_engine, seed_tenant, seed_principal),
            raise_server_exceptions=False,
        )
        created = seed.post(
            "/api/v1/contents", json=CREATE_BODY, headers=_headers(seed_tenant)
        )
        content_id = created.json()["content_id"]

        config = _config(postgres18["runtime_url"])
        app, _, _ = _compose(
            config,
            gate=_DisabledGate(),
            uow_factory=SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
            request_identity_authenticator=FixedPrincipalAuthenticator(seed_principal),
            security_resolver=StubSecurityContextResolver(
                seed_tenant, seed_principal
            ),
        )
        client = TestClient(app, raise_server_exceptions=False)
        assert client.get("/livez").status_code == 200
        ready = client.get("/readyz")
        assert ready.status_code == 200
        assert ready.json()["status"] == "ready"
        assert (
            client.get(
                f"/api/v1/contents/{content_id}", headers=_headers(seed_tenant)
            ).status_code
            == 200
        )
        assert (
            client.get("/api/v1/contents", headers=_headers(seed_tenant)).status_code
            == 200
        )
        assert (
            client.get(
                "/api/v1/teacher-os/review-queue", headers=_headers(seed_tenant)
            ).status_code
            == 200
        )

    def test_health_and_reads_with_broken_gate(
        self, postgres18, runtime_engine
    ) -> None:
        config = _config(postgres18["runtime_url"])
        app, tenant, _ = _compose(
            config,
            gate=_BoomGate(),
            uow_factory=SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
        )
        client = TestClient(app, raise_server_exceptions=False)
        assert client.get("/livez").status_code == 200
        assert client.get("/readyz").status_code == 200
        assert (
            client.get("/api/v1/contents", headers=_headers(tenant)).status_code
            == 200
        )


class TestEnabledRegression:
    def test_enabled_create_and_authority_still_enforced(
        self, postgres18, runtime_engine
    ) -> None:
        config = _config(postgres18["runtime_url"])
        gate = load_api_mutation_activation_gate(
            {
                ENV_API_MUTATION_ACTIVATION: "ENABLED",
                ENV_API_MUTATION_AUTHORIZED_GIT_SHA: VALID_GIT_SHA,
                ENV_API_MUTATION_AUTHORIZED_ARTIFACT_DIGEST: VALID_DIGEST,
            },
            config.release_identity,
        )
        tenant, principal = uuid4(), uuid4()
        app, _, _ = _compose(
            config,
            gate=gate,
            uow_factory=SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
            request_identity_authenticator=FixedPrincipalAuthenticator(principal),
            security_resolver=StubSecurityContextResolver(tenant, principal),
        )
        client = TestClient(app, raise_server_exceptions=False)
        created = client.post(
            "/api/v1/contents", json=CREATE_BODY, headers=_headers(tenant)
        )
        assert created.status_code == 201
        content_id = created.json()["content_id"]

        other = uuid4()
        denied = client.post(
            "/api/v1/contents",
            json=CREATE_BODY,
            headers=_headers(other),
        )
        assert denied.status_code in {401, 403}
        assert denied.json().get("code") != "mutations_not_activated"

        app2, _, _ = _compose(
            config,
            gate=gate,
            uow_factory=SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
            request_identity_authenticator=FixedPrincipalAuthenticator(principal),
            security_resolver=StubSecurityContextResolver(tenant, principal),
            review_authorization=AllowReviewAuthorization(allow_submit=False),
        )
        client2 = TestClient(app2, raise_server_exceptions=False)
        etag = created.headers["ETag"]
        appended = client2.post(
            f"/api/v1/contents/{content_id}/versions",
            json={
                "schema_id": "test.generic",
                "schema_version": 1,
                "payload": {"marker": 1},
            },
            headers=_headers(tenant, **{"If-Match": etag}),
        )
        assert appended.status_code == 201
        version_id = appended.json()["version_id"]
        forbidden = client2.post(
            f"/api/v1/contents/{content_id}/versions/{version_id}/actions/submit-for-review",
            json={},
            headers=_headers(tenant, **{"If-Match": appended.headers["ETag"]}),
        )
        assert forbidden.status_code == 403
        assert forbidden.json()["code"] != "mutations_not_activated"

    def test_client_headers_cannot_enable_mutations(self, postgres18) -> None:
        config = _config(postgres18["runtime_url"])
        factory = _CountingUowFactory()
        app, tenant, _ = _compose(
            config, gate=_DisabledGate(), uow_factory=factory
        )
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/api/v1/contents",
            json=CREATE_BODY,
            headers=_headers(
                tenant,
                **{
                    "X-Mutations-Enabled": "true",
                    "X-Activation": "ENABLED",
                },
            ),
        )
        _assert_blocked(response)
        assert factory.calls == 0
