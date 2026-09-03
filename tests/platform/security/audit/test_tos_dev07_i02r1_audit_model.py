"""TOS-DEV07-I02R1 — TeachingExecution audit action model validation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from aieos.platform.events.models import MutationEventContext
from aieos.platform.resources import ResourceRef
from aieos.platform.security.audit import (
    SecurityAuditAction,
    SecurityAuditExecutionChannel,
    build_security_mutation_audit_record,
)
from aieos.platform.security.audit.errors import InvalidSecurityAuditError

pytestmark = pytest.mark.tos_dev07_i02r1

FIXED_NOW = datetime(2026, 9, 3, 9, 0, tzinfo=UTC)


def _event() -> MutationEventContext:
    return MutationEventContext(
        correlation_id=uuid.uuid7(),
        causation_id=uuid.uuid7(),
        actor_principal_id=uuid.uuid7(),
        effective_actor_id=uuid.uuid7(),
    )


def _build(
    *,
    action: SecurityAuditAction,
    resource_type: str,
    before: int | None,
    after: int,
    primary_revision: int | None = None,
):
    if primary_revision is None:
        primary_revision = after
    return build_security_mutation_audit_record(
        tenant_id=uuid.uuid7(),
        action=action,
        primary_resource_ref=ResourceRef(
            resource_type, uuid.uuid7(), primary_revision
        ),
        resource_revision_before=before,
        resource_revision_after=after,
        related_resource_refs=(),
        mutation_event_context=_event(),
        executing_principal_id=uuid.uuid7(),
        execution_channel=SecurityAuditExecutionChannel.API,
        occurred_at=FIXED_NOW,
    )


class TestTeachingExecutionAuditModel:
    def test_start_and_observation_create_null_to_zero(self) -> None:
        assert _build(
            action=SecurityAuditAction.TEACHING_EXECUTION_START,
            resource_type="teaching.execution",
            before=None,
            after=0,
        )
        assert _build(
            action=SecurityAuditAction.TEACHING_EXECUTION_OBSERVATION_CREATE,
            resource_type="teaching.execution.observation",
            before=None,
            after=0,
        )
        with pytest.raises(InvalidSecurityAuditError) as exc:
            _build(
                action=SecurityAuditAction.TEACHING_EXECUTION_START,
                resource_type="teaching.execution",
                before=0,
                after=1,
            )
        assert "teaching.execution.start" in str(exc.value)
        assert "teaching.assignment.create" not in str(exc.value)

    def test_complete_cancel_observation_correct_increment(self) -> None:
        assert _build(
            action=SecurityAuditAction.TEACHING_EXECUTION_COMPLETE,
            resource_type="teaching.execution",
            before=2,
            after=3,
        )
        assert _build(
            action=SecurityAuditAction.TEACHING_EXECUTION_CANCEL,
            resource_type="teaching.execution",
            before=0,
            after=1,
        )
        assert _build(
            action=SecurityAuditAction.TEACHING_EXECUTION_OBSERVATION_CORRECT,
            resource_type="teaching.execution.observation",
            before=4,
            after=5,
        )
        with pytest.raises(InvalidSecurityAuditError):
            _build(
                action=SecurityAuditAction.TEACHING_EXECUTION_COMPLETE,
                resource_type="teaching.execution",
                before=None,
                after=0,
            )
        with pytest.raises(InvalidSecurityAuditError):
            _build(
                action=SecurityAuditAction.TEACHING_EXECUTION_OBSERVATION_CORRECT,
                resource_type="teaching.execution.observation",
                before=1,
                after=3,
            )

    def test_assignment_create_still_valid(self) -> None:
        assert _build(
            action=SecurityAuditAction.TEACHING_ASSIGNMENT_CREATE,
            resource_type="teaching.assignment",
            before=None,
            after=0,
        )
