"""AI application errors. Technology-neutral."""

from __future__ import annotations


class AIApplicationError(Exception):
    """Base error for AI execution / GenerationRun application failures."""


class GenerationRunNotFound(AIApplicationError):
    """GenerationRun is not visible in the execution tenant."""


class GenerationRunConflict(AIApplicationError):
    """Optimistic concurrency or unique conflict on GenerationRun."""


class PersistenceOperationFailed(AIApplicationError):
    """Infrastructure/transaction failure."""


class PersistenceInvariantViolation(AIApplicationError):
    """A GenerationRun persistence invariant failed."""
