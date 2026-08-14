"""Workflow dispatcher claim-lost signaling."""

from __future__ import annotations


class WorkflowDispatchClaimLost(Exception):
    """Fenced claim no longer owns the intent row; persistence must not change."""
