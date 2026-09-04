"""Shared fixtures for TOS-DEV08-I02 ClassroomAssessment application tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from aieos.development.schemas import (
    build_development_schema_registry,
    development_content_type_names,
)
from aieos.development.school_context import DevelopmentSchoolContextClassReader
from aieos.domains.assessment.infrastructure.persistence.uow import (
    SqlAlchemyAssessmentUnitOfWorkFactory,
)
from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.domains.teaching.infrastructure.persistence.uow import (
    SqlAlchemyTeachingUnitOfWorkFactory,
)
from aieos.domains.teaching.application.ports import (
    RemediationAssessmentSourceFactory,
)
from aieos.platform.api.app import create_app
from aieos.platform.events.models import MutationEventContext
from aieos.platform.runtime.remediation_assessment_source import (
    SqlAlchemyRemediationAssessmentSource,
)
from tests.domains.teaching.helpers_dev06_i03 import (
    seed_published_learner_content,
    seed_teacher_only_content,
    seed_content_head,
    republish_content_to_new_version,
)
from tests.fakes import (
    AllowAssetCurrentGovernance,
    AllowAssetReferenceValidation,
    AllowClassroomAssessmentAuthorization,
    AllowPublicationAuthorization,
    AllowPublicationGovernance,
    AllowReviewAuthorization,
    AllowReviewCommentPolicy,
    FixedPrincipalAuthenticator,
    StubSecurityContextResolver,
)

FIXED_NOW = datetime(2026, 9, 4, 10, 0, tzinfo=UTC)
IDEMPOTENCY_RETENTION = timedelta(hours=24)
CURSOR_KEY = b"tos-dev08-i02-test-cursor-key"
RECORD_PATH = "/api/v1/assessment/classroom-assessments"


class _AllowTeachingWorkAuthorization:
    def authorize(self, *, tenant_id, principal_id, capability) -> None:
        return None


def headers(
    tenant_id: UUID,
    *,
    idempotency_key: str | None = None,
    if_match: str | None = None,
) -> dict[str, str]:
    out = {"X-AIEOS-Tenant-ID": str(tenant_id)}
    if idempotency_key is not None:
        out["Idempotency-Key"] = idempotency_key
    if if_match is not None:
        out["If-Match"] = if_match
    return out


def event_context(principal_id: UUID) -> MutationEventContext:
    return MutationEventContext(
        correlation_id=uuid.uuid7(),
        causation_id=uuid.uuid7(),
        actor_principal_id=principal_id,
        effective_actor_id=principal_id,
    )


def build_assessment_client(
    runtime_engine: Engine,
    tenant_id: UUID,
    principal_id: UUID,
    *,
    school_context_reader: object | None = None,
    with_school_context: bool = True,
    assessment_authorization: object | None = None,
    teaching_authorization: object | None = None,
    remediation_assessment_source_factory: RemediationAssessmentSourceFactory | None = None,
) -> TestClient:
    reader: object | None
    if not with_school_context:
        reader = None
    else:
        reader = school_context_reader or DevelopmentSchoolContextClassReader(
            tenant_id=tenant_id,
            teacher_principal_id=principal_id,
        )
    auth = assessment_authorization or AllowClassroomAssessmentAuthorization()
    app = create_app(
        uow_factory=SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
        teaching_uow_factory=SqlAlchemyTeachingUnitOfWorkFactory(
            runtime_engine,
            remediation_assessment_source_factory=(
                remediation_assessment_source_factory
                or SqlAlchemyRemediationAssessmentSource
            ),
        ),
        assessment_uow_factory=SqlAlchemyAssessmentUnitOfWorkFactory(runtime_engine),
        assessment_authorization=auth,  # type: ignore[arg-type]
        request_identity_authenticator=FixedPrincipalAuthenticator(principal_id),
        security_resolver=StubSecurityContextResolver(tenant_id, principal_id),
        content_types=StaticContentTypeCatalog(development_content_type_names()),
        cursor_signing_key=CURSOR_KEY,
        schema_registry=build_development_schema_registry(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
        review_authorization=AllowReviewAuthorization(),
        review_comment_policy=AllowReviewCommentPolicy(),
        publication_authorization=AllowPublicationAuthorization(),
        publication_governance=AllowPublicationGovernance(),
        asset_reference_validation=AllowAssetReferenceValidation(),
        asset_current_governance=AllowAssetCurrentGovernance(),
        school_context_class_reader=reader,  # type: ignore[arg-type]
        teaching_authorization=(
            teaching_authorization or _AllowTeachingWorkAuthorization()
        ),  # type: ignore[arg-type]
    )
    return TestClient(app, raise_server_exceptions=False)


class MutableSchoolContextClassReader:
    """Test double: current ClassRef membership can be revoked mid-test."""

    def __init__(
        self,
        *,
        tenant_id: UUID,
        teacher_principal_id: UUID,
        class_refs: tuple[str, ...] = ("class-5a", "class-5b"),
    ) -> None:
        from aieos.domains.teaching.application.school_context import (
            AssignableClassRef,
        )

        self._tenant_id = tenant_id
        self._teacher_principal_id = teacher_principal_id
        self._AssignableClassRef = AssignableClassRef
        self.class_refs = list(class_refs)
        self.raise_unavailable = False

    def list_assignable_classes(
        self,
        tenant_id: UUID,
        teacher_principal_id: UUID,
    ):
        from aieos.domains.teaching.application.errors import (
            SchoolContextUnavailable as TeachingSchoolContextUnavailable,
        )

        if self.raise_unavailable:
            raise TeachingSchoolContextUnavailable("school context unavailable")
        if (
            tenant_id != self._tenant_id
            or teacher_principal_id != self._teacher_principal_id
        ):
            return ()
        return tuple(
            self._AssignableClassRef(class_ref=ref, display_label=ref)
            for ref in self.class_refs
        )


def complete_execution_with_binding(
    runtime_engine: Engine,
    *,
    tenant_id: UUID,
    principal_id: UUID,
    content_id: UUID,
    version_id: UUID,
    content_type: str,
    class_ref: str = "class-5a",
    key_prefix: str = "exec",
):
    """Start+complete TeachingExecution bound to the given ContentVersion."""
    from aieos.domains.teaching.application.audit import api_mutation_audit_provenance
    from aieos.domains.teaching.application.models import (
        TeachingExecutionContentBindingInput,
    )
    from tests.domains.teaching.helpers_dev07_i02 import (
        FIXED_NOW,
        FixedTeachingWorkArtifacts,
        complete_service,
        event_context,
        seed_teaching_work,
        start_execution,
        work_artifact,
    )

    work_id = seed_teaching_work(
        runtime_engine, tenant_id=tenant_id, principal_id=principal_id
    )
    artifacts = FixedTeachingWorkArtifacts(
        items=(
            work_artifact(
                content_id=content_id,
                version_id=version_id,
                content_type=content_type,
                artifact_kind=content_type,
            ),
        )
    )
    started = start_execution(
        runtime_engine,
        tenant_id=tenant_id,
        principal_id=principal_id,
        work_id=work_id,
        class_ref=class_ref,
        idempotency_key=f"{key_prefix}-start",
        bindings=(
            TeachingExecutionContentBindingInput(
                content_id=content_id,
                content_version_id=version_id,
                artifact_kind=content_type,
            ),
        ),
        artifacts=artifacts,
    )
    completed = complete_service(
        runtime_engine, tenant_id=tenant_id, principal_id=principal_id
    ).complete(
        tenant_id,
        principal_id,
        execution_id=started.execution_id,
        expected_aggregate_revision=started.aggregate_revision,
        idempotency_key=f"{key_prefix}-complete",
        event_context=event_context(principal_id),
        audit_provenance=api_mutation_audit_provenance(principal_id),
        now=FIXED_NOW,
    )
    return completed, work_id


__all__ = [
    "FIXED_NOW",
    "RECORD_PATH",
    "MutableSchoolContextClassReader",
    "build_assessment_client",
    "complete_execution_with_binding",
    "event_context",
    "headers",
    "republish_content_to_new_version",
    "seed_content_head",
    "seed_published_learner_content",
    "seed_teacher_only_content",
]
