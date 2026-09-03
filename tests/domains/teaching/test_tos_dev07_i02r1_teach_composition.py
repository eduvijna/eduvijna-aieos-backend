"""TOS-DEV07-I02R1 — Teach composition assignment server-side filter proofs."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy.engine import Engine

from aieos.development.school_context import DevelopmentSchoolContextClassReader
from aieos.domains.teaching.application.school_context import (
    SchoolContextClassAuthorityService,
)
from aieos.domains.teaching.application.teach_composition import (
    GetTeacherOsTeachContextService,
)
from aieos.domains.teaching.domain.assignment import TeachingAssignment
from aieos.domains.teaching.domain.identities import WorkId
from aieos.domains.teaching.infrastructure.persistence.uow import (
    SqlAlchemyTeachingUnitOfWorkFactory,
)
from tests.domains.teaching.helpers_dev06_i03 import seed_published_worksheet
from tests.domains.teaching.helpers_dev07_i02 import (
    FIXED_NOW,
    EmptyTeachingWorkArtifacts,
    seed_teaching_work,
)

pytestmark = pytest.mark.tos_dev07_i02


def _insert_assignment(
    runtime_engine: Engine,
    *,
    tenant_id,
    principal_id,
    content_id,
    content_version_id,
    class_ref: str,
    source_work_id: WorkId | None = None,
    assigned_at=None,
) -> TeachingAssignment:
    assignment = TeachingAssignment.create(
        tenant_id=tenant_id,
        teacher_principal_id=principal_id,
        content_id=content_id,
        content_version_id=content_version_id,
        class_ref=class_ref,
        assigned_at=assigned_at or FIXED_NOW,
        source_work_id=source_work_id,
    )
    factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
    with factory(tenant_id) as uow:
        uow.assignments.insert(assignment)
        uow.commit()
    return assignment


class TestTeachCompositionAssignmentFilters:
    def test_relevant_assignment_beyond_100_unrelated(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        other_principal = uuid.uuid7()
        work_id = seed_teaching_work(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        other_work = seed_teaching_work(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        content_id, version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        # >100 unrelated assignments (newer timestamps so they would win a naive scan).
        for i in range(101):
            _insert_assignment(
                runtime_engine,
                tenant_id=tenant_id,
                principal_id=principal_id,
                content_id=content_id,
                content_version_id=version_id,
                class_ref=f"noise-{i}",
                assigned_at=FIXED_NOW + timedelta(seconds=i + 1),
            )
        wrong_work = _insert_assignment(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            content_id=content_id,
            content_version_id=version_id,
            class_ref="class-5a",
            source_work_id=other_work,
            assigned_at=FIXED_NOW + timedelta(seconds=200),
        )
        wrong_class = _insert_assignment(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            content_id=content_id,
            content_version_id=version_id,
            class_ref="class-5b",
            source_work_id=work_id,
            assigned_at=FIXED_NOW + timedelta(seconds=201),
        )
        other_teacher = _insert_assignment(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=other_principal,
            content_id=content_id,
            content_version_id=version_id,
            class_ref="class-5a",
            source_work_id=work_id,
            assigned_at=FIXED_NOW + timedelta(seconds=202),
        )
        relevant = _insert_assignment(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            content_id=content_id,
            content_version_id=version_id,
            class_ref="class-5a",
            source_work_id=work_id,
            assigned_at=FIXED_NOW,  # older than noise; must still surface via filter
        )

        service = GetTeacherOsTeachContextService(
            SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine),
            SchoolContextClassAuthorityService(
                DevelopmentSchoolContextClassReader(
                    tenant_id=tenant_id,
                    teacher_principal_id=principal_id,
                )
            ),
            EmptyTeachingWorkArtifacts(),
        )
        from aieos.domains.teaching.application.models import (
            GetTeacherOsTeachContextQuery,
        )

        context = service.get(
            tenant_id,
            principal_id,
            GetTeacherOsTeachContextQuery(
                work_id=work_id.value, class_ref="class-5a"
            ),
        )
        ids = {row.assignment_id.value for row in context.assignments}
        assert relevant.assignment_id.value in ids
        assert wrong_work.assignment_id.value not in ids
        assert wrong_class.assignment_id.value not in ids
        assert other_teacher.assignment_id.value not in ids
        assert len(context.assignments) == 1

        # Composition is read-only: relevant assignment lifecycle unchanged.
        factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
        with factory(tenant_id) as uow:
            reloaded = uow.assignments.get(relevant.assignment_id)
        assert reloaded is not None
        assert reloaded.lifecycle_state.value == "ACTIVE"
        assert int(reloaded.aggregate_revision) == 0
