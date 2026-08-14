"""Temporal gateway protocol and Client-backed implementation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol

from temporalio.client import Client, WorkflowFailureError, WorkflowHandle
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from aieos.platform.workflows.constants import (
    CONTENT_REVIEW_TASK_QUEUE,
    ERROR_TEMPORAL_UNAVAILABLE,
    ERROR_WORKFLOW_IDENTITY_CONFLICT,
    ERROR_WORKFLOW_NOT_FOUND,
    ERROR_WORKFLOW_TERMINAL_MISMATCH,
    QUERY_STATE,
    SIGNAL_REVIEW_DECISION_RECORDED,
)
from aieos.platform.workflows.temporal.content_review import ContentReviewWorkflowV1

_TERMINAL = frozenset(
    {"COMPLETED", "FAILED", "CANCELED", "TERMINATED", "TIMED_OUT"}
)


@dataclass(frozen=True, slots=True)
class StartDeliveryResult:
    delivered: bool
    error_code: str | None = None
    permanent: bool = False


@dataclass(frozen=True, slots=True)
class CommandDeliveryResult:
    delivered: bool
    error_code: str | None = None
    permanent: bool = False


class TemporalReviewGateway(Protocol):
    async def start_content_review(
        self,
        *,
        temporal_workflow_id: str,
        task_queue: str,
        start_input: dict[str, Any],
    ) -> StartDeliveryResult: ...

    async def deliver_review_decision(
        self,
        *,
        temporal_workflow_id: str,
        command_payload: dict[str, Any],
        result_timeout_seconds: float,
    ) -> CommandDeliveryResult: ...


class TemporalClientReviewGateway:
    def __init__(self, client: Client) -> None:
        self._client = client

    async def start_content_review(
        self,
        *,
        temporal_workflow_id: str,
        task_queue: str,
        start_input: dict[str, Any],
    ) -> StartDeliveryResult:
        try:
            await self._client.start_workflow(
                ContentReviewWorkflowV1.run,
                start_input,
                id=temporal_workflow_id,
                task_queue=task_queue or CONTENT_REVIEW_TASK_QUEUE,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            )
            return StartDeliveryResult(delivered=True)
        except WorkflowAlreadyStartedError:
            return await self._reconcile_existing_start(
                temporal_workflow_id=temporal_workflow_id,
                start_input=start_input,
            )
        except RPCError:
            return StartDeliveryResult(
                delivered=False,
                error_code=ERROR_TEMPORAL_UNAVAILABLE,
                permanent=False,
            )
        except OSError:
            return StartDeliveryResult(
                delivered=False,
                error_code=ERROR_TEMPORAL_UNAVAILABLE,
                permanent=False,
            )

    async def deliver_review_decision(
        self,
        *,
        temporal_workflow_id: str,
        command_payload: dict[str, Any],
        result_timeout_seconds: float,
    ) -> CommandDeliveryResult:
        handle: WorkflowHandle = self._client.get_workflow_handle(temporal_workflow_id)
        try:
            description = await handle.describe()
            status_name = getattr(description.status, "name", str(description.status))
            if status_name in _TERMINAL:
                return await self._reconcile_terminal_command(
                    handle, command_payload, result_timeout_seconds
                )
            await handle.signal(SIGNAL_REVIEW_DECISION_RECORDED, command_payload)
            result = await asyncio.wait_for(
                handle.result(), timeout=result_timeout_seconds
            )
            return self._match_command_result(result, command_payload)
        except TimeoutError:
            return CommandDeliveryResult(
                delivered=False,
                error_code=ERROR_TEMPORAL_UNAVAILABLE,
                permanent=False,
            )
        except WorkflowFailureError:
            return await self._reconcile_terminal_command(
                handle, command_payload, result_timeout_seconds
            )
        except RPCError as exc:
            if exc.status == RPCStatusCode.NOT_FOUND:
                return CommandDeliveryResult(
                    delivered=False,
                    error_code=ERROR_WORKFLOW_NOT_FOUND,
                    permanent=True,
                )
            return CommandDeliveryResult(
                delivered=False,
                error_code=ERROR_TEMPORAL_UNAVAILABLE,
                permanent=False,
            )
        except OSError:
            return CommandDeliveryResult(
                delivered=False,
                error_code=ERROR_TEMPORAL_UNAVAILABLE,
                permanent=False,
            )

    async def _reconcile_existing_start(
        self,
        *,
        temporal_workflow_id: str,
        start_input: dict[str, Any],
    ) -> StartDeliveryResult:
        handle = self._client.get_workflow_handle(temporal_workflow_id)
        try:
            state = await handle.query(QUERY_STATE)
        except RPCError as exc:
            if exc.status == RPCStatusCode.NOT_FOUND:
                return StartDeliveryResult(
                    delivered=False,
                    error_code=ERROR_WORKFLOW_NOT_FOUND,
                    permanent=True,
                )
            return StartDeliveryResult(
                delivered=False,
                error_code=ERROR_TEMPORAL_UNAVAILABLE,
                permanent=False,
            )
        if not _start_identity_matches(state, start_input):
            return StartDeliveryResult(
                delivered=False,
                error_code=ERROR_WORKFLOW_IDENTITY_CONFLICT,
                permanent=True,
            )
        return StartDeliveryResult(delivered=True)

    async def _reconcile_terminal_command(
        self,
        handle: WorkflowHandle,
        command_payload: dict[str, Any],
        result_timeout_seconds: float,
    ) -> CommandDeliveryResult:
        try:
            result = await asyncio.wait_for(
                handle.result(), timeout=result_timeout_seconds
            )
        except TimeoutError:
            return CommandDeliveryResult(
                delivered=False,
                error_code=ERROR_TEMPORAL_UNAVAILABLE,
                permanent=False,
            )
        except WorkflowFailureError:
            return CommandDeliveryResult(
                delivered=False,
                error_code=ERROR_WORKFLOW_TERMINAL_MISMATCH,
                permanent=True,
            )
        except RPCError:
            return CommandDeliveryResult(
                delivered=False,
                error_code=ERROR_TEMPORAL_UNAVAILABLE,
                permanent=False,
            )
        return self._match_command_result(result, command_payload)

    @staticmethod
    def _match_command_result(
        result: dict[str, Any],
        command_payload: dict[str, Any],
    ) -> CommandDeliveryResult:
        if (
            str(result.get("command_id")) == str(command_payload["command_id"])
            and str(result.get("review_decision_id"))
            == str(command_payload["review_decision_id"])
            and str(result.get("decision")) == str(command_payload["decision"])
        ):
            return CommandDeliveryResult(delivered=True)
        return CommandDeliveryResult(
            delivered=False,
            error_code=ERROR_WORKFLOW_TERMINAL_MISMATCH,
            permanent=True,
        )


def _start_identity_matches(
    state: dict[str, Any], start_input: dict[str, Any]
) -> bool:
    return (
        str(state.get("workflow_instance_id"))
        == str(start_input["workflow_instance_id"])
        and str(state.get("content_id")) == str(start_input["content_id"])
        and str(state.get("version_id")) == str(start_input["version_id"])
    )
