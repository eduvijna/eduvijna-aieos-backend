"""TOS-DEV02 Lane B — Teaching Work durability proofs against real PostgreSQL.

Every assertion runs through the published HTTP contract and the real
SQLAlchemy adapter under the runtime role (RLS enforced, no DELETE grant).
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from uuid import UUID

import pytest
from sqlalchemy import text

from aieos.domains.teaching.application.models import (
    ListTeachingWorksQuery,
    RefineTeachingWorkCommand,
)
from aieos.domains.teaching.application.queries import ListTeachingWorksService
from aieos.domains.teaching.domain.identities import AggregateRevision, WorkId
from aieos.domains.teaching.infrastructure.persistence.uow import (
    SqlAlchemyTeachingUnitOfWorkFactory,
)
from tests.domains.teaching.helpers import build_client, create_work, headers

pytestmark = pytest.mark.tos_dev02

TARGET_DATE = (date(2026, 8, 27) + timedelta(days=1)).isoformat()


def _key(label: str) -> str:
    return f"tos-dev02-{label}-{uuid.uuid7()}"


class TestTeachingWorkCreate:
    def test_create_returns_durable_work_with_etag_and_location(
        self, runtime_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = build_client(runtime_engine, tenant_id, principal_id)

        created = create_work(
            client,
            tenant_id,
            goal_text="Prepare tomorrow's fractions lesson",
            target_date=TARGET_DATE,
            idempotency_key=_key("create"),
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["intent_type"] == "prepare_tomorrow"
        assert body["goal_text"] == "Prepare tomorrow's fractions lesson"
        assert body["class_label"] == "Grade 5B"
        assert body["target_date"] == TARGET_DATE
        assert body["aggregate_revision"] == 0
        assert body["archived_at"] is None
        assert UUID(body["work_id"]).version == 7
        assert created.headers["ETag"] == '"r0"'
        assert created.headers["Location"] == (
            f"/api/v1/teaching/works/{body['work_id']}"
        )

        fetched = client.get(
            f"/api/v1/teaching/works/{body['work_id']}", headers=headers(tenant_id)
        )
        assert fetched.status_code == 200, fetched.text
        assert fetched.json() == body
        assert fetched.headers["ETag"] == '"r0"'

    def test_create_requires_idempotency_key(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        client = build_client(runtime_engine, tenant_id, uuid.uuid7())
        response = client.post(
            "/api/v1/teaching/works",
            json={
                "intent_type": "prepare_tomorrow",
                "goal_text": "no key",
                "target_date": TARGET_DATE,
                "locale": "en-IN",
            },
            headers=headers(tenant_id),
        )
        assert response.status_code == 400, response.text
        assert response.json()["code"] == "idempotency_key_required"

    def test_create_replays_same_key_and_rejects_drifted_request(
        self, runtime_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = build_client(runtime_engine, tenant_id, uuid.uuid7())
        key = _key("replay")

        first = create_work(
            client,
            tenant_id,
            goal_text="Replayable intent",
            target_date=TARGET_DATE,
            idempotency_key=key,
        )
        assert first.status_code == 201, first.text
        replay = create_work(
            client,
            tenant_id,
            goal_text="Replayable intent",
            target_date=TARGET_DATE,
            idempotency_key=key,
        )
        assert replay.status_code == 201, replay.text
        assert replay.json()["work_id"] == first.json()["work_id"]

        drifted = create_work(
            client,
            tenant_id,
            goal_text="Different intent under the same key",
            target_date=TARGET_DATE,
            idempotency_key=key,
        )
        assert drifted.status_code == 409, drifted.text
        assert drifted.json()["code"] == "idempotency_key_reused"

        listed = client.get("/api/v1/teaching/works", headers=headers(tenant_id))
        assert listed.status_code == 200
        assert len(listed.json()["items"]) == 1

    def test_unknown_intent_type_is_rejected(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        client = build_client(runtime_engine, tenant_id, uuid.uuid7())
        response = create_work(
            client,
            tenant_id,
            goal_text="Unregistered intent",
            target_date=TARGET_DATE,
            idempotency_key=_key("bad-intent"),
            intent_type="summarise_yesterday",
        )
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "invalid_teaching_work_request"


class TestTeachingWorkRefine:
    def test_refine_patches_fields_and_bumps_revision(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        client = build_client(runtime_engine, tenant_id, uuid.uuid7())
        created = create_work(
            client,
            tenant_id,
            goal_text="Original goal",
            target_date=TARGET_DATE,
            idempotency_key=_key("refine-create"),
        )
        assert created.status_code == 201, created.text
        work_id = created.json()["work_id"]

        refined = client.patch(
            f"/api/v1/teaching/works/{work_id}",
            json={"goal_text": "Refined goal", "topic": "Equivalent fractions"},
            headers=headers(
                tenant_id,
                idempotency_key=_key("refine"),
                if_match=created.headers["ETag"],
            ),
        )
        assert refined.status_code == 200, refined.text
        body = refined.json()
        assert body["goal_text"] == "Refined goal"
        assert body["topic"] == "Equivalent fractions"
        assert body["subject"] == "Mathematics"  # untouched field is preserved
        assert body["class_label"] == "Grade 5B"
        assert body["aggregate_revision"] == 1
        assert refined.headers["ETag"] == '"r1"'
        assert body["updated_at"] > created.json()["updated_at"]
        assert body["created_at"] == created.json()["created_at"]

    def test_refine_can_clear_a_nullable_contextual_field(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        client = build_client(runtime_engine, tenant_id, uuid.uuid7())
        created = create_work(
            client,
            tenant_id,
            goal_text="Clearable topic",
            target_date=TARGET_DATE,
            idempotency_key=_key("clear-create"),
        )
        work_id = created.json()["work_id"]

        cleared = client.patch(
            f"/api/v1/teaching/works/{work_id}",
            json={"topic": None},
            headers=headers(
                tenant_id,
                idempotency_key=_key("clear"),
                if_match=created.headers["ETag"],
            ),
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["topic"] is None
        assert cleared.json()["subject"] == "Mathematics"

    def test_refine_requires_if_match_and_rejects_stale_revision(
        self, runtime_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        client = build_client(runtime_engine, tenant_id, uuid.uuid7())
        created = create_work(
            client,
            tenant_id,
            goal_text="Precondition goal",
            target_date=TARGET_DATE,
            idempotency_key=_key("stale-create"),
        )
        work_id = created.json()["work_id"]

        missing = client.patch(
            f"/api/v1/teaching/works/{work_id}",
            json={"goal_text": "no precondition"},
            headers=headers(tenant_id, idempotency_key=_key("stale-missing")),
        )
        assert missing.status_code == 428, missing.text
        assert missing.json()["code"] == "precondition_required"

        first = client.patch(
            f"/api/v1/teaching/works/{work_id}",
            json={"goal_text": "first refine"},
            headers=headers(
                tenant_id,
                idempotency_key=_key("stale-first"),
                if_match='"r0"',
            ),
        )
        assert first.status_code == 200, first.text

        stale = client.patch(
            f"/api/v1/teaching/works/{work_id}",
            json={"goal_text": "second refine on stale revision"},
            headers=headers(
                tenant_id,
                idempotency_key=_key("stale-second"),
                if_match='"r0"',
            ),
        )
        assert stale.status_code == 412, stale.text
        assert stale.json()["code"] == "resource_revision_conflict"

        current = client.get(
            f"/api/v1/teaching/works/{work_id}", headers=headers(tenant_id)
        )
        assert current.json()["goal_text"] == "first refine"
        assert current.json()["aggregate_revision"] == 1

    def test_refine_replay_is_idempotent(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        client = build_client(runtime_engine, tenant_id, uuid.uuid7())
        created = create_work(
            client,
            tenant_id,
            goal_text="Idempotent refine",
            target_date=TARGET_DATE,
            idempotency_key=_key("idem-refine-create"),
        )
        work_id = created.json()["work_id"]
        key = _key("idem-refine")

        first = client.patch(
            f"/api/v1/teaching/works/{work_id}",
            json={"goal_text": "refined once"},
            headers=headers(tenant_id, idempotency_key=key, if_match='"r0"'),
        )
        assert first.status_code == 200, first.text
        replay = client.patch(
            f"/api/v1/teaching/works/{work_id}",
            json={"goal_text": "refined once"},
            headers=headers(tenant_id, idempotency_key=key, if_match='"r0"'),
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["aggregate_revision"] == 1
        assert replay.json() == first.json()

    def test_empty_refine_body_is_rejected(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        client = build_client(runtime_engine, tenant_id, uuid.uuid7())
        created = create_work(
            client,
            tenant_id,
            goal_text="Empty patch",
            target_date=TARGET_DATE,
            idempotency_key=_key("empty-create"),
        )
        work_id = created.json()["work_id"]
        response = client.patch(
            f"/api/v1/teaching/works/{work_id}",
            json={},
            headers=headers(
                tenant_id, idempotency_key=_key("empty"), if_match='"r0"'
            ),
        )
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "invalid_teaching_work_request"


class TestTeachingWorkOwnershipAndTenancy:
    def test_another_teacher_in_the_same_tenant_cannot_read_or_refine(
        self, runtime_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        owner = uuid.uuid7()
        other_teacher = uuid.uuid7()
        owner_client = build_client(runtime_engine, tenant_id, owner)
        other_client = build_client(runtime_engine, tenant_id, other_teacher)

        created = create_work(
            owner_client,
            tenant_id,
            goal_text="Owner-only work",
            target_date=TARGET_DATE,
            idempotency_key=_key("owner-create"),
        )
        work_id = created.json()["work_id"]

        foreign_get = other_client.get(
            f"/api/v1/teaching/works/{work_id}", headers=headers(tenant_id)
        )
        assert foreign_get.status_code == 403, foreign_get.text
        assert foreign_get.json()["code"] == "forbidden"

        foreign_patch = other_client.patch(
            f"/api/v1/teaching/works/{work_id}",
            json={"goal_text": "hijacked"},
            headers=headers(
                tenant_id, idempotency_key=_key("owner-hijack"), if_match='"r0"'
            ),
        )
        assert foreign_patch.status_code == 403, foreign_patch.text

        foreign_list = other_client.get(
            "/api/v1/teaching/works", headers=headers(tenant_id)
        )
        assert foreign_list.status_code == 200
        assert foreign_list.json()["items"] == []

        unchanged = owner_client.get(
            f"/api/v1/teaching/works/{work_id}", headers=headers(tenant_id)
        )
        assert unchanged.json()["goal_text"] == "Owner-only work"
        assert unchanged.json()["aggregate_revision"] == 0

    def test_row_level_security_isolates_tenants(self, runtime_engine) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        principal = uuid.uuid7()
        client_a = build_client(runtime_engine, tenant_a, principal)
        client_b = build_client(runtime_engine, tenant_b, principal)

        created = create_work(
            client_a,
            tenant_a,
            goal_text="Tenant A only",
            target_date=TARGET_DATE,
            idempotency_key=_key("tenant-a"),
        )
        work_id = created.json()["work_id"]

        foreign = client_b.get(
            f"/api/v1/teaching/works/{work_id}", headers=headers(tenant_b)
        )
        assert foreign.status_code == 404, foreign.text
        assert foreign.json()["code"] == "teaching_work_not_found"

        listed_b = client_b.get("/api/v1/teaching/works", headers=headers(tenant_b))
        assert listed_b.json()["items"] == []

        # Same principal, same connection pool, different tenant GUC: the RLS
        # policy — not application filtering alone — must hide the row.
        factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
        with factory(tenant_b) as uow:
            assert uow.works.get(WorkId(UUID(work_id))) is None
        with factory(tenant_a) as uow:
            assert uow.works.get(WorkId(UUID(work_id))) is not None

    def test_runtime_role_cannot_delete_teaching_work(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        client = build_client(runtime_engine, tenant_id, uuid.uuid7())
        created = create_work(
            client,
            tenant_id,
            goal_text="Undeletable work",
            target_date=TARGET_DATE,
            idempotency_key=_key("nodelete"),
        )
        work_id = created.json()["work_id"]

        with runtime_engine.connect() as conn:
            conn.execute(
                text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            with pytest.raises(Exception) as excinfo:
                conn.execute(
                    text("DELETE FROM teaching.works WHERE work_id = :wid"),
                    {"wid": work_id},
                )
            assert "permission denied" in str(excinfo.value).lower()
            conn.rollback()

        still_there = client.get(
            f"/api/v1/teaching/works/{work_id}", headers=headers(tenant_id)
        )
        assert still_there.status_code == 200


class TestTeachingWorkDurability:
    def test_work_survives_unit_of_work_recreation(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = build_client(runtime_engine, tenant_id, principal_id)
        created = create_work(
            client,
            tenant_id,
            goal_text="Durable across UoW",
            target_date=TARGET_DATE,
            idempotency_key=_key("durable"),
        )
        work_id = WorkId(UUID(created.json()["work_id"]))

        factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
        with factory(tenant_id) as uow:
            first = uow.works.get(work_id)
        assert first is not None
        assert first.goal_text == "Durable across UoW"
        assert int(first.aggregate_revision) == 0

        # A completely independent factory + Unit of Work still sees the row.
        second_factory = SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
        with second_factory(tenant_id) as uow:
            second = uow.works.get(work_id)
        assert second is not None
        assert second.work_id == first.work_id
        assert second.teacher_principal_id == principal_id

        refined = client.patch(
            f"/api/v1/teaching/works/{work_id}",
            json={"goal_text": "Durable and refined"},
            headers=headers(
                tenant_id, idempotency_key=_key("durable-refine"), if_match='"r0"'
            ),
        )
        assert refined.status_code == 200, refined.text

        with SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)(tenant_id) as uow:
            third = uow.works.get(work_id)
        assert third is not None
        assert third.goal_text == "Durable and refined"
        assert int(third.aggregate_revision) == 1

    def test_list_is_teacher_scoped_and_excludes_archived_by_default(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = build_client(runtime_engine, tenant_id, principal_id)

        active = create_work(
            client,
            tenant_id,
            goal_text="Active work",
            target_date=TARGET_DATE,
            idempotency_key=_key("list-active"),
        )
        archived = create_work(
            client,
            tenant_id,
            goal_text="Archived work",
            target_date=TARGET_DATE,
            idempotency_key=_key("list-archived"),
        )
        assert active.status_code == 201
        assert archived.status_code == 201
        archived_id = archived.json()["work_id"]

        # Archiving has no TOS-DEV02 HTTP contract; set the column directly to
        # prove the read path honours archived_at.
        with bootstrap_engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE teaching.works SET archived_at = now() "
                    "WHERE work_id = :wid"
                ),
                {"wid": archived_id},
            )

        default_list = client.get("/api/v1/teaching/works", headers=headers(tenant_id))
        assert default_list.status_code == 200
        ids = [item["work_id"] for item in default_list.json()["items"]]
        assert ids == [active.json()["work_id"]]

        with_archived = client.get(
            "/api/v1/teaching/works",
            params={"include_archived": "true"},
            headers=headers(tenant_id),
        )
        assert sorted(item["work_id"] for item in with_archived.json()["items"]) == (
            sorted([active.json()["work_id"], archived_id])
        )

        service = ListTeachingWorksService(
            SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine)
        )
        result = service.list(
            tenant_id, principal_id, ListTeachingWorksQuery(limit=10)
        )
        assert [str(item.work_id) for item in result.items] == [
            active.json()["work_id"]
        ]
        assert result.has_more is False


class TestNoTeachingIntentSystemOfRecord:
    def test_information_schema_has_no_teaching_intent_table(
        self, bootstrap_engine
    ) -> None:
        with bootstrap_engine.connect() as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'teaching'"
                    )
                )
            }
        assert tables == {
            "works",
            "assignments",
            "executions",
            "execution_content_bindings",
            "execution_observations",
        }
        assert "teaching_intents" not in tables
        assert not any("intent" in name for name in tables)
        assert not any("mission" in name for name in tables)

    def test_no_intent_or_mission_table_exists_in_any_schema(
        self, bootstrap_engine
    ) -> None:
        with bootstrap_engine.connect() as conn:
            rows = [
                (row[0], row[1])
                for row in conn.execute(
                    text(
                        "SELECT table_schema, table_name "
                        "FROM information_schema.tables "
                        "WHERE table_schema NOT IN "
                        "('pg_catalog', 'information_schema')"
                    )
                )
            ]
        offenders = [
            f"{schema}.{table}"
            for schema, table in rows
            if "teaching_intent" in table or table in {"missions", "mission"}
        ]
        assert offenders == []


class TestApplicationLayerContracts:
    def test_refine_command_reports_no_changes_for_empty_patch(self) -> None:
        assert RefineTeachingWorkCommand().has_changes() is False
        assert RefineTeachingWorkCommand(topic=None).has_changes() is True

    def test_aggregate_revision_rejects_negative_values(self) -> None:
        from aieos.domains.teaching.domain.errors import InvalidAggregateRevisionError

        with pytest.raises(InvalidAggregateRevisionError):
            AggregateRevision(-1)
        assert int(AggregateRevision(0).next()) == 1
