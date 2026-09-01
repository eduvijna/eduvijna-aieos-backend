"""TOS-DEV06-I03R1 — real transaction failure atomicity for TeachingAssignment CREATE."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.development.school_context import development_class_authority
from aieos.domains.teaching.application.assignment_create import (
    CreateTeachingAssignmentService,
)
from aieos.domains.teaching.application.audit import api_mutation_audit_provenance
from aieos.domains.teaching.application.errors import PersistenceOperationFailed
from aieos.domains.teaching.application.models import CreateTeachingAssignmentCommand
from aieos.domains.teaching.infrastructure.persistence.audit_repository import (
    TeachingSecurityMutationAuditRepository,
)
from aieos.domains.teaching.infrastructure.persistence.uow import (
    SqlAlchemyTeachingUnitOfWorkFactory,
)
from aieos.platform.events.persistence.repositories import SqlAlchemyOutboxRepository
from aieos.platform.api.infrastructure.persistence.repositories import (
    SqlAlchemyIdempotencyRepository,
)
from tests.domains.teaching.helpers_dev06_i03 import (
    FIXED_NOW,
    IDEMPOTENCY_RETENTION,
    event_context,
    seed_published_worksheet,
)

pytestmark = pytest.mark.tos_dev06_i03


def _service(
    runtime_engine: Engine, *, tenant_id: uuid.UUID, principal_id: uuid.UUID
) -> CreateTeachingAssignmentService:
    return CreateTeachingAssignmentService(
        SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine),
        development_class_authority(
            tenant_id=tenant_id, teacher_principal_id=principal_id
        ),
        idempotency_retention=IDEMPOTENCY_RETENTION,
    )


def _command(content_id: uuid.UUID, version_id: uuid.UUID) -> CreateTeachingAssignmentCommand:
    return CreateTeachingAssignmentCommand(
        content_id=content_id,
        content_version_id=version_id,
        class_ref="class-5a",
    )


def _counts(bootstrap_engine: Engine, tenant_id: uuid.UUID) -> dict[str, int]:
    with bootstrap_engine.connect() as conn:
        return {
            "assignments": int(
                conn.execute(
                    text(
                        "SELECT count(*) FROM teaching.assignments WHERE tenant_id = :tid"
                    ),
                    {"tid": tenant_id},
                ).scalar_one()
            ),
            "outbox": int(
                conn.execute(
                    text(
                        "SELECT count(*) FROM integration.outbox_messages "
                        "WHERE tenant_id = :tid AND event_type LIKE "
                        "'io.eduvijna.aieos.teaching.assignment.%'"
                    ),
                    {"tid": tenant_id},
                ).scalar_one()
            ),
            "audit": int(
                conn.execute(
                    text(
                        "SELECT count(*) FROM security.audit_records "
                        "WHERE tenant_id = :tid AND action LIKE 'teaching.assignment.%'"
                    ),
                    {"tid": tenant_id},
                ).scalar_one()
            ),
            "idempotency": int(
                conn.execute(
                    text(
                        "SELECT count(*) FROM api.idempotency_records WHERE tenant_id = :tid"
                    ),
                    {"tid": tenant_id},
                ).scalar_one()
            ),
        }


class TestCreateAtomicityFailureInjection:
    def test_outbox_failure_rolls_back(
        self, runtime_engine: Engine, bootstrap_engine: Engine, monkeypatch
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )

        def boom(self, *args, **kwargs):  # noqa: ANN001
            raise PersistenceOperationFailed("outbox insert failed")

        monkeypatch.setattr(SqlAlchemyOutboxRepository, "insert", boom)
        with pytest.raises(PersistenceOperationFailed):
            _service(runtime_engine, tenant_id=tenant_id, principal_id=principal_id).create(
                tenant_id,
                principal_id,
                _command(content_id, version_id),
                idempotency_key="i03r1-outbox-fail",
                event_context=event_context(principal_id),
                audit_provenance=api_mutation_audit_provenance(principal_id),
                now=FIXED_NOW,
            )
        counts = _counts(bootstrap_engine, tenant_id)
        assert counts == {
            "assignments": 0,
            "outbox": 0,
            "audit": 0,
            "idempotency": 0,
        }

    def test_audit_failure_rolls_back(
        self, runtime_engine: Engine, bootstrap_engine: Engine, monkeypatch
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )

        def boom(self, record) -> None:  # noqa: ANN001
            raise PersistenceOperationFailed("audit insert failed")

        monkeypatch.setattr(TeachingSecurityMutationAuditRepository, "insert", boom)
        with pytest.raises(PersistenceOperationFailed):
            _service(runtime_engine, tenant_id=tenant_id, principal_id=principal_id).create(
                tenant_id,
                principal_id,
                _command(content_id, version_id),
                idempotency_key="i03r1-audit-fail",
                event_context=event_context(principal_id),
                audit_provenance=api_mutation_audit_provenance(principal_id),
                now=FIXED_NOW,
            )
        counts = _counts(bootstrap_engine, tenant_id)
        assert counts == {
            "assignments": 0,
            "outbox": 0,
            "audit": 0,
            "idempotency": 0,
        }

    def test_idempotency_failure_rolls_back(
        self, runtime_engine: Engine, bootstrap_engine: Engine, monkeypatch
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        content_id, version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )

        def boom(self, outcome) -> None:  # noqa: ANN001
            raise PersistenceOperationFailed("idempotency insert failed")

        monkeypatch.setattr(SqlAlchemyIdempotencyRepository, "insert", boom)
        with pytest.raises(PersistenceOperationFailed):
            _service(runtime_engine, tenant_id=tenant_id, principal_id=principal_id).create(
                tenant_id,
                principal_id,
                _command(content_id, version_id),
                idempotency_key="i03r1-idem-fail",
                event_context=event_context(principal_id),
                audit_provenance=api_mutation_audit_provenance(principal_id),
                now=FIXED_NOW,
            )
        counts = _counts(bootstrap_engine, tenant_id)
        assert counts == {
            "assignments": 0,
            "outbox": 0,
            "audit": 0,
            "idempotency": 0,
        }
