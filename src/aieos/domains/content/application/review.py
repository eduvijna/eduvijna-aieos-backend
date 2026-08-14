"""Authoritative Generic Content review submit and decide orchestration."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from aieos.domains.content.application.errors import (
    AggregateRevisionConflict,
    ContentNotFound,
    ContentVersionNotFound,
    IdempotencyKeyReused,
    InvalidContentRequest,
    PersistenceInvariantViolation,
    ReviewAlreadyDecided,
    ReviewDecisionNotAllowed,
    ReviewRequiresNewVersion,
    ReviewSubmitNotAllowed,
    ReviewVersionNotCurrent,
    WorkflowCoordinationFailed,
)
from aieos.domains.content.application.models import (
    ReviewDecisionResult,
    ReviewSubmissionResult,
)
from aieos.domains.content.application.ports import (
    CONTENT_REVIEW_DECIDE,
    CONTENT_REVIEW_SUBMIT,
    ContentUnitOfWork,
    ContentUnitOfWorkFactory,
    ReviewAuthorizationPort,
    ReviewCommentPolicy,
)
from aieos.domains.content.domain.errors import ReviewDecisionBindingError
from aieos.domains.content.domain.identities import (
    AggregateRevision,
    ContentId,
    ContentVersionId,
    ReviewDecisionId,
)
from aieos.domains.content.domain.review import (
    ReviewDecision,
    ReviewDecisionCode,
    normalize_reason_code,
    normalize_review_comment,
)
from aieos.domains.content.domain.states import StewardshipState
from aieos.platform.idempotency.hashing import fingerprint_material, hash_idempotency_key
from aieos.platform.idempotency.models import (
    CONTENT_REVIEW_APPROVE_V1,
    CONTENT_REVIEW_REJECT_V1,
    CONTENT_REVIEW_REQUEST_CHANGES_V1,
    CONTENT_REVIEW_SUBMIT_V1,
    IdempotencyOutcome,
    IdempotencyScope,
)
from aieos.platform.workflows.constants import (
    CONTENT_REVIEW_TASK_QUEUE,
    CONTENT_REVIEW_WORKFLOW_MAJOR,
    CONTENT_REVIEW_WORKFLOW_TYPE,
    INTENT_PENDING,
    SIGNAL_REVIEW_DECISION_RECORDED,
    content_review_business_key,
    content_review_temporal_workflow_id,
    review_decision_command_business_key,
)
from aieos.platform.workflows.identities import (
    WorkflowCommandId,
    WorkflowCommandIntentId,
    WorkflowInstanceId,
    WorkflowStartIntentId,
)
from aieos.platform.workflows.models import WorkflowCommandIntent, WorkflowStartIntent

_FROZEN_SUBMIT_STATE = StewardshipState.IN_REVIEW.value
_FROZEN_APPROVE_STATE = StewardshipState.APPROVED.value
_FROZEN_NEGATIVE_STATE = StewardshipState.GENERATED.value


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(UTC)


def _review_fingerprint(
    *,
    content_id: ContentId,
    version_id: ContentVersionId,
    expected_aggregate_revision: AggregateRevision,
    reason_code: str | None = None,
    comment: str | None = None,
    include_comment: bool,
) -> str:
    material: dict[str, object] = {
        "content_id": str(content_id),
        "version_id": str(version_id),
        "expected_aggregate_revision": int(expected_aggregate_revision),
    }
    if include_comment:
        material["reason_code"] = reason_code
        material["comment"] = comment
    return fingerprint_material(material)


def _parse_review_text(
    *,
    reason_code: str | None,
    comment: str | None,
    require_comment: bool,
) -> tuple[str | None, str | None]:
    try:
        code = normalize_reason_code(reason_code)
        text = normalize_review_comment(comment)
    except ReviewDecisionBindingError as exc:
        raise InvalidContentRequest("review comment or reason_code is invalid") from exc
    if require_comment and text is None:
        raise InvalidContentRequest("REQUEST_CHANGES requires a non-empty comment")
    return code, text


def _require_visible_version(
    uow: ContentUnitOfWork,
    content_id: ContentId,
    version_id: ContentVersionId,
):
    found = uow.versions.get(version_id)
    if found is None or found.content_id != content_id:
        raise ContentVersionNotFound(
            "ContentVersion is not visible for the requested Content"
        )
    return found


def _require_current(head, version_id: ContentVersionId) -> None:
    if head.current_version_id is None or head.current_version_id != version_id:
        raise ReviewVersionNotCurrent(
            "requested version is not the current ContentVersion"
        )


class ReviewCommandService:
    def __init__(
        self,
        uow_factory: ContentUnitOfWorkFactory,
        authorization: ReviewAuthorizationPort,
        comment_policy: ReviewCommentPolicy,
        *,
        idempotency_retention: timedelta,
    ) -> None:
        if idempotency_retention.total_seconds() <= 0:
            raise ValueError("idempotency_retention must be a positive duration")
        self._uow_factory = uow_factory
        self._authorization = authorization
        self._comment_policy = comment_policy
        self._idempotency_retention = idempotency_retention

    def submit(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        *,
        content_id: ContentId,
        version_id: ContentVersionId,
        expected_aggregate_revision: AggregateRevision,
        idempotency_key: str,
        correlation_id: UUID,
        now: datetime | None = None,
    ) -> ReviewSubmissionResult:
        self._authorization.authorize(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            content_id=content_id,
            version_id=version_id,
            capability=CONTENT_REVIEW_SUBMIT,
        )
        decided_at = _now(now)
        fingerprint = _review_fingerprint(
            content_id=content_id,
            version_id=version_id,
            expected_aggregate_revision=expected_aggregate_revision,
            include_comment=False,
        )
        scope = IdempotencyScope(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            operation=CONTENT_REVIEW_SUBMIT_V1,
            key_sha256=hash_idempotency_key(idempotency_key),
        )
        with self._uow_factory(execution_tenant_id) as uow:
            uow.idempotency.acquire_scope(scope)
            existing = uow.idempotency.get(scope)
            if existing is not None:
                if existing.request_fingerprint_sha256 != fingerprint:
                    raise IdempotencyKeyReused("idempotency key already bound")
                if existing.result_version_id is None:
                    raise PersistenceInvariantViolation(
                        "idempotent submit outcome is missing version identity"
                    )
                found = uow.contents.get(ContentId(existing.result_content_id))
                if found is None:
                    raise PersistenceInvariantViolation(
                        "idempotent submit outcome is not visible"
                    )
                return ReviewSubmissionResult(
                    content_id=ContentId(existing.result_content_id),
                    version_id=ContentVersionId(existing.result_version_id),
                    stewardship_state=_FROZEN_SUBMIT_STATE,
                    aggregate_revision=AggregateRevision(
                        existing.result_aggregate_revision
                    ),
                )
            revision = self._submit_new(
                uow,
                execution_tenant_id,
                content_id=content_id,
                version_id=version_id,
                expected_aggregate_revision=expected_aggregate_revision,
                correlation_id=correlation_id,
                updated_at=decided_at,
            )
            uow.idempotency.insert(
                IdempotencyOutcome(
                    tenant_id=scope.tenant_id,
                    principal_id=scope.principal_id,
                    operation=scope.operation,
                    key_sha256=scope.key_sha256,
                    request_fingerprint_sha256=fingerprint,
                    result_content_id=content_id.value,
                    result_version_id=version_id.value,
                    result_review_decision_id=None,
                    result_aggregate_revision=int(revision),
                    created_at=decided_at,
                    expires_at=decided_at + self._idempotency_retention,
                )
            )
            uow.commit()
        return ReviewSubmissionResult(
            content_id=content_id,
            version_id=version_id,
            stewardship_state=_FROZEN_SUBMIT_STATE,
            aggregate_revision=revision,
        )

    def approve(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        *,
        content_id: ContentId,
        version_id: ContentVersionId,
        expected_aggregate_revision: AggregateRevision,
        reason_code: str | None,
        comment: str | None,
        idempotency_key: str,
        correlation_id: UUID,
        now: datetime | None = None,
    ) -> ReviewDecisionResult:
        return self._decide(
            execution_tenant_id,
            principal_id,
            content_id=content_id,
            version_id=version_id,
            expected_aggregate_revision=expected_aggregate_revision,
            decision=ReviewDecisionCode.APPROVE,
            operation=CONTENT_REVIEW_APPROVE_V1,
            target_state=StewardshipState.APPROVED.value,
            frozen_state=_FROZEN_APPROVE_STATE,
            reason_code=reason_code,
            comment=comment,
            require_comment=False,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            now=now,
        )

    def request_changes(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        *,
        content_id: ContentId,
        version_id: ContentVersionId,
        expected_aggregate_revision: AggregateRevision,
        reason_code: str | None,
        comment: str | None,
        idempotency_key: str,
        correlation_id: UUID,
        now: datetime | None = None,
    ) -> ReviewDecisionResult:
        return self._decide(
            execution_tenant_id,
            principal_id,
            content_id=content_id,
            version_id=version_id,
            expected_aggregate_revision=expected_aggregate_revision,
            decision=ReviewDecisionCode.REQUEST_CHANGES,
            operation=CONTENT_REVIEW_REQUEST_CHANGES_V1,
            target_state=StewardshipState.GENERATED.value,
            frozen_state=_FROZEN_NEGATIVE_STATE,
            reason_code=reason_code,
            comment=comment,
            require_comment=True,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            now=now,
        )

    def reject(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        *,
        content_id: ContentId,
        version_id: ContentVersionId,
        expected_aggregate_revision: AggregateRevision,
        reason_code: str | None,
        comment: str | None,
        idempotency_key: str,
        correlation_id: UUID,
        now: datetime | None = None,
    ) -> ReviewDecisionResult:
        return self._decide(
            execution_tenant_id,
            principal_id,
            content_id=content_id,
            version_id=version_id,
            expected_aggregate_revision=expected_aggregate_revision,
            decision=ReviewDecisionCode.REJECT,
            operation=CONTENT_REVIEW_REJECT_V1,
            target_state=StewardshipState.GENERATED.value,
            frozen_state=_FROZEN_NEGATIVE_STATE,
            reason_code=reason_code,
            comment=comment,
            require_comment=False,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            now=now,
        )

    def _decide(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        *,
        content_id: ContentId,
        version_id: ContentVersionId,
        expected_aggregate_revision: AggregateRevision,
        decision: ReviewDecisionCode,
        operation: str,
        target_state: str,
        frozen_state: str,
        reason_code: str | None,
        comment: str | None,
        require_comment: bool,
        idempotency_key: str,
        correlation_id: UUID,
        now: datetime | None,
    ) -> ReviewDecisionResult:
        self._authorization.authorize(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            content_id=content_id,
            version_id=version_id,
            capability=CONTENT_REVIEW_DECIDE,
        )
        code, text = _parse_review_text(
            reason_code=reason_code,
            comment=comment,
            require_comment=require_comment,
        )
        decided_at = _now(now)
        fingerprint = _review_fingerprint(
            content_id=content_id,
            version_id=version_id,
            expected_aggregate_revision=expected_aggregate_revision,
            reason_code=code,
            comment=text,
            include_comment=True,
        )
        scope = IdempotencyScope(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            operation=operation,
            key_sha256=hash_idempotency_key(idempotency_key),
        )
        with self._uow_factory(execution_tenant_id) as uow:
            uow.idempotency.acquire_scope(scope)
            existing = uow.idempotency.get(scope)
            if existing is not None:
                if existing.request_fingerprint_sha256 != fingerprint:
                    raise IdempotencyKeyReused("idempotency key already bound")
                return self._replay_decision(uow, existing, frozen_state)
            self._comment_policy.evaluate(text)
            stored, revision = self._decide_new(
                uow,
                execution_tenant_id,
                principal_id=principal_id,
                content_id=content_id,
                version_id=version_id,
                expected_aggregate_revision=expected_aggregate_revision,
                decision=decision,
                reason_code=code,
                comment=text,
                target_state=target_state,
                correlation_id=correlation_id,
                decided_at=decided_at,
            )
            uow.idempotency.insert(
                IdempotencyOutcome(
                    tenant_id=scope.tenant_id,
                    principal_id=scope.principal_id,
                    operation=scope.operation,
                    key_sha256=scope.key_sha256,
                    request_fingerprint_sha256=fingerprint,
                    result_content_id=content_id.value,
                    result_version_id=version_id.value,
                    result_review_decision_id=stored.review_decision_id.value,
                    result_aggregate_revision=int(revision),
                    created_at=decided_at,
                    expires_at=decided_at + self._idempotency_retention,
                )
            )
            uow.commit()
        return ReviewDecisionResult(
            review_decision_id=stored.review_decision_id,
            content_id=stored.content_id,
            version_id=stored.version_id,
            decision=stored.decision.value,
            reason_code=stored.reason_code,
            comment=stored.comment,
            decided_at=stored.decided_at,
            stewardship_state=frozen_state,
            aggregate_revision=revision,
        )

    def _submit_new(
        self,
        uow: ContentUnitOfWork,
        execution_tenant_id: UUID,
        *,
        content_id: ContentId,
        version_id: ContentVersionId,
        expected_aggregate_revision: AggregateRevision,
        correlation_id: UUID,
        updated_at: datetime,
    ) -> AggregateRevision:
        head = uow.contents.get_head_for_update(content_id)
        if head is None or head.tenant_id != execution_tenant_id:
            raise ContentNotFound("Content is not visible in the execution tenant")
        _require_visible_version(uow, content_id, version_id)
        if head.aggregate_revision != expected_aggregate_revision:
            raise AggregateRevisionConflict(
                "expected aggregate_revision does not match stored head"
            )
        _require_current(head, version_id)
        if head.stewardship_state != StewardshipState.GENERATED.value:
            raise ReviewSubmitNotAllowed(
                "submit-for-review is not allowed in the current stewardship state"
            )
        if uow.reviews.get_for_version(content_id, version_id) is not None:
            raise ReviewRequiresNewVersion(
                "this ContentVersion already has a terminal ReviewDecision"
            )
        resulting = uow.contents.transition_stewardship(
            content_id=content_id,
            tenant_id=execution_tenant_id,
            expected_revision=expected_aggregate_revision,
            expected_current_version_id=version_id,
            expected_state=StewardshipState.GENERATED.value,
            target_state=StewardshipState.IN_REVIEW.value,
            updated_at=updated_at,
        )
        if resulting is None:
            raise AggregateRevisionConflict(
                "aggregate head changed before submit could commit"
            )
        workflow_instance_id = WorkflowInstanceId.generate()
        business_key = content_review_business_key(
            content_id=str(content_id),
            version_id=str(version_id),
        )
        temporal_workflow_id = content_review_temporal_workflow_id(
            str(workflow_instance_id)
        )
        uow.workflow_intents.insert_start_intent(
            WorkflowStartIntent(
                workflow_start_intent_id=WorkflowStartIntentId.generate(),
                tenant_id=execution_tenant_id,
                workflow_instance_id=workflow_instance_id,
                workflow_type=CONTENT_REVIEW_WORKFLOW_TYPE,
                workflow_major_version=CONTENT_REVIEW_WORKFLOW_MAJOR,
                temporal_workflow_id=temporal_workflow_id,
                task_queue=CONTENT_REVIEW_TASK_QUEUE,
                business_key=business_key,
                input={
                    "workflow_instance_id": str(workflow_instance_id),
                    "tenant_id": str(execution_tenant_id),
                    "content_id": str(content_id),
                    "version_id": str(version_id),
                    "correlation_id": str(correlation_id),
                },
                status=INTENT_PENDING,
                attempt_count=0,
                available_at=updated_at,
                claimed_by=None,
                claimed_until=None,
                delivered_at=None,
                last_error_code=None,
                created_at=updated_at,
            )
        )
        return resulting

    def _decide_new(
        self,
        uow: ContentUnitOfWork,
        execution_tenant_id: UUID,
        *,
        principal_id: UUID,
        content_id: ContentId,
        version_id: ContentVersionId,
        expected_aggregate_revision: AggregateRevision,
        decision: ReviewDecisionCode,
        reason_code: str | None,
        comment: str | None,
        target_state: str,
        correlation_id: UUID,
        decided_at: datetime,
    ) -> tuple[ReviewDecision, AggregateRevision]:
        head = uow.contents.get_head_for_update(content_id)
        if head is None or head.tenant_id != execution_tenant_id:
            raise ContentNotFound("Content is not visible in the execution tenant")
        _require_visible_version(uow, content_id, version_id)
        if head.aggregate_revision != expected_aggregate_revision:
            raise AggregateRevisionConflict(
                "expected aggregate_revision does not match stored head"
            )
        _require_current(head, version_id)
        if head.stewardship_state != StewardshipState.IN_REVIEW.value:
            raise ReviewDecisionNotAllowed(
                "review decision is not allowed in the current stewardship state"
            )
        if uow.reviews.get_for_version(content_id, version_id) is not None:
            raise ReviewAlreadyDecided(
                "this ContentVersion already has a terminal ReviewDecision"
            )
        start_intent = uow.workflow_intents.get_start_intent_by_business_key(
            workflow_type=CONTENT_REVIEW_WORKFLOW_TYPE,
            business_key=content_review_business_key(
                content_id=str(content_id),
                version_id=str(version_id),
            ),
        )
        if start_intent is None:
            raise WorkflowCoordinationFailed(
                "review workflow start intent is missing for the review cycle"
            )
        stored = ReviewDecision(
            review_decision_id=ReviewDecisionId.generate(),
            tenant_id=execution_tenant_id,
            content_id=content_id,
            version_id=version_id,
            decision=decision,
            reason_code=reason_code,
            comment=comment,
            reviewer_principal_id=principal_id,
            effective_actor_id=principal_id,
            delegation_id=None,
            decided_at=decided_at,
            correlation_id=correlation_id,
        )
        uow.reviews.insert(stored)
        resulting = uow.contents.transition_stewardship(
            content_id=content_id,
            tenant_id=execution_tenant_id,
            expected_revision=expected_aggregate_revision,
            expected_current_version_id=version_id,
            expected_state=StewardshipState.IN_REVIEW.value,
            target_state=target_state,
            updated_at=decided_at,
        )
        if resulting is None:
            raise AggregateRevisionConflict(
                "aggregate head changed before the review decision could commit"
            )
        command_id = WorkflowCommandId.generate()
        uow.workflow_intents.insert_command_intent(
            WorkflowCommandIntent(
                workflow_command_intent_id=WorkflowCommandIntentId.generate(),
                tenant_id=execution_tenant_id,
                workflow_instance_id=start_intent.workflow_instance_id,
                temporal_workflow_id=start_intent.temporal_workflow_id,
                command_id=command_id,
                command_type=SIGNAL_REVIEW_DECISION_RECORDED,
                business_key=review_decision_command_business_key(
                    str(stored.review_decision_id)
                ),
                payload={
                    "command_id": str(command_id),
                    "workflow_instance_id": str(start_intent.workflow_instance_id),
                    "review_decision_id": str(stored.review_decision_id),
                    "content_id": str(content_id),
                    "version_id": str(version_id),
                    "decision": decision.value,
                    "correlation_id": str(correlation_id),
                },
                status=INTENT_PENDING,
                attempt_count=0,
                available_at=decided_at,
                claimed_by=None,
                claimed_until=None,
                delivered_at=None,
                last_error_code=None,
                created_at=decided_at,
            )
        )
        return stored, resulting

    def _replay_decision(
        self,
        uow: ContentUnitOfWork,
        existing: IdempotencyOutcome,
        frozen_state: str,
    ) -> ReviewDecisionResult:
        if existing.result_review_decision_id is None:
            raise PersistenceInvariantViolation(
                "idempotent review outcome is missing ReviewDecision identity"
            )
        stored = uow.reviews.get(ReviewDecisionId(existing.result_review_decision_id))
        if stored is None:
            raise PersistenceInvariantViolation(
                "idempotent review outcome is not visible"
            )
        return ReviewDecisionResult(
            review_decision_id=stored.review_decision_id,
            content_id=stored.content_id,
            version_id=stored.version_id,
            decision=stored.decision.value,
            reason_code=stored.reason_code,
            comment=stored.comment,
            decided_at=stored.decided_at,
            stewardship_state=frozen_state,
            aggregate_revision=AggregateRevision(existing.result_aggregate_revision),
        )
