"""Production API runtime dependency composition.

Engine + ApiRuntimeConfig → explicit ApiRuntimeDependencies.
No global singleton. No import-time Engine or network I/O.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

from sqlalchemy.engine import Engine

from aieos.domains.asset.domain.resource_type import ASSET_RESOURCE_TYPES_V1
from aieos.domains.asset.infrastructure.blobstore import AiStorBlobStore, AiStorBlobStoreConfig
from aieos.domains.asset.infrastructure.persistence.postgres_use_authority import (
    PostgresAssetUseAuthority,
)
from aieos.domains.content.application.asset_authority_adapters import (
    AssetAuthorityCurrentGovernanceAdapter,
    AssetAuthorityReferenceValidationAdapter,
)
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.domains.teaching.infrastructure.persistence.uow import (
    SqlAlchemyTeachingUnitOfWorkFactory,
)
from aieos.platform.governance import (
    BaselinePublicationGovernanceV1,
    DeterministicReviewCommentPolicyV1,
)
from aieos.platform.runtime.activation import (
    load_api_mutation_activation_gate_from_process_environment,
)
from aieos.platform.runtime.composition import ApiRuntimeDependencies
from aieos.platform.runtime.content_production import (
    build_production_content_schema_registry,
    build_production_content_type_catalog,
)
from aieos.platform.runtime.errors import RuntimeConfigurationError
from aieos.platform.runtime.models import ApiRuntimeConfig
from aieos.platform.runtime.readiness import SqlAlchemyApiReadinessProbe
from aieos.platform.resources.asset_use import AssetUseAuthority
from aieos.platform.security.auth_config import AuthRuntimeConfig
from aieos.platform.security.authority import CurrentAuthoritySecurityContextResolver
from aieos.platform.security.authorization import (
    AIEOS_CONTENT_CAPABILITIES,
    AuthorizationKernel,
    KernelCurrentTenantAccessAuthority,
    KernelPublicationAuthorization,
    KernelReviewAuthorization,
)
from aieos.platform.security.jwt_bearer import JwtBearerRequestIdentityAuthenticator

ENV_AISTOR_ENDPOINT_URL = "AIEOS_AISTOR_ENDPOINT_URL"
ENV_AISTOR_BUCKET = "AIEOS_AISTOR_BUCKET"
ENV_AISTOR_REGION = "AIEOS_AISTOR_REGION"
ENV_AISTOR_ACCESS_KEY_ID = "AIEOS_AISTOR_ACCESS_KEY_ID"
ENV_AISTOR_SECRET_ACCESS_KEY = "AIEOS_AISTOR_SECRET_ACCESS_KEY"
ENV_AISTOR_CONNECT_TIMEOUT_SECONDS = "AIEOS_AISTOR_CONNECT_TIMEOUT_SECONDS"
ENV_AISTOR_READ_TIMEOUT_SECONDS = "AIEOS_AISTOR_READ_TIMEOUT_SECONDS"
ENV_AISTOR_CA_BUNDLE_PATH = "AIEOS_AISTOR_CA_BUNDLE_PATH"

_REQUIRED_AISTOR_ENV = (
    ENV_AISTOR_ENDPOINT_URL,
    ENV_AISTOR_BUCKET,
    ENV_AISTOR_REGION,
    ENV_AISTOR_ACCESS_KEY_ID,
    ENV_AISTOR_SECRET_ACCESS_KEY,
)

_POSITIVE_NUMBER = re.compile(r"[1-9][0-9]*(?:\.[0-9]+)?")


def _require(environ: Mapping[str, str], name: str) -> str:
    raw = environ.get(name)
    if raw is None or raw.strip() == "":
        raise RuntimeConfigurationError(f"{name} is required and must be non-empty")
    return raw.strip()


def _parse_positive_float(name: str, raw: str) -> float:
    if not _POSITIVE_NUMBER.fullmatch(raw):
        raise RuntimeConfigurationError(f"{name} must be a positive number")
    value = float(raw)
    if value <= 0:
        raise RuntimeConfigurationError(f"{name} must be a positive number")
    return value


def _load_aistor_blob_store_config(environ: Mapping[str, str]) -> AiStorBlobStoreConfig:
    for name in _REQUIRED_AISTOR_ENV:
        _require(environ, name)
    connect_timeout = 5.0
    read_timeout = 60.0
    if ENV_AISTOR_CONNECT_TIMEOUT_SECONDS in environ:
        connect_timeout = _parse_positive_float(
            ENV_AISTOR_CONNECT_TIMEOUT_SECONDS,
            _require(environ, ENV_AISTOR_CONNECT_TIMEOUT_SECONDS),
        )
    if ENV_AISTOR_READ_TIMEOUT_SECONDS in environ:
        read_timeout = _parse_positive_float(
            ENV_AISTOR_READ_TIMEOUT_SECONDS,
            _require(environ, ENV_AISTOR_READ_TIMEOUT_SECONDS),
        )
    ca_bundle_path = None
    if ENV_AISTOR_CA_BUNDLE_PATH in environ:
        ca_bundle_path = _require(environ, ENV_AISTOR_CA_BUNDLE_PATH)
    try:
        return AiStorBlobStoreConfig(
            endpoint_url=_require(environ, ENV_AISTOR_ENDPOINT_URL),
            bucket=_require(environ, ENV_AISTOR_BUCKET),
            region=_require(environ, ENV_AISTOR_REGION),
            access_key_id=_require(environ, ENV_AISTOR_ACCESS_KEY_ID),
            secret_access_key=_require(environ, ENV_AISTOR_SECRET_ACCESS_KEY),
            connect_timeout_seconds=connect_timeout,
            read_timeout_seconds=read_timeout,
            ca_bundle_path=ca_bundle_path,
        )
    except ValueError as exc:
        raise RuntimeConfigurationError(str(exc)) from exc


def _build_asset_use_authority(
    engine: Engine,
    environ: Mapping[str, str],
) -> AssetUseAuthority:
    blob_store = AiStorBlobStore.from_config(_load_aistor_blob_store_config(environ))
    return PostgresAssetUseAuthority(engine, blob_store)


def compose_api_runtime_dependencies(
    *,
    engine: Engine,
    config: ApiRuntimeConfig,
    environ: Mapping[str, str] | None = None,
) -> ApiRuntimeDependencies:
    """Compose governed production dependencies for API startup."""
    env = os.environ if environ is None else environ
    auth_config = AuthRuntimeConfig(
        issuer=config.auth_issuer,
        audience=config.auth_audience,
        jwks_uri=config.auth_jwks_uri,
    )
    kernel = AuthorizationKernel(
        engine,
        known_capabilities=AIEOS_CONTENT_CAPABILITIES,
    )
    asset_authority = _build_asset_use_authority(engine, env)
    handled_types = tuple(sorted(ASSET_RESOURCE_TYPES_V1))
    return ApiRuntimeDependencies(
        uow_factory=SqlAlchemyContentUnitOfWorkFactory(engine),
        teaching_uow_factory=SqlAlchemyTeachingUnitOfWorkFactory(engine),
        request_identity_authenticator=JwtBearerRequestIdentityAuthenticator(
            auth_config
        ),
        security_resolver=CurrentAuthoritySecurityContextResolver(
            KernelCurrentTenantAccessAuthority(kernel)
        ),
        content_types=build_production_content_type_catalog(),
        schema_registry=build_production_content_schema_registry(),
        review_authorization=KernelReviewAuthorization(kernel),
        review_comment_policy=DeterministicReviewCommentPolicyV1(),
        publication_authorization=KernelPublicationAuthorization(kernel),
        publication_governance=BaselinePublicationGovernanceV1(),
        asset_reference_validation=AssetAuthorityReferenceValidationAdapter(
            asset_authority,
            handled_resource_types=handled_types,
        ),
        asset_current_governance=AssetAuthorityCurrentGovernanceAdapter(
            asset_authority,
            handled_resource_types=handled_types,
        ),
        readiness_probe=SqlAlchemyApiReadinessProbe(engine, config),
        mutation_activation_gate=load_api_mutation_activation_gate_from_process_environment(
            config.release_identity
        ),
    )
