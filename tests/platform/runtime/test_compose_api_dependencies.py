"""Production API dependency composition tests."""

from __future__ import annotations

import base64
import inspect
from unittest.mock import MagicMock, patch

import pytest

from aieos.domains.content.domain.errors import SchemaNotFoundError
from aieos.platform.runtime.compose_api_dependencies import (
    ENV_AISTOR_ACCESS_KEY_ID,
    ENV_AISTOR_BUCKET,
    ENV_AISTOR_ENDPOINT_URL,
    ENV_AISTOR_REGION,
    ENV_AISTOR_SECRET_ACCESS_KEY,
    compose_api_runtime_dependencies,
)
from aieos.platform.runtime.composition import ApiRuntimeDependencies
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
    load_api_runtime_config,
)
from aieos.platform.security.auth_config import (
    ENV_AUTH_AUDIENCE,
    ENV_AUTH_ISSUER,
    ENV_AUTH_JWKS_URI,
)
from aieos.platform.security.jwt_bearer import JwtBearerRequestIdentityAuthenticator

pytestmark = pytest.mark.ped_i01

VALID_GIT_SHA = "a" * 40
VALID_DIGEST = "sha256:" + ("b" * 64)
SECRET_AISTOR = "SUPER_SECRET_AISTOR_KEY"
SECRET_CURSOR = b"SUPER_SECRET_CURSOR"
CURSOR_B64 = base64.b64encode(SECRET_CURSOR).decode("ascii")


def _api_environ(**overrides: str) -> dict[str, str]:
    env = {
        ENV_DEPLOYMENT_ENVIRONMENT: "PRODUCTION",
        ENV_RELEASE_VERSION: "0.1.0",
        ENV_GIT_SHA: VALID_GIT_SHA,
        ENV_BUILD_ID: "build-compose",
        ENV_ARTIFACT_DIGEST: VALID_DIGEST,
        ENV_RUNTIME_DATABASE_URL: "postgresql+psycopg://aieos_runtime:pw@127.0.0.1:5432/aieos",
        ENV_RUNTIME_DATABASE_ROLE: "aieos_runtime",
        ENV_SCHEMA_OWNER_ROLE: "aieos_content_owner",
        ENV_SECURITY_SCHEMA_OWNER_ROLE: "aieos_security_owner",
        ENV_MIGRATOR_ROLE: "aieos_migrator",
        ENV_CURSOR_SIGNING_KEY_B64: CURSOR_B64,
        ENV_IDEMPOTENCY_RETENTION_SECONDS: "86400",
        ENV_RUNTIME_DATABASE_CONNECT_TIMEOUT_SECONDS: "5",
        ENV_AUTH_ISSUER: "https://issuer.example.test/",
        ENV_AUTH_AUDIENCE: "aieos-api",
        ENV_AUTH_JWKS_URI: "https://issuer.example.test/.well-known/jwks.json",
        ENV_AISTOR_ENDPOINT_URL: "https://aistor.example.test/",
        ENV_AISTOR_BUCKET: "primary",
        ENV_AISTOR_REGION: "us-east-1",
        ENV_AISTOR_ACCESS_KEY_ID: "access-key-id",
        ENV_AISTOR_SECRET_ACCESS_KEY: SECRET_AISTOR,
    }
    env.update(overrides)
    return env


def test_compose_returns_complete_api_runtime_dependencies() -> None:
    config = load_api_runtime_config(_api_environ())
    engine = MagicMock()
    environ = _api_environ()
    with patch(
        "aieos.platform.runtime.compose_api_dependencies.AiStorBlobStore.from_config"
    ) as from_config:
        from_config.return_value = MagicMock()
        dependencies = compose_api_runtime_dependencies(
            engine=engine,
            config=config,
            environ=environ,
        )
    assert isinstance(dependencies, ApiRuntimeDependencies)
    sig = inspect.signature(ApiRuntimeDependencies)
    for name in sig.parameters:
        assert getattr(dependencies, name) is not None
    assert not dependencies.content_types.contains("test.generic")
    with pytest.raises(SchemaNotFoundError):
        dependencies.schema_registry.get("test.generic", 1)


def test_production_compose_rejects_test_prefix_content_types() -> None:
    config = load_api_runtime_config(_api_environ())
    engine = MagicMock()
    with patch(
        "aieos.platform.runtime.compose_api_dependencies.AiStorBlobStore.from_config"
    ) as from_config:
        from_config.return_value = MagicMock()
        dependencies = compose_api_runtime_dependencies(
            engine=engine,
            config=config,
            environ=_api_environ(),
        )
    for content_type in ("test.generic", "test.other"):
        assert not dependencies.content_types.contains(content_type)


def test_compose_uses_jwt_bearer_authenticator_without_eager_jwks() -> None:
    config = load_api_runtime_config(_api_environ())
    engine = MagicMock()
    with patch(
        "aieos.platform.runtime.compose_api_dependencies.AiStorBlobStore.from_config"
    ) as from_config:
        from_config.return_value = MagicMock()
        dependencies = compose_api_runtime_dependencies(
            engine=engine,
            config=config,
            environ=_api_environ(),
        )
    assert isinstance(
        dependencies.request_identity_authenticator,
        JwtBearerRequestIdentityAuthenticator,
    )
    assert dependencies.request_identity_authenticator._jwk_client is not None


def test_compose_missing_aistor_config_fails_closed() -> None:
    config = load_api_runtime_config(_api_environ())
    engine = MagicMock()
    env = _api_environ()
    del env[ENV_AISTOR_SECRET_ACCESS_KEY]
    with pytest.raises(Exception) as excinfo:
        compose_api_runtime_dependencies(engine=engine, config=config, environ=env)
    message = str(excinfo.value)
    assert ENV_AISTOR_SECRET_ACCESS_KEY in message
    assert SECRET_AISTOR not in message


def test_compose_repr_does_not_leak_secrets() -> None:
    config = load_api_runtime_config(_api_environ())
    text = repr(config)
    assert SECRET_CURSOR.decode("ascii") not in text
    assert "SUPER_SECRET" not in text
