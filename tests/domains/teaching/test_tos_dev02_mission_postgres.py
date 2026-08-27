"""TOS-DEV02 Lane B — Today's Mission projection against real PostgreSQL.

Scenario A: pending review items exist        -> hero = review
Scenario B: no pending review, work exists    -> hero = continue_work
Scenario C: nothing pending, no work          -> hero = prepare_tomorrow

The review side uses the real Review Queue projection built by submitting real
Content versions for review. Nothing about the count is mocked.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from uuid import UUID

import pytest

from aieos.development.schemas import DEV_CONTENT_TYPE, DEV_SCHEMA_ID, DEV_SCHEMA_VERSION
from tests.domains.teaching.helpers import build_client, create_work, headers

pytestmark = pytest.mark.tos_dev02

MISSION_DATE = date(2026, 8, 27)
TARGET_DATE = (MISSION_DATE + timedelta(days=1)).isoformat()


def _key(label: str) -> str:
    return f"tos-dev02-mission-{label}-{uuid.uuid7()}"


def _mission(client, tenant_id: UUID, mission_date: date = MISSION_DATE):
    return client.get(
        "/api/v1/teacher-os/today/mission",
        params={"mission_date": mission_date.isoformat()},
        headers=headers(tenant_id),
    )


def _submit_for_review(client, tenant_id: UUID, *, title: str) -> str:
    created = client.post(
        "/api/v1/contents",
        json={
            "content_type": DEV_CONTENT_TYPE,
            "title": title,
            "description": "synthetic TOS-DEV02 mission input",
            "locale": "en-IN",
        },
        headers=headers(tenant_id, idempotency_key=_key(f"content-{title}")),
    )
    assert created.status_code == 201, created.text
    content_id = created.json()["content_id"]
    appended = client.post(
        f"/api/v1/contents/{content_id}/versions",
        json={
            "schema_id": DEV_SCHEMA_ID,
            "schema_version": DEV_SCHEMA_VERSION,
            "payload": {"marker": title, "synthetic": True},
        },
        headers={
            **headers(tenant_id, idempotency_key=_key(f"version-{title}")),
            "If-Match": created.headers["ETag"],
        },
    )
    assert appended.status_code == 201, appended.text
    version_id = appended.json()["version_id"]
    submitted = client.post(
        f"/api/v1/contents/{content_id}/versions/{version_id}"
        "/actions/submit-for-review",
        headers={
            **headers(tenant_id, idempotency_key=_key(f"submit-{title}")),
            "If-Match": appended.headers["ETag"],
        },
    )
    assert submitted.status_code == 200, submitted.text
    return content_id


class TestMissionScenarioA:
    def test_pending_review_items_make_review_the_hero_action(
        self, runtime_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = build_client(runtime_engine, tenant_id, uuid.uuid7())

        _submit_for_review(client, tenant_id, title="mission-a-1")
        _submit_for_review(client, tenant_id, title="mission-a-2")
        # A Work also exists, so this proves review *outranks* continue_work.
        created = create_work(
            client,
            tenant_id,
            goal_text="Scenario A work",
            target_date=TARGET_DATE,
            idempotency_key=_key("a"),
        )
        assert created.status_code == 201, created.text

        response = _mission(client, tenant_id)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["mission_date"] == MISSION_DATE.isoformat()
        assert body["review"]["pending_count"] == 2
        assert body["hero_action"]["kind"] == "review"
        assert body["hero_action"]["work_id"] is None
        # Preparation is still projected alongside the hero action.
        assert body["preparation"]["active_work_count"] == 1
        assert body["preparation"]["continue_work"]["work_id"] == (
            created.json()["work_id"]
        )

    def test_pending_count_falls_to_zero_after_the_queue_is_cleared(
        self, runtime_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = build_client(runtime_engine, tenant_id, uuid.uuid7())
        _submit_for_review(client, tenant_id, title="mission-a-drain")

        before = _mission(client, tenant_id).json()
        assert before["review"]["pending_count"] == 1
        assert before["hero_action"]["kind"] == "review"

        queue = client.get(
            "/api/v1/teacher-os/review-queue", headers=headers(tenant_id)
        )
        assert queue.status_code == 200, queue.text
        for item in queue.json()["items"]:
            detail = client.get(
                f"/api/v1/teacher-os/review-queue/{item['content_id']}"
                f"/versions/{item['version_id']}",
                headers=headers(tenant_id),
            )
            assert detail.status_code == 200, detail.text
            approved = client.post(
                f"/api/v1/contents/{item['content_id']}/versions"
                f"/{item['version_id']}/actions/approve",
                json={},
                headers={
                    **headers(tenant_id, idempotency_key=_key("approve")),
                    "If-Match": detail.headers["ETag"],
                },
            )
            assert approved.status_code == 200, approved.text

        after = _mission(client, tenant_id).json()
        assert after["review"]["pending_count"] == 0
        assert after["hero_action"]["kind"] == "prepare_tomorrow"


class TestMissionScenarioB:
    def test_active_work_without_pending_review_yields_continue_work(
        self, runtime_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = build_client(runtime_engine, tenant_id, uuid.uuid7())

        create_work(
            client,
            tenant_id,
            goal_text="Older scenario B work",
            target_date=TARGET_DATE,
            idempotency_key=_key("b-old"),
        )
        newer = create_work(
            client,
            tenant_id,
            goal_text="Newer scenario B work",
            target_date=TARGET_DATE,
            idempotency_key=_key("b-new"),
            subject="Science",
            topic="Photosynthesis",
        )
        assert newer.status_code == 201, newer.text

        body = _mission(client, tenant_id).json()
        assert body["review"]["pending_count"] == 0
        assert body["hero_action"]["kind"] == "continue_work"
        assert body["hero_action"]["work_id"] == newer.json()["work_id"]
        assert body["preparation"]["active_work_count"] == 2
        summary = body["preparation"]["continue_work"]
        assert summary["work_id"] == newer.json()["work_id"]
        assert summary["goal_text"] == "Newer scenario B work"
        assert summary["subject"] == "Science"
        assert summary["topic"] == "Photosynthesis"
        assert summary["target_date"] == TARGET_DATE
        assert summary["aggregate_revision"] == 0

    def test_continue_work_follows_the_most_recently_updated_work(
        self, runtime_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = build_client(runtime_engine, tenant_id, uuid.uuid7())

        first = create_work(
            client,
            tenant_id,
            goal_text="First work",
            target_date=TARGET_DATE,
            idempotency_key=_key("b-first"),
        )
        second = create_work(
            client,
            tenant_id,
            goal_text="Second work",
            target_date=TARGET_DATE,
            idempotency_key=_key("b-second"),
        )
        assert _mission(client, tenant_id).json()["hero_action"]["work_id"] == (
            second.json()["work_id"]
        )

        refined = client.patch(
            f"/api/v1/teaching/works/{first.json()['work_id']}",
            json={"goal_text": "First work, refined most recently"},
            headers=headers(
                tenant_id, idempotency_key=_key("b-refine"), if_match='"r0"'
            ),
        )
        assert refined.status_code == 200, refined.text

        body = _mission(client, tenant_id).json()
        assert body["hero_action"]["kind"] == "continue_work"
        assert body["hero_action"]["work_id"] == first.json()["work_id"]
        assert body["preparation"]["continue_work"]["goal_text"] == (
            "First work, refined most recently"
        )
        assert body["preparation"]["continue_work"]["aggregate_revision"] == 1


class TestMissionScenarioC:
    def test_empty_teacher_day_yields_prepare_tomorrow(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        client = build_client(runtime_engine, tenant_id, uuid.uuid7())

        response = _mission(client, tenant_id)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["review"]["pending_count"] == 0
        assert body["preparation"]["active_work_count"] == 0
        assert body["preparation"]["continue_work"] is None
        assert body["hero_action"]["kind"] == "prepare_tomorrow"
        assert body["hero_action"]["work_id"] is None


class TestMissionScopeAndContract:
    def test_mission_is_scoped_to_the_calling_teacher(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        owner_client = build_client(runtime_engine, tenant_id, uuid.uuid7())
        other_client = build_client(runtime_engine, tenant_id, uuid.uuid7())

        create_work(
            owner_client,
            tenant_id,
            goal_text="Owner work",
            target_date=TARGET_DATE,
            idempotency_key=_key("scope"),
        )

        owner_body = _mission(owner_client, tenant_id).json()
        assert owner_body["hero_action"]["kind"] == "continue_work"

        other_body = _mission(other_client, tenant_id).json()
        assert other_body["preparation"]["active_work_count"] == 0
        assert other_body["preparation"]["continue_work"] is None
        assert other_body["hero_action"]["kind"] == "prepare_tomorrow"

    def test_mission_is_tenant_isolated(self, runtime_engine) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        principal = uuid.uuid7()
        client_a = build_client(runtime_engine, tenant_a, principal)
        client_b = build_client(runtime_engine, tenant_b, principal)

        _submit_for_review(client_a, tenant_a, title="mission-tenant-a")
        create_work(
            client_a,
            tenant_a,
            goal_text="Tenant A work",
            target_date=TARGET_DATE,
            idempotency_key=_key("tenant-a"),
        )

        body_b = _mission(client_b, tenant_b).json()
        assert body_b["review"]["pending_count"] == 0
        assert body_b["preparation"]["active_work_count"] == 0
        assert body_b["hero_action"]["kind"] == "prepare_tomorrow"

    def test_mission_date_is_required_and_validated(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        client = build_client(runtime_engine, tenant_id, uuid.uuid7())

        missing = client.get(
            "/api/v1/teacher-os/today/mission", headers=headers(tenant_id)
        )
        assert missing.status_code == 422, missing.text

        malformed = client.get(
            "/api/v1/teacher-os/today/mission",
            params={"mission_date": "27-08-2026"},
            headers=headers(tenant_id),
        )
        assert malformed.status_code == 422, malformed.text

        echoed = _mission(client, tenant_id, date(2027, 1, 15)).json()
        assert echoed["mission_date"] == "2027-01-15"

    def test_mission_read_persists_nothing(self, runtime_engine, bootstrap_engine):
        from sqlalchemy import text

        tenant_id = uuid.uuid7()
        client = build_client(runtime_engine, tenant_id, uuid.uuid7())
        create_work(
            client,
            tenant_id,
            goal_text="Projection purity",
            target_date=TARGET_DATE,
            idempotency_key=_key("purity"),
        )

        def _work_rows() -> int:
            with bootstrap_engine.connect() as conn:
                return conn.execute(
                    text(
                        "SELECT count(*) FROM teaching.works "
                        "WHERE tenant_id = :tid"
                    ),
                    {"tid": str(tenant_id)},
                ).scalar_one()

        before = _work_rows()
        for _ in range(3):
            assert _mission(client, tenant_id).status_code == 200
        assert _work_rows() == before
