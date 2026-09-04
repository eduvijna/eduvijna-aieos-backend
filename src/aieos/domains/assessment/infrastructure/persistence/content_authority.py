"""Assessment Content authority adapter — Case C current publication check."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.engine import Connection

from aieos.domains.assessment.application.composition import (
    ASSESSMENT_ELIGIBLE_CONTENT_TYPES,
)
from aieos.domains.assessment.application.errors import (
    ContentNotEligibleForAssessment,
    ContentNotFoundForAssessment,
    ContentVersionMismatch,
)
from aieos.domains.content.domain.identities import ContentId, ContentVersionId
from aieos.domains.content.infrastructure.persistence.repositories import (
    SqlAlchemyContentRepository,
)


class SqlAlchemyAssessmentContentAuthorityAdapter:
    def __init__(self, connection: Connection, execution_tenant_id: UUID) -> None:
        self._contents = SqlAlchemyContentRepository(connection, execution_tenant_id)

    def verify_current_published_assessment_content(
        self,
        *,
        content_id: UUID,
        content_version_id: UUID,
    ) -> str:
        head = self._contents.get_head_for_update(ContentId(content_id))
        if head is None:
            raise ContentNotFoundForAssessment(
                "Content is not visible in the execution tenant"
            )
        requested = ContentVersionId(content_version_id)
        if head.published_version_id is None or head.published_version_id != requested:
            raise ContentVersionMismatch(
                "requested ContentVersion is not the current published version"
            )
        if head.content_type not in ASSESSMENT_ELIGIBLE_CONTENT_TYPES:
            raise ContentNotEligibleForAssessment(
                "Content type is not Assessment-eligible"
            )
        return head.content_type
