"""SAI-I01 security mutation-audit contract tests."""

from __future__ import annotations

import uuid
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest

from aieos.platform.events.models import MutationEventContext
from aieos.platform.resources import ResourceRef
from aieos.platform.security.audit import (
    AuditRecordId,
    InvalidSecurityAuditError,
    SecurityAuditAction,
    SecurityAuditExecutionChannel,
    SecurityMutationAuditContext,
    SecurityMutationAuditRecord,
    SecurityMutationAuditRepository,
    build_security_mutation_audit_record,
)

pytestmark = pytest.mark.sai_i01

FIXED_NOW = datetime(2026, 8, 15, 0, 0, tzinfo=UTC)
VALID_TRACE = "a" * 31 + "1"


def _event(
    *,
    actor: UUID | None = None,
    effective: UUID | None = None,
    correlation: UUID | None = None,
    causation: UUID | None = None,
) -> MutationEventContext:
    return MutationEventContext(
        correlation_id=correlation or uuid.uuid7(),
        causation_id=causation or uuid.uuid7(),
        actor_principal_id=actor or uuid.uuid7(),
        effective_actor_id=effective or uuid.uuid7(),
    )


def _content_ref(*, revision: int | None) -> ResourceRef:
    return ResourceRef(
        resource_type="content.content",
        resource_id=uuid.uuid7(),
        resource_revision=revision,
    )


def _build(
    *,
    action: SecurityAuditAction = SecurityAuditAction.CONTENT_CREATE,
    before: int | None = None,
    after: int = 0,
    primary: ResourceRef | None = None,
    related: tuple[ResourceRef, ...] = (),
    event: MutationEventContext | None = None,
    executing: UUID | None = None,
    channel: SecurityAuditExecutionChannel = SecurityAuditExecutionChannel.API,
    delegation: UUID | None = None,
    trace_id: str | None = None,
    occurred_at: datetime = FIXED_NOW,
    audit_record_id: AuditRecordId | None = None,
    tenant_id: UUID | None = None,
) -> SecurityMutationAuditRecord:
    primary_ref = primary or _content_ref(revision=after)
    return build_security_mutation_audit_record(
        tenant_id=tenant_id or uuid.uuid7(),
        action=action,
        primary_resource_ref=primary_ref,
        resource_revision_before=before,
        resource_revision_after=after,
        related_resource_refs=related,
        mutation_event_context=event or _event(),
        executing_principal_id=executing or uuid.uuid7(),
        execution_channel=channel,
        occurred_at=occurred_at,
        delegation_id=delegation,
        trace_id=trace_id,
        audit_record_id=audit_record_id,
    )


class TestAuditRecordId:
    def test_generate_is_uuid7(self) -> None:
        generated = AuditRecordId.generate()
        assert generated.value.version == 7

    def test_uuid7_accepted(self) -> None:
        value = uuid.uuid7()
        assert AuditRecordId(value).value == value

    def test_uuid4_rejected(self) -> None:
        with pytest.raises(InvalidSecurityAuditError):
            AuditRecordId(uuid.uuid4())

    def test_malformed_string_rejected(self) -> None:
        with pytest.raises(InvalidSecurityAuditError):
            AuditRecordId("not-a-uuid")

    def test_immutable(self) -> None:
        record_id = AuditRecordId.generate()
        with pytest.raises(Exception):
            record_id.value = uuid.uuid7()  # type: ignore[misc]


class TestActionsAndChannels:
    def test_exact_action_vocabulary(self) -> None:
        assert {a.value for a in SecurityAuditAction} == {
            "content.create",
            "content.version.create",
            "content.review.submit",
            "content.review.approve",
            "content.review.request_changes",
            "content.review.reject",
            "content.publish",
            "content.ai.materialize",
            "content.migration.import",
            "asset.create",
            "asset.revision.register",
            "asset.revision.activate",
            "asset.lifecycle.withdraw",
            "asset.lifecycle.restore",
            "asset.lifecycle.delete",
            "asset.quarantine.set",
            "asset.quarantine.clear",
            "asset.safety.pass",
            "asset.safety.fail",
            "teaching.assignment.create",
            "teaching.assignment.due_update",
            "teaching.assignment.close",
            "teaching.assignment.cancel",
            "teaching.execution.start",
            "teaching.execution.complete",
            "teaching.execution.cancel",
            "teaching.execution.observation.create",
            "teaching.execution.observation.correct",
        }
        assert "content.archive" not in {a.value for a in SecurityAuditAction}
        assert "asset.purge" not in {a.value for a in SecurityAuditAction}

    def test_unknown_action_string_rejected_by_builder(self) -> None:
        with pytest.raises(InvalidSecurityAuditError):
            build_security_mutation_audit_record(
                tenant_id=uuid.uuid7(),
                action="content.create",  # type: ignore[arg-type]
                primary_resource_ref=_content_ref(revision=0),
                resource_revision_before=None,
                resource_revision_after=0,
                related_resource_refs=(),
                mutation_event_context=_event(),
                executing_principal_id=uuid.uuid7(),
                execution_channel=SecurityAuditExecutionChannel.API,
                occurred_at=FIXED_NOW,
            )

    def test_exact_channel_vocabulary(self) -> None:
        assert {c.value for c in SecurityAuditExecutionChannel} == {
            "API",
            "WORKFLOW_ACTIVITY",
            "AI_MATERIALIZATION",
            "MIGRATION",
            "SYSTEM",
        }

    def test_unknown_channel_rejected(self) -> None:
        with pytest.raises(InvalidSecurityAuditError):
            SecurityMutationAuditContext(
                mutation_event_context=_event(),
                executing_principal_id=uuid.uuid7(),
                execution_channel="HTTP",  # type: ignore[arg-type]
            )


class TestContextAndTrace:
    def test_context_derives_actors_and_correlation(self) -> None:
        actor = uuid.uuid7()
        effective = uuid.uuid7()
        correlation = uuid.uuid7()
        causation = uuid.uuid7()
        executing = uuid.uuid7()
        delegation = uuid.uuid7()
        ctx = SecurityMutationAuditContext(
            mutation_event_context=_event(
                actor=actor,
                effective=effective,
                correlation=correlation,
                causation=causation,
            ),
            executing_principal_id=executing,
            execution_channel=SecurityAuditExecutionChannel.API,
            delegation_id=delegation,
        )
        assert ctx.initiating_principal_id == actor
        assert ctx.effective_actor_id == effective
        assert ctx.correlation_id == correlation
        assert ctx.causation_id == causation
        assert ctx.executing_principal_id == executing
        assert ctx.delegation_id == delegation

    def test_valid_trace_accepted(self) -> None:
        ctx = SecurityMutationAuditContext(
            mutation_event_context=_event(),
            executing_principal_id=uuid.uuid7(),
            execution_channel=SecurityAuditExecutionChannel.SYSTEM,
            trace_id=VALID_TRACE,
        )
        assert ctx.trace_id == VALID_TRACE

    @pytest.mark.parametrize(
        "trace",
        [
            "A" * 32,
            "a" * 31,
            "a" * 33,
            "g" + "a" * 31,
            "0" * 32,
        ],
    )
    def test_invalid_trace_rejected(self, trace: str) -> None:
        with pytest.raises(InvalidSecurityAuditError):
            SecurityMutationAuditContext(
                mutation_event_context=_event(),
                executing_principal_id=uuid.uuid7(),
                execution_channel=SecurityAuditExecutionChannel.API,
                trace_id=trace,
            )

    def test_none_trace_accepted(self) -> None:
        ctx = SecurityMutationAuditContext(
            mutation_event_context=_event(),
            executing_principal_id=uuid.uuid7(),
            execution_channel=SecurityAuditExecutionChannel.API,
            trace_id=None,
        )
        assert ctx.trace_id is None


class TestRevisionsAndRefs:
    def test_content_create_revision(self) -> None:
        assert _build(action=SecurityAuditAction.CONTENT_CREATE, before=None, after=0)
        with pytest.raises(InvalidSecurityAuditError):
            _build(action=SecurityAuditAction.CONTENT_CREATE, before=0, after=1)

    def test_migration_import_revision(self) -> None:
        assert _build(
            action=SecurityAuditAction.CONTENT_MIGRATION_IMPORT,
            before=None,
            after=1,
            primary=_content_ref(revision=1),
        )
        with pytest.raises(InvalidSecurityAuditError):
            _build(
                action=SecurityAuditAction.CONTENT_MIGRATION_IMPORT,
                before=None,
                after=0,
                primary=_content_ref(revision=0),
            )

    def test_normal_increment_revision(self) -> None:
        assert _build(
            action=SecurityAuditAction.CONTENT_VERSION_CREATE,
            before=5,
            after=6,
            primary=_content_ref(revision=6),
        )
        with pytest.raises(InvalidSecurityAuditError):
            _build(
                action=SecurityAuditAction.CONTENT_REVIEW_APPROVE,
                before=5,
                after=7,
                primary=_content_ref(revision=7),
            )
        with pytest.raises(InvalidSecurityAuditError):
            _build(
                action=SecurityAuditAction.CONTENT_PUBLISH,
                before=None,
                after=1,
                primary=_content_ref(revision=1),
            )

    @pytest.mark.parametrize("bad", [True, 1.0, -1])
    def test_revision_type_rejected(self, bad: object) -> None:
        with pytest.raises(InvalidSecurityAuditError):
            _build(
                action=SecurityAuditAction.CONTENT_VERSION_CREATE,
                before=0,
                after=bad,  # type: ignore[arg-type]
                primary=_content_ref(revision=1),
            )

    def test_primary_ref_coherence(self) -> None:
        with pytest.raises(InvalidSecurityAuditError):
            _build(
                action=SecurityAuditAction.CONTENT_CREATE,
                before=None,
                after=0,
                primary=_content_ref(revision=1),
            )
        with pytest.raises(InvalidSecurityAuditError):
            _build(
                action=SecurityAuditAction.CONTENT_CREATE,
                before=None,
                after=0,
                primary=_content_ref(revision=None),
            )

    def test_related_refs_rules(self) -> None:
        primary = _content_ref(revision=0)
        related = tuple(
            ResourceRef("content.content_version", uuid.uuid7(), None)
            for _ in range(16)
        )
        assert _build(primary=primary, related=related)
        with pytest.raises(InvalidSecurityAuditError):
            _build(
                primary=primary,
                related=related
                + (ResourceRef("content.content_version", uuid.uuid7(), None),),
            )
        dup = ResourceRef("content.content_version", uuid.uuid7(), 1)
        with pytest.raises(InvalidSecurityAuditError):
            _build(primary=primary, related=(dup, dup))
        with pytest.raises(InvalidSecurityAuditError):
            _build(primary=primary, related=(primary,))
        with pytest.raises(InvalidSecurityAuditError):
            _build(
                primary=primary,
                related=({"resource_type": "x"},),  # type: ignore[arg-type]
            )


class TestOccurredAtAndAllowList:
    def test_aware_datetime_normalized_to_utc(self) -> None:
        offset = timezone(timedelta(hours=5, minutes=30))
        instant = datetime(2026, 8, 15, 5, 30, tzinfo=offset)
        record = _build(occurred_at=instant)
        assert record.occurred_at == FIXED_NOW
        assert record.occurred_at.tzinfo == UTC

    def test_naive_datetime_rejected(self) -> None:
        with pytest.raises(InvalidSecurityAuditError):
            _build(occurred_at=datetime(2026, 8, 15, 0, 0))

    def test_exact_field_allow_list(self) -> None:
        names = {f.name for f in fields(SecurityMutationAuditRecord)}
        assert names == {
            "audit_record_id",
            "tenant_id",
            "action",
            "primary_resource_ref",
            "resource_revision_before",
            "resource_revision_after",
            "related_resource_refs",
            "audit_context",
            "occurred_at",
        }
        forbidden = {
            "metadata",
            "details",
            "payload",
            "roles",
            "permissions",
            "claims",
            "token",
            "headers",
            "prompt",
            "comment",
            "context_json",
            "request",
            "response",
            "jwt",
            "cookie",
            "provenance_dump",
            "raw_comment",
        }
        assert names.isdisjoint(forbidden)

    def test_record_immutable(self) -> None:
        record = _build()
        with pytest.raises(Exception):
            record.action = SecurityAuditAction.CONTENT_PUBLISH  # type: ignore[misc]
        with pytest.raises(InvalidSecurityAuditError):
            replace(record, action="content.create")  # type: ignore[arg-type]


class TestBuilderAndRepositoryPort:
    def test_builder_has_no_independent_correlation_params(self) -> None:
        import inspect

        params = set(inspect.signature(build_security_mutation_audit_record).parameters)
        assert "correlation_id" not in params
        assert "causation_id" not in params
        assert "metadata" not in params
        assert "roles" not in params

    def test_repository_port_insert_only(self) -> None:
        from pathlib import Path

        methods = {
            name
            for name, value in SecurityMutationAuditRepository.__dict__.items()
            if callable(value) and not name.startswith("_")
        }
        assert methods == {"insert"}
        source = Path(
            __import__(
                "aieos.platform.security.audit.ports", fromlist=["ports"]
            ).__file__
        ).read_text(encoding="utf-8")
        for forbidden in (
            "commit",
            "rollback",
            "update",
            "delete",
            "list",
            "search",
            "get_all",
        ):
            assert f"def {forbidden}" not in source
