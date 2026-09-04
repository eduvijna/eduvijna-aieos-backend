"""Closed security mutation-audit action and execution-channel vocabularies."""

from __future__ import annotations

from enum import StrEnum


class SecurityAuditAction(StrEnum):
    """Typed committed-mutation audit actions. Not arbitrary caller text."""

    CONTENT_CREATE = "content.create"
    CONTENT_VERSION_CREATE = "content.version.create"
    CONTENT_REVIEW_SUBMIT = "content.review.submit"
    CONTENT_REVIEW_APPROVE = "content.review.approve"
    CONTENT_REVIEW_REQUEST_CHANGES = "content.review.request_changes"
    CONTENT_REVIEW_REJECT = "content.review.reject"
    CONTENT_PUBLISH = "content.publish"
    CONTENT_AI_MATERIALIZE = "content.ai.materialize"
    CONTENT_MIGRATION_IMPORT = "content.migration.import"
    ASSET_CREATE = "asset.create"
    ASSET_REVISION_REGISTER = "asset.revision.register"
    ASSET_REVISION_ACTIVATE = "asset.revision.activate"
    ASSET_LIFECYCLE_WITHDRAW = "asset.lifecycle.withdraw"
    ASSET_LIFECYCLE_RESTORE = "asset.lifecycle.restore"
    ASSET_LIFECYCLE_DELETE = "asset.lifecycle.delete"
    ASSET_QUARANTINE_SET = "asset.quarantine.set"
    ASSET_QUARANTINE_CLEAR = "asset.quarantine.clear"
    ASSET_SAFETY_PASS = "asset.safety.pass"
    ASSET_SAFETY_FAIL = "asset.safety.fail"
    TEACHING_ASSIGNMENT_CREATE = "teaching.assignment.create"
    TEACHING_ASSIGNMENT_DUE_UPDATE = "teaching.assignment.due_update"
    TEACHING_ASSIGNMENT_CLOSE = "teaching.assignment.close"
    TEACHING_ASSIGNMENT_CANCEL = "teaching.assignment.cancel"
    TEACHING_EXECUTION_START = "teaching.execution.start"
    TEACHING_EXECUTION_COMPLETE = "teaching.execution.complete"
    TEACHING_EXECUTION_CANCEL = "teaching.execution.cancel"
    TEACHING_EXECUTION_OBSERVATION_CREATE = (
        "teaching.execution.observation.create"
    )
    TEACHING_EXECUTION_OBSERVATION_CORRECT = (
        "teaching.execution.observation.correct"
    )
    ASSESSMENT_CLASSROOM_RECORD = "assessment.classroom.record"
    ASSESSMENT_CLASSROOM_CORRECT = "assessment.classroom.correct"
    ASSESSMENT_CLASSROOM_VOID = "assessment.classroom.void"


class SecurityAuditExecutionChannel(StrEnum):
    """Historical execution channel. Grants no authority."""

    API = "API"
    WORKFLOW_ACTIVITY = "WORKFLOW_ACTIVITY"
    AI_MATERIALIZATION = "AI_MATERIALIZATION"
    MIGRATION = "MIGRATION"
    SYSTEM = "SYSTEM"


_CONTENT_CREATE_ACTIONS = frozenset({SecurityAuditAction.CONTENT_CREATE})
_CONTENT_MIGRATION_IMPORT_ACTIONS = frozenset(
    {SecurityAuditAction.CONTENT_MIGRATION_IMPORT}
)
_CONTENT_INCREMENT_ACTIONS = frozenset(
    {
        SecurityAuditAction.CONTENT_VERSION_CREATE,
        SecurityAuditAction.CONTENT_REVIEW_SUBMIT,
        SecurityAuditAction.CONTENT_REVIEW_APPROVE,
        SecurityAuditAction.CONTENT_REVIEW_REQUEST_CHANGES,
        SecurityAuditAction.CONTENT_REVIEW_REJECT,
        SecurityAuditAction.CONTENT_PUBLISH,
        SecurityAuditAction.CONTENT_AI_MATERIALIZE,
    }
)
_ASSET_CREATE_ACTIONS = frozenset({SecurityAuditAction.ASSET_CREATE})
_ASSET_STABLE_REGISTRATION_ACTIONS = frozenset(
    {SecurityAuditAction.ASSET_REVISION_REGISTER}
)
_ASSET_INCREMENT_ACTIONS = frozenset(
    {
        SecurityAuditAction.ASSET_REVISION_ACTIVATE,
        SecurityAuditAction.ASSET_LIFECYCLE_WITHDRAW,
        SecurityAuditAction.ASSET_LIFECYCLE_RESTORE,
        SecurityAuditAction.ASSET_LIFECYCLE_DELETE,
        SecurityAuditAction.ASSET_QUARANTINE_SET,
        SecurityAuditAction.ASSET_QUARANTINE_CLEAR,
        SecurityAuditAction.ASSET_SAFETY_PASS,
        SecurityAuditAction.ASSET_SAFETY_FAIL,
    }
)
_TEACHING_CREATE_ACTIONS = frozenset(
    {
        SecurityAuditAction.TEACHING_ASSIGNMENT_CREATE,
        SecurityAuditAction.TEACHING_EXECUTION_START,
        SecurityAuditAction.TEACHING_EXECUTION_OBSERVATION_CREATE,
    }
)
_TEACHING_INCREMENT_ACTIONS = frozenset(
    {
        SecurityAuditAction.TEACHING_ASSIGNMENT_DUE_UPDATE,
        SecurityAuditAction.TEACHING_ASSIGNMENT_CLOSE,
        SecurityAuditAction.TEACHING_ASSIGNMENT_CANCEL,
        SecurityAuditAction.TEACHING_EXECUTION_COMPLETE,
        SecurityAuditAction.TEACHING_EXECUTION_CANCEL,
        SecurityAuditAction.TEACHING_EXECUTION_OBSERVATION_CORRECT,
    }
)
_ASSESSMENT_CREATE_ACTIONS = frozenset(
    {SecurityAuditAction.ASSESSMENT_CLASSROOM_RECORD}
)
_ASSESSMENT_INCREMENT_ACTIONS = frozenset(
    {
        SecurityAuditAction.ASSESSMENT_CLASSROOM_CORRECT,
        SecurityAuditAction.ASSESSMENT_CLASSROOM_VOID,
    }
)


def is_content_create_action(action: SecurityAuditAction) -> bool:
    return action in _CONTENT_CREATE_ACTIONS


def is_content_migration_import_action(action: SecurityAuditAction) -> bool:
    return action in _CONTENT_MIGRATION_IMPORT_ACTIONS


def is_content_increment_action(action: SecurityAuditAction) -> bool:
    return action in _CONTENT_INCREMENT_ACTIONS


def is_asset_create_action(action: SecurityAuditAction) -> bool:
    return action in _ASSET_CREATE_ACTIONS


def is_asset_stable_registration_action(action: SecurityAuditAction) -> bool:
    return action in _ASSET_STABLE_REGISTRATION_ACTIONS


def is_asset_increment_action(action: SecurityAuditAction) -> bool:
    return action in _ASSET_INCREMENT_ACTIONS


def is_asset_audit_action(action: SecurityAuditAction) -> bool:
    return (
        is_asset_create_action(action)
        or is_asset_stable_registration_action(action)
        or is_asset_increment_action(action)
    )


def is_content_audit_action(action: SecurityAuditAction) -> bool:
    return (
        is_content_create_action(action)
        or is_content_migration_import_action(action)
        or is_content_increment_action(action)
    )


def is_teaching_create_action(action: SecurityAuditAction) -> bool:
    return action in _TEACHING_CREATE_ACTIONS


def is_teaching_increment_action(action: SecurityAuditAction) -> bool:
    return action in _TEACHING_INCREMENT_ACTIONS


def is_teaching_audit_action(action: SecurityAuditAction) -> bool:
    return is_teaching_create_action(action) or is_teaching_increment_action(action)


def is_assessment_create_action(action: SecurityAuditAction) -> bool:
    return action in _ASSESSMENT_CREATE_ACTIONS


def is_assessment_increment_action(action: SecurityAuditAction) -> bool:
    return action in _ASSESSMENT_INCREMENT_ACTIONS


def is_assessment_audit_action(action: SecurityAuditAction) -> bool:
    return is_assessment_create_action(action) or is_assessment_increment_action(
        action
    )


def is_create_action(action: SecurityAuditAction) -> bool:
    """Content create family. Kept for existing Content callers."""
    return is_content_create_action(action)


def is_migration_import_action(action: SecurityAuditAction) -> bool:
    """Content migration-import family. Kept for existing Content callers."""
    return is_content_migration_import_action(action)


def is_increment_action(action: SecurityAuditAction) -> bool:
    """Content increment family. Kept for existing Content callers."""
    return is_content_increment_action(action)
