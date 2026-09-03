"""TOS-DEV07-I02 — TeachingExecution HTTP contract tests."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.engine import Engine

from aieos.domains.teaching.domain.observation_kind import ObservationKind
from tests.domains.teaching.helpers_dev07_i02 import (
    START_PATH,
    TEACH_CONTEXT_PATH,
    build_execution_client,
    headers,
    seed_teaching_work,
)

pytestmark = pytest.mark.tos_dev07_i02


class _EmptyReader:
    def list_assignable_classes(self, tenant_id, teacher_principal_id):
        return ()


class TestExecutionHttp:
    def test_start_201_location_etag_idempotency_key(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        work_id = seed_teaching_work(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        client = build_execution_client(runtime_engine, tenant_id, principal_id)
        key = "i02-http-start"
        response = client.post(
            START_PATH,
            headers=headers(tenant_id, idempotency_key=key),
            json={
                "work_id": str(work_id.value),
                "class_ref": "class-5a",
                "bindings": [],
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["lifecycle_state"] == "IN_PROGRESS"
        assert response.headers["Location"] == (
            f"/api/v1/teaching/executions/{body['execution_id']}"
        )
        assert response.headers["ETag"] == '"r0"'
        # Request Idempotency-Key accepted (mutation succeeds); key is not echoed.
        assert "Idempotency-Key" not in response.headers or response.headers.get(
            "Idempotency-Key"
        ) == key

    def test_get_with_etag_and_observations(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        work_id = seed_teaching_work(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        client = build_execution_client(runtime_engine, tenant_id, principal_id)
        created = client.post(
            START_PATH,
            headers=headers(tenant_id, idempotency_key="i02-http-get-start"),
            json={"work_id": str(work_id.value), "class_ref": "class-5a"},
        )
        assert created.status_code == 201, created.text
        execution_id = created.json()["execution_id"]
        obs = client.post(
            f"{START_PATH}/{execution_id}/observations",
            headers=headers(tenant_id, idempotency_key="i02-http-get-obs"),
            json={
                "observation_kind": ObservationKind.CLASS_OBSERVATION.value,
                "body": "class note",
            },
        )
        assert obs.status_code == 201, obs.text
        got = client.get(
            f"{START_PATH}/{execution_id}",
            headers=headers(tenant_id),
        )
        assert got.status_code == 200, got.text
        assert got.headers["ETag"] == '"r0"'
        payload = got.json()
        assert len(payload["observations"]) == 1
        assert payload["observations"][0]["body"] == "class note"

    def test_observation_create_correct_with_if_match(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        work_id = seed_teaching_work(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        client = build_execution_client(runtime_engine, tenant_id, principal_id)
        created = client.post(
            START_PATH,
            headers=headers(tenant_id, idempotency_key="i02-http-obs-start"),
            json={"work_id": str(work_id.value), "class_ref": "class-5a"},
        )
        execution_id = created.json()["execution_id"]
        created_obs = client.post(
            f"{START_PATH}/{execution_id}/observations",
            headers=headers(tenant_id, idempotency_key="i02-http-obs-create"),
            json={
                "observation_kind": ObservationKind.PRIVATE_EXECUTION_NOTE.value,
                "body": "first",
            },
        )
        assert created_obs.status_code == 201, created_obs.text
        assert created_obs.headers["ETag"] == '"r0"'
        observation_id = created_obs.json()["observation_id"]
        corrected = client.patch(
            f"{START_PATH}/{execution_id}/observations/{observation_id}",
            headers=headers(
                tenant_id,
                idempotency_key="i02-http-obs-correct",
                if_match='"r0"',
            ),
            json={"body": "corrected"},
        )
        assert corrected.status_code == 200, corrected.text
        assert corrected.headers["ETag"] == '"r1"'
        assert corrected.json()["body"] == "corrected"

    def test_complete_and_cancel(self, runtime_engine: Engine) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        work_a = seed_teaching_work(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        work_b = seed_teaching_work(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        client = build_execution_client(runtime_engine, tenant_id, principal_id)
        a = client.post(
            START_PATH,
            headers=headers(tenant_id, idempotency_key="i02-http-complete-start"),
            json={"work_id": str(work_a.value), "class_ref": "class-5a"},
        )
        b = client.post(
            START_PATH,
            headers=headers(tenant_id, idempotency_key="i02-http-cancel-start"),
            json={"work_id": str(work_b.value), "class_ref": "class-5a"},
        )
        complete = client.post(
            f"{START_PATH}/{a.json()['execution_id']}/actions/complete",
            headers=headers(
                tenant_id,
                idempotency_key="i02-http-complete",
                if_match=a.headers["ETag"],
            ),
        )
        assert complete.status_code == 200, complete.text
        assert complete.json()["lifecycle_state"] == "COMPLETED"
        cancel = client.post(
            f"{START_PATH}/{b.json()['execution_id']}/actions/cancel",
            headers=headers(
                tenant_id,
                idempotency_key="i02-http-cancel",
                if_match=b.headers["ETag"],
            ),
        )
        assert cancel.status_code == 200, cancel.text
        assert cancel.json()["lifecycle_state"] == "CANCELLED"

    def test_unauthorized_class_problem_details(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        work_id = seed_teaching_work(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        client = build_execution_client(
            runtime_engine,
            tenant_id,
            principal_id,
            school_context_reader=_EmptyReader(),
        )
        response = client.post(
            START_PATH,
            headers=headers(tenant_id, idempotency_key="i02-http-unauth"),
            json={"work_id": str(work_id.value), "class_ref": "class-5a"},
        )
        assert response.status_code == 403
        problem = response.json()
        assert problem["code"] == "class_ref_not_assignable"
        assert problem["title"] == "ClassRef not assignable"

    def test_body_spoof_tenant_teacher_rejected(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        work_id = seed_teaching_work(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        client = build_execution_client(runtime_engine, tenant_id, principal_id)
        for field, value in (
            ("tenant_id", str(uuid.uuid7())),
            ("teacher_principal_id", str(uuid.uuid7())),
            ("teacher_id", str(uuid.uuid7())),
        ):
            response = client.post(
                START_PATH,
                headers=headers(tenant_id, idempotency_key=f"i02-spoof-{field}"),
                json={
                    "work_id": str(work_id.value),
                    "class_ref": "class-5a",
                    field: value,
                },
            )
            assert response.status_code == 422, response.text

    def test_teach_context_get(self, runtime_engine: Engine) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        work_id = seed_teaching_work(
            runtime_engine, tenant_id=tenant_id, principal_id=principal_id
        )
        client = build_execution_client(
            runtime_engine,
            tenant_id,
            principal_id,
            wire_teach_context=True,
        )
        started = client.post(
            START_PATH,
            headers=headers(tenant_id, idempotency_key="i02-http-ctx-start"),
            json={"work_id": str(work_id.value), "class_ref": "class-5a"},
        )
        assert started.status_code == 201, started.text
        response = client.get(
            TEACH_CONTEXT_PATH,
            headers=headers(tenant_id),
            params={
                "work_id": str(work_id.value),
                "class_ref": "class-5a",
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["class_ref"] == "class-5a"
        assert body["display_label"] == "Grade 5A"
        assert body["work"]["work_id"] == str(work_id.value)
        assert len(body["executions"]) == 1
        assert body["artifacts"] == []
