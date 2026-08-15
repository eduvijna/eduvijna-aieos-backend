"""Operational /livez and /readyz routes (PED-I02).

Excluded from product OpenAPI. No authentication, tenant, or mutation gate.
"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import JSONResponse

from aieos.platform.runtime.models import WorkloadKind
from aieos.platform.runtime.readiness import ApiReadinessProbe


def _release_payload(request: Request) -> dict[str, str]:
    identity = request.app.state.release_identity
    return {
        "application_version": identity.application_version,
        "git_sha": identity.git_sha,
        "build_id": identity.build_id,
        "artifact_digest": identity.artifact_digest,
    }


def register_operational_health_routes(app: FastAPI) -> None:
    """Attach /livez and /readyz with include_in_schema=False."""

    router = APIRouter(include_in_schema=False)

    @router.get("/livez")
    def livez(request: Request) -> dict[str, object]:
        return {
            "status": "alive",
            "workload": WorkloadKind.API.value,
            "deployment_environment": request.app.state.deployment_environment.value,
            "release": _release_payload(request),
        }

    @router.get("/readyz")
    def readyz(request: Request) -> Response:
        probe: ApiReadinessProbe = request.app.state.readiness_probe
        result = probe.check()
        body = {
            "status": "ready" if result.ready else "not_ready",
            "code": result.code.value,
            "workload": WorkloadKind.API.value,
            "deployment_environment": request.app.state.deployment_environment.value,
            "release": _release_payload(request),
        }
        return JSONResponse(body, status_code=200 if result.ready else 503)

    app.include_router(router)
