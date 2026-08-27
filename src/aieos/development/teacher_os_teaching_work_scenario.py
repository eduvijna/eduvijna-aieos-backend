"""TOS-DEV02 Teaching Work development reference scenario.

NON_PRODUCTION. Synthetic tenant/principal only, reusing the TOS-DEV01
identities so both scenarios describe one coherent synthetic teacher.

Reference Work is created through the published HTTP contract:

  POST /api/v1/teaching/works  →  GET /api/v1/teacher-os/today/mission

No AI generation, no content creation, and no automatic seeding: the loader
CLI is the only entry point.

Repeatability: the scenario first lists the teacher's existing Work and reuses
any row carrying the scenario goal_text marker, so re-running never creates
duplicates. Deterministic Idempotency-Key values give a second layer of
protection inside the retention window.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from aieos.development.teacher_os_review_scenario import (
    SYNTHETIC_PRINCIPAL_ID,
    SYNTHETIC_TENANT_ID,
)

__all__ = [
    "SCENARIO_ID",
    "SYNTHETIC_PRINCIPAL_ID",
    "SYNTHETIC_TENANT_ID",
    "TeachingWorkSpec",
    "WORK_SPECS",
    "ensure_teacher_os_teaching_work_scenario",
    "write_scenario_report",
]

SCENARIO_ID = "tos-dev02-teacher-os-teaching-work"
INTENT_PREPARE_TOMORROW = "prepare_tomorrow"


@dataclass(frozen=True, slots=True)
class TeachingWorkSpec:
    key: str
    goal_text: str
    class_label: str
    subject: str
    topic: str
    locale: str


WORK_SPECS: tuple[TeachingWorkSpec, ...] = (
    TeachingWorkSpec(
        key="prepare-tomorrow-fractions",
        goal_text=(
            "[TOS-DEV02:prepare-tomorrow-fractions] Prepare tomorrow's Grade 5 "
            "fractions lesson so every learner can compare unlike denominators."
        ),
        class_label="Grade 5B",
        subject="Mathematics",
        topic="Comparing fractions",
        locale="en-IN",
    ),
    TeachingWorkSpec(
        key="prepare-tomorrow-photosynthesis",
        goal_text=(
            "[TOS-DEV02:prepare-tomorrow-photosynthesis] Prepare tomorrow's "
            "Grade 6 science lesson introducing photosynthesis."
        ),
        class_label="Grade 6A",
        subject="Science",
        topic="Photosynthesis",
        locale="en-IN",
    ),
)


@dataclass(slots=True)
class SeededTeachingWork:
    key: str
    work_id: str
    intent_type: str
    goal_text: str
    target_date: str
    etag: str


@dataclass(slots=True)
class TeachingWorkScenarioReport:
    scenario_id: str
    tenant_id: str
    principal_id: str
    mission_date: str
    target_date: str
    works: list[SeededTeachingWork]
    reused_existing: bool
    mission_hero_action_kind: str
    mission_pending_review_count: int
    mission_active_work_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "tenant_id": self.tenant_id,
            "principal_id": self.principal_id,
            "mission_date": self.mission_date,
            "target_date": self.target_date,
            "reused_existing": self.reused_existing,
            "mission_hero_action_kind": self.mission_hero_action_kind,
            "mission_pending_review_count": self.mission_pending_review_count,
            "mission_active_work_count": self.mission_active_work_count,
            "works": [asdict(work) for work in self.works],
        }


def _idem_key(tenant_id: UUID, work_key: str, step: str) -> str:
    return f"tos-dev02:{SCENARIO_ID}:{tenant_id}:{work_key}:{step}"


def _headers(tenant_id: UUID, *, idempotency_key: str | None = None) -> dict[str, str]:
    out = {"X-AIEOS-Tenant-ID": str(tenant_id)}
    if idempotency_key is not None:
        out["Idempotency-Key"] = idempotency_key
    return out


def _list_works(client: Any, tenant_id: UUID) -> list[dict[str, Any]]:
    response = client.get(
        "/api/v1/teaching/works",
        params={"limit": 100},
        headers=_headers(tenant_id),
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"teaching works list failed: {response.status_code} {response.text}"
        )
    return list(response.json()["items"])


def _find_work(items: list[dict[str, Any]], *, goal_text: str) -> dict[str, Any] | None:
    for item in items:
        if item.get("goal_text") == goal_text:
            return item
    return None


def _seed_one(
    client: Any,
    tenant_id: UUID,
    spec: TeachingWorkSpec,
    *,
    target_date: date,
    existing_items: list[dict[str, Any]],
) -> tuple[SeededTeachingWork, bool]:
    existing = _find_work(existing_items, goal_text=spec.goal_text)
    if existing is not None:
        return (
            SeededTeachingWork(
                key=spec.key,
                work_id=existing["work_id"],
                intent_type=existing["intent_type"],
                goal_text=existing["goal_text"],
                target_date=existing["target_date"],
                etag=f'"r{existing["aggregate_revision"]}"',
            ),
            True,
        )

    created = client.post(
        "/api/v1/teaching/works",
        json={
            "intent_type": INTENT_PREPARE_TOMORROW,
            "goal_text": spec.goal_text,
            "target_date": target_date.isoformat(),
            "locale": spec.locale,
            "class_label": spec.class_label,
            "subject": spec.subject,
            "topic": spec.topic,
        },
        headers=_headers(
            tenant_id, idempotency_key=_idem_key(tenant_id, spec.key, "create")
        ),
    )
    if created.status_code not in (200, 201):
        raise RuntimeError(f"teaching work create failed for {spec.key}: {created.text}")
    body = created.json()
    return (
        SeededTeachingWork(
            key=spec.key,
            work_id=body["work_id"],
            intent_type=body["intent_type"],
            goal_text=body["goal_text"],
            target_date=body["target_date"],
            etag=created.headers["ETag"],
        ),
        False,
    )


def _read_mission(client: Any, tenant_id: UUID, mission_date: date) -> dict[str, Any]:
    response = client.get(
        "/api/v1/teacher-os/today/mission",
        params={"mission_date": mission_date.isoformat()},
        headers=_headers(tenant_id),
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"today's mission read failed: {response.status_code} {response.text}"
        )
    return dict(response.json())


def ensure_teacher_os_teaching_work_scenario(
    client: Any,
    *,
    tenant_id: UUID,
    principal_id: UUID,
    scenario_date: date,
) -> TeachingWorkScenarioReport:
    """Idempotently ensure reference Teaching Work exists, then read the Mission.

    target_date is tomorrow relative to scenario_date, matching the
    prepare_tomorrow intent.
    """
    target_date = scenario_date + timedelta(days=1)
    existing_items = _list_works(client, tenant_id)
    works: list[SeededTeachingWork] = []
    reused_all = True
    for spec in WORK_SPECS:
        seeded, reused = _seed_one(
            client,
            tenant_id,
            spec,
            target_date=target_date,
            existing_items=existing_items,
        )
        works.append(seeded)
        reused_all = reused_all and reused
        if not reused:
            existing_items = _list_works(client, tenant_id)

    mission = _read_mission(client, tenant_id, scenario_date)
    return TeachingWorkScenarioReport(
        scenario_id=SCENARIO_ID,
        tenant_id=str(tenant_id),
        principal_id=str(principal_id),
        mission_date=mission["mission_date"],
        target_date=target_date.isoformat(),
        works=works,
        reused_existing=reused_all,
        mission_hero_action_kind=mission["hero_action"]["kind"],
        mission_pending_review_count=mission["review"]["pending_count"],
        mission_active_work_count=mission["preparation"]["active_work_count"],
    )


def write_scenario_report(report: TeachingWorkScenarioReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8")
