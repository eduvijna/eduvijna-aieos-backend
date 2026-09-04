"""Compose production-faithful API dependencies with local-only adapters.

LOCAL DEVELOPMENT ONLY — NEVER PRODUCTION.
"""

from __future__ import annotations

from sqlalchemy.engine import Engine

from aieos.development.auth_adapters import (
    DevelopmentAssetCurrentUsePermit,
    DevelopmentAssetReferencePermit,
    DevelopmentClassroomAssessmentPermit,
    DevelopmentPublicationAuthorizationPermit,
    DevelopmentPublicationGovernancePermit,
    DevelopmentReviewAuthorizationPermit,
    DevelopmentReviewCommentPermit,
)
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.domains.teaching.infrastructure.persistence.uow import (
    SqlAlchemyTeachingUnitOfWorkFactory,
)
from aieos.domains.assessment.infrastructure.persistence.uow import (
    SqlAlchemyAssessmentUnitOfWorkFactory,
)
from aieos.platform.runtime.activation import (
    load_api_mutation_activation_gate_from_process_environment,
)
from aieos.platform.runtime.composition import ApiRuntimeDependencies
from aieos.platform.runtime.content_production import (
    build_production_content_schema_registry,
    build_production_content_type_catalog,
)
from aieos.platform.runtime.models import ApiRuntimeConfig
from aieos.platform.runtime.readiness import SqlAlchemyApiReadinessProbe
from tools.dev.local_auth import (
    LocalDevelopmentBearerAuthenticator,
    LocalDevelopmentTenantSecurityResolver,
)
from tools.dev.local_config import (
    LOCAL_BEARER_TOKEN,
    LOCAL_DEV_PRINCIPAL_ID,
    LOCAL_DEV_TENANT_ID,
)


def compose_local_api_runtime_dependencies(
    *,
    engine: Engine,
    config: ApiRuntimeConfig,
) -> ApiRuntimeDependencies:
    """Explicit local composition — no JWT/JWKS fetch, no AIStor network I/O."""
    return ApiRuntimeDependencies(
        uow_factory=SqlAlchemyContentUnitOfWorkFactory(engine),
        teaching_uow_factory=SqlAlchemyTeachingUnitOfWorkFactory(engine),
        assessment_uow_factory=SqlAlchemyAssessmentUnitOfWorkFactory(engine),
        assessment_authorization=DevelopmentClassroomAssessmentPermit(),
        request_identity_authenticator=LocalDevelopmentBearerAuthenticator(
            principal_id=LOCAL_DEV_PRINCIPAL_ID,
            expected_bearer_token=LOCAL_BEARER_TOKEN,
        ),
        security_resolver=LocalDevelopmentTenantSecurityResolver(
            authorized_tenant_id=LOCAL_DEV_TENANT_ID,
            principal_id=LOCAL_DEV_PRINCIPAL_ID,
        ),
        content_types=build_production_content_type_catalog(),
        schema_registry=build_production_content_schema_registry(),
        review_authorization=DevelopmentReviewAuthorizationPermit(),
        review_comment_policy=DevelopmentReviewCommentPermit(),
        publication_authorization=DevelopmentPublicationAuthorizationPermit(),
        publication_governance=DevelopmentPublicationGovernancePermit(),
        asset_reference_validation=DevelopmentAssetReferencePermit(),
        asset_current_governance=DevelopmentAssetCurrentUsePermit(),
        readiness_probe=SqlAlchemyApiReadinessProbe(engine, config),
        mutation_activation_gate=load_api_mutation_activation_gate_from_process_environment(
            config.release_identity
        ),
    )
