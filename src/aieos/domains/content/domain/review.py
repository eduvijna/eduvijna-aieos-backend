"""ReviewDecision domain contract.

A review decision always identifies an exact ContentVersion.
approval(vN) never approves vN+1.
REQUEST_CHANGES and REJECT are review history, not stewardship states.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from aieos.domains.content.domain.errors import (
    InvalidReviewDecisionError,
    ReviewDecisionBindingError,
)
from aieos.domains.content.domain.identities import (
    ContentId,
    ContentVersionId,
    ReviewDecisionId,
    require_foreign_uuid,
)

_REASON_CODE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_MAX_REASON_CODE = 64
_MAX_COMMENT = 4000


class ReviewDecisionCode(StrEnum):
    APPROVE = "APPROVE"
    REQUEST_CHANGES = "REQUEST_CHANGES"
    REJECT = "REJECT"


FROZEN_REVIEW_DECISION_CODES: frozenset[ReviewDecisionCode] = frozenset(ReviewDecisionCode)


def parse_review_decision_code(value: str | ReviewDecisionCode) -> ReviewDecisionCode:
    if isinstance(value, ReviewDecisionCode):
        return value
    try:
        return ReviewDecisionCode(value)
    except ValueError as exc:
        raise InvalidReviewDecisionError(
            f"unknown review decision {value!r}; "
            f"allowed={sorted(c.value for c in ReviewDecisionCode)}"
        ) from exc


def normalize_reason_code(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ReviewDecisionBindingError("reason_code must be a string when provided")
    code = value.strip()
    if not code:
        return None
    if len(code) > _MAX_REASON_CODE or _REASON_CODE.fullmatch(code) is None:
        raise ReviewDecisionBindingError("reason_code is not a bounded stable code")
    return code


def normalize_review_comment(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ReviewDecisionBindingError("comment must be a string when provided")
    comment = value.strip()
    if not comment:
        return None
    if len(comment) > _MAX_COMMENT:
        raise ReviewDecisionBindingError("comment exceeds the bounded length")
    return comment


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    """Immutable review decision bound to one ContentVersion."""

    review_decision_id: ReviewDecisionId
    tenant_id: UUID
    content_id: ContentId
    version_id: ContentVersionId
    decision: ReviewDecisionCode
    reason_code: str | None
    comment: str | None
    reviewer_principal_id: UUID
    effective_actor_id: UUID
    delegation_id: UUID | None
    decided_at: datetime
    correlation_id: UUID

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision", parse_review_decision_code(self.decision))
        require_foreign_uuid(self.tenant_id, label="tenant_id")
        require_foreign_uuid(self.reviewer_principal_id, label="reviewer_principal_id")
        require_foreign_uuid(self.effective_actor_id, label="effective_actor_id")
        require_foreign_uuid(self.correlation_id, label="correlation_id")
        if self.delegation_id is not None:
            require_foreign_uuid(self.delegation_id, label="delegation_id")
        if self.version_id is None:
            raise ReviewDecisionBindingError("review decision requires an exact version_id")
        if self.decided_at.tzinfo is None or self.decided_at.utcoffset() is None:
            raise ReviewDecisionBindingError("decided_at must be timezone-aware")
        object.__setattr__(self, "reason_code", normalize_reason_code(self.reason_code))
        object.__setattr__(self, "comment", normalize_review_comment(self.comment))
        if self.decision is ReviewDecisionCode.REQUEST_CHANGES and self.comment is None:
            raise ReviewDecisionBindingError(
                "REQUEST_CHANGES requires a non-empty comment"
            )

    def applies_to(self, version_id: ContentVersionId) -> bool:
        """Approval/feedback never transfers between versions."""
        return self.version_id == version_id

    def closes_review_of(self, version_id: ContentVersionId) -> bool:
        """REQUEST_CHANGES and REJECT close review of that immutable version."""
        if not self.applies_to(version_id):
            return False
        return self.decision in {
            ReviewDecisionCode.REQUEST_CHANGES,
            ReviewDecisionCode.REJECT,
            ReviewDecisionCode.APPROVE,
        }
