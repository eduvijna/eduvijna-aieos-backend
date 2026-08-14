"""GCI-I06R1 shared append stewardship gate against PostgreSQL 18."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.application.errors import ContentVersionAppendNotAllowed
from aieos.domains.content.domain.identities import AggregateRevision, ContentId
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from tests.domains.content.infrastructure.persistence.test_gci_i03_append import (
    FIXED_NOW,
    _append,
    _content_row,
    _make_version,
    _seed_content,
    _version_count,
)

pytestmark = pytest.mark.gci_i06

ARCHIVED_AT = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)


def _set_state(
    bootstrap_engine: Engine,
    content_id: ContentId,
    *,
    state: str,
    archived_at: datetime | None = None,
) -> None:
    with bootstrap_engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE content.contents
                SET stewardship_state = :state,
                    archived_at = :archived_at,
                    published_version_id = CASE
                        WHEN :state = 'ARCHIVED' THEN NULL
                        ELSE published_version_id
                    END
                WHERE content_id = :cid
                """
            ),
            {
                "state": state,
                "archived_at": archived_at,
                "cid": content_id.value,
            },
        )


class TestDirectAppendStewardshipGate:
    def test_draft_append_becomes_generated(
        self, runtime_engine, bootstrap_engine, postgres18
    ) -> None:
        assert postgres18["server_version"].startswith("18.")
        tenant_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id=tenant_id)
        version = _make_version(
            tenant_id=tenant_id, content_id=content_id, version_number=1, parent_version_id=None
        )
        _append(runtime_engine, tenant_id, version, 0)
        row = _content_row(bootstrap_engine, content_id)
        assert row.stewardship_state == "GENERATED"
        assert row.current_version_id == version.version_id.value
        assert int(row.aggregate_revision) == 1

    def test_generated_append_stays_generated(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        content_id = _seed_content(
            bootstrap_engine, tenant_id=tenant_id, stewardship_state="GENERATED"
        )
        v1 = _make_version(
            tenant_id=tenant_id, content_id=content_id, version_number=1, parent_version_id=None
        )
        _append(runtime_engine, tenant_id, v1, 0)
        v2 = _make_version(
            tenant_id=tenant_id,
            content_id=content_id,
            version_number=2,
            parent_version_id=v1.version_id,
        )
        _append(runtime_engine, tenant_id, v2, 1)
        row = _content_row(bootstrap_engine, content_id)
        assert row.stewardship_state == "GENERATED"
        assert row.current_version_id == v2.version_id.value
        assert int(row.aggregate_revision) == 2

    def test_approved_append_becomes_generated_and_preserves_published(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        content_id = _seed_content(
            bootstrap_engine, tenant_id=tenant_id, stewardship_state="APPROVED"
        )
        v1 = _make_version(
            tenant_id=tenant_id, content_id=content_id, version_number=1, parent_version_id=None
        )
        _append(runtime_engine, tenant_id, v1, 0)
        with bootstrap_engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE content.contents SET published_version_id = :vid, "
                    "stewardship_state = 'APPROVED' WHERE content_id = :cid"
                ),
                {"vid": v1.version_id.value, "cid": content_id.value},
            )
        v2 = _make_version(
            tenant_id=tenant_id,
            content_id=content_id,
            version_number=2,
            parent_version_id=v1.version_id,
        )
        _append(runtime_engine, tenant_id, v2, 1)
        row = _content_row(bootstrap_engine, content_id)
        assert row.stewardship_state == "GENERATED"
        assert row.current_version_id == v2.version_id.value
        assert row.published_version_id == v1.version_id.value

    def test_in_review_append_is_blocked(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id=tenant_id)
        v1 = _make_version(
            tenant_id=tenant_id, content_id=content_id, version_number=1, parent_version_id=None
        )
        _append(runtime_engine, tenant_id, v1, 0)
        _set_state(bootstrap_engine, content_id, state="IN_REVIEW")
        before = _content_row(bootstrap_engine, content_id)
        v2 = _make_version(
            tenant_id=tenant_id,
            content_id=content_id,
            version_number=2,
            parent_version_id=v1.version_id,
        )
        with pytest.raises(ContentVersionAppendNotAllowed):
            _append(runtime_engine, tenant_id, v2, int(before.aggregate_revision))
        row = _content_row(bootstrap_engine, content_id)
        assert row.stewardship_state == "IN_REVIEW"
        assert row.current_version_id == v1.version_id.value
        assert int(row.aggregate_revision) == int(before.aggregate_revision)
        assert _version_count(bootstrap_engine, content_id) == 1

    def test_archived_append_is_blocked(self, runtime_engine, bootstrap_engine) -> None:
        tenant_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id=tenant_id)
        v1 = _make_version(
            tenant_id=tenant_id, content_id=content_id, version_number=1, parent_version_id=None
        )
        _append(runtime_engine, tenant_id, v1, 0)
        _set_state(
            bootstrap_engine, content_id, state="ARCHIVED", archived_at=ARCHIVED_AT
        )
        before = _content_row(bootstrap_engine, content_id)
        v2 = _make_version(
            tenant_id=tenant_id,
            content_id=content_id,
            version_number=2,
            parent_version_id=v1.version_id,
        )
        with pytest.raises(ContentVersionAppendNotAllowed):
            _append(runtime_engine, tenant_id, v2, int(before.aggregate_revision))
        row = _content_row(bootstrap_engine, content_id)
        assert row.stewardship_state == "ARCHIVED"
        assert int(row.aggregate_revision) == int(before.aggregate_revision)
        assert _version_count(bootstrap_engine, content_id) == 1
        with bootstrap_engine.connect() as conn:
            archived_at = conn.execute(
                text(
                    "SELECT archived_at FROM content.contents WHERE content_id = :cid"
                ),
                {"cid": content_id.value},
            ).scalar_one()
        assert archived_at == ARCHIVED_AT


class TestAdvanceExpectedStatePredicate:
    def test_mismatched_expected_state_does_not_advance_or_orphan(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        content_id = _seed_content(bootstrap_engine, tenant_id=tenant_id)
        version = _make_version(
            tenant_id=tenant_id, content_id=content_id, version_number=1, parent_version_id=None
        )
        factory = SqlAlchemyContentUnitOfWorkFactory(runtime_engine)
        with factory(tenant_id) as uow:
            uow.versions.insert(version, None)
            resulting = uow.contents.advance_current_version(
                content_id=content_id,
                tenant_id=tenant_id,
                expected_revision=AggregateRevision(0),
                expected_current_version_id=None,
                expected_state="APPROVED",
                new_version_id=version.version_id,
                updated_at=FIXED_NOW,
            )
            assert resulting is None
            uow.rollback()
        row = _content_row(bootstrap_engine, content_id)
        assert row.current_version_id is None
        assert int(row.aggregate_revision) == 0
        assert row.stewardship_state == "DRAFT"
        assert _version_count(bootstrap_engine, content_id) == 0
