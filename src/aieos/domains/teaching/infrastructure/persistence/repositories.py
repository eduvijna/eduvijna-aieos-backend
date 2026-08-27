"""SQLAlchemy Core Teaching repositories. They never commit or rollback."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.engine import Connection

from aieos.domains.teaching.application.errors import PersistenceInvariantViolation
from aieos.domains.teaching.domain.identities import AggregateRevision, WorkId
from aieos.domains.teaching.domain.work import TeachingWork
from aieos.domains.teaching.infrastructure.persistence.errors import (
    reraise_as_application_error,
)
from aieos.domains.teaching.infrastructure.persistence.models import works_table


def teaching_work_from_row(row) -> TeachingWork:
    try:
        return TeachingWork(
            work_id=WorkId(row["work_id"]),
            tenant_id=row["tenant_id"],
            teacher_principal_id=row["teacher_principal_id"],
            intent_type=row["intent_type"],
            goal_text=row["goal_text"],
            class_label=row["class_label"],
            subject=row["subject"],
            topic=row["topic"],
            target_date=row["target_date"],
            locale=row["locale"],
            aggregate_revision=AggregateRevision(int(row["aggregate_revision"])),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            archived_at=row["archived_at"],
        )
    except Exception as exc:
        raise PersistenceInvariantViolation(
            "stored TeachingWork row violates the aggregate contract"
        ) from exc


class SqlAlchemyTeachingWorkRepository:
    def __init__(self, connection: Connection, execution_tenant_id: UUID) -> None:
        self._connection = connection
        self._execution_tenant_id = execution_tenant_id

    def insert(self, work: TeachingWork) -> None:
        try:
            self._connection.execute(
                works_table.insert().values(
                    work_id=work.work_id.value,
                    tenant_id=work.tenant_id,
                    teacher_principal_id=work.teacher_principal_id,
                    intent_type=work.intent_type.value,
                    goal_text=work.goal_text,
                    class_label=work.class_label,
                    subject=work.subject,
                    topic=work.topic,
                    target_date=work.target_date,
                    locale=work.locale,
                    aggregate_revision=int(work.aggregate_revision),
                    created_at=work.created_at,
                    updated_at=work.updated_at,
                    archived_at=work.archived_at,
                )
            )
        except Exception as exc:
            reraise_as_application_error(exc)

    def get(self, work_id: WorkId) -> TeachingWork | None:
        try:
            row = (
                self._connection.execute(
                    select(works_table).where(
                        works_table.c.work_id == work_id.value,
                        works_table.c.tenant_id == self._execution_tenant_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
        except Exception as exc:
            reraise_as_application_error(exc)
        if row is None:
            return None
        return teaching_work_from_row(row)

    def get_for_update(self, work_id: WorkId) -> TeachingWork | None:
        try:
            row = (
                self._connection.execute(
                    select(works_table)
                    .where(
                        works_table.c.work_id == work_id.value,
                        works_table.c.tenant_id == self._execution_tenant_id,
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
        except Exception as exc:
            reraise_as_application_error(exc)
        if row is None:
            return None
        return teaching_work_from_row(row)

    def update(
        self,
        work: TeachingWork,
        *,
        expected_revision: AggregateRevision,
    ) -> bool:
        """Compare-and-set on aggregate_revision. False means a lost race."""
        try:
            result = self._connection.execute(
                update(works_table)
                .where(
                    works_table.c.work_id == work.work_id.value,
                    works_table.c.tenant_id == self._execution_tenant_id,
                    works_table.c.aggregate_revision == int(expected_revision),
                )
                .values(
                    goal_text=work.goal_text,
                    class_label=work.class_label,
                    subject=work.subject,
                    topic=work.topic,
                    target_date=work.target_date,
                    locale=work.locale,
                    aggregate_revision=int(work.aggregate_revision),
                    updated_at=work.updated_at,
                )
            )
        except Exception as exc:
            reraise_as_application_error(exc)
        return result.rowcount == 1

    def list_for_teacher(
        self,
        *,
        teacher_principal_id: UUID,
        limit: int,
        include_archived: bool,
    ) -> list[TeachingWork]:
        statement = select(works_table).where(
            works_table.c.tenant_id == self._execution_tenant_id,
            works_table.c.teacher_principal_id == teacher_principal_id,
        )
        if not include_archived:
            statement = statement.where(works_table.c.archived_at.is_(None))
        statement = statement.order_by(
            works_table.c.updated_at.desc(), works_table.c.work_id.desc()
        ).limit(limit)
        try:
            rows = self._connection.execute(statement).mappings().all()
        except Exception as exc:
            reraise_as_application_error(exc)
        return [teaching_work_from_row(row) for row in rows]

    def most_recently_updated_for_teacher(
        self,
        *,
        teacher_principal_id: UUID,
    ) -> TeachingWork | None:
        rows = self.list_for_teacher(
            teacher_principal_id=teacher_principal_id,
            limit=1,
            include_archived=False,
        )
        return rows[0] if rows else None

    def count_active_for_teacher(self, *, teacher_principal_id: UUID) -> int:
        try:
            value = self._connection.execute(
                select(func.count())
                .select_from(works_table)
                .where(
                    works_table.c.tenant_id == self._execution_tenant_id,
                    works_table.c.teacher_principal_id == teacher_principal_id,
                    works_table.c.archived_at.is_(None),
                )
            ).scalar_one()
        except Exception as exc:
            reraise_as_application_error(exc)
        return int(value)
