"""SQLAlchemy Core Teaching repositories. They never commit or rollback."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.engine import Connection

from aieos.domains.teaching.application.errors import PersistenceInvariantViolation
from aieos.domains.teaching.domain.assignment import TeachingAssignment
from aieos.domains.teaching.domain.execution import TeachingExecution
from aieos.domains.teaching.domain.execution_content_binding import (
    TeachingExecutionContentBinding,
)
from aieos.domains.teaching.domain.execution_lifecycle import ExecutionLifecycleState
from aieos.domains.teaching.domain.execution_observation import (
    TeachingExecutionObservation,
)
from aieos.domains.teaching.domain.identities import (
    AggregateRevision,
    AssignmentId,
    ExecutionId,
    ObservationId,
    ObservationRevision,
    WorkId,
)
from aieos.domains.teaching.domain.work import TeachingWork
from aieos.domains.teaching.infrastructure.persistence.errors import (
    reraise_as_application_error,
)
from aieos.domains.teaching.infrastructure.persistence.models import (
    assignments_table,
    execution_content_bindings_table,
    execution_observations_table,
    executions_table,
    works_table,
)


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


def teaching_assignment_from_row(row) -> TeachingAssignment:
    try:
        source_work_id = row["source_work_id"]
        return TeachingAssignment(
            assignment_id=AssignmentId(row["assignment_id"]),
            tenant_id=row["tenant_id"],
            teacher_principal_id=row["teacher_principal_id"],
            content_id=row["content_id"],
            content_version_id=row["content_version_id"],
            audience_type=row["audience_type"],
            class_ref=row["class_ref"],
            audience_display_label=row["audience_display_label"],
            source_work_id=None if source_work_id is None else WorkId(source_work_id),
            lifecycle_state=row["lifecycle_state"],
            assigned_at=row["assigned_at"],
            available_from=row["available_from"],
            due_at=row["due_at"],
            closed_at=row["closed_at"],
            cancelled_at=row["cancelled_at"],
            aggregate_revision=AggregateRevision(int(row["aggregate_revision"])),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
    except Exception as exc:
        raise PersistenceInvariantViolation(
            "stored TeachingAssignment row violates the aggregate contract"
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


class SqlAlchemyTeachingAssignmentRepository:
    def __init__(self, connection: Connection, execution_tenant_id: UUID) -> None:
        self._connection = connection
        self._execution_tenant_id = execution_tenant_id

    def insert(self, assignment: TeachingAssignment) -> None:
        try:
            self._connection.execute(
                assignments_table.insert().values(
                    assignment_id=assignment.assignment_id.value,
                    tenant_id=assignment.tenant_id,
                    teacher_principal_id=assignment.teacher_principal_id,
                    content_id=assignment.content_id,
                    content_version_id=assignment.content_version_id,
                    audience_type=assignment.audience_type.value,
                    class_ref=assignment.class_ref,
                    audience_display_label=assignment.audience_display_label,
                    source_work_id=(
                        None
                        if assignment.source_work_id is None
                        else assignment.source_work_id.value
                    ),
                    lifecycle_state=assignment.lifecycle_state.value,
                    assigned_at=assignment.assigned_at,
                    available_from=assignment.available_from,
                    due_at=assignment.due_at,
                    closed_at=assignment.closed_at,
                    cancelled_at=assignment.cancelled_at,
                    aggregate_revision=int(assignment.aggregate_revision),
                    created_at=assignment.created_at,
                    updated_at=assignment.updated_at,
                )
            )
        except Exception as exc:
            reraise_as_application_error(exc)

    def get(self, assignment_id: AssignmentId) -> TeachingAssignment | None:
        try:
            row = (
                self._connection.execute(
                    select(assignments_table).where(
                        assignments_table.c.assignment_id == assignment_id.value,
                        assignments_table.c.tenant_id == self._execution_tenant_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
        except Exception as exc:
            reraise_as_application_error(exc)
        if row is None:
            return None
        return teaching_assignment_from_row(row)

    def get_for_update(
        self, assignment_id: AssignmentId
    ) -> TeachingAssignment | None:
        try:
            row = (
                self._connection.execute(
                    select(assignments_table)
                    .where(
                        assignments_table.c.assignment_id == assignment_id.value,
                        assignments_table.c.tenant_id == self._execution_tenant_id,
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
        return teaching_assignment_from_row(row)

    def update(
        self,
        assignment: TeachingAssignment,
        *,
        expected_revision: AggregateRevision,
    ) -> bool:
        """Compare-and-set mutable assignment state. False means a lost race."""
        try:
            result = self._connection.execute(
                update(assignments_table)
                .where(
                    assignments_table.c.assignment_id == assignment.assignment_id.value,
                    assignments_table.c.tenant_id == self._execution_tenant_id,
                    assignments_table.c.aggregate_revision == int(expected_revision),
                )
                .values(
                    due_at=assignment.due_at,
                    lifecycle_state=assignment.lifecycle_state.value,
                    closed_at=assignment.closed_at,
                    cancelled_at=assignment.cancelled_at,
                    aggregate_revision=int(assignment.aggregate_revision),
                    updated_at=assignment.updated_at,
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
        lifecycle_state: str | None = None,
    ) -> list[TeachingAssignment]:
        statement = select(assignments_table).where(
            assignments_table.c.tenant_id == self._execution_tenant_id,
            assignments_table.c.teacher_principal_id == teacher_principal_id,
        )
        if lifecycle_state is not None:
            statement = statement.where(
                assignments_table.c.lifecycle_state == lifecycle_state
            )
        statement = statement.order_by(
            assignments_table.c.updated_at.desc(),
            assignments_table.c.assignment_id.desc(),
        ).limit(limit)
        try:
            rows = self._connection.execute(statement).mappings().all()
        except Exception as exc:
            reraise_as_application_error(exc)
        return [teaching_assignment_from_row(row) for row in rows]


def teaching_execution_from_row(
    row,
    bindings: tuple[TeachingExecutionContentBinding, ...] = (),
) -> TeachingExecution:
    try:
        return TeachingExecution(
            execution_id=ExecutionId(row["execution_id"]),
            tenant_id=row["tenant_id"],
            teacher_principal_id=row["teacher_principal_id"],
            work_id=WorkId(row["work_id"]),
            class_ref=row["class_ref"],
            lifecycle_state=row["lifecycle_state"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            cancelled_at=row["cancelled_at"],
            aggregate_revision=AggregateRevision(int(row["aggregate_revision"])),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            bindings=bindings,
        )
    except Exception as exc:
        raise PersistenceInvariantViolation(
            "stored TeachingExecution row violates the aggregate contract"
        ) from exc


def teaching_execution_binding_from_row(row) -> TeachingExecutionContentBinding:
    try:
        return TeachingExecutionContentBinding(
            execution_id=ExecutionId(row["execution_id"]),
            content_id=row["content_id"],
            content_version_id=row["content_version_id"],
            artifact_kind=row["artifact_kind"],
        )
    except Exception as exc:
        raise PersistenceInvariantViolation(
            "stored TeachingExecutionContentBinding row violates the contract"
        ) from exc


def teaching_execution_observation_from_row(row) -> TeachingExecutionObservation:
    try:
        return TeachingExecutionObservation(
            observation_id=ObservationId(row["observation_id"]),
            execution_id=ExecutionId(row["execution_id"]),
            observation_kind=row["observation_kind"],
            body=row["body"],
            recorded_at=row["recorded_at"],
            updated_at=row["updated_at"],
            revision=ObservationRevision(int(row["revision"])),
        )
    except Exception as exc:
        raise PersistenceInvariantViolation(
            "stored TeachingExecutionObservation row violates the contract"
        ) from exc


class SqlAlchemyTeachingExecutionRepository:
    def __init__(self, connection: Connection, execution_tenant_id: UUID) -> None:
        self._connection = connection
        self._execution_tenant_id = execution_tenant_id

    def insert(self, execution: TeachingExecution) -> None:
        try:
            self._connection.execute(
                executions_table.insert().values(
                    execution_id=execution.execution_id.value,
                    tenant_id=execution.tenant_id,
                    teacher_principal_id=execution.teacher_principal_id,
                    work_id=execution.work_id.value,
                    class_ref=execution.class_ref,
                    lifecycle_state=execution.lifecycle_state.value,
                    started_at=execution.started_at,
                    completed_at=execution.completed_at,
                    cancelled_at=execution.cancelled_at,
                    aggregate_revision=int(execution.aggregate_revision),
                    created_at=execution.created_at,
                    updated_at=execution.updated_at,
                )
            )
            for binding in execution.bindings:
                self._connection.execute(
                    execution_content_bindings_table.insert().values(
                        tenant_id=execution.tenant_id,
                        execution_id=binding.execution_id.value,
                        content_id=binding.content_id,
                        content_version_id=binding.content_version_id,
                        artifact_kind=binding.artifact_kind,
                    )
                )
        except Exception as exc:
            reraise_as_application_error(exc)

    def _load_bindings(
        self, execution_id: ExecutionId
    ) -> tuple[TeachingExecutionContentBinding, ...]:
        rows = (
            self._connection.execute(
                select(execution_content_bindings_table)
                .where(
                    execution_content_bindings_table.c.execution_id
                    == execution_id.value,
                    execution_content_bindings_table.c.tenant_id
                    == self._execution_tenant_id,
                )
                .order_by(
                    execution_content_bindings_table.c.content_id,
                    execution_content_bindings_table.c.content_version_id,
                )
            )
            .mappings()
            .all()
        )
        return tuple(teaching_execution_binding_from_row(row) for row in rows)

    def get(self, execution_id: ExecutionId) -> TeachingExecution | None:
        try:
            row = (
                self._connection.execute(
                    select(executions_table).where(
                        executions_table.c.execution_id == execution_id.value,
                        executions_table.c.tenant_id == self._execution_tenant_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None
            bindings = self._load_bindings(execution_id)
        except Exception as exc:
            reraise_as_application_error(exc)
        return teaching_execution_from_row(row, bindings)

    def get_for_update(
        self, execution_id: ExecutionId
    ) -> TeachingExecution | None:
        try:
            row = (
                self._connection.execute(
                    select(executions_table)
                    .where(
                        executions_table.c.execution_id == execution_id.value,
                        executions_table.c.tenant_id == self._execution_tenant_id,
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None
            bindings = self._load_bindings(execution_id)
        except Exception as exc:
            reraise_as_application_error(exc)
        return teaching_execution_from_row(row, bindings)

    def update(
        self,
        execution: TeachingExecution,
        *,
        expected_revision: AggregateRevision,
    ) -> bool:
        """Compare-and-set mutable execution lifecycle. False means a lost race."""
        try:
            result = self._connection.execute(
                update(executions_table)
                .where(
                    executions_table.c.execution_id == execution.execution_id.value,
                    executions_table.c.tenant_id == self._execution_tenant_id,
                    executions_table.c.aggregate_revision == int(expected_revision),
                )
                .values(
                    lifecycle_state=execution.lifecycle_state.value,
                    completed_at=execution.completed_at,
                    cancelled_at=execution.cancelled_at,
                    aggregate_revision=int(execution.aggregate_revision),
                    updated_at=execution.updated_at,
                )
            )
        except Exception as exc:
            reraise_as_application_error(exc)
        return result.rowcount == 1

    def list_bindings(
        self, execution_id: ExecutionId
    ) -> list[TeachingExecutionContentBinding]:
        try:
            return list(self._load_bindings(execution_id))
        except Exception as exc:
            reraise_as_application_error(exc)

    def insert_observation(
        self, observation: TeachingExecutionObservation
    ) -> None:
        """Insert observation only while parent execution is IN_PROGRESS.

        Locks the parent execution row so terminalization races fail closed.
        """
        try:
            parent = self.get_for_update(observation.execution_id)
            if parent is None:
                raise PersistenceInvariantViolation(
                    "cannot insert observation for unknown TeachingExecution"
                )
            if parent.lifecycle_state is not ExecutionLifecycleState.IN_PROGRESS:
                raise PersistenceInvariantViolation(
                    "observations cannot be created after TeachingExecution "
                    "becomes terminal"
                )
            self._connection.execute(
                execution_observations_table.insert().values(
                    observation_id=observation.observation_id.value,
                    tenant_id=self._execution_tenant_id,
                    execution_id=observation.execution_id.value,
                    observation_kind=observation.observation_kind.value,
                    body=observation.body,
                    recorded_at=observation.recorded_at,
                    updated_at=observation.updated_at,
                    revision=int(observation.revision),
                )
            )
        except PersistenceInvariantViolation:
            raise
        except Exception as exc:
            reraise_as_application_error(exc)

    def get_observation(
        self, observation_id: ObservationId
    ) -> TeachingExecutionObservation | None:
        try:
            row = (
                self._connection.execute(
                    select(execution_observations_table).where(
                        execution_observations_table.c.observation_id
                        == observation_id.value,
                        execution_observations_table.c.tenant_id
                        == self._execution_tenant_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
        except Exception as exc:
            reraise_as_application_error(exc)
        if row is None:
            return None
        return teaching_execution_observation_from_row(row)

    def list_observations(
        self, execution_id: ExecutionId
    ) -> list[TeachingExecutionObservation]:
        try:
            rows = (
                self._connection.execute(
                    select(execution_observations_table)
                    .where(
                        execution_observations_table.c.execution_id
                        == execution_id.value,
                        execution_observations_table.c.tenant_id
                        == self._execution_tenant_id,
                    )
                    .order_by(
                        execution_observations_table.c.recorded_at.asc(),
                        execution_observations_table.c.observation_id.asc(),
                    )
                )
                .mappings()
                .all()
            )
        except Exception as exc:
            reraise_as_application_error(exc)
        return [teaching_execution_observation_from_row(row) for row in rows]

    def update_observation(
        self,
        observation: TeachingExecutionObservation,
        *,
        expected_revision: ObservationRevision,
    ) -> bool:
        """CAS observation correction while parent remains IN_PROGRESS.

        Locks the claimed parent execution first so a race with terminalization
        fails closed. The CAS predicate also requires the stored observation's
        execution_id to match the claimed parent, so a caller cannot authorize
        mutation of a terminal-owned observation by supplying a different
        IN_PROGRESS execution_id.
        """
        try:
            parent = self.get_for_update(observation.execution_id)
            if parent is None:
                raise PersistenceInvariantViolation(
                    "cannot update observation for unknown TeachingExecution"
                )
            if parent.lifecycle_state is not ExecutionLifecycleState.IN_PROGRESS:
                raise PersistenceInvariantViolation(
                    "observations are immutable after TeachingExecution becomes "
                    "terminal"
                )
            result = self._connection.execute(
                update(execution_observations_table)
                .where(
                    execution_observations_table.c.observation_id
                    == observation.observation_id.value,
                    execution_observations_table.c.tenant_id
                    == self._execution_tenant_id,
                    execution_observations_table.c.execution_id
                    == observation.execution_id.value,
                    execution_observations_table.c.revision
                    == int(expected_revision),
                )
                .values(
                    body=observation.body,
                    updated_at=observation.updated_at,
                    revision=int(observation.revision),
                )
            )
        except PersistenceInvariantViolation:
            raise
        except Exception as exc:
            reraise_as_application_error(exc)
        return result.rowcount == 1

    def list_for_teacher(
        self,
        *,
        teacher_principal_id: UUID,
        limit: int,
        work_id: WorkId | None = None,
        class_ref: str | None = None,
        lifecycle_state: str | None = None,
    ) -> list[TeachingExecution]:
        statement = select(executions_table).where(
            executions_table.c.tenant_id == self._execution_tenant_id,
            executions_table.c.teacher_principal_id == teacher_principal_id,
        )
        if work_id is not None:
            statement = statement.where(executions_table.c.work_id == work_id.value)
        if class_ref is not None:
            statement = statement.where(executions_table.c.class_ref == class_ref)
        if lifecycle_state is not None:
            statement = statement.where(
                executions_table.c.lifecycle_state == lifecycle_state
            )
        statement = statement.order_by(
            executions_table.c.updated_at.desc(),
            executions_table.c.execution_id.desc(),
        ).limit(limit)
        try:
            rows = self._connection.execute(statement).mappings().all()
            result: list[TeachingExecution] = []
            for row in rows:
                execution_id = ExecutionId(row["execution_id"])
                bindings = self._load_bindings(execution_id)
                result.append(teaching_execution_from_row(row, bindings))
        except Exception as exc:
            reraise_as_application_error(exc)
        return result
