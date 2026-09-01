"""Fail-closed API mutation activation safety interlock (PED-I03).

Activation failure yields read-only / no mutation. It does not fail API
startup, liveness, or PED-I02 readiness. Local, deterministic, non-networked.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from fastapi import FastAPI, Request
from fastapi.routing import APIRoute
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from starlette.routing import Match

from aieos.platform.api.problems import problem_response
from aieos.platform.runtime.models import ReleaseIdentity

ENV_API_MUTATION_ACTIVATION = "AIEOS_API_MUTATION_ACTIVATION"
ENV_API_MUTATION_AUTHORIZED_GIT_SHA = "AIEOS_API_MUTATION_AUTHORIZED_GIT_SHA"
ENV_API_MUTATION_AUTHORIZED_ARTIFACT_DIGEST = (
    "AIEOS_API_MUTATION_AUTHORIZED_ARTIFACT_DIGEST"
)

ACTIVATION_ENABLED = "ENABLED"
ACTIVATION_DISABLED = "DISABLED"

FROZEN_API_MUTATION_OPERATION_IDS: frozenset[str] = frozenset(
    {
        "content_create",
        "content_version_append",
        "content_review_submit",
        "content_review_approve",
        "content_review_request_changes",
        "content_review_reject",
        "content_publish",
        "teaching_work_create",
        "teaching_work_refine",
        "teaching_work_generate",
        "teaching_work_prepare",
        "teaching_assignment_create",
        "teaching_assignment_due_update",
        "teaching_assignment_close",
        "teaching_assignment_cancel",
    }
)

READ_ONLY_OPERATION_IDS: frozenset[str] = frozenset(
    {
        "content_get",
        "content_list",
        "content_version_get",
        "teacher_os_review_queue_list",
        "teacher_os_review_queue_get",
        "teaching_work_get",
        "teaching_work_list",
        "teacher_os_today_mission",
        "teacher_os_school_context_classes_list",
        "teaching_work_artifacts_list",
        "teaching_assignment_get",
        "teaching_assignment_list",
    }
)

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_ARTIFACT_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")

_BLOCKED_CODE = "mutations_not_activated"
_BLOCKED_TITLE = "Mutations not activated"
_BLOCKED_DETAIL = (
    "Content mutations are not activated for this runtime release."
)


class MutationActivationStatus(StrEnum):
    ENABLED = "ENABLED"
    DISABLED = "DISABLED"
    INVALID_CONFIGURATION = "INVALID_CONFIGURATION"
    RELEASE_MISMATCH = "RELEASE_MISMATCH"


@dataclass(frozen=True, slots=True)
class MutationActivationDecision:
    enabled: bool
    status: MutationActivationStatus


class ApiMutationActivationGate(Protocol):
    def check(self) -> MutationActivationDecision: ...


class ConfiguredApiMutationActivationGate:
    """Deterministic gate holding a frozen activation decision."""

    def __init__(self, decision: MutationActivationDecision) -> None:
        self._decision = decision

    def check(self) -> MutationActivationDecision:
        return self._decision


def _disabled(status: MutationActivationStatus) -> MutationActivationDecision:
    return MutationActivationDecision(enabled=False, status=status)


def load_api_mutation_activation_gate(
    environ: Mapping[str, str],
    release_identity: ReleaseIdentity,
) -> ConfiguredApiMutationActivationGate:
    """Load a fail-closed mutation activation gate from environment values.

    Ordinary missing/invalid/mismatch cases never raise — they disable mutation.
    """
    raw = environ.get(ENV_API_MUTATION_ACTIVATION)
    if raw is None or raw.strip() == "":
        return ConfiguredApiMutationActivationGate(
            _disabled(MutationActivationStatus.DISABLED)
        )
    value = raw.strip()
    if value == ACTIVATION_DISABLED:
        return ConfiguredApiMutationActivationGate(
            _disabled(MutationActivationStatus.DISABLED)
        )
    if value != ACTIVATION_ENABLED:
        return ConfiguredApiMutationActivationGate(
            _disabled(MutationActivationStatus.INVALID_CONFIGURATION)
        )

    auth_sha = environ.get(ENV_API_MUTATION_AUTHORIZED_GIT_SHA)
    auth_digest = environ.get(ENV_API_MUTATION_AUTHORIZED_ARTIFACT_DIGEST)
    if (
        auth_sha is None
        or auth_sha.strip() == ""
        or auth_digest is None
        or auth_digest.strip() == ""
    ):
        return ConfiguredApiMutationActivationGate(
            _disabled(MutationActivationStatus.INVALID_CONFIGURATION)
        )
    auth_sha = auth_sha.strip()
    auth_digest = auth_digest.strip()
    if not _GIT_SHA.fullmatch(auth_sha) or not _ARTIFACT_DIGEST.fullmatch(auth_digest):
        return ConfiguredApiMutationActivationGate(
            _disabled(MutationActivationStatus.INVALID_CONFIGURATION)
        )
    if (
        auth_sha != release_identity.git_sha
        or auth_digest != release_identity.artifact_digest
    ):
        return ConfiguredApiMutationActivationGate(
            _disabled(MutationActivationStatus.RELEASE_MISMATCH)
        )
    return ConfiguredApiMutationActivationGate(
        MutationActivationDecision(
            enabled=True, status=MutationActivationStatus.ENABLED
        )
    )


def load_api_mutation_activation_gate_from_process_environment(
    release_identity: ReleaseIdentity,
) -> ConfiguredApiMutationActivationGate:
    import os

    return load_api_mutation_activation_gate(os.environ, release_identity)


class MutationRouteClassificationError(RuntimeError):
    """A write-capable /api/v1 route is missing explicit mutation classification."""


def iter_api_v1_routes(app: FastAPI) -> list[APIRoute]:
    """Collect APIRoute objects under /api/v1, including included routers."""
    found: list[APIRoute] = []
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path.startswith("/api/v1"):
            found.append(route)
        original = getattr(route, "original_router", None)
        if original is None:
            continue
        for nested in original.routes:
            if isinstance(nested, APIRoute) and nested.path.startswith("/api/v1"):
                found.append(nested)
    return found


def discover_write_operation_ids(app: FastAPI) -> frozenset[str]:
    discovered: set[str] = set()
    for route in iter_api_v1_routes(app):
        methods = {m.upper() for m in (route.methods or set())}
        if methods & _WRITE_METHODS:
            oid = route.operation_id
            if not oid:
                raise MutationRouteClassificationError(
                    f"write-capable route {route.path} missing operation_id"
                )
            discovered.add(oid)
    return frozenset(discovered)


def assert_mutation_route_classification(app: FastAPI) -> None:
    """Fail closed if write routes diverge from the frozen mutation inventory."""
    discovered = discover_write_operation_ids(app)
    if discovered != FROZEN_API_MUTATION_OPERATION_IDS:
        raise MutationRouteClassificationError(
            "write-capable /api/v1 operation_ids must equal the frozen "
            f"mutation inventory; got {sorted(discovered)} expected "
            f"{sorted(FROZEN_API_MUTATION_OPERATION_IDS)}"
        )
    for route in iter_api_v1_routes(app):
        methods = {m.upper() for m in (route.methods or set())}
        if methods <= _SAFE_METHODS and route.operation_id in (
            FROZEN_API_MUTATION_OPERATION_IDS
        ):
            raise MutationRouteClassificationError(
                f"read-only route {route.operation_id} must not be classified "
                "as a mutation"
            )


def _resolve_operation_id(app: FastAPI, request: Request) -> str | None:
    for route in iter_api_v1_routes(app):
        match, _child = route.matches(request.scope)
        if match == Match.FULL:
            return route.operation_id
    return None


def _mutations_blocked_response(request: Request) -> Response:
    return problem_response(
        request,
        status=503,
        code=_BLOCKED_CODE,
        title=_BLOCKED_TITLE,
        detail=_BLOCKED_DETAIL,
    )


class MutationActivationMiddleware(BaseHTTPMiddleware):
    """Reject frozen mutation operations before UoW / business execution."""

    def __init__(self, app, gate: ApiMutationActivationGate) -> None:
        super().__init__(app)
        self._gate = gate

    async def dispatch(self, request: Request, call_next) -> Response:
        app: FastAPI = request.app
        operation_id = _resolve_operation_id(app, request)
        if operation_id in FROZEN_API_MUTATION_OPERATION_IDS:
            try:
                decision = self._gate.check()
            except Exception:
                return _mutations_blocked_response(request)
            if not decision.enabled:
                return _mutations_blocked_response(request)
        return await call_next(request)


def install_mutation_activation_interlock(
    app: FastAPI,
    gate: ApiMutationActivationGate,
) -> None:
    """Classify routes fail-closed and install the activation middleware.

    Middleware is appended so RequestContextMiddleware remains outermost and
    assigns request/correlation ids before the activation check.
    """
    assert_mutation_route_classification(app)
    app.user_middleware.append(Middleware(MutationActivationMiddleware, gate=gate))
    app.middleware_stack = None
    app.state.mutation_activation_gate = gate
    app.state.mutation_operation_ids = FROZEN_API_MUTATION_OPERATION_IDS
