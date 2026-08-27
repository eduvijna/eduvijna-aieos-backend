"""Translate driver/ORM exceptions into Teaching application persistence errors."""

from __future__ import annotations

from typing import NoReturn

from psycopg.errors import UniqueViolation
from sqlalchemy.exc import IntegrityError

from aieos.domains.teaching.application.errors import (
    PersistenceInvariantViolation,
    PersistenceOperationFailed,
    TeachingApplicationError,
)


def translate_infrastructure_error(
    exc: BaseException,
    *,
    unique_conflict: type[TeachingApplicationError] | None = None,
    unique_message: str | None = None,
) -> TeachingApplicationError:
    if isinstance(exc, TeachingApplicationError):
        return exc
    orig = getattr(exc, "orig", None)
    is_unique = isinstance(exc, UniqueViolation) or isinstance(orig, UniqueViolation)
    if is_unique:
        if unique_conflict is not None:
            return unique_conflict(unique_message or "unique constraint violated")
        return PersistenceInvariantViolation(
            "teaching persistence invariant was violated"
        )
    if isinstance(exc, IntegrityError):
        return PersistenceInvariantViolation(
            "teaching persistence invariant was violated"
        )
    return PersistenceOperationFailed("teaching persistence operation failed")


def reraise_as_application_error(
    exc: BaseException,
    *,
    unique_conflict: type[TeachingApplicationError] | None = None,
    unique_message: str | None = None,
) -> NoReturn:
    if isinstance(exc, TeachingApplicationError):
        raise exc
    raise translate_infrastructure_error(
        exc,
        unique_conflict=unique_conflict,
        unique_message=unique_message,
    ) from exc
