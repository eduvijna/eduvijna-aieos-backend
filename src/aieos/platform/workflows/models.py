"""Framework-neutral workflow start and command intent models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping
from uuid import UUID

from aieos.platform.workflows.identities import (
    WorkflowCommandId,
    WorkflowCommandIntentId,
    WorkflowInstanceId,
    WorkflowStartIntentId,
)


@dataclass(frozen=True, slots=True)
class WorkflowStartIntent:
    workflow_start_intent_id: WorkflowStartIntentId
    tenant_id: UUID
    workflow_instance_id: WorkflowInstanceId
    workflow_type: str
    workflow_major_version: int
    temporal_workflow_id: str
    task_queue: str
    business_key: str
    input: Mapping[str, object]
    status: str
    attempt_count: int
    available_at: datetime
    claimed_by: str | None
    claimed_until: datetime | None
    delivered_at: datetime | None
    last_error_code: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class WorkflowCommandIntent:
    workflow_command_intent_id: WorkflowCommandIntentId
    tenant_id: UUID
    workflow_instance_id: WorkflowInstanceId
    temporal_workflow_id: str
    command_id: WorkflowCommandId
    command_type: str
    business_key: str
    payload: Mapping[str, object]
    status: str
    attempt_count: int
    available_at: datetime
    claimed_by: str | None
    claimed_until: datetime | None
    delivered_at: datetime | None
    last_error_code: str | None
    created_at: datetime
