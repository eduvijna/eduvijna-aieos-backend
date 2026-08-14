"""Content-transaction workflow-intent repository port (insert/read only)."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from aieos.platform.workflows.models import WorkflowCommandIntent, WorkflowStartIntent


class WorkflowIntentRepository(Protocol):
    """Same PostgreSQL transaction as Content mutations. No claim/deliver."""

    def insert_start_intent(self, intent: WorkflowStartIntent) -> None: ...

    def get_start_intent_by_business_key(
        self,
        *,
        workflow_type: str,
        business_key: str,
    ) -> WorkflowStartIntent | None: ...

    def insert_command_intent(self, intent: WorkflowCommandIntent) -> None: ...

    def get_command_intent_by_business_key(
        self,
        *,
        business_key: str,
    ) -> WorkflowCommandIntent | None: ...
