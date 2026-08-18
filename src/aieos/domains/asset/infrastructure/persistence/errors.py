"""Translate driver/ORM exceptions into Asset application persistence errors."""

from __future__ import annotations

from typing import NoReturn

from psycopg.errors import UniqueViolation
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from aieos.domains.asset.application.mutation_errors import (
    AssetApplicationError,
    AssetIdentityConflict,
    AssetPersistenceFailed,
)


def _constraint_blob(exc: BaseException) -> str:
    orig = getattr(exc, "orig", None) or exc
    diag = getattr(orig, "diag", None)
    constraint = getattr(diag, "constraint_name", None) if diag is not None else None
    return f"{constraint or ''} {orig}".lower()


def translate_infrastructure_error(exc: BaseException) -> AssetApplicationError:
    if isinstance(exc, AssetApplicationError):
        return exc
    orig = getattr(exc, "orig", None)
    is_unique = isinstance(exc, UniqueViolation) or isinstance(orig, UniqueViolation)
    if is_unique:
        blob = _constraint_blob(exc)
        if "pk_assets" in blob or "uq_assets" in blob:
            return AssetIdentityConflict("asset identity already exists")
        if "pk_asset_revisions" in blob or "uq_asset_revisions" in blob:
            return AssetIdentityConflict("asset revision identity already exists")
        if "pk_asset_revision_states" in blob:
            return AssetIdentityConflict("asset revision state identity already exists")
        return AssetPersistenceFailed("asset persistence invariant was violated")
    if isinstance(exc, (IntegrityError, SQLAlchemyError)):
        return AssetPersistenceFailed("asset persistence operation failed")
    return AssetPersistenceFailed("asset persistence operation failed")


def reraise_as_application_error(exc: BaseException) -> NoReturn:
    if isinstance(exc, AssetApplicationError):
        raise exc
    raise translate_infrastructure_error(exc) from exc
