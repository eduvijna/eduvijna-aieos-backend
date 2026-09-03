"""TOS-DEV07-I02 — TeachingExecution application service tests."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.engine import Engine

from aieos.domains.teaching.application.audit import api_mutation_audit_provenance
from aieos.domains.teaching.application.errors import (
    AggregateRevisionConflict,
    ClassRefNotAssignable,
    IdempotencyKeyReused,
    SchoolContextUnavailable,
)
from aieos.domains.teaching.application.models import (
    CreateTeachingExecutionObservationCommand,
    StartTeachingExecutionCommand,
)
from aieos.domains.teaching.application.school_context import (
    AssignableClassRef,
    SchoolContextClassAuthorityService,
)
from aieos.domains.teaching.domain.identities import AggregateRevision
from aieos.domains.teaching.domain.observation_kind import ObservationKind
from aieos.platform.events.constants import (
    EVENT_TEACHING_EXECUTION_CANCELLED_V1,
    EVENT_TEACHING_EXECUTION_COMPLETED_V1,
    EVENT_TEACHING_EXECUTION_STARTED_V1,
)
from aieos.platform.security.audit import SecurityAuditAction
from tests.domains.teaching.helpers_dev06_i03 import (
    create_assignment,
    seed_published_worksheet,
)
from tests.domains.teaching.helpers_dev07_i02 import (
    FIXED_NOW,
    cancel_service,
    complete_service,
    count_executions,
    event_context,
    fetch_audit,
    fetch_outbox,
    observation_create_service,
    seed_teaching_work,
    start_execution,
    start_service,
)

pytestmark = pytest.mark.tos_dev07_i02


class _UnavailableReader:
    def list_assignable_classes(self, tenant_id, teacher_principal_id):
        raise SchoolContextUnavailable("School Context is temporarily unavailable")


class _EmptyReader:
    def list_assignable_classes(self, tenant_id, teacher_principal_id):
        return ()


class TestStartTeachingExecution:
    def test_a_authorized_start_in_progress(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        work_id = seed_teaching_work(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        result = start_execution(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            idempotency_key="i02-a-start",
        )
        assert result.lifecycle_state == "IN_PROGRESS"
        assert result.class_ref == "class-5a"
        assert int(result.aggregate_revision) == 0
        assert result.bindings == ()
        assert count_executions(bootstrap_engine, tenant_id=tenant_id) == 1

    def test_b_unauthorized_class_ref_no_commit(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        work_id = seed_teaching_work(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        authority = SchoolContextClassAuthorityService(_EmptyReader())
        service = start_service(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            class_authority=authority,
        )
        with pytest.raises(ClassRefNotAssignable):
            service.start(
                tenant_id,
                principal_id,
                StartTeachingExecutionCommand(
                    work_id=work_id.value,
                    class_ref="class-5a",
                ),
                idempotency_key="i02-b-unauth",
                event_context=event_context(principal_id),
                audit_provenance=api_mutation_audit_provenance(principal_id),
                now=FIXED_NOW,
            )
        assert count_executions(bootstrap_engine, tenant_id=tenant_id) == 0
        assert (
            fetch_outbox(
                bootstrap_engine,
                tenant_id=tenant_id,
                event_type=EVENT_TEACHING_EXECUTION_STARTED_V1,
            )
            == []
        )

    def test_b_school_context_unavailable_no_commit(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        work_id = seed_teaching_work(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        authority = SchoolContextClassAuthorityService(_UnavailableReader())
        service = start_service(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            class_authority=authority,
        )
        with pytest.raises(SchoolContextUnavailable):
            service.start(
                tenant_id,
                principal_id,
                StartTeachingExecutionCommand(
                    work_id=work_id.value,
                    class_ref="class-5a",
                ),
                idempotency_key="i02-b-unavail",
                event_context=event_context(principal_id),
                audit_provenance=api_mutation_audit_provenance(principal_id),
                now=FIXED_NOW,
            )
        assert count_executions(bootstrap_engine, tenant_id=tenant_id) == 0

    def test_d_same_key_same_request_replays(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        work_id = seed_teaching_work(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        first = start_execution(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            idempotency_key="i02-d-same",
        )
        second = start_execution(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            idempotency_key="i02-d-same",
        )
        assert first.execution_id == second.execution_id
        assert count_executions(bootstrap_engine, tenant_id=tenant_id) == 1

    def test_e_same_key_different_request_rejects(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        work_id = seed_teaching_work(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        start_execution(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            idempotency_key="i02-e-reuse",
            class_ref="class-5a",
        )
        with pytest.raises(IdempotencyKeyReused):
            start_execution(
                runtime_engine,
                tenant_id=tenant_id,
                principal_id=principal_id,
                work_id=work_id,
                idempotency_key="i02-e-reuse",
                class_ref="class-5b",
            )

    def test_f_different_key_same_teacher_work_class_separate(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        work_id = seed_teaching_work(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        a = start_execution(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            idempotency_key="i02-f-a",
        )
        b = start_execution(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            idempotency_key="i02-f-b",
        )
        assert a.execution_id != b.execution_id
        assert count_executions(bootstrap_engine, tenant_id=tenant_id) == 2

    def test_h_zero_bindings_accepted(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        work_id = seed_teaching_work(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        result = start_execution(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            idempotency_key="i02-h-zero",
            bindings=(),
        )
        assert result.bindings == ()


class TestLifecycleCompleteCancel:
    def test_o_complete_in_progress(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        work_id = seed_teaching_work(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        started = start_execution(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            idempotency_key="i02-o-start",
        )
        completed = complete_service(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        ).complete(
            tenant_id,
            principal_id,
            execution_id=started.execution_id,
            expected_aggregate_revision=started.aggregate_revision,
            idempotency_key="i02-o-complete",
            event_context=event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        assert completed.lifecycle_state == "COMPLETED"
        assert int(completed.aggregate_revision) == 1
        assert completed.completed_at is not None

    def test_p_cancel_in_progress(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        work_id = seed_teaching_work(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        started = start_execution(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            idempotency_key="i02-p-start",
        )
        cancelled = cancel_service(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        ).cancel(
            tenant_id,
            principal_id,
            execution_id=started.execution_id,
            expected_aggregate_revision=started.aggregate_revision,
            idempotency_key="i02-p-cancel",
            event_context=event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        assert cancelled.lifecycle_state == "CANCELLED"
        assert int(cancelled.aggregate_revision) == 1
        assert cancelled.cancelled_at is not None

    def test_q_stale_revision_fails(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        work_id = seed_teaching_work(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        started = start_execution(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            idempotency_key="i02-q-start",
        )
        with pytest.raises(AggregateRevisionConflict):
            complete_service(
                runtime_engine, tenant_id=tenant_id, principal_id=principal_id
            ).complete(
                tenant_id,
                principal_id,
                execution_id=started.execution_id,
                expected_aggregate_revision=AggregateRevision(99),
                idempotency_key="i02-q-stale",
                event_context=event_context(principal_id),
                audit_provenance=api_mutation_audit_provenance(principal_id),
                now=FIXED_NOW,
            )


class TestOutboxAuditAndIndependence:
    def test_s_t_u_outbox_events_for_lifecycle(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        work_id = seed_teaching_work(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        started = start_execution(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            idempotency_key="i02-stu-start",
        )
        start_rows = fetch_outbox(
            bootstrap_engine,
            tenant_id=tenant_id,
            event_type=EVENT_TEACHING_EXECUTION_STARTED_V1,
            execution_id=started.execution_id.value,
        )
        assert len(start_rows) == 1
        assert start_rows[0]["envelope"]["type"] == EVENT_TEACHING_EXECUTION_STARTED_V1

        completed = complete_service(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        ).complete(
            tenant_id,
            principal_id,
            execution_id=started.execution_id,
            expected_aggregate_revision=started.aggregate_revision,
            idempotency_key="i02-stu-complete",
            event_context=event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        complete_rows = fetch_outbox(
            bootstrap_engine,
            tenant_id=tenant_id,
            event_type=EVENT_TEACHING_EXECUTION_COMPLETED_V1,
            execution_id=completed.execution_id.value,
        )
        assert len(complete_rows) == 1

        work_id_2 = seed_teaching_work(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        started_2 = start_execution(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id_2,
            idempotency_key="i02-stu-start-2",
        )
        cancelled = cancel_service(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        ).cancel(
            tenant_id,
            principal_id,
            execution_id=started_2.execution_id,
            expected_aggregate_revision=started_2.aggregate_revision,
            idempotency_key="i02-stu-cancel",
            event_context=event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        cancel_rows = fetch_outbox(
            bootstrap_engine,
            tenant_id=tenant_id,
            event_type=EVENT_TEACHING_EXECUTION_CANCELLED_V1,
            execution_id=cancelled.execution_id.value,
        )
        assert len(cancel_rows) == 1

    def test_w_audit_actions_present(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        work_id = seed_teaching_work(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        started = start_execution(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            idempotency_key="i02-w-start",
        )
        start_audit = fetch_audit(
            bootstrap_engine,
            tenant_id=tenant_id,
            action=SecurityAuditAction.TEACHING_EXECUTION_START.value,
            execution_id=started.execution_id.value,
        )
        assert len(start_audit) == 1
        assert start_audit[0]["resource_revision_after"] == 0

        completed = complete_service(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        ).complete(
            tenant_id,
            principal_id,
            execution_id=started.execution_id,
            expected_aggregate_revision=started.aggregate_revision,
            idempotency_key="i02-w-complete",
            event_context=event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        complete_audit = fetch_audit(
            bootstrap_engine,
            tenant_id=tenant_id,
            action=SecurityAuditAction.TEACHING_EXECUTION_COMPLETE.value,
            execution_id=completed.execution_id.value,
        )
        assert len(complete_audit) == 1

        work_id_2 = seed_teaching_work(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        started_2 = start_execution(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id_2,
            idempotency_key="i02-w-start-2",
        )
        cancelled = cancel_service(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        ).cancel(
            tenant_id,
            principal_id,
            execution_id=started_2.execution_id,
            expected_aggregate_revision=started_2.aggregate_revision,
            idempotency_key="i02-w-cancel",
            event_context=event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        cancel_audit = fetch_audit(
            bootstrap_engine,
            tenant_id=tenant_id,
            action=SecurityAuditAction.TEACHING_EXECUTION_CANCEL.value,
            execution_id=cancelled.execution_id.value,
        )
        assert len(cancel_audit) == 1

    def test_v_observation_create_no_observation_outbox(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        work_id = seed_teaching_work(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        started = start_execution(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            idempotency_key="i02-v-start",
        )
        observation_create_service(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        ).create(
            tenant_id,
            principal_id,
            execution_id=started.execution_id,
            command=CreateTeachingExecutionObservationCommand(
                observation_kind=ObservationKind.PRIVATE_EXECUTION_NOTE.value,
                body="note",
            ),
            idempotency_key="i02-v-obs",
            event_context=event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        for etype in (
            "io.eduvijna.aieos.teaching.execution.observation.created.v1",
            "io.eduvijna.aieos.teaching.execution.observation.corrected.v1",
        ):
            assert (
                fetch_outbox(
                    bootstrap_engine, tenant_id=tenant_id, event_type=etype
                )
                == []
            )
        assert len(
            fetch_outbox(
                bootstrap_engine,
                tenant_id=tenant_id,
                event_type=EVENT_TEACHING_EXECUTION_STARTED_V1,
                execution_id=started.execution_id.value,
            )
        ) == 1

    def test_r_complete_does_not_change_assignment_lifecycle(
        self, runtime_engine: Engine, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        work_id = seed_teaching_work(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        content_id, version_id = seed_published_worksheet(
            bootstrap_engine, tenant_id=tenant_id
        )
        assignment = create_assignment(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            content_id=content_id,
            content_version_id=version_id,
            idempotency_key="i02-r-assignment",
        )
        assert assignment.lifecycle_state == "ACTIVE"
        started = start_execution(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            idempotency_key="i02-r-start",
        )
        complete_service(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        ).complete(
            tenant_id,
            principal_id,
            execution_id=started.execution_id,
            expected_aggregate_revision=started.aggregate_revision,
            idempotency_key="i02-r-complete",
            event_context=event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        from aieos.domains.teaching.infrastructure.persistence.uow import (
            SqlAlchemyTeachingUnitOfWorkFactory,
        )

        with SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)(tenant_id) as uow:
            reloaded = uow.assignments.get(assignment.assignment_id)
        assert reloaded is not None
        assert reloaded.lifecycle_state.value == "ACTIVE"
