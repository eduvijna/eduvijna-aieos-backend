from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import CheckConstraint

from aieos.domains.teaching.application.audit import api_mutation_audit_provenance
from aieos.domains.teaching.application.errors import (
    ClassRefNotAssignable,
    IdempotencyKeyReused,
    RemediationAssessmentForbidden,
    RemediationAssessmentNotFound,
    RemediationAssessmentNotRecorded,
    RemediationAssessmentRevisionConflict,
)
from aieos.domains.teaching.application.models import (
    CreateRemediationTeachingWorkCommand,
)
from aieos.domains.teaching.application.ports import (
    RemediationAssessmentSourceSnapshot,
)
from aieos.domains.teaching.application.remediation_create import (
    CreateRemediationTeachingWorkService,
)
from aieos.domains.teaching.application.school_context import AssignableClassRef
from aieos.domains.teaching.domain.identities import AssignmentId, ExecutionId, WorkId
from aieos.platform.events.models import MutationEventContext
from aieos.platform.security.audit.actions import (
    SecurityAuditAction,
    is_teaching_audit_action,
    is_teaching_create_action,
)
from aieos.platform.security.audit.persistence.models import audit_records_table

pytestmark = pytest.mark.tos_dev09_i02
ROOT = Path(__file__).resolve().parents[3]


class _Repo:
    def __init__(self) -> None:
        self.items = {}
        self.bindings = {}

    def insert(self, item) -> None:
        key = next(
            getattr(item, name)
            for name in ("assignment_id", "execution_id", "work_id")
            if hasattr(item, name)
        )
        self.items[key] = item

    def get(self, key):
        return self.items.get(key)

    def list_bindings(self, execution_id):
        return self.bindings.get(execution_id, [])


class _Idempotency:
    def __init__(self) -> None:
        self.outcomes = {}

    def acquire_scope(self, scope) -> None:
        pass

    def get(self, scope):
        return self.outcomes.get(scope)

    def insert(self, outcome) -> None:
        from aieos.platform.idempotency.models import IdempotencyScope

        scope = IdempotencyScope(
            outcome.tenant_id,
            outcome.principal_id,
            outcome.operation,
            outcome.key_sha256,
        )
        self.outcomes[scope] = outcome


class _Audit:
    def __init__(self) -> None:
        self.records = []

    def insert(self, record) -> None:
        self.records.append(record)


class _Uow:
    def __init__(self, snapshot) -> None:
        self.snapshot = snapshot
        self.works = _Repo()
        self.remediation_origins = _Repo()
        self.idempotency = _Idempotency()
        self.audit = _Audit()
        self.assignments = _Repo()
        self.executions = _Repo()
        self.commits = 0
        self.loads = 0

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        pass

    def load_recorded_assessment_for_update(self, assessment_id):
        self.loads += 1
        return self.snapshot if self.snapshot.assessment_id == assessment_id else None

    def commit(self) -> None:
        self.commits += 1


class _Factory:
    def __init__(self, uow) -> None:
        self.uow = uow

    def __call__(self, tenant_id):
        assert tenant_id == self.uow.snapshot.tenant_id
        return self.uow


class _Authority:
    def __init__(self, class_ref: str) -> None:
        self.class_ref = class_ref
        self.calls = []

    def require_assignable_class_ref(self, tenant_id, principal_id, class_ref):
        self.calls.append((tenant_id, principal_id, class_ref))
        assert class_ref == self.class_ref
        return AssignableClassRef(class_ref, "Grade 7 · Mathematics")


class _Authorization:
    def __init__(self) -> None:
        self.calls = []

    def authorize(self, *, tenant_id, principal_id, capability) -> None:
        self.calls.append((tenant_id, principal_id, capability))


def _fixture():
    tenant_id, principal_id = uuid.uuid7(), uuid.uuid7()
    assessment_id = uuid.uuid7()
    snapshot = RemediationAssessmentSourceSnapshot(
        assessment_id=assessment_id,
        tenant_id=tenant_id,
        teacher_principal_id=principal_id,
        class_ref="school/class/7a",
        content_id=uuid.uuid7(),
        content_version_id=uuid.uuid7(),
        class_result_level="NOT_YET_DEMONSTRATED",
        lifecycle_state="RECORDED",
        work_id=None,
        execution_id=None,
        assignment_id=None,
        aggregate_revision=2,
    )
    command = CreateRemediationTeachingWorkCommand(
        assessment_id=assessment_id,
        expected_assessment_aggregate_revision=2,
        goal_text="Re-teach equivalent fractions",
        target_date=date(2026, 9, 8),
        locale="en-IN",
        subject="Mathematics",
        topic="Fractions",
    )
    return tenant_id, principal_id, snapshot, command


def _call(snapshot, command, principal_id=None):
    caller = principal_id or snapshot.teacher_principal_id
    uow = _Uow(snapshot)
    service = CreateRemediationTeachingWorkService(
        _Factory(uow),
        _Authority(snapshot.class_ref),
        _Authorization(),
        _Authorization(),
        idempotency_retention=timedelta(hours=1),
    )
    return service, uow, caller, {
        "idempotency_key": f"unit-{uuid.uuid7()}",
        "event_context": MutationEventContext(
            uuid.uuid7(), uuid.uuid7(), caller, caller
        ),
        "audit_provenance": api_mutation_audit_provenance(caller),
    }


def test_success_origin_audit_atomicity_and_replay() -> None:
    tenant_id, principal_id, snapshot, command = _fixture()
    uow, authority, teaching_authorization, assessment_authorization = (
        _Uow(snapshot),
        _Authority(snapshot.class_ref),
        _Authorization(),
        _Authorization(),
    )
    service = CreateRemediationTeachingWorkService(
        _Factory(uow),
        authority,
        teaching_authorization,
        assessment_authorization,
        idempotency_retention=timedelta(hours=24),
    )
    context = MutationEventContext(
        correlation_id=uuid.uuid7(),
        causation_id=uuid.uuid7(),
        actor_principal_id=principal_id,
        effective_actor_id=principal_id,
    )
    now = datetime(2026, 9, 4, tzinfo=UTC)
    first = service.create(
        tenant_id,
        principal_id,
        command,
        idempotency_key="dev09-i02-key",
        event_context=context,
        audit_provenance=api_mutation_audit_provenance(principal_id),
        now=now,
    )
    assert first.intent_type == "remediate_class"
    assert first.class_label == "Grade 7 · Mathematics"
    origin = uow.remediation_origins.get(first.work_id)
    assert origin.source_assessment_id == snapshot.assessment_id
    assert origin.source_assessment_aggregate_revision == 2
    assert origin.source_class_result_level_snapshot.value == "NOT_YET_DEMONSTRATED"
    assert len(uow.audit.records) == 1
    assert uow.audit.records[0].action is SecurityAuditAction.TEACHING_WORK_REMEDIATION_CREATE
    assert uow.commits == 1

    replay = service.create(
        tenant_id,
        principal_id,
        command,
        idempotency_key="dev09-i02-key",
        event_context=context,
        audit_provenance=api_mutation_audit_provenance(principal_id),
        now=now,
    )
    assert replay.work_id == first.work_id
    assert len(uow.audit.records) == 1
    assert uow.loads == 1
    assert [call[2] for call in teaching_authorization.calls] == [
        "teaching.work.create",
        "teaching.work.create",
    ]
    assert [call[2] for call in assessment_authorization.calls] == [
        "assessment.classroom.read",
        "assessment.classroom.read",
    ]
    assert len(authority.calls) == 2


def test_different_fingerprint_conflicts_without_second_mutation() -> None:
    tenant_id, principal_id, snapshot, command = _fixture()
    uow = _Uow(snapshot)
    service = CreateRemediationTeachingWorkService(
        _Factory(uow),
        _Authority(snapshot.class_ref),
        _Authorization(),
        _Authorization(),
        idempotency_retention=timedelta(hours=1),
    )
    context = MutationEventContext(
        uuid.uuid7(), uuid.uuid7(), principal_id, principal_id
    )
    kwargs = {
        "idempotency_key": "same-key",
        "event_context": context,
        "audit_provenance": api_mutation_audit_provenance(principal_id),
    }
    service.create(tenant_id, principal_id, command, **kwargs)
    changed = CreateRemediationTeachingWorkCommand(
        assessment_id=command.assessment_id,
        expected_assessment_aggregate_revision=(
            command.expected_assessment_aggregate_revision
        ),
        goal_text="Different goal",
        target_date=command.target_date,
        locale=command.locale,
        subject=command.subject,
        topic=command.topic,
    )
    with pytest.raises(IdempotencyKeyReused):
        service.create(tenant_id, principal_id, changed, **kwargs)
    assert len(uow.audit.records) == 1


@pytest.mark.parametrize(
    ("source_change", "command_change", "error"),
    (
        ({"lifecycle_state": "VOIDED"}, {}, RemediationAssessmentNotRecorded),
        ({}, {"expected_assessment_aggregate_revision": 1}, RemediationAssessmentRevisionConflict),
        ({"teacher_principal_id": uuid.UUID(int=1)}, {}, RemediationAssessmentForbidden),
    ),
)
def test_invalid_assessment_state_revision_and_owner(
    source_change, command_change, error
) -> None:
    _tenant, principal, snapshot, command = _fixture()
    source = replace(snapshot, **source_change)
    changed_command = replace(command, **command_change)
    service, uow, caller, kwargs = _call(source, changed_command, principal)
    with pytest.raises(error):
        service.create(source.tenant_id, caller, changed_command, **kwargs)
    assert len(uow.audit.records) == 0
    assert uow.commits == 0


def test_assessment_not_found() -> None:
    tenant, principal, snapshot, command = _fixture()
    service, uow, caller, kwargs = _call(snapshot, command, principal)
    missing = replace(command, assessment_id=uuid.uuid7())
    with pytest.raises(RemediationAssessmentNotFound):
        service.create(tenant, caller, missing, **kwargs)
    assert uow.commits == 0


def test_current_class_ref_denial() -> None:
    tenant, principal, snapshot, command = _fixture()
    uow = _Uow(snapshot)

    class Deny:
        def require_assignable_class_ref(self, *args):
            raise ClassRefNotAssignable("revoked")

    service = CreateRemediationTeachingWorkService(
        _Factory(uow),
        Deny(),
        _Authorization(),
        _Authorization(),
        idempotency_retention=timedelta(hours=1),
    )
    with pytest.raises(ClassRefNotAssignable):
        service.create(
            tenant,
            principal,
            command,
            idempotency_key="denied",
            event_context=MutationEventContext(
                uuid.uuid7(), uuid.uuid7(), principal, principal
            ),
            audit_provenance=api_mutation_audit_provenance(principal),
        )
    assert uow.commits == 0


@pytest.mark.parametrize("kind", ("work", "execution", "assignment"))
def test_composition_owner_must_match_assessment_teacher(kind: str) -> None:
    _tenant, principal, snapshot, command = _fixture()
    foreign = uuid.uuid7()
    source_id = uuid.uuid7()
    changes = {f"{kind}_id": source_id}
    source = replace(snapshot, **changes)
    service, uow, caller, kwargs = _call(source, command, principal)
    work_id = WorkId(source_id if kind == "work" else uuid.uuid7())
    if kind == "work":
        uow.works.items[WorkId(source_id)] = SimpleNamespace(
            teacher_principal_id=foreign
        )
    elif kind == "execution":
        execution_id = ExecutionId(source_id)
        uow.executions.items[execution_id] = SimpleNamespace(
            execution_id=execution_id,
            teacher_principal_id=foreign,
            class_ref=source.class_ref,
            work_id=work_id,
        )
    else:
        assignment_id = AssignmentId(source_id)
        uow.assignments.items[assignment_id] = SimpleNamespace(
            assignment_id=assignment_id,
            teacher_principal_id=foreign,
            class_ref=source.class_ref,
            content_id=source.content_id,
            content_version_id=source.content_version_id,
            source_work_id=work_id,
        )
    with pytest.raises(RemediationAssessmentForbidden):
        service.create(source.tenant_id, caller, command, **kwargs)
    assert uow.commits == 0


def test_audit_mirrors_and_migration_vocabulary() -> None:
    assert is_teaching_create_action(
        SecurityAuditAction.TEACHING_WORK_REMEDIATION_CREATE
    )
    assert is_teaching_audit_action(
        SecurityAuditAction.TEACHING_WORK_REMEDIATION_CREATE
    )
    constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in audit_records_table.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name
    }
    for name in (
        "ck_audit_records_action",
        "ck_audit_records_primary_revision_family",
        "ck_audit_records_revision_semantics",
    ):
        assert "teaching.work.remediation.create" in constraints[name]
    migration = (
        ROOT
        / "migrations"
        / "versions"
        / "tosd090002_teaching_work_remediation_audit.py"
    ).read_text(encoding="utf-8")
    assert 'revision: str = "tosd090002"' in migration
    assert 'down_revision: str | None = "tosd090001"' in migration
    assert "TOS-DEV09-I02 downgrade refused" in migration


def test_strict_http_request_has_only_authorized_fields() -> None:
    from aieos.domains.teaching.api.v1.models import (
        RemediationTeachingWorkCreateRequest,
    )

    assert set(RemediationTeachingWorkCreateRequest.model_fields) == {
        "assessment_id",
        "expected_assessment_aggregate_revision",
        "goal_text",
        "target_date",
        "locale",
        "subject",
        "topic",
    }
