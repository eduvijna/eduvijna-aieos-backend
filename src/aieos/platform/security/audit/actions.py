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


class SecurityAuditExecutionChannel(StrEnum):
    """Historical execution channel. Grants no authority."""

    API = "API"
    WORKFLOW_ACTIVITY = "WORKFLOW_ACTIVITY"
    AI_MATERIALIZATION = "AI_MATERIALIZATION"
    MIGRATION = "MIGRATION"
    SYSTEM = "SYSTEM"


_CREATE_ACTIONS = frozenset({SecurityAuditAction.CONTENT_CREATE})
_MIGRATION_IMPORT_ACTIONS = frozenset({SecurityAuditAction.CONTENT_MIGRATION_IMPORT})
_INCREMENT_ACTIONS = frozenset(
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


def is_create_action(action: SecurityAuditAction) -> bool:
    return action in _CREATE_ACTIONS


def is_migration_import_action(action: SecurityAuditAction) -> bool:
    return action in _MIGRATION_IMPORT_ACTIONS


def is_increment_action(action: SecurityAuditAction) -> bool:
    return action in _INCREMENT_ACTIONS
