"""TOS-DEV02 Lane B — development reference scenario loader proofs.

Runs against real PostgreSQL through the same HTTP contracts the CLI loader
uses. Synthetic tenant/principal only.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from aieos.development.app_factory import build_development_review_scenario_app
from aieos.development.teacher_os_teaching_work_scenario import (
    SCENARIO_ID,
    WORK_SPECS,
    ensure_teacher_os_teaching_work_scenario,
    write_scenario_report,
)

pytestmark = pytest.mark.tos_dev02

SCENARIO_DATE = date(2026, 8, 27)


def _client(runtime_engine, tenant_id, principal_id) -> TestClient:
    app = build_development_review_scenario_app(
        runtime_engine, tenant_id=tenant_id, principal_id=principal_id
    )
    return TestClient(app, raise_server_exceptions=False)


class TestTeachingWorkScenarioLoader:
    def test_scenario_seeds_reference_work_and_reads_the_mission(
        self, runtime_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, principal_id)

        report = ensure_teacher_os_teaching_work_scenario(
            client,
            tenant_id=tenant_id,
            principal_id=principal_id,
            scenario_date=SCENARIO_DATE,
        )
        assert report.scenario_id == SCENARIO_ID
        assert report.reused_existing is False
        assert len(report.works) == len(WORK_SPECS)
        assert {work.key for work in report.works} == {s.key for s in WORK_SPECS}

        expected_target = (SCENARIO_DATE + timedelta(days=1)).isoformat()
        assert report.target_date == expected_target
        assert all(work.target_date == expected_target for work in report.works)
        assert all(work.intent_type == "prepare_tomorrow" for work in report.works)

        assert report.mission_date == SCENARIO_DATE.isoformat()
        assert report.mission_pending_review_count == 0
        assert report.mission_active_work_count == len(WORK_SPECS)
        assert report.mission_hero_action_kind == "continue_work"

        listed = client.get(
            "/api/v1/teaching/works",
            headers={"X-AIEOS-Tenant-ID": str(tenant_id)},
        )
        assert listed.status_code == 200
        assert len(listed.json()["items"]) == len(WORK_SPECS)

    def test_rerunning_the_scenario_reuses_existing_work(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, principal_id)

        first = ensure_teacher_os_teaching_work_scenario(
            client,
            tenant_id=tenant_id,
            principal_id=principal_id,
            scenario_date=SCENARIO_DATE,
        )
        second = ensure_teacher_os_teaching_work_scenario(
            client,
            tenant_id=tenant_id,
            principal_id=principal_id,
            scenario_date=SCENARIO_DATE,
        )
        assert second.reused_existing is True
        assert {w.work_id for w in second.works} == {w.work_id for w in first.works}

        listed = client.get(
            "/api/v1/teaching/works",
            headers={"X-AIEOS-Tenant-ID": str(tenant_id)},
        )
        assert len(listed.json()["items"]) == len(WORK_SPECS)

    def test_scenario_creates_no_intent_or_mission_rows(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, principal_id)
        ensure_teacher_os_teaching_work_scenario(
            client,
            tenant_id=tenant_id,
            principal_id=principal_id,
            scenario_date=SCENARIO_DATE,
        )
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
            works = conn.execute(
                text(
                    "SELECT count(*) FROM teaching.works WHERE tenant_id = :tid"
                ),
                {"tid": str(tenant_id)},
            ).scalar_one()
            assignments = conn.execute(
                text(
                    "SELECT count(*) FROM teaching.assignments "
                    "WHERE tenant_id = :tid"
                ),
                {"tid": str(tenant_id)},
            ).scalar_one()
        assert tables == {
            "works",
            "assignments",
            "executions",
            "execution_content_bindings",
            "execution_observations",
            "work_remediation_origins",
        }
        assert works == len(WORK_SPECS)
        assert assignments == 0

    def test_report_is_written_as_non_secret_json(
        self, runtime_engine, tmp_path
    ) -> None:
        tenant_id = uuid.uuid7()
        principal_id = uuid.uuid7()
        client = _client(runtime_engine, tenant_id, principal_id)
        report = ensure_teacher_os_teaching_work_scenario(
            client,
            tenant_id=tenant_id,
            principal_id=principal_id,
            scenario_date=SCENARIO_DATE,
        )
        path = tmp_path / "nested" / "tos-dev02.json"
        write_scenario_report(report, path)

        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["scenario_id"] == SCENARIO_ID
        assert payload["tenant_id"] == str(tenant_id)
        assert payload["mission_hero_action_kind"] == "continue_work"
        assert len(payload["works"]) == len(WORK_SPECS)
        serialized = json.dumps(payload).lower()
        for secret_marker in ("password", "secret", "token", "authorization"):
            assert secret_marker not in serialized


class TestScenarioIsExplicitOnly:
    def test_loader_module_has_no_import_time_side_effects(self, runtime_engine) -> None:
        import importlib

        module = importlib.import_module(
            "aieos.development.teacher_os_teaching_work_scenario"
        )
        importlib.reload(module)
        assert module.SCENARIO_ID == SCENARIO_ID
        assert callable(module.ensure_teacher_os_teaching_work_scenario)
