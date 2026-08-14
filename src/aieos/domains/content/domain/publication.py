"""Publication domain contract.

APPROVED != PUBLISHED. Publication is a separate immutable domain fact
bound to an exact ContentVersion. This module does not implement publish
commands, authorization, outbox, API, or persistence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from aieos.domains.content.domain.errors import PublicationBindingError
from aieos.domains.content.domain.identities import (
    ContentId,
    ContentVersionId,
    PublicationId,
    ReviewDecisionId,
    require_foreign_uuid,
)
from aieos.domains.content.domain.states import StewardshipState


@dataclass(frozen=True, slots=True)
class Publication:
    """Immutable publication fact. Distinct from APPROVED stewardship state."""

    publication_id: PublicationId
    tenant_id: UUID
    content_id: ContentId
    version_id: ContentVersionId
    approval_decision_id: ReviewDecisionId
    published_by_principal_id: UUID
    effective_actor_id: UUID
    published_at: datetime
    correlation_id: UUID

    def __post_init__(self) -> None:
        require_foreign_uuid(self.tenant_id, label="tenant_id")
        require_foreign_uuid(
            self.published_by_principal_id, label="published_by_principal_id"
        )
        require_foreign_uuid(self.effective_actor_id, label="effective_actor_id")
        require_foreign_uuid(self.correlation_id, label="correlation_id")
        if self.version_id is None:
            raise PublicationBindingError("publication requires an exact version_id")
        if self.published_at.tzinfo is None or self.published_at.utcoffset() is None:
            raise PublicationBindingError("published_at must be timezone-aware")

    def references_version(self, version_id: ContentVersionId) -> bool:
        return self.version_id == version_id

    def is_stewardship_state(self) -> bool:
        """Publication is not a stewardship state."""
        return False

    def equivalent_stewardship_state(self) -> StewardshipState | None:
        """There is no PUBLISHED stewardship state to map onto."""
        return None
