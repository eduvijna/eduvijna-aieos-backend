"""SQLAlchemy Core repositories. They never commit or rollback."""

from __future__ import annotations

from datetime import datetime
from typing import Mapping
from uuid import UUID

from sqlalchemy import and_, or_, select, update
from sqlalchemy.engine import Connection

from aieos.domains.content.application.errors import (
    ContentAlreadyExists,
    ReviewAlreadyDecided,
    VersionAlreadyExists,
)
from aieos.domains.content.application.models import LockedContentHead
from aieos.domains.content.domain.content import Content
from aieos.domains.content.domain.identities import (
    AggregateRevision,
    ContentId,
    ContentVersionId,
    ReviewDecisionId,
    VersionNumber,
)
from aieos.domains.content.domain.review import ReviewDecision
from aieos.domains.content.domain.version import ContentVersion
from aieos.domains.content.infrastructure.persistence.errors import (
    reraise_as_application_error,
)
from aieos.domains.content.infrastructure.persistence.mapping import (
    content_from_row,
    content_version_from_row,
    payload_as_json,
    provenance_as_json,
    review_decision_from_row,
)
from aieos.domains.content.infrastructure.persistence.models import (
    content_versions_table,
    contents_table,
    review_decisions_table,
)


class SqlAlchemyContentVersionRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(
        self,
        version: ContentVersion,
        provenance: Mapping[str, object] | None,
    ) -> None:
        parent = version.parent_version_id
        values: dict[str, object] = {
            "version_id": version.version_id.value,
            "tenant_id": version.tenant_id,
            "content_id": version.content_id.value,
            "version_number": version.version_number.value,
            "parent_version_id": None if parent is None else parent.value,
            "schema_id": str(version.schema_id),
            "schema_version": version.schema_version.value,
            "payload": payload_as_json(version),
            "payload_sha256": version.payload.sha256.value,
            "origin": version.origin.value,
            "created_at": version.created_at,
            "created_by_principal_id": version.created_by_principal_id,
        }
        if provenance is not None:
            values["provenance"] = provenance_as_json(provenance)
        try:
            self._connection.execute(content_versions_table.insert().values(**values))
        except Exception as exc:
            reraise_as_application_error(
                exc,
                unique_conflict=VersionAlreadyExists,
                unique_message="ContentVersion identity or version_number already exists",
            )

    def get(self, version_id: ContentVersionId) -> ContentVersion | None:
        try:
            row = self._connection.execute(
                select(content_versions_table).where(
                    content_versions_table.c.version_id == version_id.value
                )
            ).mappings().one_or_none()
            if row is None:
                return None
            return content_version_from_row(row)
        except Exception as exc:
            reraise_as_application_error(exc)


class SqlAlchemyContentRepository:
    def __init__(self, connection: Connection, execution_tenant_id: UUID) -> None:
        self._connection = connection
        self._execution_tenant_id = execution_tenant_id

    def insert(self, content: Content) -> None:
        values: dict[str, object] = {
            "content_id": content.content_id.value,
            "tenant_id": content.tenant_id,
            "owner_principal_id": content.owner_principal_id,
            "content_type": content.content_type.value,
            "title": content.title,
            "description": content.description,
            "locale": content.locale,
            "stewardship_state": content.stewardship_state.value,
            "current_version_id": (
                None
                if content.current_version_id is None
                else content.current_version_id.value
            ),
            "published_version_id": (
                None
                if content.published_version_id is None
                else content.published_version_id.value
            ),
            "aggregate_revision": content.aggregate_revision.value,
            "created_at": content.created_at,
            "created_by_principal_id": content.created_by_principal_id,
            "updated_at": content.updated_at,
            "archived_at": content.archived_at,
        }
        try:
            self._connection.execute(contents_table.insert().values(**values))
        except Exception as exc:
            reraise_as_application_error(
                exc,
                unique_conflict=ContentAlreadyExists,
                unique_message="Content identity already exists",
            )

    def get(self, content_id: ContentId) -> Content | None:
        try:
            row = (
                self._connection.execute(
                    select(contents_table).where(
                        contents_table.c.content_id == content_id.value,
                        contents_table.c.tenant_id == self._execution_tenant_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None
            return content_from_row(row)
        except Exception as exc:
            reraise_as_application_error(exc)

    def list_page(
        self,
        *,
        limit: int,
        after_created_at: datetime | None,
        after_content_id: ContentId | None,
    ) -> list[Content]:
        stmt = (
            select(contents_table)
            .where(contents_table.c.tenant_id == self._execution_tenant_id)
            .order_by(
                contents_table.c.created_at.desc(),
                contents_table.c.content_id.desc(),
            )
            .limit(limit)
        )
        if after_created_at is not None and after_content_id is not None:
            stmt = stmt.where(
                or_(
                    contents_table.c.created_at < after_created_at,
                    and_(
                        contents_table.c.created_at == after_created_at,
                        contents_table.c.content_id < after_content_id.value,
                    ),
                )
            )
        try:
            rows = self._connection.execute(stmt).mappings().all()
            return [content_from_row(row) for row in rows]
        except Exception as exc:
            reraise_as_application_error(exc)

    def get_head_for_update(self, content_id: ContentId) -> LockedContentHead | None:
        stmt = (
            select(
                contents_table.c.tenant_id,
                contents_table.c.content_id,
                contents_table.c.aggregate_revision,
                contents_table.c.current_version_id,
                contents_table.c.published_version_id,
                contents_table.c.stewardship_state,
                contents_table.c.content_type,
                content_versions_table.c.version_number,
            )
            .select_from(
                contents_table.outerjoin(
                    content_versions_table,
                    (
                        content_versions_table.c.tenant_id == contents_table.c.tenant_id
                    )
                    & (
                        content_versions_table.c.content_id
                        == contents_table.c.content_id
                    )
                    & (
                        content_versions_table.c.version_id
                        == contents_table.c.current_version_id
                    ),
                )
            )
            .where(
                contents_table.c.content_id == content_id.value,
                contents_table.c.tenant_id == self._execution_tenant_id,
            )
            .with_for_update(of=contents_table)
        )
        try:
            row = self._connection.execute(stmt).one_or_none()
        except Exception as exc:
            reraise_as_application_error(exc)
        if row is None:
            return None
        current_id = row.current_version_id
        current_number = row.version_number
        published = row.published_version_id
        return LockedContentHead(
            tenant_id=row.tenant_id,
            content_id=ContentId(row.content_id),
            aggregate_revision=AggregateRevision(int(row.aggregate_revision)),
            current_version_id=(
                None if current_id is None else ContentVersionId(current_id)
            ),
            current_version_number=(
                None if current_number is None else VersionNumber(int(current_number))
            ),
            published_version_id=(
                None if published is None else ContentVersionId(published)
            ),
            stewardship_state=row.stewardship_state,
            content_type=row.content_type,
        )

    def advance_current_version(
        self,
        *,
        content_id: ContentId,
        tenant_id: UUID,
        expected_revision: AggregateRevision,
        expected_current_version_id: ContentVersionId | None,
        expected_state: str,
        new_version_id: ContentVersionId,
        updated_at: datetime,
    ) -> AggregateRevision | None:
        expected_current = (
            None
            if expected_current_version_id is None
            else expected_current_version_id.value
        )
        stmt = (
            update(contents_table)
            .where(
                contents_table.c.tenant_id == tenant_id,
                contents_table.c.content_id == content_id.value,
                contents_table.c.aggregate_revision == expected_revision.value,
                contents_table.c.current_version_id.is_not_distinct_from(
                    expected_current
                ),
                contents_table.c.stewardship_state == expected_state,
            )
            .values(
                current_version_id=new_version_id.value,
                aggregate_revision=contents_table.c.aggregate_revision + 1,
                updated_at=updated_at,
                stewardship_state="GENERATED",
            )
            .returning(contents_table.c.aggregate_revision)
        )
        try:
            row = self._connection.execute(stmt).one_or_none()
        except Exception as exc:
            reraise_as_application_error(exc)
        if row is None:
            return None
        return AggregateRevision(int(row.aggregate_revision))

    def transition_stewardship(
        self,
        *,
        content_id: ContentId,
        tenant_id: UUID,
        expected_revision: AggregateRevision,
        expected_current_version_id: ContentVersionId,
        expected_state: str,
        target_state: str,
        updated_at: datetime,
    ) -> AggregateRevision | None:
        stmt = (
            update(contents_table)
            .where(
                contents_table.c.tenant_id == tenant_id,
                contents_table.c.content_id == content_id.value,
                contents_table.c.current_version_id == expected_current_version_id.value,
                contents_table.c.stewardship_state == expected_state,
                contents_table.c.aggregate_revision == expected_revision.value,
            )
            .values(
                stewardship_state=target_state,
                aggregate_revision=contents_table.c.aggregate_revision + 1,
                updated_at=updated_at,
            )
            .returning(contents_table.c.aggregate_revision)
        )
        try:
            row = self._connection.execute(stmt).one_or_none()
        except Exception as exc:
            reraise_as_application_error(exc)
        if row is None:
            return None
        return AggregateRevision(int(row.aggregate_revision))


class SqlAlchemyReviewDecisionRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, decision: ReviewDecision) -> None:
        try:
            self._connection.execute(
                review_decisions_table.insert().values(
                    review_decision_id=decision.review_decision_id.value,
                    tenant_id=decision.tenant_id,
                    content_id=decision.content_id.value,
                    version_id=decision.version_id.value,
                    decision=decision.decision.value,
                    reason_code=decision.reason_code,
                    comment=decision.comment,
                    reviewer_principal_id=decision.reviewer_principal_id,
                    effective_actor_id=decision.effective_actor_id,
                    delegation_id=decision.delegation_id,
                    decided_at=decision.decided_at,
                    correlation_id=decision.correlation_id,
                )
            )
        except Exception as exc:
            reraise_as_application_error(
                exc,
                unique_conflict=ReviewAlreadyDecided,
                unique_message="this ContentVersion already has a terminal ReviewDecision",
            )

    def get(self, review_decision_id: ReviewDecisionId) -> ReviewDecision | None:
        try:
            row = (
                self._connection.execute(
                    select(review_decisions_table).where(
                        review_decisions_table.c.review_decision_id
                        == review_decision_id.value
                    )
                )
                .mappings()
                .one_or_none()
            )
        except Exception as exc:
            reraise_as_application_error(exc)
        if row is None:
            return None
        return review_decision_from_row(row)

    def get_for_version(
        self, content_id: ContentId, version_id: ContentVersionId
    ) -> ReviewDecision | None:
        try:
            row = (
                self._connection.execute(
                    select(review_decisions_table).where(
                        review_decisions_table.c.content_id == content_id.value,
                        review_decisions_table.c.version_id == version_id.value,
                    )
                )
                .mappings()
                .one_or_none()
            )
        except Exception as exc:
            reraise_as_application_error(exc)
        if row is None:
            return None
        return review_decision_from_row(row)
