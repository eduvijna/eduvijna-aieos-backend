"""Map persistence exceptions to AI application errors."""

from __future__ import annotations

from aieos.platform.ai.application.errors import (
    GenerationRunConflict,
    PersistenceInvariantViolation,
    PersistenceOperationFailed,
)


def reraise_as_application_error(
    exc: BaseException,
    *,
    unique_conflict: type[Exception] = GenerationRunConflict,
    unique_message: str = "GenerationRun unique constraint violated",
) -> None:
    message = str(exc).lower()
    if "unique" in message or "duplicate" in message:
        raise unique_conflict(unique_message) from exc
    if "check" in message or "violat" in message:
        raise PersistenceInvariantViolation("GenerationRun invariant violated") from exc
    raise PersistenceOperationFailed("AI persistence operation failed") from exc
