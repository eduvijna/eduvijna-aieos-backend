"""Content publication eligibility adapter for TeachingAssignment CREATE."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.engine import Connection

from aieos.domains.content.domain.identities import ContentId, ContentVersionId
from aieos.domains.content.infrastructure.persistence.repositories import (
    SqlAlchemyContentRepository,
)
from aieos.domains.education.schema import is_learner_assignable_content_type
from aieos.domains.teaching.application.errors import (
    ContentNotEligibleForAssignment,
    ContentNotFoundForAssignment,
    ContentVersionMismatch,
)


class SqlAlchemyContentAssignmentEligibilityAdapter:
    def __init__(self, connection: Connection, execution_tenant_id: UUID) -> None:
        self._contents = SqlAlchemyContentRepository(connection, execution_tenant_id)

    def verify_published_learner_content_with_lock(
        self,
        *,
        content_id: ContentId,
        content_version_id: ContentVersionId,
    ) -> str:
        head = self._contents.get_head_for_update(content_id)
        if head is None:
            raise ContentNotFoundForAssignment(
                "Content is not visible in the execution tenant"
            )
        if head.published_version_id != content_version_id:
            raise ContentVersionMismatch(
                "requested ContentVersion is not the published exact version"
            )
        if not is_learner_assignable_content_type(head.content_type):
            raise ContentNotEligibleForAssignment(
                "Content type is not learner-assignable for classroom assignment"
            )
        return head.content_type
