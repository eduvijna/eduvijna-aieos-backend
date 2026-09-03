"""Shared fixtures for TOS-DEV07-I02 TeachingExecution application tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.development.schemas import (
    build_development_schema_registry,
    development_content_type_names,
)
from aieos.development.school_context import DevelopmentSchoolContextClassReader
from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.domains.teaching.application.artifacts import (
    ListTeachingWorkArtifactsService,
    WorkArtifactsResult,
)
from aieos.domains.teaching.application.audit import api_mutation_audit_provenance
from aieos.domains.teaching.application.execution_mutations import (
    CancelTeachingExecutionService,
    CompleteTeachingExecutionService,
)
from aieos.domains.teaching.application.execution_observations import (
    CorrectTeachingExecutionObservationService,
    CreateTeachingExecutionObservationService,
)
from aieos.domains.teaching.application.execution_start import (
    StartTeachingExecutionService,
)
from aieos.domains.teaching.application.models import (
    StartTeachingExecutionCommand,
    TeachingExecutionContentBindingInput,
    TeachingExecutionReadModel,
)
from aieos.domains.teaching.application.school_context import (
    SchoolContextClassAuthority,
    SchoolContextClassAuthorityService,
)
from aieos.domains.teaching.application.teach_composition import (
    GetTeacherOsTeachContextService,
)
from aieos.domains.teaching.domain.identities import WorkId
from aieos.domains.teaching.domain.work import TeachingWork
from aieos.domains.teaching.infrastructure.persistence.uow import (
    SqlAlchemyTeachingUnitOfWorkFactory,
)
from aieos.platform.api.app import create_app
from aieos.platform.events.models import MutationEventContext
from tests.fakes import (
    AllowAssetCurrentGovernance,
    AllowAssetReferenceValidation,
    AllowPublicationAuthorization,
    AllowPublicationGovernance,
    AllowReviewAuthorization,
    AllowReviewCommentPolicy,
    FixedPrincipalAuthenticator,
    StubSecurityContextResolver,
)

FIXED_NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
IDEMPOTENCY_RETENTION = timedelta(hours=24)
CURSOR_KEY = b"tos-dev07-i02-test-cursor-key"
START_PATH = "/api/v1/teaching/executions"
TEACH_CONTEXT_PATH = "/api/v1/teacher-os/teach/context"


class EmptyTeachingWorkArtifacts:
    """Stub artifacts projection for zero-binding / teach-context tests."""

    def list(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        work_id: WorkId,
    ) -> WorkArtifactsResult:
        return WorkArtifactsResult(work_id=work_id, items=())


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


def build_execution_client(
    runtime_engine: Engine,
    tenant_id: UUID,
    principal_id: UUID,
    *,
    school_context_reader: object | None = None,
    artifacts: ListTeachingWorkArtifactsService | EmptyTeachingWorkArtifacts | None = None,
    wire_teach_context: bool = False,
) -> TestClient:
    reader = school_context_reader or DevelopmentSchoolContextClassReader(
        tenant_id=tenant_id,
        teacher_principal_id=principal_id,
    )
    app = create_app(
        uow_factory=SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
        teaching_uow_factory=SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine),
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
    )
    stub = artifacts if artifacts is not None else (
        EmptyTeachingWorkArtifacts() if wire_teach_context else None
    )
    if stub is not None:
        factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
        authority = app.state.school_context_class_authority
        app.state.list_teaching_work_artifacts_service = stub
        app.state.start_teaching_execution_service = StartTeachingExecutionService(
            factory,
            authority,
            stub,  # type: ignore[arg-type]
            idempotency_retention=IDEMPOTENCY_RETENTION,
        )
        if wire_teach_context or artifacts is not None:
            app.state.teacher_os_teach_context_service = GetTeacherOsTeachContextService(
                factory,
                authority,
                stub,  # type: ignore[arg-type]
            )
    return TestClient(app, raise_server_exceptions=False)


def seed_teaching_work(
    runtime_engine: Engine,
    *,
    tenant_id: UUID,
    principal_id: UUID,
) -> WorkId:
    work = TeachingWork.create_from_intent(
        tenant_id=tenant_id,
        teacher_principal_id=principal_id,
        intent_type="prepare_tomorrow",
        goal_text="Teach fractions",
        target_date=FIXED_NOW.date(),
        locale="en-IN",
        created_at=FIXED_NOW,
    )
    factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
    with factory(tenant_id) as uow:
        uow.works.insert(work)
        uow.commit()
    return work.work_id


def start_service(
    runtime_engine: Engine,
    *,
    tenant_id: UUID,
    principal_id: UUID,
    class_authority: SchoolContextClassAuthority | None = None,
    artifacts: ListTeachingWorkArtifactsService | EmptyTeachingWorkArtifacts | None = None,
) -> StartTeachingExecutionService:
    authority = class_authority or SchoolContextClassAuthorityService(
        DevelopmentSchoolContextClassReader(
            tenant_id=tenant_id,
            teacher_principal_id=principal_id,
        )
    )
    return StartTeachingExecutionService(
        SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine),
        authority,
        artifacts,  # type: ignore[arg-type]
        idempotency_retention=IDEMPOTENCY_RETENTION,
    )


def complete_service(
    runtime_engine: Engine,
    *,
    tenant_id: UUID,
    principal_id: UUID,
    class_authority: SchoolContextClassAuthority | None = None,
) -> CompleteTeachingExecutionService:
    authority = class_authority or SchoolContextClassAuthorityService(
        DevelopmentSchoolContextClassReader(
            tenant_id=tenant_id,
            teacher_principal_id=principal_id,
        )
    )
    return CompleteTeachingExecutionService(
        SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine),
        authority,
        idempotency_retention=IDEMPOTENCY_RETENTION,
    )


def cancel_service(
    runtime_engine: Engine,
    *,
    tenant_id: UUID,
    principal_id: UUID,
    class_authority: SchoolContextClassAuthority | None = None,
) -> CancelTeachingExecutionService:
    authority = class_authority or SchoolContextClassAuthorityService(
        DevelopmentSchoolContextClassReader(
            tenant_id=tenant_id,
            teacher_principal_id=principal_id,
        )
    )
    return CancelTeachingExecutionService(
        SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine),
        authority,
        idempotency_retention=IDEMPOTENCY_RETENTION,
    )


def observation_create_service(
    runtime_engine: Engine,
    *,
    tenant_id: UUID,
    principal_id: UUID,
    class_authority: SchoolContextClassAuthority | None = None,
) -> CreateTeachingExecutionObservationService:
    authority = class_authority or SchoolContextClassAuthorityService(
        DevelopmentSchoolContextClassReader(
            tenant_id=tenant_id,
            teacher_principal_id=principal_id,
        )
    )
    return CreateTeachingExecutionObservationService(
        SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine),
        authority,
        idempotency_retention=IDEMPOTENCY_RETENTION,
    )


def observation_correct_service(
    runtime_engine: Engine,
    *,
    tenant_id: UUID,
    principal_id: UUID,
    class_authority: SchoolContextClassAuthority | None = None,
) -> CorrectTeachingExecutionObservationService:
    authority = class_authority or SchoolContextClassAuthorityService(
        DevelopmentSchoolContextClassReader(
            tenant_id=tenant_id,
            teacher_principal_id=principal_id,
        )
    )
    return CorrectTeachingExecutionObservationService(
        SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine),
        authority,
        idempotency_retention=IDEMPOTENCY_RETENTION,
    )


def start_execution(
    runtime_engine: Engine,
    *,
    tenant_id: UUID,
    principal_id: UUID,
    work_id: WorkId | UUID,
    idempotency_key: str,
    class_ref: str = "class-5a",
    bindings: tuple[TeachingExecutionContentBindingInput, ...] = (),
    class_authority: SchoolContextClassAuthority | None = None,
    artifacts: ListTeachingWorkArtifactsService | EmptyTeachingWorkArtifacts | None = None,
    now: datetime | None = None,
) -> TeachingExecutionReadModel:
    service = start_service(
        runtime_engine,
        tenant_id=tenant_id,
        principal_id=principal_id,
        class_authority=class_authority,
        artifacts=artifacts,
    )
    wid = work_id.value if isinstance(work_id, WorkId) else work_id
    return service.start(
        tenant_id,
        principal_id,
        StartTeachingExecutionCommand(
            work_id=wid,
            class_ref=class_ref,
            bindings=bindings,
        ),
        idempotency_key=idempotency_key,
        event_context=event_context(principal_id),
        audit_provenance=api_mutation_audit_provenance(principal_id),
        now=now or FIXED_NOW,
    )


def count_rows(
    bootstrap_engine: Engine,
    sql: str,
    *,
    tenant_id: UUID,
    extra: dict | None = None,
) -> int:
    params = {"tid": tenant_id, **(extra or {})}
    with bootstrap_engine.connect() as conn:
        return int(conn.execute(text(sql), params).scalar_one())


def fetch_outbox(
    bootstrap_engine: Engine,
    *,
    tenant_id: UUID,
    event_type: str,
    execution_id: UUID | None = None,
) -> list[dict]:
    sql = """
        SELECT event_type, envelope, aggregate_revision
        FROM integration.outbox_messages
        WHERE tenant_id = :tid AND event_type = :etype
    """
    params: dict[str, Any] = {"tid": tenant_id, "etype": event_type}
    if execution_id is not None:
        sql += " AND aggregate_id = :eid"
        params["eid"] = (
            execution_id.value
            if hasattr(execution_id, "value")
            else execution_id
        )
    with bootstrap_engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return [dict(row) for row in rows]


def fetch_audit(
    bootstrap_engine: Engine,
    *,
    tenant_id: UUID,
    action: str,
    execution_id: UUID | None = None,
) -> list[dict]:
    sql = """
        SELECT action, primary_resource_type, primary_resource_id,
               primary_resource_revision, resource_revision_before,
               resource_revision_after, related_resource_refs,
               executing_principal_id, effective_actor_id, execution_channel
        FROM security.audit_records
        WHERE tenant_id = :tid AND action = :action
    """
    params: dict[str, Any] = {"tid": tenant_id, "action": action}
    if execution_id is not None:
        sql += " AND primary_resource_id = :eid"
        params["eid"] = (
            execution_id.value
            if hasattr(execution_id, "value")
            else execution_id
        )
    with bootstrap_engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return [dict(row) for row in rows]


def count_executions(bootstrap_engine: Engine, *, tenant_id: UUID) -> int:
    return count_rows(
        bootstrap_engine,
        "SELECT count(*) FROM teaching.executions WHERE tenant_id = :tid",
        tenant_id=tenant_id,
    )
