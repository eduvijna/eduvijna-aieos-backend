"""TeachingWork aggregate contract.

TeachingWork is the durable teacher-owned preparation container. It is created
*from* a Teaching Intent request; the intent itself is never persisted as its
own aggregate. Nothing here generates content or calls an AI model.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final
from uuid import UUID

from aieos.domains.teaching.domain.errors import InvalidTeachingWorkError
from aieos.domains.teaching.domain.identities import (
    AggregateRevision,
    WorkId,
    require_foreign_uuid,
)
from aieos.domains.teaching.domain.intent_type import IntentType, parse_intent_type


class UnsetType:
    """Sentinel distinguishing "field omitted" from "field explicitly cleared"."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "UNSET"


UNSET: Final = UnsetType()

MAX_GOAL_TEXT_LENGTH: Final = 2000
MAX_LABEL_LENGTH: Final = 255


def _require_aware(value: datetime, *, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise InvalidTeachingWorkError(f"{label} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidTeachingWorkError(f"{label} must be timezone-aware")
    return value


def _require_text(value: str, *, label: str, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidTeachingWorkError(f"{label} must be a non-empty string")
    stripped = value.strip()
    if len(stripped) > max_length:
        raise InvalidTeachingWorkError(
            f"{label} must be at most {max_length} characters"
        )
    return stripped


def _optional_text(value: str | None, *, label: str, max_length: int) -> str | None:
    if value is None:
        return None
    return _require_text(value, label=label, max_length=max_length)


def _require_target_date(value: date, *, label: str = "target_date") -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise InvalidTeachingWorkError(f"{label} must be a calendar date")
    return value


@dataclass(frozen=True, slots=True)
class TeachingWork:
    """Durable Teaching Work snapshot.

    class_label is contextual free text captured from the teacher (for example
    "Grade 5B"). It is NOT a foreign key into any Class System of Record and
    must never be treated as one.
    """

    work_id: WorkId
    tenant_id: UUID
    teacher_principal_id: UUID
    intent_type: IntentType
    goal_text: str
    class_label: str | None
    subject: str | None
    topic: str | None
    target_date: date
    locale: str
    aggregate_revision: AggregateRevision
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None

    def __post_init__(self) -> None:
        set_ = object.__setattr__
        set_(self, "intent_type", parse_intent_type(self.intent_type))
        set_(
            self,
            "goal_text",
            _require_text(
                self.goal_text, label="goal_text", max_length=MAX_GOAL_TEXT_LENGTH
            ),
        )
        set_(
            self,
            "class_label",
            _optional_text(
                self.class_label, label="class_label", max_length=MAX_LABEL_LENGTH
            ),
        )
        set_(
            self,
            "subject",
            _optional_text(self.subject, label="subject", max_length=MAX_LABEL_LENGTH),
        )
        set_(
            self,
            "topic",
            _optional_text(self.topic, label="topic", max_length=MAX_LABEL_LENGTH),
        )
        set_(
            self,
            "locale",
            _require_text(self.locale, label="locale", max_length=MAX_LABEL_LENGTH),
        )
        set_(self, "target_date", _require_target_date(self.target_date))
        require_foreign_uuid(self.tenant_id, label="tenant_id")
        require_foreign_uuid(self.teacher_principal_id, label="teacher_principal_id")
        if not isinstance(self.work_id, WorkId):
            raise InvalidTeachingWorkError("work_id must be a WorkId")
        if not isinstance(self.aggregate_revision, AggregateRevision):
            raise InvalidTeachingWorkError(
                "aggregate_revision must be an AggregateRevision"
            )
        _require_aware(self.created_at, label="created_at")
        _require_aware(self.updated_at, label="updated_at")
        if self.archived_at is not None:
            _require_aware(self.archived_at, label="archived_at")

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    @classmethod
    def create_from_intent(
        cls,
        *,
        tenant_id: UUID,
        teacher_principal_id: UUID,
        intent_type: IntentType | str,
        goal_text: str,
        target_date: date,
        locale: str,
        created_at: datetime,
        class_label: str | None = None,
        subject: str | None = None,
        topic: str | None = None,
        work_id: WorkId | None = None,
    ) -> TeachingWork:
        """Materialize a durable Work from a transient Teaching Intent request."""
        return cls(
            work_id=WorkId.generate() if work_id is None else work_id,
            tenant_id=tenant_id,
            teacher_principal_id=teacher_principal_id,
            intent_type=intent_type,
            goal_text=goal_text,
            class_label=class_label,
            subject=subject,
            topic=topic,
            target_date=target_date,
            locale=locale,
            aggregate_revision=AggregateRevision(0),
            created_at=created_at,
            updated_at=created_at,
            archived_at=None,
        )

    def refine(
        self,
        *,
        updated_at: datetime,
        goal_text: str | UnsetType = UNSET,
        class_label: str | None | UnsetType = UNSET,
        subject: str | None | UnsetType = UNSET,
        topic: str | None | UnsetType = UNSET,
        target_date: date | UnsetType = UNSET,
        locale: str | UnsetType = UNSET,
    ) -> TeachingWork:
        """Return the refined Work with the next aggregate revision.

        Only teacher-editable preparation fields may be refined. Identity,
        ownership, intent_type, and created_at are immutable after creation.
        """
        if self.is_archived:
            raise InvalidTeachingWorkError("an archived TeachingWork cannot be refined")
        _require_aware(updated_at, label="updated_at")
        changes: dict[str, Any] = {
            "aggregate_revision": self.aggregate_revision.next(),
            "updated_at": updated_at,
        }
        for field, value in (
            ("goal_text", goal_text),
            ("class_label", class_label),
            ("subject", subject),
            ("topic", topic),
            ("target_date", target_date),
            ("locale", locale),
        ):
            if not isinstance(value, UnsetType):
                changes[field] = value
        return dataclasses.replace(self, **changes)
