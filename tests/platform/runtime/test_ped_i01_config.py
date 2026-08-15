"""PED-I01 API runtime configuration and composition tests."""

from __future__ import annotations

import base64
import inspect
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.domain.schema import ContentSchemaRegistry
from aieos.platform.runtime import (
    ApiRuntimeConfig,
    ApiRuntimeDependencies,
    DeploymentEnvironment,
    RuntimeConfigurationError,
    WorkloadKind,
    compose_api_application,
    load_api_runtime_config,
)
from aieos.platform.runtime.config import (
    ENV_ARTIFACT_DIGEST,
    ENV_BUILD_ID,
    ENV_CURSOR_SIGNING_KEY_B64,
    ENV_DEPLOYMENT_ENVIRONMENT,
    ENV_GIT_SHA,
    ENV_IDEMPOTENCY_RETENTION_SECONDS,
    ENV_MIGRATOR_DATABASE_URL,
    ENV_MIGRATOR_ROLE,
    ENV_RELEASE_VERSION,
    ENV_RUNTIME_DATABASE_CONNECT_TIMEOUT_SECONDS,
    ENV_RUNTIME_DATABASE_ROLE,
    ENV_RUNTIME_DATABASE_URL,
    ENV_SCHEMA_OWNER_ROLE,
    ENV_SECURITY_SCHEMA_OWNER_ROLE,
)
from aieos.platform.runtime.readiness import ReadinessCode, ReadinessResult
from tests.fakes import (
    AllowAssetCurrentGovernance,
    AllowAssetReferenceValidation,
    AllowPublicationAuthorization,
    AllowPublicationGovernance,
    AllowReviewAuthorization,
    AllowReviewCommentPolicy,
    StubSecurityContextResolver,
    make_test_schema_registry,
)

pytestmark = pytest.mark.ped_i01

VALID_GIT_SHA = "a" * 40
VALID_DIGEST = "sha256:" + ("b" * 64)
SECRET_DB_PASSWORD = "SUPER_SECRET_DB_PASSWORD"
SECRET_CURSOR_KEY = b"SUPER_SECRET_CURSOR_KEY"
CURSOR_B64 = base64.b64encode(SECRET_CURSOR_KEY).decode("ascii")

REQUIRED_ENV_NAMES = (
    ENV_DEPLOYMENT_ENVIRONMENT,
    ENV_RELEASE_VERSION,
    ENV_GIT_SHA,
    ENV_BUILD_ID,
    ENV_ARTIFACT_DIGEST,
    ENV_RUNTIME_DATABASE_URL,
    ENV_RUNTIME_DATABASE_ROLE,
    ENV_SCHEMA_OWNER_ROLE,
    ENV_SECURITY_SCHEMA_OWNER_ROLE,
    ENV_MIGRATOR_ROLE,
    ENV_CURSOR_SIGNING_KEY_B64,
    ENV_IDEMPOTENCY_RETENTION_SECONDS,
    ENV_RUNTIME_DATABASE_CONNECT_TIMEOUT_SECONDS,
)


class _ReadyProbe:
    def check(self) -> ReadinessResult:
        return ReadinessResult(True, ReadinessCode.READY)


def _valid_environ(
    *,
    environment: str = "STAGING",
    runtime_role: str = "aieos_runtime",
    content_owner: str = "aieos_content_owner",
    security_owner: str = "aieos_security_owner",
    migrator: str = "aieos_migrator",
    db_password: str = SECRET_DB_PASSWORD,
    cursor_b64: str = CURSOR_B64,
    retention: str = "86400",
    connect_timeout: str = "5",
    git_sha: str = VALID_GIT_SHA,
    artifact_digest: str = VALID_DIGEST,
) -> dict[str, str]:
    return {
        ENV_DEPLOYMENT_ENVIRONMENT: environment,
        ENV_RELEASE_VERSION: "0.1.0",
        ENV_GIT_SHA: git_sha,
        ENV_BUILD_ID: "build-1",
        ENV_ARTIFACT_DIGEST: artifact_digest,
        ENV_RUNTIME_DATABASE_URL: (
            f"postgresql+psycopg://{runtime_role}:{db_password}"
            f"@127.0.0.1:5432/aieos"
        ),
        ENV_RUNTIME_DATABASE_ROLE: runtime_role,
        ENV_SCHEMA_OWNER_ROLE: content_owner,
        ENV_SECURITY_SCHEMA_OWNER_ROLE: security_owner,
        ENV_MIGRATOR_ROLE: migrator,
        ENV_CURSOR_SIGNING_KEY_B64: cursor_b64,
        ENV_IDEMPOTENCY_RETENTION_SECONDS: retention,
        ENV_RUNTIME_DATABASE_CONNECT_TIMEOUT_SECONDS: connect_timeout,
    }


def _assert_no_secret_leak(blob: str) -> None:
    assert SECRET_DB_PASSWORD not in blob
    assert SECRET_CURSOR_KEY.decode("ascii") not in blob
    assert "SUPER_SECRET" not in blob


class _UnusedUowFactory:
    def __call__(self, execution_tenant_id: Any) -> Any:
        raise AssertionError("composition must not open a DB unit of work")


class TestValidConfigLoad:
    @pytest.mark.parametrize("environment", ["STAGING", "PRODUCTION"])
    def test_complete_valid_config_loads(self, environment: str) -> None:
        config = load_api_runtime_config(_valid_environ(environment=environment))
        assert config.environment is DeploymentEnvironment(environment)
        assert config.release_identity.git_sha == VALID_GIT_SHA
        assert config.release_identity.artifact_digest == VALID_DIGEST
        assert config.runtime_database_role == "aieos_runtime"
        assert config.idempotency_retention == timedelta(seconds=86400)
        assert config.runtime_database_connect_timeout_seconds == 5
        assert config.cursor_signing_key == SECRET_CURSOR_KEY
        _assert_no_secret_leak(repr(config))
        _assert_no_secret_leak(str(config))


class TestRequiredFields:
    @pytest.mark.parametrize("missing", REQUIRED_ENV_NAMES)
    def test_missing_required_field_fails(self, missing: str) -> None:
        env = _valid_environ()
        del env[missing]
        with pytest.raises(RuntimeConfigurationError) as excinfo:
            load_api_runtime_config(env)
        message = str(excinfo.value)
        assert missing in message
        _assert_no_secret_leak(message)
        _assert_no_secret_leak(repr(excinfo.value))


class TestSecretRedaction:
    def test_config_and_error_redact_secrets(self) -> None:
        config = load_api_runtime_config(_valid_environ())
        _assert_no_secret_leak(repr(config))
        _assert_no_secret_leak(str(config))
        with pytest.raises(RuntimeConfigurationError) as excinfo:
            bad = _valid_environ()
            bad[ENV_RUNTIME_DATABASE_URL] = (
                f"postgresql+psycopg://wrong:{SECRET_DB_PASSWORD}@127.0.0.1:5432/aieos"
            )
            load_api_runtime_config(bad)
        _assert_no_secret_leak(str(excinfo.value))
        _assert_no_secret_leak(repr(excinfo.value))


class TestReleaseIdentity:
    @pytest.mark.parametrize(
        "git_sha",
        [
            "a" * 7,
            "A" * 40,
            "g" + ("a" * 39),
            "a" * 39,
            "a" * 41,
        ],
    )
    def test_rejects_invalid_git_sha(self, git_sha: str) -> None:
        with pytest.raises(RuntimeConfigurationError) as excinfo:
            load_api_runtime_config(_valid_environ(git_sha=git_sha))
        assert ENV_GIT_SHA in str(excinfo.value)

    @pytest.mark.parametrize(
        "digest",
        [
            "sha1:" + ("b" * 40),
            "sha256:" + ("b" * 63),
            "sha256:" + ("B" * 64),
            "sha256:" + ("b" * 65),
            "b" * 64,
        ],
    )
    def test_rejects_invalid_artifact_digest(self, digest: str) -> None:
        with pytest.raises(RuntimeConfigurationError) as excinfo:
            load_api_runtime_config(_valid_environ(artifact_digest=digest))
        assert ENV_ARTIFACT_DIGEST in str(excinfo.value)


class TestRoleSeparation:
    @pytest.mark.parametrize(
        ("runtime", "content", "security", "migrator"),
        [
            ("same", "same", "sec", "mig"),
            ("same", "content", "same", "mig"),
            ("same", "content", "sec", "same"),
            ("runtime", "same", "same", "mig"),
            ("runtime", "same", "sec", "same"),
            ("runtime", "content", "same", "same"),
        ],
    )
    def test_role_collisions_fail(
        self, runtime: str, content: str, security: str, migrator: str
    ) -> None:
        with pytest.raises(RuntimeConfigurationError) as excinfo:
            load_api_runtime_config(
                _valid_environ(
                    runtime_role=runtime,
                    content_owner=content,
                    security_owner=security,
                    migrator=migrator,
                )
            )
        assert "role separation" in str(excinfo.value).lower()


class TestMigratorCredentialLeak:
    def test_aieos_database_url_rejected(self) -> None:
        env = _valid_environ()
        env[ENV_MIGRATOR_DATABASE_URL] = (
            f"postgresql+psycopg://aieos_migrator:{SECRET_DB_PASSWORD}@127.0.0.1:5432/aieos"
        )
        with pytest.raises(RuntimeConfigurationError) as excinfo:
            load_api_runtime_config(env)
        assert ENV_MIGRATOR_DATABASE_URL in str(excinfo.value)
        _assert_no_secret_leak(str(excinfo.value))


class TestDsnRoleCoherence:
    def test_url_username_must_equal_runtime_role(self) -> None:
        env = _valid_environ(runtime_role="aieos_runtime_b")
        env[ENV_RUNTIME_DATABASE_URL] = (
            f"postgresql+psycopg://aieos_runtime_a:{SECRET_DB_PASSWORD}"
            f"@127.0.0.1:5432/aieos"
        )
        with pytest.raises(RuntimeConfigurationError) as excinfo:
            load_api_runtime_config(env)
        assert ENV_RUNTIME_DATABASE_URL in str(excinfo.value)
        assert ENV_RUNTIME_DATABASE_ROLE in str(excinfo.value)
        _assert_no_secret_leak(str(excinfo.value))

    def test_database_name_required(self) -> None:
        env = _valid_environ()
        env[ENV_RUNTIME_DATABASE_URL] = (
            f"postgresql+psycopg://aieos_runtime:{SECRET_DB_PASSWORD}@127.0.0.1"
        )
        with pytest.raises(RuntimeConfigurationError) as excinfo:
            load_api_runtime_config(env)
        assert ENV_RUNTIME_DATABASE_URL in str(excinfo.value)
        assert "database name" in str(excinfo.value).lower()
        _assert_no_secret_leak(str(excinfo.value))


class TestPsycopg3DriverExact:
    def test_postgresql_psycopg_accepted(self) -> None:
        config = load_api_runtime_config(_valid_environ())
        assert "postgresql+psycopg://" in config.runtime_database_url

    @pytest.mark.parametrize(
        "url",
        [
            f"postgresql://aieos_runtime:{SECRET_DB_PASSWORD}@127.0.0.1:5432/aieos",
            f"postgresql+psycopg2://aieos_runtime:{SECRET_DB_PASSWORD}@127.0.0.1:5432/aieos",
            f"postgresql+pg8000://aieos_runtime:{SECRET_DB_PASSWORD}@127.0.0.1:5432/aieos",
            f"postgresql+asyncpg://aieos_runtime:{SECRET_DB_PASSWORD}@127.0.0.1:5432/aieos",
            f"sqlite:///{SECRET_DB_PASSWORD}.db",
        ],
    )
    def test_non_psycopg3_drivers_rejected(self, url: str) -> None:
        env = _valid_environ()
        env[ENV_RUNTIME_DATABASE_URL] = url
        with pytest.raises(RuntimeConfigurationError) as excinfo:
            load_api_runtime_config(env)
        message = str(excinfo.value)
        assert ENV_RUNTIME_DATABASE_URL in message
        assert "Psycopg" in message or "psycopg" in message.lower()
        _assert_no_secret_leak(message)
        _assert_no_secret_leak(repr(excinfo.value))
        assert url not in message


class TestCursorKey:
    def test_rejects_empty_and_malformed(self) -> None:
        for cursor_b64 in ("", "%%%not-base64%%%", base64.b64encode(b"").decode("ascii")):
            with pytest.raises(RuntimeConfigurationError) as excinfo:
                load_api_runtime_config(_valid_environ(cursor_b64=cursor_b64))
            assert ENV_CURSOR_SIGNING_KEY_B64 in str(excinfo.value)
            _assert_no_secret_leak(str(excinfo.value))

    def test_accepts_non_empty_valid_base64(self) -> None:
        config = load_api_runtime_config(_valid_environ())
        assert config.cursor_signing_key == SECRET_CURSOR_KEY


class TestIdempotencyRetention:
    @pytest.mark.parametrize("raw", ["0", "-1", "1.5", "abc"])
    def test_rejects_non_positive_integer(self, raw: str) -> None:
        with pytest.raises(RuntimeConfigurationError) as excinfo:
            load_api_runtime_config(_valid_environ(retention=raw))
        assert ENV_IDEMPOTENCY_RETENTION_SECONDS in str(excinfo.value)

    def test_accepts_positive_integer(self) -> None:
        config = load_api_runtime_config(_valid_environ(retention="3600"))
        assert config.idempotency_retention == timedelta(seconds=3600)


class TestEnvironmentFailClosed:
    @pytest.mark.parametrize("value", ["dev", "test", "local", "DEV", "production", ""])
    def test_unknown_or_non_prod_environment_rejected(self, value: str) -> None:
        env = _valid_environ()
        if value == "":
            del env[ENV_DEPLOYMENT_ENVIRONMENT]
        else:
            env[ENV_DEPLOYMENT_ENVIRONMENT] = value
        with pytest.raises(RuntimeConfigurationError):
            load_api_runtime_config(env)


class TestComposition:
    def _dependencies(self) -> ApiRuntimeDependencies:
        return ApiRuntimeDependencies(
            uow_factory=_UnusedUowFactory(),
            security_resolver=StubSecurityContextResolver(uuid4(), uuid4()),
            content_types=StaticContentTypeCatalog({"test.generic"}),
            schema_registry=make_test_schema_registry(),
            review_authorization=AllowReviewAuthorization(),
            review_comment_policy=AllowReviewCommentPolicy(),
            publication_authorization=AllowPublicationAuthorization(),
            publication_governance=AllowPublicationGovernance(),
            asset_reference_validation=AllowAssetReferenceValidation(),
            asset_current_governance=AllowAssetCurrentGovernance(),
            readiness_probe=_ReadyProbe(),
        )

    def test_composition_requires_explicit_dependencies(self) -> None:
        sig = inspect.signature(ApiRuntimeDependencies)
        required = [
            name
            for name, param in sig.parameters.items()
            if param.default is inspect.Parameter.empty
        ]
        assert "readiness_probe" in required
        assert "security_resolver" in required
        assert "review_authorization" in required
        assert "publication_authorization" in required
        assert "asset_reference_validation" in required
        with pytest.raises(TypeError):
            ApiRuntimeDependencies()  # type: ignore[call-arg]

    def test_compose_app_stores_release_identity_without_engine(self) -> None:
        config = load_api_runtime_config(_valid_environ(environment="PRODUCTION"))
        app = compose_api_application(config, self._dependencies())
        assert app.state.release_identity.git_sha == VALID_GIT_SHA
        assert app.state.release_identity.artifact_digest == VALID_DIGEST
        assert app.state.deployment_environment is DeploymentEnvironment.PRODUCTION
        assert hasattr(app.state, "create_content_service")
        assert hasattr(app.state, "cursor_codec")
        openapi = app.openapi()
        paths = set(openapi.get("paths", {}))
        assert "/api/v1/contents" in paths
        assert "/livez" not in paths
        assert "/readyz" not in paths
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/openapi.json")
        assert response.status_code == 200
        _assert_no_secret_leak(repr(config))

    def test_workload_kind_includes_api_not_migrator(self) -> None:
        assert WorkloadKind.API.value == "API"
        assert "MIGRATOR" not in {k.name for k in WorkloadKind}
