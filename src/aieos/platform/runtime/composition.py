"""Explicit API application composition. No engine, server, or singleton."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import FastAPI

from aieos.domains.content.application.ports import (
    AssetCurrentGovernancePort,
    AssetReferenceValidationPort,
    ContentTypeCatalog,
    ContentUnitOfWorkFactory,
    PublicationAuthorizationPort,
    PublicationGovernancePort,
    ReviewAuthorizationPort,
    ReviewCommentPolicy,
)
from aieos.domains.content.domain.schema import ContentSchemaRegistry
from aieos.domains.teaching.application.ports import TeachingUnitOfWorkFactory
from aieos.platform.api.app import create_app
from aieos.platform.runtime.activation import (
    ApiMutationActivationGate,
    install_mutation_activation_interlock,
)
from aieos.platform.runtime.health import register_operational_health_routes
from aieos.platform.runtime.models import ApiRuntimeConfig, ReleaseIdentity
from aieos.platform.runtime.readiness import ApiReadinessProbe
from aieos.platform.security.authenticator import RequestIdentityAuthenticator
from aieos.platform.security.context import SecurityContextResolver


@dataclass(frozen=True, slots=True)
class ApiRuntimeDependencies:
    """All production-facing contracts required by API composition.

    No dependency is optional. No permissive production defaults.
    """

    uow_factory: ContentUnitOfWorkFactory
    teaching_uow_factory: TeachingUnitOfWorkFactory
    request_identity_authenticator: RequestIdentityAuthenticator
    security_resolver: SecurityContextResolver
    content_types: ContentTypeCatalog
    schema_registry: ContentSchemaRegistry
    review_authorization: ReviewAuthorizationPort
    review_comment_policy: ReviewCommentPolicy
    publication_authorization: PublicationAuthorizationPort
    publication_governance: PublicationGovernancePort
    asset_reference_validation: AssetReferenceValidationPort
    asset_current_governance: AssetCurrentGovernancePort
    readiness_probe: ApiReadinessProbe
    mutation_activation_gate: ApiMutationActivationGate


def compose_api_application(
    config: ApiRuntimeConfig,
    dependencies: ApiRuntimeDependencies,
) -> FastAPI:
    """Compose the FastAPI application from config + explicit dependencies.

    Does not create a SQLAlchemy Engine. Does not start an ASGI server.
    Installs the PED-I03 fail-closed mutation activation interlock.
    Registers operational /livez and /readyz (independent of activation).
    """
    app = create_app(
        uow_factory=dependencies.uow_factory,
        teaching_uow_factory=dependencies.teaching_uow_factory,
        request_identity_authenticator=dependencies.request_identity_authenticator,
        security_resolver=dependencies.security_resolver,
        content_types=dependencies.content_types,
        cursor_signing_key=config.cursor_signing_key,
        schema_registry=dependencies.schema_registry,
        idempotency_retention=config.idempotency_retention,
        review_authorization=dependencies.review_authorization,
        review_comment_policy=dependencies.review_comment_policy,
        publication_authorization=dependencies.publication_authorization,
        publication_governance=dependencies.publication_governance,
        asset_reference_validation=dependencies.asset_reference_validation,
        asset_current_governance=dependencies.asset_current_governance,
    )
    app.state.release_identity = ReleaseIdentity(
        application_version=config.release_identity.application_version,
        git_sha=config.release_identity.git_sha,
        build_id=config.release_identity.build_id,
        artifact_digest=config.release_identity.artifact_digest,
    )
    app.state.deployment_environment = config.environment
    app.state.readiness_probe = dependencies.readiness_probe
    register_operational_health_routes(app)
    install_mutation_activation_interlock(
        app, dependencies.mutation_activation_gate
    )
    return app
