"""Compose the Today's Mission projection.

Mission is derived on every read. Nothing here writes, caches, or persists a
mission row. Removing every Mission code path would lose no durable state.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from aieos.domains.teaching.application.errors import InvalidTeachingWorkRequest
from aieos.domains.teaching.application.mission_models import (
    ContinueWorkSummary,
    HeroAction,
    HeroActionKind,
    PreparationProjection,
    ReviewProjection,
    TeacherOsMission,
)
from aieos.domains.teaching.application.ports import (
    ReviewQueuePendingCountPort,
    TeachingUnitOfWorkFactory,
)
from aieos.domains.teaching.domain.work import TeachingWork


def _continue_work_summary(work: TeachingWork) -> ContinueWorkSummary:
    return ContinueWorkSummary(
        work_id=work.work_id,
        intent_type=work.intent_type.value,
        goal_text=work.goal_text,
        class_label=work.class_label,
        subject=work.subject,
        topic=work.topic,
        target_date=work.target_date,
        aggregate_revision=int(work.aggregate_revision),
        updated_at=work.updated_at,
    )


class GetTeacherOsTodayMissionService:
    """Derive one teacher's mission for one local educational day."""

    def __init__(
        self,
        uow_factory: TeachingUnitOfWorkFactory,
        review_queue_pending_count: ReviewQueuePendingCountPort,
    ) -> None:
        self._uow_factory = uow_factory
        self._review_queue_pending_count = review_queue_pending_count

    def get(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        *,
        mission_date: date,
    ) -> TeacherOsMission:
        """Build the mission for the caller-supplied local educational day.

        DEV02 temporary behaviour: mission_date is supplied by the client as a
        validated calendar date because no teacher time-zone System of Record
        exists yet. Once teacher time zones are governed, the server derives
        the local educational day and this input is removed.
        """
        if isinstance(mission_date, bool) or not isinstance(mission_date, date):
            raise InvalidTeachingWorkRequest("mission_date must be a calendar date")

        pending = self._review_queue_pending_count.pending_count(execution_tenant_id)
        if pending < 0:
            raise InvalidTeachingWorkRequest("pending review count must not be negative")

        with self._uow_factory(execution_tenant_id) as uow:
            active_count = uow.works.count_active_for_teacher(
                teacher_principal_id=principal_id
            )
            candidate = uow.works.most_recently_updated_for_teacher(
                teacher_principal_id=principal_id
            )

        continue_work = None if candidate is None else _continue_work_summary(candidate)

        if pending > 0:
            hero = HeroAction(kind=HeroActionKind.REVIEW)
        elif continue_work is not None:
            hero = HeroAction(
                kind=HeroActionKind.CONTINUE_WORK, work_id=continue_work.work_id
            )
        else:
            hero = HeroAction(kind=HeroActionKind.PREPARE_TOMORROW)

        return TeacherOsMission(
            mission_date=mission_date,
            review=ReviewProjection(pending_count=pending),
            preparation=PreparationProjection(
                active_work_count=active_count,
                continue_work=continue_work,
            ),
            hero_action=hero,
        )
