"""PED-I02 API engine / readiness / health tests."""

from __future__ import annotations

import base64
from dataclasses import replace
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.platform.runtime import (
    EXPECTED_ALEMBIC_HEAD,
    ApiRuntimeDependencies,
    MutationActivationDecision,
    MutationActivationStatus,
    ReadinessCode,
    ReadinessResult,
    SqlAlchemyApiReadinessProbe,
    compose_api_application,
    create_api_runtime_engine,
    load_api_runtime_config,
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
    BOOTSTRAP_USER,
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
    FixedPrincipalAuthenticator,
    StubSecurityContextResolver,
    make_test_schema_registry,
)

pytestmark = pytest.mark.ped_i02

VALID_GIT_SHA = "c" * 40
VALID_DIGEST = "sha256:" + ("d" * 64)
SECRET_DB_PASSWORD = "SUPER_SECRET_DB_PASSWORD"
SECRET_CURSOR = b"SUPER_SECRET_CURSOR_KEY_PED_I02"
CURSOR_B64 = base64.b64encode(SECRET_CURSOR).decode("ascii")


class _CountingProbe:
    def __init__(self, result: ReadinessResult) -> None:
        self.calls = 0
        self._result = result

    def check(self) -> ReadinessResult:
        self.calls += 1
        return self._result


class _BoomIfCalledProbe:
    def __init__(self) -> None:
        self.calls = 0

    def check(self) -> ReadinessResult:
        self.calls += 1
        raise AssertionError("livez must not invoke readiness")


class _DisabledMutationGate:
    def check(self) -> MutationActivationDecision:
        return MutationActivationDecision(
            False, MutationActivationStatus.DISABLED
        )


def _config_for_runtime_url(runtime_url: str, *, timeout: str = "5"):
    return load_api_runtime_config(
        {
            ENV_DEPLOYMENT_ENVIRONMENT: "STAGING",
            ENV_RELEASE_VERSION: "0.1.0",
            ENV_GIT_SHA: VALID_GIT_SHA,
            ENV_BUILD_ID: "build-ped-i02",
            ENV_ARTIFACT_DIGEST: VALID_DIGEST,
            ENV_RUNTIME_DATABASE_URL: runtime_url,
            ENV_RUNTIME_DATABASE_ROLE: RUNTIME_USER,
            ENV_SCHEMA_OWNER_ROLE: SCHEMA_OWNER_ROLE,
            ENV_SECURITY_SCHEMA_OWNER_ROLE: SECURITY_SCHEMA_OWNER_ROLE,
            ENV_MIGRATOR_ROLE: "aieos_migrator",
            ENV_CURSOR_SIGNING_KEY_B64: CURSOR_B64,
            ENV_IDEMPOTENCY_RETENTION_SECONDS: "86400",
            ENV_RUNTIME_DATABASE_CONNECT_TIMEOUT_SECONDS: timeout,
            "AIEOS_AUTH_ISSUER": "https://issuer.example.test/",
            "AIEOS_AUTH_AUDIENCE": "aieos-api",
            "AIEOS_AUTH_JWKS_URI": "https://issuer.example.test/.well-known/jwks.json",
        }
    )


def _compose(config, probe, uow_factory) -> Any:
    return compose_api_application(
        config,
        ApiRuntimeDependencies(
            uow_factory=uow_factory,
            teaching_uow_factory=uow_factory,
            request_identity_authenticator=FixedPrincipalAuthenticator(uuid4()),
            security_resolver=StubSecurityContextResolver(uuid4(), uuid4()),
            content_types=StaticContentTypeCatalog({"test.generic"}),
            schema_registry=make_test_schema_registry(),
            review_authorization=AllowReviewAuthorization(),
            review_comment_policy=AllowReviewCommentPolicy(),
            publication_authorization=AllowPublicationAuthorization(),
            publication_governance=AllowPublicationGovernance(),
            asset_reference_validation=AllowAssetReferenceValidation(),
            asset_current_governance=AllowAssetCurrentGovernance(),
            readiness_probe=probe,
            mutation_activation_gate=_DisabledMutationGate(),
        ),
    )


def _assert_no_secret(blob: str) -> None:
    assert SECRET_DB_PASSWORD not in blob
    assert SECRET_CURSOR.decode("ascii") not in blob
    assert "postgresql+psycopg://" not in blob or "SUPER_SECRET" not in blob


class TestConnectTimeoutConfig:
    @pytest.mark.parametrize("raw", ["0", "-1", "1.5", "abc", ""])
    def test_connect_timeout_fail_closed(self, postgres18, raw: str) -> None:
        from aieos.platform.runtime import RuntimeConfigurationError

        env = {
            ENV_DEPLOYMENT_ENVIRONMENT: "PRODUCTION",
            ENV_RELEASE_VERSION: "0.1.0",
            ENV_GIT_SHA: VALID_GIT_SHA,
            ENV_BUILD_ID: "b",
            ENV_ARTIFACT_DIGEST: VALID_DIGEST,
            ENV_RUNTIME_DATABASE_URL: postgres18["runtime_url"],
            ENV_RUNTIME_DATABASE_ROLE: RUNTIME_USER,
            ENV_SCHEMA_OWNER_ROLE: SCHEMA_OWNER_ROLE,
            ENV_SECURITY_SCHEMA_OWNER_ROLE: SECURITY_SCHEMA_OWNER_ROLE,
            ENV_MIGRATOR_ROLE: postgres18["migrator_user"],
            ENV_CURSOR_SIGNING_KEY_B64: CURSOR_B64,
            ENV_IDEMPOTENCY_RETENTION_SECONDS: "86400",
        }
        if raw != "":
            env[ENV_RUNTIME_DATABASE_CONNECT_TIMEOUT_SECONDS] = raw
        with pytest.raises(RuntimeConfigurationError) as excinfo:
            load_api_runtime_config(env)
        assert ENV_RUNTIME_DATABASE_CONNECT_TIMEOUT_SECONDS in str(excinfo.value)
        _assert_no_secret(str(excinfo.value))


class TestEngineFactory:
    def test_engine_construction_does_not_connect(self, postgres18) -> None:
        config = _config_for_runtime_url(
            # Valid syntax pointing at a closed port — construction must still succeed.
            f"postgresql+psycopg://{RUNTIME_USER}:{SECRET_DB_PASSWORD}"
            f"@127.0.0.1:1/aieos"
        )
        engine = create_api_runtime_engine(config)
        assert engine.pool._pre_ping is True
        assert engine.hide_parameters is True
        engine.dispose()

    def test_shared_engine_with_uow_factory(self, postgres18, runtime_engine) -> None:
        config = _config_for_runtime_url(postgres18["runtime_url"])
        engine = create_api_runtime_engine(config)
        try:
            factory = SqlAlchemyContentUnitOfWorkFactory(engine)
            probe = SqlAlchemyApiReadinessProbe(engine, config)
            assert probe.check().ready is True
            tenant = uuid4()
            with factory(tenant) as uow:
                assert uow.contents is not None
        finally:
            engine.dispose()


class TestRealReadiness:
    def test_ready_against_postgres18(self, postgres18, runtime_engine) -> None:
        assert postgres18["server_version"].startswith("18.")
        config = _config_for_runtime_url(postgres18["runtime_url"])
        probe = SqlAlchemyApiReadinessProbe(runtime_engine, config)
        result = probe.check()
        assert result == ReadinessResult(True, ReadinessCode.READY)
        with runtime_engine.connect() as conn:
            row = conn.execute(
                text("SELECT current_user, session_user, current_database()")
            ).one()
            assert row == (RUNTIME_USER, RUNTIME_USER, "aieos")
            assert (
                conn.execute(
                    text(
                        "SELECT has_database_privilege("
                        "current_user, current_database(), 'CREATE')"
                    )
                ).scalar_one()
                is False
            )
            for schema in (
                "content",
                "api",
                "workflow",
                "integration",
                "teaching",
                "ai",
                "assessment",
                "security",
            ):
                assert (
                    conn.execute(
                        text(
                            "SELECT has_schema_privilege("
                            "current_user, :schema, 'CREATE')"
                        ),
                        {"schema": schema},
                    ).scalar_one()
                    is False
                )
            owners = {
                name: owner
                for name, owner in conn.execute(
                    text(
                        """
                        SELECT n.nspname, r.rolname
                        FROM pg_namespace n
                        JOIN pg_roles r ON r.oid = n.nspowner
                        WHERE n.nspname IN
                          ('content', 'api', 'workflow', 'integration',
                           'teaching', 'ai', 'assessment', 'security')
                        """
                    )
                )
            }
            for schema in (
                "content",
                "api",
                "workflow",
                "integration",
                "teaching",
                "ai",
                "assessment",
            ):
                assert owners[schema] == SCHEMA_OWNER_ROLE
            assert owners["security"] == SECURITY_SCHEMA_OWNER_ROLE
            assert (
                conn.execute(
                    text(
                        """
                        SELECT count(*) FROM pg_namespace n
                        JOIN pg_roles r ON r.oid = n.nspowner
                        WHERE n.nspname IN
                          ('content', 'api', 'workflow', 'integration',
                           'teaching', 'ai', 'assessment', 'security')
                          AND r.rolname = current_user
                        """
                    )
                ).scalar_one()
                == 0
            )

    def test_wrong_login_not_ready(self, postgres18, migration_runtime_engine) -> None:
        config = _config_for_runtime_url(postgres18["runtime_url"])
        probe = SqlAlchemyApiReadinessProbe(migration_runtime_engine, config)
        result = probe.check()
        assert result.ready is False
        assert result.code is ReadinessCode.DATABASE_IDENTITY_MISMATCH

    def test_session_user_set_role_defense(
        self, postgres18, bootstrap_engine
    ) -> None:
        config = _config_for_runtime_url(postgres18["runtime_url"])

        class _SetRoleEngine:
            def connect(self):
                conn = bootstrap_engine.connect()
                conn.execute(text(f"SET ROLE {RUNTIME_USER}"))
                return conn

        probe = SqlAlchemyApiReadinessProbe(_SetRoleEngine(), config)  # type: ignore[arg-type]
        result = probe.check()
        assert result.ready is False
        assert result.code is ReadinessCode.DATABASE_IDENTITY_MISMATCH

    def test_unsafe_superuser_role(self, postgres18, bootstrap_engine) -> None:
        config = _config_for_runtime_url(postgres18["runtime_url"])
        unsafe_config = replace(config, runtime_database_role=BOOTSTRAP_USER)
        probe = SqlAlchemyApiReadinessProbe(bootstrap_engine, unsafe_config)
        result = probe.check()
        assert result.ready is False
        assert result.code is ReadinessCode.DATABASE_ROLE_UNSAFE

    def test_owner_membership_rejected(
        self, postgres18, runtime_engine, bootstrap_engine
    ) -> None:
        config = _config_for_runtime_url(postgres18["runtime_url"])
        probe = SqlAlchemyApiReadinessProbe(runtime_engine, config)
        assert probe.check().ready is True
        with bootstrap_engine.connect() as conn:
            with conn.begin():
                conn.execute(
                    text(f"GRANT {SCHEMA_OWNER_ROLE} TO {RUNTIME_USER}")
                )
        try:
            result = probe.check()
            assert result.ready is False
            assert result.code is ReadinessCode.DATABASE_ROLE_MEMBERSHIP_UNSAFE
        finally:
            with bootstrap_engine.connect() as conn:
                with conn.begin():
                    conn.execute(
                        text(f"REVOKE {SCHEMA_OWNER_ROLE} FROM {RUNTIME_USER}")
                    )
        assert probe.check().ready is True

    def test_schema_owner_mismatch(self, postgres18, runtime_engine) -> None:
        config = _config_for_runtime_url(postgres18["runtime_url"])
        bad = replace(config, content_schema_owner_role="aieos_not_an_owner")
        probe = SqlAlchemyApiReadinessProbe(runtime_engine, bad)
        result = probe.check()
        assert result.ready is False
        assert result.code is ReadinessCode.DATABASE_SCHEMA_OWNER_MISMATCH

    def test_alembic_head_success_and_mismatch(
        self, postgres18, runtime_engine, bootstrap_engine
    ) -> None:
        config = _config_for_runtime_url(postgres18["runtime_url"])
        probe = SqlAlchemyApiReadinessProbe(runtime_engine, config)
        assert probe.check().code is ReadinessCode.READY
        assert EXPECTED_ALEMBIC_HEAD == "tosd080001"
        with bootstrap_engine.connect() as conn:
            with conn.begin():
                original = conn.execute(
                    text("SELECT version_num FROM public.alembic_version")
                ).scalar_one()
                conn.execute(
                    text(
                        "UPDATE public.alembic_version SET version_num = 'gcii130001'"
                    )
                )
        try:
            result = probe.check()
            assert result.ready is False
            assert result.code is ReadinessCode.DATABASE_SCHEMA_REVISION_MISMATCH
        finally:
            with bootstrap_engine.connect() as conn:
                with conn.begin():
                    conn.execute(
                        text(
                            "UPDATE public.alembic_version SET version_num = :v"
                        ),
                        {"v": original},
                    )
        assert probe.check().ready is True

    def test_alembic_select_privilege_missing(
        self, postgres18, runtime_engine, bootstrap_engine
    ) -> None:
        config = _config_for_runtime_url(postgres18["runtime_url"])
        probe = SqlAlchemyApiReadinessProbe(runtime_engine, config)
        with bootstrap_engine.connect() as conn:
            with conn.begin():
                conn.execute(
                    text(
                        f"REVOKE SELECT ON TABLE public.alembic_version "
                        f"FROM {RUNTIME_USER}"
                    )
                )
        try:
            result = probe.check()
            assert result.ready is False
            assert result.code is ReadinessCode.DATABASE_SCHEMA_REVISION_UNAVAILABLE
        finally:
            with bootstrap_engine.connect() as conn:
                with conn.begin():
                    conn.execute(
                        text(
                            f"GRANT SELECT ON TABLE public.alembic_version "
                            f"TO {RUNTIME_USER}"
                        )
                    )
        assert probe.check().ready is True

    def test_alembic_select_allowed_writes_denied(self, runtime_engine) -> None:
        with runtime_engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT count(*) FROM public.alembic_version")
                ).scalar_one()
                == 1
            )
            for sql in (
                "INSERT INTO public.alembic_version(version_num) VALUES ('x')",
                "UPDATE public.alembic_version SET version_num = 'x'",
                "DELETE FROM public.alembic_version",
                "TRUNCATE public.alembic_version",
            ):
                with pytest.raises(Exception):
                    with conn.begin_nested():
                        conn.execute(text(sql))

    def test_dispatchers_no_write_on_alembic(
        self, event_dispatcher_engine, workflow_dispatcher_engine
    ) -> None:
        for engine in (event_dispatcher_engine, workflow_dispatcher_engine):
            with engine.connect() as conn:
                with pytest.raises(Exception):
                    with conn.begin_nested():
                        conn.execute(
                            text(
                                "INSERT INTO public.alembic_version(version_num) "
                                "VALUES ('x')"
                            )
                        )
                with pytest.raises(Exception):
                    with conn.begin_nested():
                        conn.execute(
                            text(
                                "UPDATE public.alembic_version "
                                "SET version_num = 'x'"
                            )
                        )

    def test_database_unavailable(self, postgres18) -> None:
        config = _config_for_runtime_url(
            f"postgresql+psycopg://{RUNTIME_USER}:{SECRET_DB_PASSWORD}"
            f"@127.0.0.1:1/aieos",
            timeout="1",
        )
        engine = create_api_runtime_engine(config)
        try:
            probe = SqlAlchemyApiReadinessProbe(engine, config)
            result = probe.check()
            assert result.ready is False
            assert result.code is ReadinessCode.DATABASE_UNAVAILABLE
            assert result.code.value == "DATABASE_UNAVAILABLE"
            # Sanitized: no exception text on the result object.
            assert "password" not in repr(result).lower()
            assert "SUPER_SECRET" not in repr(result)
        finally:
            engine.dispose()


class TestHealthHttp:
    def test_livez_never_calls_readiness(self, postgres18) -> None:
        config = _config_for_runtime_url(postgres18["runtime_url"])
        probe = _BoomIfCalledProbe()

        class _Unused:
            def __call__(self, tenant_id):
                raise AssertionError("livez must not open UoW")

        app = _compose(config, probe, _Unused())
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/livez")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "alive"
        assert body["workload"] == "API"
        assert body["deployment_environment"] == "STAGING"
        assert body["release"]["application_version"] == "0.1.0"
        assert body["release"]["git_sha"] == VALID_GIT_SHA
        assert body["release"]["build_id"] == "build-ped-i02"
        assert body["release"]["artifact_digest"] == VALID_DIGEST
        assert probe.calls == 0
        _assert_no_secret(response.text)

    def test_ready_and_not_ready_http(self, postgres18, runtime_engine) -> None:
        config = _config_for_runtime_url(postgres18["runtime_url"])
        ready_probe = SqlAlchemyApiReadinessProbe(runtime_engine, config)
        app = _compose(
            config,
            ready_probe,
            SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
        )
        client = TestClient(app, raise_server_exceptions=False)
        live = client.get("/livez")
        ready = client.get("/readyz")
        assert live.status_code == 200
        assert ready.status_code == 200
        assert ready.json()["status"] == "ready"
        assert ready.json()["code"] == "READY"
        assert ready.json()["release"]["artifact_digest"] == VALID_DIGEST
        _assert_no_secret(ready.text)

        failed = _CountingProbe(
            ReadinessResult(False, ReadinessCode.DATABASE_UNAVAILABLE)
        )
        app2 = _compose(
            config, failed, SqlAlchemyContentUnitOfWorkFactory(runtime_engine)
        )
        client2 = TestClient(app2, raise_server_exceptions=False)
        assert client2.get("/livez").status_code == 200
        not_ready = client2.get("/readyz")
        assert not_ready.status_code == 503
        assert not_ready.json()["status"] == "not_ready"
        assert not_ready.json()["code"] == "DATABASE_UNAVAILABLE"
        assert "Traceback" not in not_ready.text
        assert failed.calls == 1
        _assert_no_secret(not_ready.text)

    def test_health_excluded_from_openapi(self, postgres18, runtime_engine) -> None:
        config = _config_for_runtime_url(postgres18["runtime_url"])
        app = _compose(
            config,
            SqlAlchemyApiReadinessProbe(runtime_engine, config),
            SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
        )
        schema = app.openapi()
        assert "/livez" not in schema.get("paths", {})
        assert "/readyz" not in schema.get("paths", {})
        assert "/api/v1/contents" in schema.get("paths", {})
