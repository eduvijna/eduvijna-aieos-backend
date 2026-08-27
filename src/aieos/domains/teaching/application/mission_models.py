"""Today's Mission projection contracts.

Mission is a *derived projection*. There is no mission aggregate, no mission
table, and no mission persistence. Every field below is recomputed on read
from the Review Queue projection and durable Teaching Work rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from aieos.domains.teaching.domain.identities import WorkId


class HeroActionKind(StrEnum):
    REVIEW = "review"
    CONTINUE_WORK = "continue_work"
    PREPARE_TOMORROW = "prepare_tomorrow"


@dataclass(frozen=True, slots=True)
class ReviewProjection:
    """Derived from the Teacher OS Review Queue. Counts only, no queue copy."""

    pending_count: int


@dataclass(frozen=True, slots=True)
class ContinueWorkSummary:
    """The Teaching Work the teacher is most likely to resume."""

    work_id: WorkId
    intent_type: str
    goal_text: str
    class_label: str | None
    subject: str | None
    topic: str | None
    target_date: date
    aggregate_revision: int
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PreparationProjection:
    """Derived from durable teaching.works rows owned by this teacher."""

    active_work_count: int
    continue_work: ContinueWorkSummary | None


@dataclass(frozen=True, slots=True)
class HeroAction:
    """The single highest-priority next action for the educational day."""

    kind: HeroActionKind
    work_id: WorkId | None = None

    @property
    def kind_value(self) -> str:
        return self.kind.value


@dataclass(frozen=True, slots=True)
class TeacherOsMission:
    """Today's Mission projection for one teacher on one local educational day."""

    mission_date: date
    review: ReviewProjection
    preparation: PreparationProjection
    hero_action: HeroAction
