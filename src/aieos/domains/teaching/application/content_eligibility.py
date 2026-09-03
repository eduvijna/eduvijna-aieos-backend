"""Content eligibility ports for TeachingAssignment CREATE and TeachingExecution START."""

from __future__ import annotations

from typing import Protocol

from aieos.domains.content.domain.identities import ContentId, ContentVersionId


class ContentAssignmentEligibilityPort(Protocol):
    """Race-safe Content verification under row lock for Teaching mutations."""

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

    def verify_execution_content_version_with_lock(
        self,
        *,
        content_id: ContentId,
        content_version_id: ContentVersionId,
    ) -> None:
        """Lock Content head and verify the exact ContentVersion belongs to it.

        Does not require published or learner-assignable status. Raises when
        Content is missing or the version does not exist under that Content.
        """
