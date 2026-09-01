"""Shared fixtures for TOS-DEV06-I03 TeachingAssignment application tests."""

from __future__ import annotations

import json
import uuid
from typing import Any
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.development.schemas import (
    build_development_schema_registry,
    development_content_type_names,
)
from aieos.development.school_context import DevelopmentSchoolContextClassReader
from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.domain.version import ContentPayload, canonical_payload_json
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.domains.education.schema import (
    ANSWER_KEY_CONTENT_TYPE,
    HOMEWORK_CONTENT_TYPE,
    LESSON_PLAN_CONTENT_TYPE,
    QUIZ_CONTENT_TYPE,
    TEACHER_NOTES_CONTENT_TYPE,
    WORKSHEET_CONTENT_TYPE,
)
from aieos.domains.teaching.application.assignment_create import (
    CreateTeachingAssignmentService,
)
from aieos.domains.teaching.application.audit import api_mutation_audit_provenance
from aieos.domains.teaching.application.models import CreateTeachingAssignmentCommand
from aieos.domains.teaching.application.school_context import (
    SchoolContextClassAuthorityService,
)
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

FIXED_NOW = datetime(2026, 8, 31, 14, 0, tzinfo=UTC)
IDEMPOTENCY_RETENTION = timedelta(hours=24)
CURSOR_KEY = b"tos-dev06-i03-test-cursor-key"
CREATE_PATH = "/api/v1/teaching/assignments"

_SCHEMA_BY_TYPE: dict[str, tuple[str, int]] = {
    WORKSHEET_CONTENT_TYPE: ("education.worksheet", 1),
    QUIZ_CONTENT_TYPE: ("education.quiz", 1),
    HOMEWORK_CONTENT_TYPE: ("education.homework", 1),
    LESSON_PLAN_CONTENT_TYPE: ("education.lesson_plan", 1),
    ANSWER_KEY_CONTENT_TYPE: ("education.answer_key", 1),
    TEACHER_NOTES_CONTENT_TYPE: ("education.teacher_notes", 1),
    "unknown.kind": ("education.unknown", 1),
}


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


def build_assignment_client(
    runtime_engine: Engine,
    tenant_id: UUID,
    principal_id: UUID,
    *,
    school_context_reader: object | None = None,
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
    return TestClient(app, raise_server_exceptions=False)


def event_context(principal_id: UUID) -> MutationEventContext:
    return MutationEventContext(
        correlation_id=uuid.uuid7(),
        causation_id=uuid.uuid7(),
        actor_principal_id=principal_id,
        effective_actor_id=principal_id,
    )


def create_service(
    runtime_engine: Engine,
    *,
    tenant_id: UUID,
    principal_id: UUID,
    class_authority: SchoolContextClassAuthorityService | None = None,
) -> CreateTeachingAssignmentService:
    authority = class_authority or SchoolContextClassAuthorityService(
        DevelopmentSchoolContextClassReader(
            tenant_id=tenant_id,
            teacher_principal_id=principal_id,
        )
    )
    return CreateTeachingAssignmentService(
        SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine),
        authority,
        idempotency_retention=IDEMPOTENCY_RETENTION,
    )


def create_assignment(
    runtime_engine: Engine,
    *,
    tenant_id: UUID,
    principal_id: UUID,
    content_id: UUID,
    content_version_id: UUID,
    idempotency_key: str,
    class_ref: str = "class-5a",
    class_authority: SchoolContextClassAuthorityService | None = None,
) -> object:
    service = create_service(
        runtime_engine,
        tenant_id=tenant_id,
        principal_id=principal_id,
        class_authority=class_authority,
    )
    return service.create(
        tenant_id,
        principal_id,
        CreateTeachingAssignmentCommand(
            content_id=content_id,
            content_version_id=content_version_id,
            class_ref=class_ref,
        ),
        idempotency_key=idempotency_key,
        event_context=event_context(principal_id),
        audit_provenance=api_mutation_audit_provenance(principal_id),
        now=FIXED_NOW,
    )


def seed_content_head(
    bootstrap_engine: Engine,
    *,
    tenant_id: UUID,
    content_type: str,
    published: bool = True,
    owner_id: UUID | None = None,
    marker: str | None = None,
) -> tuple[UUID, UUID]:
    """Insert APPROVED content head; optionally published. Returns (content_id, version_id)."""
    content_id = uuid.uuid7()
    version_id = uuid.uuid7()
    owner = owner_id or uuid.uuid7()
    schema_id, schema_version = _SCHEMA_BY_TYPE.get(
        content_type, ("education.unknown", 1)
    )
    payload = ContentPayload.from_mapping({"marker": marker or f"i03-{content_type}"})
    published_version_id = version_id if published else None
    with bootstrap_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO content.contents (
                    content_id, tenant_id, owner_principal_id, content_type, title,
                    description, locale, stewardship_state, current_version_id,
                    published_version_id, aggregate_revision, created_at,
                    created_by_principal_id, updated_at, archived_at
                ) VALUES (
                    :content_id, :tenant_id, :owner, :content_type, 'Title',
                    'Description', 'en-IN', 'APPROVED', :version_id,
                    :published_version_id, 1, :now, :owner, :now, NULL
                )
                """
            ),
            {
                "content_id": content_id,
                "tenant_id": tenant_id,
                "owner": owner,
                "content_type": content_type,
                "version_id": version_id,
                "published_version_id": published_version_id,
                "now": FIXED_NOW,
            },
        )
        conn.execute(
            text(
                """
                INSERT INTO content.content_versions (
                    version_id, tenant_id, content_id, version_number, parent_version_id,
                    schema_id, schema_version, payload, payload_sha256, origin,
                    provenance, created_at, created_by_principal_id
                ) VALUES (
                    :vid, :tid, :cid, 1, NULL,
                    :schema_id, :schema_version, CAST(:payload AS jsonb),
                    :sha, 'HUMAN',
                    CAST(:prov AS jsonb), :now, :actor
                )
                """
            ),
            {
                "vid": version_id,
                "tid": tenant_id,
                "cid": content_id,
                "schema_id": schema_id,
                "schema_version": schema_version,
                "payload": canonical_payload_json(payload.body),
                "sha": payload.sha256.value,
                "prov": json.dumps({}),
                "now": FIXED_NOW,
                "actor": owner,
            },
        )
        if published:
            decision_id = uuid.uuid7()
            conn.execute(
                text(
                    """
                    INSERT INTO content.review_decisions (
                        review_decision_id, tenant_id, content_id, version_id,
                        decision, comment, decided_at, reviewer_principal_id,
                        effective_actor_id, correlation_id
                    ) VALUES (
                        :did, :tid, :cid, :vid, 'APPROVE', NULL, :now, :actor, :actor, :corr
                    )
                    """
                ),
                {
                    "did": decision_id,
                    "tid": tenant_id,
                    "cid": content_id,
                    "vid": version_id,
                    "now": FIXED_NOW,
                    "actor": owner,
                    "corr": uuid.uuid7(),
                },
            )
            pub_id = uuid.uuid7()
            conn.execute(
                text(
                    """
                    INSERT INTO content.publications (
                        publication_id, tenant_id, content_id, version_id,
                        approval_decision_id, published_by_principal_id,
                        effective_actor_id, published_at, correlation_id
                    ) VALUES (
                        :pid, :tid, :cid, :vid, :did, :actor, :actor, :now, :corr
                    )
                    """
                ),
                {
                    "pid": pub_id,
                    "tid": tenant_id,
                    "cid": content_id,
                    "vid": version_id,
                    "did": decision_id,
                    "now": FIXED_NOW,
                    "actor": owner,
                    "corr": uuid.uuid7(),
                },
            )
    return content_id, version_id


def seed_published_worksheet(
    bootstrap_engine: Engine,
    *,
    tenant_id: UUID,
    owner_id: UUID | None = None,
) -> tuple[UUID, UUID]:
    """Insert APPROVED+published worksheet Content head and return (content_id, version_id)."""
    return seed_content_head(
        bootstrap_engine,
        tenant_id=tenant_id,
        content_type=WORKSHEET_CONTENT_TYPE,
        published=True,
        owner_id=owner_id,
    )


def seed_published_learner_content(
    bootstrap_engine: Engine,
    *,
    tenant_id: UUID,
    content_type: str,
    owner_id: UUID | None = None,
) -> tuple[UUID, UUID]:
    return seed_content_head(
        bootstrap_engine,
        tenant_id=tenant_id,
        content_type=content_type,
        published=True,
        owner_id=owner_id,
    )


def seed_teacher_only_content(
    bootstrap_engine: Engine,
    *,
    tenant_id: UUID,
    content_type: str,
    owner_id: UUID | None = None,
) -> tuple[UUID, UUID]:
    return seed_content_head(
        bootstrap_engine,
        tenant_id=tenant_id,
        content_type=content_type,
        published=True,
        owner_id=owner_id,
    )


def republish_content_to_new_version(
    bootstrap_engine: Engine,
    *,
    tenant_id: UUID,
    content_id: UUID,
    parent_version_id: UUID,
    owner_id: UUID,
    content_type: str = WORKSHEET_CONTENT_TYPE,
) -> UUID:
    """Insert version 2 and move published pointer off version 1 (race CASE A)."""
    version_v2 = uuid.uuid7()
    schema_id, schema_version = _SCHEMA_BY_TYPE.get(
        content_type, ("education.worksheet", 1)
    )
    payload = ContentPayload.from_mapping({"marker": "i03-assignment-v2"})
    with bootstrap_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO content.content_versions (
                    version_id, tenant_id, content_id, version_number, parent_version_id,
                    schema_id, schema_version, payload, payload_sha256, origin,
                    provenance, created_at, created_by_principal_id
                ) VALUES (
                    :vid, :tid, :cid, 2, :parent,
                    :schema_id, :schema_version, CAST(:payload AS jsonb),
                    :sha, 'HUMAN',
                    CAST(:prov AS jsonb), :now, :actor
                )
                """
            ),
            {
                "vid": version_v2,
                "tid": tenant_id,
                "cid": content_id,
                "parent": parent_version_id,
                "schema_id": schema_id,
                "schema_version": schema_version,
                "payload": canonical_payload_json(payload.body),
                "sha": payload.sha256.value,
                "prov": json.dumps({}),
                "now": FIXED_NOW,
                "actor": owner_id,
            },
        )
        conn.execute(
            text(
                """
                UPDATE content.contents
                SET published_version_id = :v2,
                    current_version_id = :v2,
                    aggregate_revision = aggregate_revision + 1,
                    updated_at = :now
                WHERE tenant_id = :tid AND content_id = :cid
                """
            ),
            {
                "v2": version_v2,
                "tid": tenant_id,
                "cid": content_id,
                "now": FIXED_NOW,
            },
        )
    return version_v2


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
    assignment_id: UUID | None = None,
) -> list[dict]:
    sql = """
        SELECT event_type, envelope, aggregate_revision
        FROM integration.outbox_messages
        WHERE tenant_id = :tid AND event_type = :etype
    """
    params: dict = {"tid": tenant_id, "etype": event_type}
    if assignment_id is not None:
        sql += " AND aggregate_id = :aid"
        params["aid"] = (
            assignment_id.value
            if hasattr(assignment_id, "value")
            else assignment_id
        )
    with bootstrap_engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return [dict(row) for row in rows]


def fetch_audit(
    bootstrap_engine: Engine,
    *,
    tenant_id: UUID,
    action: str,
    assignment_id: UUID | None = None,
) -> list[dict]:
    sql = """
        SELECT action, primary_resource_type, primary_resource_id,
               primary_resource_revision, resource_revision_before,
               resource_revision_after, related_resource_refs,
               executing_principal_id, effective_actor_id, execution_channel
        FROM security.audit_records
        WHERE tenant_id = :tid AND action = :action
    """
    params: dict = {"tid": tenant_id, "action": action}
    if assignment_id is not None:
        sql += " AND primary_resource_id = :aid"
        params["aid"] = (
            assignment_id.value
            if hasattr(assignment_id, "value")
            else assignment_id
        )
    with bootstrap_engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return [dict(row) for row in rows]


def is_lock_contention_error(exc: Any) -> bool:
    orig = getattr(exc, "orig", None)
    if orig is not None:
        pgcode = getattr(orig, "pgcode", None)
        if pgcode == "55P03":
            return True
    message = str(exc).lower()
    return (
        "lock" in message
        or "timeout" in message
        or "could not obtain lock" in message
    )
