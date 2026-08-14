"""ContentReviewWorkflowV1 — durable wait for one committed review decision.

Process truth only. Not Content/ReviewDecision/authorization authority.
Continue-As-New is not required for V1: history is bounded by design
(one start, one wait, one terminal decision).
"""

from __future__ import annotations

from typing import Any

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from aieos.platform.workflows.constants import (
        PROCESS_DECISION_OBSERVED,
        PROCESS_WAITING,
        QUERY_STATE,
        SIGNAL_REVIEW_DECISION_RECORDED,
    )

_VALID_DECISIONS = frozenset({"APPROVE", "REQUEST_CHANGES", "REJECT"})


@workflow.defn(name="ContentReviewWorkflowV1")
class ContentReviewWorkflowV1:
    def __init__(self) -> None:
        self._workflow_instance_id: str = ""
        self._tenant_id: str = ""
        self._content_id: str = ""
        self._version_id: str = ""
        self._correlation_id: str = ""
        self._process_status: str = PROCESS_WAITING
        self._command_id: str | None = None
        self._review_decision_id: str | None = None
        self._decision: str | None = None
        self._conflict: str | None = None

    @workflow.run
    async def run(self, start_input: dict[str, Any]) -> dict[str, Any]:
        self._workflow_instance_id = str(start_input["workflow_instance_id"])
        self._tenant_id = str(start_input["tenant_id"])
        self._content_id = str(start_input["content_id"])
        self._version_id = str(start_input["version_id"])
        self._correlation_id = str(start_input["correlation_id"])
        await workflow.wait_condition(
            lambda: self._decision is not None or self._conflict is not None
        )
        if self._conflict is not None:
            raise RuntimeError(self._conflict)
        return {
            "workflow_instance_id": self._workflow_instance_id,
            "content_id": self._content_id,
            "version_id": self._version_id,
            "command_id": self._command_id,
            "review_decision_id": self._review_decision_id,
            "decision": self._decision,
            "process_status": PROCESS_DECISION_OBSERVED,
        }

    @workflow.signal(name=SIGNAL_REVIEW_DECISION_RECORDED)
    def review_decision_recorded(self, command: dict[str, Any]) -> None:
        command_id = str(command["command_id"])
        if self._command_id is not None:
            if self._command_id == command_id:
                return
            self._conflict = "workflow_terminal_mismatch"
            return
        if str(command["workflow_instance_id"]) != self._workflow_instance_id:
            self._conflict = "workflow_identity_conflict"
            return
        if str(command["content_id"]) != self._content_id:
            self._conflict = "workflow_identity_conflict"
            return
        if str(command["version_id"]) != self._version_id:
            self._conflict = "workflow_identity_conflict"
            return
        decision = str(command["decision"])
        if decision not in _VALID_DECISIONS:
            self._conflict = "workflow_terminal_mismatch"
            return
        self._command_id = command_id
        self._review_decision_id = str(command["review_decision_id"])
        self._decision = decision
        self._process_status = PROCESS_DECISION_OBSERVED

    @workflow.query(name=QUERY_STATE)
    def state(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "workflow_instance_id": self._workflow_instance_id,
            "content_id": self._content_id,
            "version_id": self._version_id,
            "process_status": self._process_status,
        }
        if self._review_decision_id is not None:
            result["review_decision_id"] = self._review_decision_id
        if self._decision is not None:
            result["decision"] = self._decision
        return result
