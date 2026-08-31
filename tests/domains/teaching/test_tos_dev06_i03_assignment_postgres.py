"""TOS-DEV06-I03 — PostgreSQL assignment create and publication race tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.development.school_context import development_class_authority
from aieos.domains.content.domain.identities import ContentId, ContentVersionId
from aieos.domains.teaching.application.assignment_create import (
    CreateTeachingAssignmentService,
)
from aieos.domains.teaching.application.audit import api_mutation_audit_provenance
from aieos.domains.teaching.application.errors import ContentVersionMismatch
from aieos.domains.teaching.application.models import CreateTeachingAssignmentCommand
from aieos.domains.teaching.infrastructure.persistence.uow import (
    SqlAlchemyTeachingUnitOfWorkFactory,
)
from aieos.platform.events.models import MutationEventContext
from tests.domains.teaching.helpers_dev06_i03 import (
    FIXED_NOW,
    IDEMPOTENCY_RETENTION,
    republish_content_to_new_version,
    seed_published_worksheet,
)

pytestmark = pytest.mark.tos_dev06_i03


def _event_context(principal_id: uuid.UUID) -> MutationEventContext:
    return MutationEventContext(
        correlation_id=uuid.uuid7(),
        causation_id=uuid.uuid7(),
        actor_principal_id=principal_id,
        effective_actor_id=principal_id,
    )


class TestAssignmentCreatePostgres:
    def test_create_persists_assignment_outbox_and_audit(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
        service = CreateTeachingAssignmentService(
            factory,
            development_class_authority(
                tenant_id=tenant_id, teacher_principal_id=principal_id
            ),
            idempotency_retention=IDEMPOTENCY_RETENTION,
        )
        result = service.create(
            tenant_id,
            principal_id,
            CreateTeachingAssignmentCommand(
                content_id=content_id,
                content_version_id=version_id,
                class_ref="class-5a",
            ),
            idempotency_key="i03-create-1",
            event_context=_event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        with bootstrap_engine.connect() as conn:
            audit_count = conn.execute(
                text(
                    """
                    SELECT count(*) FROM security.audit_records
                    WHERE tenant_id = :tid
                      AND action = 'teaching.assignment.create'
                    """
                ),
                {"tid": tenant_id},
            ).scalar_one()
            outbox_count = conn.execute(
                text(
                    """
                    SELECT count(*) FROM integration.outbox_messages
                    WHERE tenant_id = :tid
                      AND event_type = 'io.eduvijna.aieos.teaching.assignment.created.v1'
                    """
                ),
                {"tid": tenant_id},
            ).scalar_one()
        assert int(audit_count) == 1
        assert int(outbox_count) == 1
        assert result.class_ref == "class-5a"

    def test_create_rejects_unpublished_version_after_republish_race(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_v1 = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        republish_content_to_new_version(
            bootstrap_engine,
            tenant_id=tenant_id,
            content_id=content_id,
            parent_version_id=version_v1,
            owner_id=uuid.uuid7(),
        )
        factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
        service = CreateTeachingAssignmentService(
            factory,
            development_class_authority(
                tenant_id=tenant_id, teacher_principal_id=principal_id
            ),
            idempotency_retention=IDEMPOTENCY_RETENTION,
        )
        with pytest.raises(ContentVersionMismatch):
            service.create(
                tenant_id,
                principal_id,
                CreateTeachingAssignmentCommand(
                    content_id=content_id,
                    content_version_id=version_v1,
                    class_ref="class-5a",
                ),
                idempotency_key="i03-race-a",
                event_context=_event_context(principal_id),
                audit_provenance=api_mutation_audit_provenance(principal_id),
                now=datetime(2026, 8, 31, 15, 0, tzinfo=UTC),
            )
