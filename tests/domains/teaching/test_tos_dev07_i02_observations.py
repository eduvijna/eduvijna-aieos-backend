"""TOS-DEV07-I02 — TeachingExecutionObservation application tests."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.engine import Engine

from aieos.domains.teaching.application.audit import api_mutation_audit_provenance
from aieos.domains.teaching.application.errors import (
    ClassRefNotAssignable,
    ObservationRevisionConflict,
    TeachingExecutionNotInProgress,
    UnsupportedObservationKind,
)
from aieos.domains.teaching.application.models import (
    CorrectTeachingExecutionObservationCommand,
    CreateTeachingExecutionObservationCommand,
)
from aieos.domains.teaching.application.school_context import (
    AssignableClassRef,
    SchoolContextClassAuthorityService,
)
from aieos.domains.teaching.domain.identities import ObservationRevision
from aieos.domains.teaching.domain.observation_kind import ObservationKind
from aieos.development.school_context import DevelopmentSchoolContextClassReader
from tests.domains.teaching.helpers_dev07_i02 import (
    FIXED_NOW,
    complete_service,
    event_context,
    observation_correct_service,
    observation_create_service,
    seed_teaching_work,
    start_execution,
)

pytestmark = pytest.mark.tos_dev07_i02


class _RevokingAuthority:
    """Allows START via Development reader; later raises ClassRefNotAssignable."""

    def __init__(self, *, tenant_id, principal_id) -> None:
        self._start = SchoolContextClassAuthorityService(
            DevelopmentSchoolContextClassReader(
                tenant_id=tenant_id,
                teacher_principal_id=principal_id,
            )
        )
        self._revoked = False

    def revoke(self) -> None:
        self._revoked = True

    def require_assignable_class_ref(
        self, tenant_id, teacher_principal_id, class_ref
    ) -> AssignableClassRef:
        if self._revoked:
            raise ClassRefNotAssignable(
                "requested ClassRef is not currently assignable for this teacher"
            )
        return self._start.require_assignable_class_ref(
            tenant_id, teacher_principal_id, class_ref
        )


class TestObservationKindsAndRevisions:
    def test_i_private_execution_note_create(
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
            idempotency_key="i02-i-start",
        )
        obs = observation_create_service(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        ).create(
            tenant_id,
            principal_id,
            execution_id=started.execution_id,
            command=CreateTeachingExecutionObservationCommand(
                observation_kind=ObservationKind.PRIVATE_EXECUTION_NOTE.value,
                body="private note",
            ),
            idempotency_key="i02-i-obs",
            event_context=event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        assert obs.observation_kind == ObservationKind.PRIVATE_EXECUTION_NOTE.value
        assert int(obs.revision) == 0

    def test_j_class_observation_create(
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
            idempotency_key="i02-j-start",
        )
        obs = observation_create_service(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        ).create(
            tenant_id,
            principal_id,
            execution_id=started.execution_id,
            command=CreateTeachingExecutionObservationCommand(
                observation_kind=ObservationKind.CLASS_OBSERVATION.value,
                body="class observation",
            ),
            idempotency_key="i02-j-obs",
            event_context=event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        assert obs.observation_kind == ObservationKind.CLASS_OBSERVATION.value

    def test_k_learner_specific_kind_rejected(
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
            idempotency_key="i02-k-start",
        )
        with pytest.raises(UnsupportedObservationKind):
            observation_create_service(
                runtime_engine, tenant_id=tenant_id, principal_id=principal_id
            ).create(
                tenant_id,
                principal_id,
                execution_id=started.execution_id,
                command=CreateTeachingExecutionObservationCommand(
                    observation_kind="LEARNER_NOTE",
                    body="not allowed",
                ),
                idempotency_key="i02-k-obs",
                event_context=event_context(principal_id),
                audit_provenance=api_mutation_audit_provenance(principal_id),
                now=FIXED_NOW,
            )

    def test_l_correct_current_revision(
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
            idempotency_key="i02-l-start",
        )
        obs = observation_create_service(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        ).create(
            tenant_id,
            principal_id,
            execution_id=started.execution_id,
            command=CreateTeachingExecutionObservationCommand(
                observation_kind=ObservationKind.PRIVATE_EXECUTION_NOTE.value,
                body="v0",
            ),
            idempotency_key="i02-l-create",
            event_context=event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        corrected = observation_correct_service(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        ).correct(
            tenant_id,
            principal_id,
            execution_id=started.execution_id,
            observation_id=obs.observation_id,
            expected_revision=obs.revision,
            command=CorrectTeachingExecutionObservationCommand(body="v1"),
            idempotency_key="i02-l-correct",
            event_context=event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        assert corrected.body == "v1"
        assert int(corrected.revision) == 1

    def test_m_correct_stale_revision_fails(
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
            idempotency_key="i02-m-start",
        )
        obs = observation_create_service(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        ).create(
            tenant_id,
            principal_id,
            execution_id=started.execution_id,
            command=CreateTeachingExecutionObservationCommand(
                observation_kind=ObservationKind.PRIVATE_EXECUTION_NOTE.value,
                body="v0",
            ),
            idempotency_key="i02-m-create",
            event_context=event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        with pytest.raises(ObservationRevisionConflict):
            observation_correct_service(
                runtime_engine, tenant_id=tenant_id, principal_id=principal_id
            ).correct(
                tenant_id,
                principal_id,
                execution_id=started.execution_id,
                observation_id=obs.observation_id,
                expected_revision=ObservationRevision(99),
                command=CorrectTeachingExecutionObservationCommand(body="stale"),
                idempotency_key="i02-m-stale",
                event_context=event_context(principal_id),
                audit_provenance=api_mutation_audit_provenance(principal_id),
                now=FIXED_NOW,
            )

    def test_n_terminal_parent_observation_mutation_fails(
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
            idempotency_key="i02-n-start",
        )
        complete_service(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        ).complete(
            tenant_id,
            principal_id,
            execution_id=started.execution_id,
            expected_aggregate_revision=started.aggregate_revision,
            idempotency_key="i02-n-complete",
            event_context=event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        with pytest.raises(TeachingExecutionNotInProgress):
            observation_create_service(
                runtime_engine, tenant_id=tenant_id, principal_id=principal_id
            ).create(
                tenant_id,
                principal_id,
                execution_id=started.execution_id,
                command=CreateTeachingExecutionObservationCommand(
                    observation_kind=ObservationKind.PRIVATE_EXECUTION_NOTE.value,
                    body="too late",
                ),
                idempotency_key="i02-n-obs",
                event_context=event_context(principal_id),
                audit_provenance=api_mutation_audit_provenance(principal_id),
                now=FIXED_NOW,
            )

    def test_class_ref_revoked_after_start_denies_later_mutation(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        work_id = seed_teaching_work(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        authority = _RevokingAuthority(
            tenant_id=tenant_id, principal_id=principal_id
        )
        started = start_execution(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            idempotency_key="i02-revoke-start",
            class_authority=authority,
        )
        authority.revoke()
        with pytest.raises(ClassRefNotAssignable):
            complete_service(
                runtime_engine,
                tenant_id=tenant_id,
                principal_id=principal_id,
                class_authority=authority,
            ).complete(
                tenant_id,
                principal_id,
                execution_id=started.execution_id,
                expected_aggregate_revision=started.aggregate_revision,
                idempotency_key="i02-revoke-complete",
                event_context=event_context(principal_id),
                audit_provenance=api_mutation_audit_provenance(principal_id),
                now=FIXED_NOW,
            )
