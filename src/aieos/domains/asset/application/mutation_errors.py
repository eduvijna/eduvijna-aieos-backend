"""Technology-neutral Asset mutation application errors (PED-I10B5).

No HTTP status codes, Problem Details, SQLAlchemy, or driver exceptions.
"""

from __future__ import annotations


class AssetApplicationError(Exception):
    """Base error for Asset mutation / persistence-boundary failures."""


class AssetNotFound(AssetApplicationError):
    """Target Asset is not visible in the current execution tenant."""


class AssetConflict(AssetApplicationError):
    """Stale expected aggregate revision or governing-state race; zero mutation."""


class AssetIdentityConflict(AssetApplicationError):
    """Unique identity collision; the service may re-read for compatible replay."""


class AssetTransitionRejected(AssetApplicationError):
    """Requested lifecycle, quarantine, or safety transition is not allowed."""


class AssetActivationRejected(AssetApplicationError):
    """Revision cannot be newly activated (safety, purge, or physical bytes)."""

    def __init__(self, reason: str, message: str = "activation rejected") -> None:
        self.reason = reason
        super().__init__(message)


class AssetPersistenceFailed(AssetApplicationError):
    """Infrastructure/transaction/connection/driver failure, not a business conflict."""


class AssetForbidden(AssetApplicationError):
    """Current principal lacks the required exact Asset capability."""
