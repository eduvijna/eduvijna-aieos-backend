"""Provider-neutral Asset current-use authority contract (ADR-AIEOS-032).

Adjacent to ResourceRef. No SQLAlchemy. No HTTP client. No Content persistence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from aieos.platform.resources import ResourceRef


class AssetUseRejectionReason(StrEnum):
    """Frozen ADR-AIEOS-034 Asset current-use rejection vocabulary."""

    NOT_FOUND = "NOT_FOUND"
    TENANT_INACCESSIBLE = "TENANT_INACCESSIBLE"
    REVISION_NOT_FOUND = "REVISION_NOT_FOUND"
    WITHDRAWN = "WITHDRAWN"
    DELETED = "DELETED"
    QUARANTINED = "QUARANTINED"
    SAFETY_PENDING = "SAFETY_PENDING"
    SAFETY_FAILED = "SAFETY_FAILED"
    BYTES_PURGED = "BYTES_PURGED"
    BYTES_MISSING = "BYTES_MISSING"
    INTEGRITY_MISMATCH = "INTEGRITY_MISMATCH"


class InvalidAssetUseAssessmentError(ValueError):
    """Raised when an AssetUseAssessment cannot be constructed."""


@dataclass(frozen=True, slots=True)
class AssetUseAssessment:
    """Typed authority observation for current Asset use."""

    usable: bool
    reason_code: AssetUseRejectionReason | None = None
    authority_revision: int | None = None
    observed_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.usable, bool):
            raise InvalidAssetUseAssessmentError("usable must be a boolean")
        if self.usable:
            if self.reason_code is not None:
                raise InvalidAssetUseAssessmentError(
                    "usable assessment must not include a reason_code"
                )
        else:
            if self.reason_code is None:
                raise InvalidAssetUseAssessmentError(
                    "unusable assessment requires a reason_code"
                )
            if not isinstance(self.reason_code, AssetUseRejectionReason):
                raise InvalidAssetUseAssessmentError(
                    "reason_code must be an AssetUseRejectionReason"
                )
        if self.authority_revision is not None:
            if isinstance(self.authority_revision, bool) or not isinstance(
                self.authority_revision, int
            ):
                raise InvalidAssetUseAssessmentError(
                    "authority_revision must be NULL or a non-negative integer"
                )
            if self.authority_revision < 0:
                raise InvalidAssetUseAssessmentError(
                    "authority_revision must be NULL or a non-negative integer"
                )
        if self.observed_at is not None:
            if not isinstance(self.observed_at, datetime):
                raise InvalidAssetUseAssessmentError(
                    "observed_at must be NULL or a timezone-aware datetime"
                )
            if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
                raise InvalidAssetUseAssessmentError(
                    "observed_at must be timezone-aware"
                )


class AssetUseAuthority(Protocol):
    """Owning Asset/File boundary current-use authority (ADR-AIEOS-032 / ADR-AIEOS-034)."""

    def assess_use(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        resource_ref: ResourceRef,
    ) -> AssetUseAssessment: ...
