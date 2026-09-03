"""TOS-DEV07-I02R1 — observation correction idempotency identity proofs."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.engine import Engine

from aieos.domains.teaching.application.audit import api_mutation_audit_provenance
from aieos.domains.teaching.application.errors import (
    IdempotencyKeyReused,
    PersistenceInvariantViolation,
)
from aieos.domains.teaching.application.models import (
    CorrectTeachingExecutionObservationCommand,
    CreateTeachingExecutionObservationCommand,
)
from aieos.domains.teaching.domain.identities import ObservationRevision
from aieos.domains.teaching.domain.observation_kind import ObservationKind
from tests.domains.teaching.helpers_dev07_i02 import (
    FIXED_NOW,
    event_context,
    observation_correct_service,
    observation_create_service,
    seed_teaching_work,
    start_execution,
)

pytestmark = pytest.mark.tos_dev07_i02


def _start_with_note(
    runtime_engine: Engine,
    *,
    tenant_id,
    principal_id,
    work_id,
    start_key: str,
    note_key: str,
    body: str = "note-0",
):
    started = start_execution(
        runtime_engine,
        tenant_id=tenant_id,
        principal_id=principal_id,
        work_id=work_id,
        idempotency_key=start_key,
    )
    created = observation_create_service(
        runtime_engine, tenant_id=tenant_id, principal_id=principal_id
    ).create(
        tenant_id,
        principal_id,
        execution_id=started.execution_id,
        command=CreateTeachingExecutionObservationCommand(
            observation_kind=ObservationKind.PRIVATE_EXECUTION_NOTE.value,
            body=body,
        ),
        idempotency_key=note_key,
        event_context=event_context(principal_id),
        audit_provenance=api_mutation_audit_provenance(principal_id),
        now=FIXED_NOW,
    )
    return started, created


class TestObservationCorrectIdempotency:
    def test_a_same_key_identical_request_replays(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        work_id = seed_teaching_work(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        started, created = _start_with_note(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            start_key="i02r1-id-a-start",
            note_key="i02r1-id-a-note",
        )
        service = observation_correct_service(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        command = CorrectTeachingExecutionObservationCommand(body="v1")
        first = service.correct(
            tenant_id,
            principal_id,
            execution_id=started.execution_id,
            observation_id=created.observation_id,
            expected_revision=ObservationRevision(0),
            command=command,
            idempotency_key="i02r1-id-a-correct",
            event_context=event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        replay = service.correct(
            tenant_id,
            principal_id,
            execution_id=started.execution_id,
            observation_id=created.observation_id,
            expected_revision=ObservationRevision(0),
            command=command,
            idempotency_key="i02r1-id-a-correct",
            event_context=event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        assert replay.observation_id == first.observation_id
        assert replay.body == first.body
        assert int(replay.revision) == int(first.revision)

    def test_b_same_key_different_body_conflicts(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        work_id = seed_teaching_work(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        started, created = _start_with_note(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            start_key="i02r1-id-b-start",
            note_key="i02r1-id-b-note",
        )
        service = observation_correct_service(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        service.correct(
            tenant_id,
            principal_id,
            execution_id=started.execution_id,
            observation_id=created.observation_id,
            expected_revision=ObservationRevision(0),
            command=CorrectTeachingExecutionObservationCommand(body="v1"),
            idempotency_key="i02r1-id-b-correct",
            event_context=event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        with pytest.raises(IdempotencyKeyReused):
            service.correct(
                tenant_id,
                principal_id,
                execution_id=started.execution_id,
                observation_id=created.observation_id,
                expected_revision=ObservationRevision(0),
                command=CorrectTeachingExecutionObservationCommand(body="v2"),
                idempotency_key="i02r1-id-b-correct",
                event_context=event_context(principal_id),
                audit_provenance=api_mutation_audit_provenance(principal_id),
                now=FIXED_NOW,
            )

    def test_c_same_key_different_expected_revision_conflicts(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        work_id = seed_teaching_work(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        started, created = _start_with_note(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            start_key="i02r1-id-c-start",
            note_key="i02r1-id-c-note",
        )
        service = observation_correct_service(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        service.correct(
            tenant_id,
            principal_id,
            execution_id=started.execution_id,
            observation_id=created.observation_id,
            expected_revision=ObservationRevision(0),
            command=CorrectTeachingExecutionObservationCommand(body="v1"),
            idempotency_key="i02r1-id-c-correct",
            event_context=event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        with pytest.raises(IdempotencyKeyReused):
            service.correct(
                tenant_id,
                principal_id,
                execution_id=started.execution_id,
                observation_id=created.observation_id,
                expected_revision=ObservationRevision(1),
                command=CorrectTeachingExecutionObservationCommand(body="v1"),
                idempotency_key="i02r1-id-c-correct",
                event_context=event_context(principal_id),
                audit_provenance=api_mutation_audit_provenance(principal_id),
                now=FIXED_NOW,
            )

    def test_d_same_key_different_execution_id_conflicts(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        work_id = seed_teaching_work(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        started_a, created_a = _start_with_note(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            start_key="i02r1-id-d-start-a",
            note_key="i02r1-id-d-note-a",
        )
        started_b, created_b = _start_with_note(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            start_key="i02r1-id-d-start-b",
            note_key="i02r1-id-d-note-b",
        )
        service = observation_correct_service(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        command = CorrectTeachingExecutionObservationCommand(body="same-body")
        service.correct(
            tenant_id,
            principal_id,
            execution_id=started_a.execution_id,
            observation_id=created_a.observation_id,
            expected_revision=ObservationRevision(0),
            command=command,
            idempotency_key="i02r1-id-d-correct",
            event_context=event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        with pytest.raises(IdempotencyKeyReused):
            service.correct(
                tenant_id,
                principal_id,
                execution_id=started_b.execution_id,
                observation_id=created_b.observation_id,
                expected_revision=ObservationRevision(0),
                command=command,
                idempotency_key="i02r1-id-d-correct",
                event_context=event_context(principal_id),
                audit_provenance=api_mutation_audit_provenance(principal_id),
                now=FIXED_NOW,
            )

    def test_e_parent_mismatch_cannot_replay_through_other_execution(
        self, runtime_engine: Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Replay path fails closed when stored outcome is not under requested execution."""
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        work_id = seed_teaching_work(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        started_a, created_a = _start_with_note(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            start_key="i02r1-id-e-start-a",
            note_key="i02r1-id-e-note-a",
        )
        started_b, _created_b = _start_with_note(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=principal_id,
            work_id=work_id,
            start_key="i02r1-id-e-start-b",
            note_key="i02r1-id-e-note-b",
        )
        service = observation_correct_service(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        command = CorrectTeachingExecutionObservationCommand(body="v1")
        # First correct under execution A binds the idempotency key.
        service.correct(
            tenant_id,
            principal_id,
            execution_id=started_a.execution_id,
            observation_id=created_a.observation_id,
            expected_revision=ObservationRevision(0),
            command=command,
            idempotency_key="i02r1-id-e-correct",
            event_context=event_context(principal_id),
            audit_provenance=api_mutation_audit_provenance(principal_id),
            now=FIXED_NOW,
        )
        # Same fingerprint fields except execution_id → conflict (not silent cross-parent replay).
        with pytest.raises(IdempotencyKeyReused):
            service.correct(
                tenant_id,
                principal_id,
                execution_id=started_b.execution_id,
                observation_id=created_a.observation_id,
                expected_revision=ObservationRevision(0),
                command=command,
                idempotency_key="i02r1-id-e-correct",
                event_context=event_context(principal_id),
                audit_provenance=api_mutation_audit_provenance(principal_id),
                now=FIXED_NOW,
            )
        # Direct persistence invariant: forged replay with matching fingerprint
        # but wrong execution_id is refused when outcome belongs to another parent.
        from aieos.domains.teaching.application import execution_observations as mod

        original = mod._correct_fingerprint

        def _spoof_fingerprint(execution_id, observation_id, expected_revision, command):
            # Force fingerprint to match the established A outcome while claiming B.
            return original(
                started_a.execution_id, observation_id, expected_revision, command
            )

        monkeypatch.setattr(mod, "_correct_fingerprint", _spoof_fingerprint)
        with pytest.raises(PersistenceInvariantViolation):
            service.correct(
                tenant_id,
                principal_id,
                execution_id=started_b.execution_id,
                observation_id=created_a.observation_id,
                expected_revision=ObservationRevision(0),
                command=command,
                idempotency_key="i02r1-id-e-correct",
                event_context=event_context(principal_id),
                audit_provenance=api_mutation_audit_provenance(principal_id),
                now=FIXED_NOW,
            )
