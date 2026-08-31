"""Content publication eligibility port for TeachingAssignment CREATE."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from aieos.domains.content.domain.identities import ContentId, ContentVersionId


class ContentAssignmentEligibilityPort(Protocol):
    """Race-safe published learner-content verification under row lock."""

    def verify_published_learner_content_with_lock(
        self,
        *,
        content_id: ContentId,
        content_version_id: ContentVersionId,
    ) -> str:
        """Return the locked content_type when eligible.

        Raises application errors when Content is missing, unpublished,
        version-mismatched, or not learner-assignable.
        """
