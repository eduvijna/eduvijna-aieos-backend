"""Translate driver/ORM exceptions into application persistence errors."""

from __future__ import annotations

from typing import NoReturn

from psycopg.errors import UniqueViolation
from sqlalchemy.exc import IntegrityError

from aieos.domains.content.application.errors import (
    ContentAlreadyExists,
    ContentApplicationError,
    PersistenceInvariantViolation,
    PersistenceOperationFailed,
    ReviewAlreadyDecided,
    VersionAlreadyExists,
)


def _constraint_blob(exc: BaseException) -> str:
    orig = getattr(exc, "orig", None) or exc
    diag = getattr(orig, "diag", None)
    constraint = getattr(diag, "constraint_name", None) if diag is not None else None
    return f"{constraint or ''} {orig}".lower()


def translate_infrastructure_error(
    exc: BaseException,
    *,
    unique_conflict: type[ContentApplicationError] | None = None,
    unique_message: str | None = None,
) -> ContentApplicationError:
    if isinstance(exc, ContentApplicationError):
        return exc
    orig = getattr(exc, "orig", None)
    is_unique = isinstance(exc, UniqueViolation) or isinstance(orig, UniqueViolation)
    if is_unique:
        if unique_conflict is not None:
            return unique_conflict(
                unique_message or "unique constraint violated"
            )
        blob = _constraint_blob(exc)
        if "review_decisions" in blob:
            return ReviewAlreadyDecided(
                "this ContentVersion already has a terminal ReviewDecision"
            )
        if "content_versions" in blob:
            return VersionAlreadyExists(
                "ContentVersion identity or version_number already exists"
            )
        if "pk_contents" in blob or "uq_contents" in blob:
            return ContentAlreadyExists("Content identity already exists")
        return PersistenceInvariantViolation(
            "content persistence invariant was violated"
        )
    if isinstance(exc, IntegrityError):
        return PersistenceInvariantViolation(
            "content persistence invariant was violated"
        )
    return PersistenceOperationFailed("content persistence operation failed")


def reraise_as_application_error(
    exc: BaseException,
    *,
    unique_conflict: type[ContentApplicationError] | None = None,
    unique_message: str | None = None,
) -> NoReturn:
    if isinstance(exc, ContentApplicationError):
        raise exc
    raise translate_infrastructure_error(
        exc,
        unique_conflict=unique_conflict,
        unique_message=unique_message,
    ) from exc
