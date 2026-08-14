"""GCI-I06 ReviewDecision domain contracts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from uuid import uuid7

import pytest

from aieos.domains.content.domain.errors import (
    InvalidReviewDecisionError,
    ReviewDecisionBindingError,
)
from aieos.domains.content.domain.identities import (
    ContentId,
    ContentVersionId,
    ReviewDecisionId,
)
from aieos.domains.content.domain.review import (
    FROZEN_REVIEW_DECISION_CODES,
    ReviewDecision,
    ReviewDecisionCode,
    parse_review_decision_code,
)
from aieos.domains.content.domain.states import StewardshipState

pytestmark = pytest.mark.gci_i06


def _now() -> datetime:
    return datetime(2026, 8, 14, 12, 0, tzinfo=UTC)


def _decision(**overrides) -> ReviewDecision:
    principal = uuid7()
    values = {
        "review_decision_id": ReviewDecisionId.generate(),
        "tenant_id": uuid7(),
        "content_id": ContentId.generate(),
        "version_id": ContentVersionId.generate(),
        "decision": ReviewDecisionCode.APPROVE,
        "reason_code": None,
        "comment": None,
        "reviewer_principal_id": principal,
        "effective_actor_id": principal,
        "delegation_id": None,
        "decided_at": _now(),
        "correlation_id": uuid7(),
    }
    values.update(overrides)
    return ReviewDecision(**values)


def test_review_decision_id_is_uuidv7() -> None:
    generated = ReviewDecisionId.generate()
    assert generated.value.version == 7
    stored = _decision()
    assert stored.review_decision_id.value.version == 7


def test_exact_decision_vocabulary_only() -> None:
    assert {member.value for member in ReviewDecisionCode} == {
        "APPROVE",
        "REQUEST_CHANGES",
        "REJECT",
    }
    assert {member.value for member in FROZEN_REVIEW_DECISION_CODES} == {
        "APPROVE",
        "REQUEST_CHANGES",
        "REJECT",
    }
    for name in ("PENDING", "CANCELLED", "SUPERSEDED", "REVIEWED", "REJECTED", "ARCHIVED"):
        with pytest.raises(InvalidReviewDecisionError):
            parse_review_decision_code(name)
    stewardship = {member.value for member in StewardshipState}
    assert "REJECTED" not in stewardship


def test_review_decision_is_immutable() -> None:
    decision = _decision()
    with pytest.raises(FrozenInstanceError):
        decision.decision = ReviewDecisionCode.REJECT  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        decision.comment = "mutated"  # type: ignore[misc]


def test_request_changes_requires_non_empty_comment() -> None:
    with pytest.raises(ReviewDecisionBindingError):
        _decision(decision=ReviewDecisionCode.REQUEST_CHANGES, comment=None)
    with pytest.raises(ReviewDecisionBindingError):
        _decision(decision=ReviewDecisionCode.REQUEST_CHANGES, comment="   ")
    stored = _decision(
        decision=ReviewDecisionCode.REQUEST_CHANGES,
        comment="  please revise  ",
    )
    assert stored.comment == "please revise"
    approve = _decision(decision=ReviewDecisionCode.APPROVE, comment=None)
    reject = _decision(decision=ReviewDecisionCode.REJECT, comment=None)
    assert approve.comment is None
    assert reject.comment is None


def test_reason_code_and_comment_bounds() -> None:
    with pytest.raises(ReviewDecisionBindingError):
        _decision(reason_code="Not_Valid")
    with pytest.raises(ReviewDecisionBindingError):
        _decision(reason_code="a" * 65)
    with pytest.raises(ReviewDecisionBindingError):
        _decision(comment="x" * 4001)
    stored = _decision(reason_code="needs.work-1", comment="ok")
    assert stored.reason_code == "needs.work-1"
    naive = datetime(2026, 8, 14, 12, 0)
    with pytest.raises(ReviewDecisionBindingError):
        _decision(decided_at=naive)
