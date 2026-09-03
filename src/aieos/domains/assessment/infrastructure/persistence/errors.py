"""Translate driver/ORM exceptions into Assessment application persistence errors."""

from __future__ import annotations

from typing import NoReturn

from psycopg.errors import UniqueViolation
from sqlalchemy.exc import IntegrityError

from aieos.domains.assessment.application.errors import (
    AssessmentApplicationError,
    PersistenceInvariantViolation,
    PersistenceOperationFailed,
)


def translate_infrastructure_error(
    exc: BaseException,
) -> AssessmentApplicationError:
    if isinstance(exc, AssessmentApplicationError):
        return exc
    orig = getattr(exc, "orig", None)
    is_unique = isinstance(exc, UniqueViolation) or isinstance(orig, UniqueViolation)
    if is_unique:
        return PersistenceInvariantViolation(
            "assessment persistence invariant was violated"
        )
    if isinstance(exc, IntegrityError):
        return PersistenceInvariantViolation(
            "assessment persistence invariant was violated"
        )
    return PersistenceOperationFailed("assessment persistence operation failed")


def reraise_as_application_error(exc: BaseException) -> NoReturn:
    if isinstance(exc, AssessmentApplicationError):
        raise exc
    raise translate_infrastructure_error(exc) from exc
