"""Temporal worker registration factory. No production main loop."""

from __future__ import annotations

from temporalio.client import Client
from temporalio.worker import Worker

from aieos.platform.workflows.constants import CONTENT_REVIEW_TASK_QUEUE
from aieos.platform.workflows.temporal.content_review import ContentReviewWorkflowV1


def create_content_review_worker(
    client: Client,
    *,
    task_queue: str = CONTENT_REVIEW_TASK_QUEUE,
) -> Worker:
    return Worker(
        client,
        task_queue=task_queue,
        workflows=[ContentReviewWorkflowV1],
    )
