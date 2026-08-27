"""TOS-DEV01 Teacher OS Review Queue development reference scenario.

NON_PRODUCTION. Synthetic tenant/principal/content only.
Creates reference artifacts through existing HTTP application contracts:

  create → append version → submit-for-review → Review Queue visibility

Repeatability: deterministic Idempotency-Key values per (tenant, artifact_key, step)
prevent unbounded duplicate creates when the same synthetic tenant is reused.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from aieos.development.schemas import (
    DEV_CONTENT_TYPE,
    DEV_SCHEMA_ID,
    DEV_SCHEMA_VERSION,
)

SCENARIO_ID = "tos-dev01-teacher-os-review-queue"
SCENARIO_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
SYNTHETIC_TENANT_ID = uuid.uuid5(SCENARIO_NAMESPACE, "aieos.tos-dev01.synthetic-tenant")
SYNTHETIC_PRINCIPAL_ID = uuid.uuid5(
    SCENARIO_NAMESPACE, "aieos.tos-dev01.synthetic-principal"
)

# Title markers used for queue matching / repeatability checks.
ARTIFACT_APPROVE = "approve-demo"
ARTIFACT_REQUEST_CHANGES = "request-changes-demo"
ARTIFACT_REJECT = "reject-demo"


@dataclass(frozen=True, slots=True)
class ScenarioArtifactSpec:
    key: str
    title: str
    description: str
    locale: str
    marker: str
    intended_action: str


ARTIFACT_SPECS: tuple[ScenarioArtifactSpec, ...] = (
    ScenarioArtifactSpec(
        key=ARTIFACT_APPROVE,
        title="[TOS-DEV01:approve-demo] Grade 5 Mathematics — Fractions Worksheet",
        description=(
            "Synthetic development worksheet placeholder for APPROVE path. "
            "Not real student or school data."
        ),
        locale="en-IN",
        marker="tos-dev01-approve-fractions",
        intended_action="approve",
    ),
    ScenarioArtifactSpec(
        key=ARTIFACT_REQUEST_CHANGES,
        title="[TOS-DEV01:request-changes-demo] Grade 5 — Fractions Lesson Outline",
        description=(
            "Synthetic development lesson-outline placeholder for REQUEST_CHANGES. "
            "Not real teacher or class data."
        ),
        locale="en-IN",
        marker="tos-dev01-request-changes-lesson",
        intended_action="request-changes",
    ),
    ScenarioArtifactSpec(
        key=ARTIFACT_REJECT,
        title="[TOS-DEV01:reject-demo] Grade 5 — Fractions Quick Check",
        description=(
            "Synthetic development assessment-like placeholder for REJECT. "
            "Not real assessment or student data."
        ),
        locale="en-IN",
        marker="tos-dev01-reject-quiz",
        intended_action="reject",
    ),
)


@dataclass(slots=True)
class SeededArtifact:
    key: str
    intended_action: str
    content_id: str
    version_id: str
    etag: str
    title: str


@dataclass(slots=True)
class ScenarioReport:
    scenario_id: str
    tenant_id: str
    principal_id: str
    content_type: str
    schema_id: str
    schema_version: int
    artifacts: list[SeededArtifact]
    reused_existing: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "tenant_id": self.tenant_id,
            "principal_id": self.principal_id,
            "content_type": self.content_type,
            "schema_id": self.schema_id,
            "schema_version": self.schema_version,
            "reused_existing": self.reused_existing,
            "artifacts": [asdict(a) for a in self.artifacts],
        }


def _idem_key(tenant_id: UUID, artifact_key: str, step: str) -> str:
    return f"tos-dev01:{SCENARIO_ID}:{tenant_id}:{artifact_key}:{step}"


def _headers(tenant_id: UUID, *, idempotency_key: str, if_match: str | None = None) -> dict[str, str]:
    out = {
        "X-AIEOS-Tenant-ID": str(tenant_id),
        "Idempotency-Key": idempotency_key,
    }
    if if_match is not None:
        out["If-Match"] = if_match
    return out


def _queue_list(client: Any, tenant_id: UUID) -> list[dict[str, Any]]:
    response = client.get(
        "/api/v1/teacher-os/review-queue",
        params={"limit": 100},
        headers={"X-AIEOS-Tenant-ID": str(tenant_id)},
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"review-queue list failed: {response.status_code} {response.text}"
        )
    return list(response.json()["items"])


def _find_queue_item(
    items: list[dict[str, Any]], *, title: str
) -> dict[str, Any] | None:
    for item in items:
        if item.get("title") == title:
            return item
    return None


def _seed_one(
    client: Any,
    tenant_id: UUID,
    spec: ScenarioArtifactSpec,
    *,
    queue_items: list[dict[str, Any]],
) -> tuple[SeededArtifact, bool]:
    existing = _find_queue_item(queue_items, title=spec.title)
    if existing is not None:
        return (
            SeededArtifact(
                key=spec.key,
                intended_action=spec.intended_action,
                content_id=existing["content_id"],
                version_id=existing["version_id"],
                etag=f'"r{existing["aggregate_revision"]}"',
                title=spec.title,
            ),
            True,
        )

    created = client.post(
        "/api/v1/contents",
        json={
            "content_type": DEV_CONTENT_TYPE,
            "title": spec.title,
            "description": spec.description,
            "locale": spec.locale,
        },
        headers=_headers(
            tenant_id, idempotency_key=_idem_key(tenant_id, spec.key, "create")
        ),
    )
    if created.status_code not in (200, 201):
        raise RuntimeError(f"create failed for {spec.key}: {created.text}")
    content_id = created.json()["content_id"]
    etag = created.headers["ETag"]

    appended = client.post(
        f"/api/v1/contents/{content_id}/versions",
        json={
            "schema_id": DEV_SCHEMA_ID,
            "schema_version": DEV_SCHEMA_VERSION,
            "payload": {
                "marker": spec.marker,
                "scenario": SCENARIO_ID,
                "intended_action": spec.intended_action,
                "synthetic": True,
            },
        },
        headers=_headers(
            tenant_id,
            idempotency_key=_idem_key(tenant_id, spec.key, "append"),
            if_match=etag,
        ),
    )
    if appended.status_code not in (200, 201):
        raise RuntimeError(f"append failed for {spec.key}: {appended.text}")
    version_id = appended.json()["version_id"]
    etag = appended.headers["ETag"]

    submitted = client.post(
        f"/api/v1/contents/{content_id}/versions/{version_id}/actions/submit-for-review",
        headers=_headers(
            tenant_id,
            idempotency_key=_idem_key(tenant_id, spec.key, "submit"),
            if_match=etag,
        ),
    )
    if submitted.status_code != 200:
        raise RuntimeError(f"submit failed for {spec.key}: {submitted.text}")

    return (
        SeededArtifact(
            key=spec.key,
            intended_action=spec.intended_action,
            content_id=content_id,
            version_id=version_id,
            etag=submitted.headers["ETag"],
            title=spec.title,
        ),
        False,
    )


def ensure_teacher_os_review_scenario(
    client: Any,
    *,
    tenant_id: UUID,
    principal_id: UUID,
) -> ScenarioReport:
    """Idempotently ensure three IN_REVIEW queue items exist for the scenario."""
    queue_items = _queue_list(client, tenant_id)
    artifacts: list[SeededArtifact] = []
    reused_all = True
    for spec in ARTIFACT_SPECS:
        seeded, reused = _seed_one(client, tenant_id, spec, queue_items=queue_items)
        artifacts.append(seeded)
        reused_all = reused_all and reused
        if not reused:
            # Refresh queue snapshot after each create so subsequent lookups see new items.
            queue_items = _queue_list(client, tenant_id)

    return ScenarioReport(
        scenario_id=SCENARIO_ID,
        tenant_id=str(tenant_id),
        principal_id=str(principal_id),
        content_type=DEV_CONTENT_TYPE,
        schema_id=DEV_SCHEMA_ID,
        schema_version=DEV_SCHEMA_VERSION,
        artifacts=artifacts,
        reused_existing=reused_all,
    )


def write_scenario_report(report: ScenarioReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8")
