"""Content-transaction and dispatcher repositories for workflow intents."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_, select, text, update
from sqlalchemy.engine import Connection, Engine

from aieos.domains.content.application.errors import (
    ContentApplicationError,
    PersistenceOperationFailed,
)
from aieos.platform.workflows.constants import (
    INTENT_CLAIMED,
    INTENT_DELIVERED,
    INTENT_PENDING,
    INTENT_QUARANTINED,
)
from aieos.platform.workflows.identities import (
    WorkflowCommandId,
    WorkflowCommandIntentId,
    WorkflowInstanceId,
    WorkflowStartIntentId,
)
from aieos.platform.workflows.models import WorkflowCommandIntent, WorkflowStartIntent
from aieos.platform.workflows.persistence.models import (
    workflow_command_intents_table,
    workflow_start_intents_table,
)


def _reraise(exc: BaseException) -> None:
    if isinstance(exc, ContentApplicationError):
        raise exc
    raise PersistenceOperationFailed("workflow intent persistence operation failed") from exc


def _start_from_row(row: Any) -> WorkflowStartIntent:
    return WorkflowStartIntent(
        workflow_start_intent_id=WorkflowStartIntentId(row.workflow_start_intent_id),
        tenant_id=row.tenant_id,
        workflow_instance_id=WorkflowInstanceId(row.workflow_instance_id),
        workflow_type=row.workflow_type,
        workflow_major_version=int(row.workflow_major_version),
        temporal_workflow_id=row.temporal_workflow_id,
        task_queue=row.task_queue,
        business_key=row.business_key,
        input=dict(row.input),
        status=row.status,
        attempt_count=int(row.attempt_count),
        available_at=row.available_at,
        claimed_by=row.claimed_by,
        claimed_until=row.claimed_until,
        delivered_at=row.delivered_at,
        last_error_code=row.last_error_code,
        created_at=row.created_at,
    )


def _command_from_row(row: Any) -> WorkflowCommandIntent:
    return WorkflowCommandIntent(
        workflow_command_intent_id=WorkflowCommandIntentId(
            row.workflow_command_intent_id
        ),
        tenant_id=row.tenant_id,
        workflow_instance_id=WorkflowInstanceId(row.workflow_instance_id),
        temporal_workflow_id=row.temporal_workflow_id,
        command_id=WorkflowCommandId(row.command_id),
        command_type=row.command_type,
        business_key=row.business_key,
        payload=dict(row.payload),
        status=row.status,
        attempt_count=int(row.attempt_count),
        available_at=row.available_at,
        claimed_by=row.claimed_by,
        claimed_until=row.claimed_until,
        delivered_at=row.delivered_at,
        last_error_code=row.last_error_code,
        created_at=row.created_at,
    )


class SqlAlchemyWorkflowIntentRepository:
    """Insert/read only. Used inside Content Unit of Work."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert_start_intent(self, intent: WorkflowStartIntent) -> None:
        try:
            self._connection.execute(
                workflow_start_intents_table.insert().values(
                    workflow_start_intent_id=intent.workflow_start_intent_id.value,
                    tenant_id=intent.tenant_id,
                    workflow_instance_id=intent.workflow_instance_id.value,
                    workflow_type=intent.workflow_type,
                    workflow_major_version=intent.workflow_major_version,
                    temporal_workflow_id=intent.temporal_workflow_id,
                    task_queue=intent.task_queue,
                    business_key=intent.business_key,
                    input=dict(intent.input),
                    status=intent.status,
                    attempt_count=intent.attempt_count,
                    available_at=intent.available_at,
                    claimed_by=intent.claimed_by,
                    claimed_until=intent.claimed_until,
                    delivered_at=intent.delivered_at,
                    last_error_code=intent.last_error_code,
                    created_at=intent.created_at,
                )
            )
        except Exception as exc:
            _reraise(exc)

    def get_start_intent_by_business_key(
        self,
        *,
        workflow_type: str,
        business_key: str,
    ) -> WorkflowStartIntent | None:
        try:
            row = self._connection.execute(
                select(workflow_start_intents_table).where(
                    workflow_start_intents_table.c.workflow_type == workflow_type,
                    workflow_start_intents_table.c.business_key == business_key,
                )
            ).one_or_none()
        except Exception as exc:
            _reraise(exc)
        if row is None:
            return None
        return _start_from_row(row)

    def insert_command_intent(self, intent: WorkflowCommandIntent) -> None:
        try:
            self._connection.execute(
                workflow_command_intents_table.insert().values(
                    workflow_command_intent_id=intent.workflow_command_intent_id.value,
                    tenant_id=intent.tenant_id,
                    workflow_instance_id=intent.workflow_instance_id.value,
                    temporal_workflow_id=intent.temporal_workflow_id,
                    command_id=intent.command_id.value,
                    command_type=intent.command_type,
                    business_key=intent.business_key,
                    payload=dict(intent.payload),
                    status=intent.status,
                    attempt_count=intent.attempt_count,
                    available_at=intent.available_at,
                    claimed_by=intent.claimed_by,
                    claimed_until=intent.claimed_until,
                    delivered_at=intent.delivered_at,
                    last_error_code=intent.last_error_code,
                    created_at=intent.created_at,
                )
            )
        except Exception as exc:
            _reraise(exc)

    def get_command_intent_by_business_key(
        self,
        *,
        business_key: str,
    ) -> WorkflowCommandIntent | None:
        try:
            row = self._connection.execute(
                select(workflow_command_intents_table).where(
                    workflow_command_intents_table.c.business_key == business_key
                )
            ).one_or_none()
        except Exception as exc:
            _reraise(exc)
        if row is None:
            return None
        return _command_from_row(row)


class SqlAlchemyWorkflowDispatcherRepository:
    """Claim / retry / deliver / quarantine. Never mutates Content tables."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def claim_start_intent(
        self,
        *,
        tenant_id: UUID,
        claimed_by: str,
        now: datetime,
        claim_until: datetime,
    ) -> WorkflowStartIntent | None:
        with self._engine.begin() as conn:
            self._set_tenant(conn, tenant_id)
            row = conn.execute(
                select(workflow_start_intents_table)
                .where(
                    or_(
                        and_(
                            workflow_start_intents_table.c.status == INTENT_PENDING,
                            workflow_start_intents_table.c.available_at <= now,
                        ),
                        and_(
                            workflow_start_intents_table.c.status == INTENT_CLAIMED,
                            workflow_start_intents_table.c.claimed_until <= now,
                        ),
                    )
                )
                .order_by(workflow_start_intents_table.c.available_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            ).one_or_none()
            if row is None:
                return None
            updated = conn.execute(
                update(workflow_start_intents_table)
                .where(
                    workflow_start_intents_table.c.workflow_start_intent_id
                    == row.workflow_start_intent_id
                )
                .values(
                    status=INTENT_CLAIMED,
                    claimed_by=claimed_by,
                    claimed_until=claim_until,
                    attempt_count=workflow_start_intents_table.c.attempt_count + 1,
                )
                .returning(workflow_start_intents_table)
            ).one()
            return _start_from_row(updated)

    def mark_start_delivered(
        self,
        *,
        tenant_id: UUID,
        workflow_start_intent_id: UUID,
        claimed_by: str,
        attempt_count: int,
        delivered_at: datetime,
    ) -> bool:
        with self._engine.begin() as conn:
            self._set_tenant(conn, tenant_id)
            result = conn.execute(
                update(workflow_start_intents_table)
                .where(
                    workflow_start_intents_table.c.workflow_start_intent_id
                    == workflow_start_intent_id,
                    workflow_start_intents_table.c.status == INTENT_CLAIMED,
                    workflow_start_intents_table.c.claimed_by == claimed_by,
                    workflow_start_intents_table.c.attempt_count == attempt_count,
                )
                .values(
                    status=INTENT_DELIVERED,
                    delivered_at=delivered_at,
                    claimed_by=None,
                    claimed_until=None,
                    last_error_code=None,
                )
            )
            return bool(result.rowcount)

    def release_start_for_retry(
        self,
        *,
        tenant_id: UUID,
        workflow_start_intent_id: UUID,
        claimed_by: str,
        attempt_count: int,
        available_at: datetime,
        error_code: str,
        quarantine: bool,
    ) -> bool:
        with self._engine.begin() as conn:
            self._set_tenant(conn, tenant_id)
            result = conn.execute(
                update(workflow_start_intents_table)
                .where(
                    workflow_start_intents_table.c.workflow_start_intent_id
                    == workflow_start_intent_id,
                    workflow_start_intents_table.c.status == INTENT_CLAIMED,
                    workflow_start_intents_table.c.claimed_by == claimed_by,
                    workflow_start_intents_table.c.attempt_count == attempt_count,
                )
                .values(
                    status=INTENT_QUARANTINED if quarantine else INTENT_PENDING,
                    available_at=available_at,
                    claimed_by=None,
                    claimed_until=None,
                    last_error_code=error_code,
                )
            )
            return bool(result.rowcount)

    def claim_command_intent(
        self,
        *,
        tenant_id: UUID,
        claimed_by: str,
        now: datetime,
        claim_until: datetime,
    ) -> WorkflowCommandIntent | None:
        with self._engine.begin() as conn:
            self._set_tenant(conn, tenant_id)
            eligible_ids = conn.execute(
                text(
                    """
                    SELECT c.workflow_command_intent_id
                    FROM workflow.workflow_command_intents c
                    INNER JOIN workflow.workflow_start_intents s
                        ON s.workflow_instance_id = c.workflow_instance_id
                    WHERE s.status = 'DELIVERED'
                      AND (
                        (c.status = 'PENDING' AND c.available_at <= :now)
                        OR (c.status = 'CLAIMED' AND c.claimed_until <= :now)
                      )
                    ORDER BY c.available_at
                    LIMIT 1
                    FOR UPDATE OF c SKIP LOCKED
                    """
                ),
                {"now": now},
            ).one_or_none()
            if eligible_ids is None:
                return None
            updated = conn.execute(
                update(workflow_command_intents_table)
                .where(
                    workflow_command_intents_table.c.workflow_command_intent_id
                    == eligible_ids.workflow_command_intent_id
                )
                .values(
                    status=INTENT_CLAIMED,
                    claimed_by=claimed_by,
                    claimed_until=claim_until,
                    attempt_count=workflow_command_intents_table.c.attempt_count + 1,
                )
                .returning(workflow_command_intents_table)
            ).one()
            return _command_from_row(updated)

    def mark_command_delivered(
        self,
        *,
        tenant_id: UUID,
        workflow_command_intent_id: UUID,
        claimed_by: str,
        attempt_count: int,
        delivered_at: datetime,
    ) -> bool:
        with self._engine.begin() as conn:
            self._set_tenant(conn, tenant_id)
            result = conn.execute(
                update(workflow_command_intents_table)
                .where(
                    workflow_command_intents_table.c.workflow_command_intent_id
                    == workflow_command_intent_id,
                    workflow_command_intents_table.c.status == INTENT_CLAIMED,
                    workflow_command_intents_table.c.claimed_by == claimed_by,
                    workflow_command_intents_table.c.attempt_count == attempt_count,
                )
                .values(
                    status=INTENT_DELIVERED,
                    delivered_at=delivered_at,
                    claimed_by=None,
                    claimed_until=None,
                    last_error_code=None,
                )
            )
            return bool(result.rowcount)

    def release_command_for_retry(
        self,
        *,
        tenant_id: UUID,
        workflow_command_intent_id: UUID,
        claimed_by: str,
        attempt_count: int,
        available_at: datetime,
        error_code: str,
        quarantine: bool,
    ) -> bool:
        with self._engine.begin() as conn:
            self._set_tenant(conn, tenant_id)
            result = conn.execute(
                update(workflow_command_intents_table)
                .where(
                    workflow_command_intents_table.c.workflow_command_intent_id
                    == workflow_command_intent_id,
                    workflow_command_intents_table.c.status == INTENT_CLAIMED,
                    workflow_command_intents_table.c.claimed_by == claimed_by,
                    workflow_command_intents_table.c.attempt_count == attempt_count,
                )
                .values(
                    status=INTENT_QUARANTINED if quarantine else INTENT_PENDING,
                    available_at=available_at,
                    claimed_by=None,
                    claimed_until=None,
                    last_error_code=error_code,
                )
            )
            return bool(result.rowcount)

    def get_start_intent(
        self,
        *,
        tenant_id: UUID,
        workflow_start_intent_id: UUID,
    ) -> WorkflowStartIntent | None:
        with self._engine.connect() as conn:
            self._set_tenant(conn, tenant_id)
            row = conn.execute(
                select(workflow_start_intents_table).where(
                    workflow_start_intents_table.c.workflow_start_intent_id
                    == workflow_start_intent_id
                )
            ).one_or_none()
            if row is None:
                return None
            return _start_from_row(row)

    @staticmethod
    def _set_tenant(conn: Connection, tenant_id: UUID) -> None:
        conn.execute(
            text("SELECT set_config('aieos.tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )
