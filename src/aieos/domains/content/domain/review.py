"""ReviewDecision domain contract.

A review decision always identifies an exact ContentVersion.
approval(vN) never approves vN+1.
REQUEST_CHANGES and REJECT are review history, not stewardship states.
"""

from __future__ import annotations

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


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    """Immutable review decision bound to one ContentVersion."""

    review_decision_id: ReviewDecisionId
    tenant_id: UUID
    content_id: ContentId
    version_id: ContentVersionId
    decision: ReviewDecisionCode
    actor_principal_id: UUID
    decided_at: datetime
    correlation_id: UUID | None
    comment: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision", parse_review_decision_code(self.decision))
        require_foreign_uuid(self.tenant_id, label="tenant_id")
        require_foreign_uuid(self.actor_principal_id, label="actor_principal_id")
        if self.correlation_id is not None:
            require_foreign_uuid(self.correlation_id, label="correlation_id")
        if self.version_id is None:
            raise ReviewDecisionBindingError("review decision requires an exact version_id")
        if self.decided_at.tzinfo is None or self.decided_at.utcoffset() is None:
            raise ReviewDecisionBindingError("decided_at must be timezone-aware")
        if self.comment is not None and not isinstance(self.comment, str):
            raise ReviewDecisionBindingError("comment must be a string when provided")

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
