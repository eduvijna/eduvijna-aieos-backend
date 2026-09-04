"""PostgreSQL lock proofs for remediation create versus Assessment mutation."""

from __future__ import annotations

import threading
import uuid

import pytest
from sqlalchemy import text

from aieos.platform.runtime.remediation_assessment_source import (
    SqlAlchemyRemediationAssessmentSource,
)
from tests.dbutil import clear_asset_audit_rows_for_schema_downgrade
from tests.domains.assessment.helpers_dev08_i02 import (
    RECORD_PATH,
    build_assessment_client,
    headers,
)
from tests.domains.teaching.test_tos_dev09_i02_http_postgres import (
    FIXED_TARGET,
    REMEDIATION_PATH,
    _record,
)

pytestmark = pytest.mark.tos_dev09_i02


@pytest.fixture(autouse=True)
def _purge_remediation_state_after_test(bootstrap_engine):
    yield
    clear_asset_audit_rows_for_schema_downgrade(bootstrap_engine)


class _HoldingAssessmentSource:
    def __init__(
        self, source, locked: threading.Event, release: threading.Event
    ) -> None:
        self._source = source
        self._locked = locked
        self._release = release

    def load_for_update(self, assessment_id):
        snapshot = self._source.load_for_update(assessment_id)
        if snapshot is not None:
            self._locked.set()
            if not self._release.wait(timeout=20):
                raise TimeoutError("test did not release Assessment row lock")
        return snapshot


class _HoldingAssessmentSourceFactory:
    def __init__(self, locked: threading.Event, release: threading.Event) -> None:
        self._locked = locked
        self._release = release

    def __call__(self, connection: object, execution_tenant_id):
        real = SqlAlchemyRemediationAssessmentSource(
            connection, execution_tenant_id
        )
        return _HoldingAssessmentSource(real, self._locked, self._release)


@pytest.mark.parametrize("mutation", ("correct", "void"))
def test_remediation_snapshot_is_locked_before_assessment_mutation(
    runtime_engine, bootstrap_engine, mutation: str
) -> None:
    tenant_id, teacher_id = uuid.uuid7(), uuid.uuid7()
    locked = threading.Event()
    release = threading.Event()
    remediation_done = threading.Event()
    mutation_started = threading.Event()
    mutation_done = threading.Event()
    responses: dict[str, object] = {}

    remediation_client = build_assessment_client(
        runtime_engine,
        tenant_id,
        teacher_id,
        remediation_assessment_source_factory=_HoldingAssessmentSourceFactory(
            locked, release
        ),
    )
    mutation_client = build_assessment_client(
        runtime_engine, tenant_id, teacher_id
    )
    _client, assessment, _content_id, _version_id = _record(
        runtime_engine,
        bootstrap_engine,
        tenant_id=tenant_id,
        principal_id=teacher_id,
        class_result_level="MIXED",
        client=remediation_client,
    )
    assessment_id = uuid.UUID(assessment["assessment_id"])
    source_revision = assessment["aggregate_revision"]

    def create_remediation() -> None:
        try:
            responses["remediation"] = remediation_client.post(
                REMEDIATION_PATH,
                headers=headers(tenant_id, idempotency_key=f"race-{mutation}-create"),
                json={
                    "assessment_id": str(assessment_id),
                    "expected_assessment_aggregate_revision": source_revision,
                    "goal_text": "Re-teach fractions after locked evidence",
                    "target_date": FIXED_TARGET,
                    "locale": "en-IN",
                    "subject": "Mathematics",
                    "topic": "Fractions",
                },
            )
        except BaseException as exc:  # noqa: BLE001 — capture for main-thread assert
            responses["remediation_error"] = exc
        finally:
            remediation_done.set()

    def mutate_assessment() -> None:
        mutation_started.set()
        try:
            path = f"{RECORD_PATH}/{assessment_id}/actions/{mutation}"
            kwargs: dict[str, object] = {}
            if mutation == "correct":
                kwargs["json"] = {
                    "class_result_level": "DEMONSTRATED",
                    "class_result_note": "corrected after remediation lock",
                }
            responses["mutation"] = mutation_client.post(
                path,
                headers=headers(
                    tenant_id,
                    idempotency_key=f"race-{mutation}-mutation",
                    if_match=f'"r{source_revision}"',
                ),
                **kwargs,
            )
        except BaseException as exc:  # noqa: BLE001 — capture for main-thread assert
            responses["mutation_error"] = exc
        finally:
            mutation_done.set()

    create_thread = threading.Thread(target=create_remediation, name="remediation")
    mutation_thread = threading.Thread(target=mutate_assessment, name=mutation)
    try:
        create_thread.start()
        assert locked.wait(timeout=15), (
            "remediation did not acquire Assessment row lock"
        )

        mutation_thread.start()
        assert mutation_started.wait(timeout=5), "mutation thread did not start"
        # While A holds FOR UPDATE, B must not finish. Poll without long sleep.
        assert not mutation_done.wait(timeout=1.0), (
            f"Assessment {mutation} completed while remediation held FOR UPDATE"
        )
        assert not remediation_done.is_set()
    finally:
        release.set()
        assert remediation_done.wait(timeout=20), "remediation create did not finish"
        assert mutation_done.wait(timeout=20), f"Assessment {mutation} did not finish"
        create_thread.join(timeout=5)
        mutation_thread.join(timeout=5)

    assert "remediation_error" not in responses, responses.get("remediation_error")
    assert "mutation_error" not in responses, responses.get("mutation_error")

    remediation_response = responses["remediation"]
    mutation_response = responses["mutation"]
    assert remediation_response.status_code == 201, remediation_response.text
    assert mutation_response.status_code == 200, mutation_response.text
    work_id = uuid.UUID(remediation_response.json()["work_id"])

    with bootstrap_engine.connect() as conn:
        origin = conn.execute(
            text(
                """
                SELECT source_assessment_aggregate_revision,
                       source_class_result_level_snapshot
                FROM teaching.work_remediation_origins
                WHERE work_id = :work_id
                """
            ),
            {"work_id": work_id},
        ).one()
        counts = conn.execute(
            text(
                """
                SELECT
                  (SELECT count(*) FROM teaching.works
                   WHERE tenant_id = :tenant_id
                     AND teacher_principal_id = :teacher_id
                     AND intent_type = 'remediate_class') AS works,
                  (SELECT count(*) FROM teaching.work_remediation_origins
                   WHERE tenant_id = :tenant_id
                     AND source_assessment_id = :assessment_id) AS origins,
                  (SELECT count(*) FROM security.audit_records
                   WHERE tenant_id = :tenant_id
                     AND action = 'teaching.work.remediation.create'
                     AND primary_resource_id = :work_id) AS audits,
                  (SELECT count(*) FROM api.idempotency_records
                   WHERE tenant_id = :tenant_id
                     AND operation =
                       'teaching_work_from_classroom_assessment_create.v1'
                     AND result_content_id = :work_id) AS idempotency
                """
            ),
            {
                "tenant_id": tenant_id,
                "teacher_id": teacher_id,
                "assessment_id": assessment_id,
                "work_id": work_id,
            },
        ).one()

    assert origin.source_assessment_aggregate_revision == source_revision
    assert origin.source_class_result_level_snapshot == "MIXED"
    assert tuple(counts) == (1, 1, 1, 1)
    if mutation == "correct":
        assert mutation_response.json()["class_result_level"] == "DEMONSTRATED"
    else:
        assert mutation_response.json()["lifecycle_state"] == "VOIDED"
