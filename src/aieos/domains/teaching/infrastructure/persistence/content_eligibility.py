"""Content eligibility adapter for TeachingAssignment CREATE and TeachingExecution START."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.engine import Connection

from aieos.domains.content.domain.identities import ContentId, ContentVersionId
from aieos.domains.content.infrastructure.persistence.repositories import (
    SqlAlchemyContentRepository,
    SqlAlchemyContentVersionRepository,
)
from aieos.domains.education.schema import (
    ContentAudience,
    PREPARATION_ARTIFACT_AUDIENCE,
)
from aieos.domains.teaching.application.errors import (
    ContentNotEligibleForAssignment,
    ContentNotFoundForAssignment,
    ContentVersionMismatch,
    ExecutionContentBindingRejected,
)


class SqlAlchemyContentAssignmentEligibilityAdapter:
    def __init__(self, connection: Connection, execution_tenant_id: UUID) -> None:
        self._execution_tenant_id = execution_tenant_id
        self._contents = SqlAlchemyContentRepository(connection, execution_tenant_id)
        self._versions = SqlAlchemyContentVersionRepository(connection)

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

    def verify_execution_content_version_with_lock(
        self,
        *,
        content_id: ContentId,
        content_version_id: ContentVersionId,
    ) -> None:
        head = self._contents.get_head_for_update(content_id)
        if head is None:
            raise ContentNotFoundForAssignment(
                "Content is not visible in the execution tenant"
            )
        version = self._versions.get(content_version_id)
        if (
            version is None
            or version.content_id != content_id
            or version.tenant_id != self._execution_tenant_id
        ):
            raise ExecutionContentBindingRejected(
                "ContentVersion does not exist under the requested Content"
            )
        audience = PREPARATION_ARTIFACT_AUDIENCE.get(head.content_type)
        if audience is None:
            raise ExecutionContentBindingRejected(
                "Content type has no classified teaching audience for execution binding"
            )
        if audience is ContentAudience.LEARNER:
            if head.published_version_id != content_version_id:
                raise ContentVersionMismatch(
                    "requested ContentVersion is not the published exact version"
                )
