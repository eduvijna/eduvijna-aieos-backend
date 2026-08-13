"""Translate driver/ORM exceptions into application persistence errors."""

from __future__ import annotations

from typing import NoReturn

from psycopg.errors import UniqueViolation
from sqlalchemy.exc import IntegrityError

from aieos.domains.content.application.errors import (
    ContentApplicationError,
    PersistenceInvariantViolation,
    PersistenceOperationFailed,
    VersionAlreadyExists,
)


def translate_infrastructure_error(exc: BaseException) -> ContentApplicationError:
    if isinstance(exc, ContentApplicationError):
        return exc
    orig = getattr(exc, "orig", None)
    if isinstance(exc, UniqueViolation) or isinstance(orig, UniqueViolation):
        return VersionAlreadyExists(
            "ContentVersion identity or version_number already exists"
        )
    if isinstance(exc, IntegrityError):
        return PersistenceInvariantViolation(
            "content persistence invariant was violated"
        )
    return PersistenceOperationFailed("content persistence operation failed")


def reraise_as_application_error(exc: BaseException) -> NoReturn:
    if isinstance(exc, ContentApplicationError):
        raise exc
    raise translate_infrastructure_error(exc) from exc
