"""PED-I03 architecture boundaries for mutation activation."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI

from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.platform.api.app import create_app
from aieos.platform.runtime.activation import (
    FROZEN_API_MUTATION_OPERATION_IDS,
    READ_ONLY_OPERATION_IDS,
    MutationActivationDecision,
    MutationActivationStatus,
    MutationRouteClassificationError,
    assert_mutation_route_classification,
    discover_write_operation_ids,
    install_mutation_activation_interlock,
    iter_api_v1_routes,
)
from tests.dbutil import REPO_ROOT
from tests.fakes import (
    AllowAssetCurrentGovernance,
    AllowAssetReferenceValidation,
    AllowPublicationAuthorization,
    AllowPublicationGovernance,
    AllowReviewAuthorization,
    AllowReviewCommentPolicy,
    IDEMPOTENCY_RETENTION,
    FixedPrincipalAuthenticator,
    StubSecurityContextResolver,
    make_test_schema_registry,
)
from uuid import uuid4

pytestmark = pytest.mark.ped_i03

RUNTIME_ROOT = REPO_ROOT / "src" / "aieos" / "platform" / "runtime"
BOUNDARY_DOC = REPO_ROOT / "docs" / "PED-I03-MUTATION-ACTIVATION-CONTRACT.md"
PED_I02_DOC = REPO_ROOT / "docs" / "PED-I02-API-DB-READINESS-CONTRACT.md"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
MIGRATIONS = REPO_ROOT / "migrations" / "versions"

_BYPASS_NEEDLES = (
    "AlwaysEnabled",
    "default_enabled = True",
    'environ.get(..., "true")',
    'environ.get(..., "ENABLED")',
    "if activation_gate is None",
    "except:\n    allow",
    "except Exception:\n            allow",
)


class _DisabledGate:
    def check(self) -> MutationActivationDecision:
        return MutationActivationDecision(
            False, MutationActivationStatus.DISABLED
        )


class _UnusedUow:
    def __call__(self, tenant_id):
        raise AssertionError("must not open UoW")


def _minimal_app() -> FastAPI:
    return create_app(
        uow_factory=_UnusedUow(),
        teaching_uow_factory=_UnusedUow(),
        request_identity_authenticator=FixedPrincipalAuthenticator(uuid4()),
        security_resolver=StubSecurityContextResolver(uuid4(), uuid4()),
        content_types=StaticContentTypeCatalog({"test.generic"}),
        cursor_signing_key=b"ped-i03-arch",
        schema_registry=make_test_schema_registry(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
        review_authorization=AllowReviewAuthorization(),
        review_comment_policy=AllowReviewCommentPolicy(),
        publication_authorization=AllowPublicationAuthorization(),
        publication_governance=AllowPublicationGovernance(),
        asset_reference_validation=AllowAssetReferenceValidation(),
        asset_current_governance=AllowAssetCurrentGovernance(),
    )


def test_frozen_mutation_inventory_matches_discovered_writes() -> None:
    app = _minimal_app()
    discovered = discover_write_operation_ids(app)
    assert discovered == FROZEN_API_MUTATION_OPERATION_IDS
    assert_mutation_route_classification(app)
    install_mutation_activation_interlock(app, _DisabledGate())
    assert app.state.mutation_operation_ids == FROZEN_API_MUTATION_OPERATION_IDS


def test_read_routes_are_not_mutations() -> None:
    app = _minimal_app()
    for route in iter_api_v1_routes(app):
        methods = {m.upper() for m in (route.methods or set())}
        if methods <= {"GET", "HEAD", "OPTIONS"}:
            assert route.operation_id in READ_ONLY_OPERATION_IDS
            assert route.operation_id not in FROZEN_API_MUTATION_OPERATION_IDS


def test_unclassified_write_route_fails_closed() -> None:
    app = _minimal_app()

    @app.post("/api/v1/unclassified-write", operation_id="unclassified_write")
    def _unclassified() -> dict:
        return {}

    with pytest.raises(MutationRouteClassificationError):
        assert_mutation_route_classification(app)


def test_no_bypass_patterns_in_runtime() -> None:
    for path in RUNTIME_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in _BYPASS_NEEDLES:
            assert needle not in text, f"{path}: {needle}"
        assert "AlwaysEnabledGate" not in text
        assert "LaunchDarkly" not in text
        assert "ConfigCat" not in text
        assert "Unleash" not in text


def test_readiness_independent_of_activation() -> None:
    readiness = (RUNTIME_ROOT / "readiness.py").read_text(encoding="utf-8")
    health = (RUNTIME_ROOT / "health.py").read_text(encoding="utf-8")
    assert "activation" not in readiness.lower()
    assert "MutationActivation" not in readiness
    assert "mutation_activation" not in health
    assert "ApiMutationActivationGate" not in health
    assert "MUTATION_ACTIVATION" not in readiness
    assert "MUTATION_ACTIVATION" not in health


def test_activation_has_no_db_nats_temporal_feature_flags() -> None:
    text = (RUNTIME_ROOT / "activation.py").read_text(encoding="utf-8")
    assert "create_engine" not in text
    assert "sqlalchemy" not in text.lower()
    assert "nats" not in text.lower()
    assert "temporal" not in text.lower()
    assert "AIEOS_DATABASE_URL" not in text
    assert "/activate" not in text
    assert "/deactivate" not in text


def test_no_ai_or_migration_service_activation_wiring() -> None:
    roots = [
        REPO_ROOT / "src" / "aieos" / "domains" / "content" / "application",
    ]
    needles = ("mutation_activation", "MUTATION_ACTIVATION", "ApiMutationActivation")
    for root in roots:
        for path in root.rglob("*.py"):
            name = path.name.lower()
            if "ai" in name or "migrat" in name or "import" in name:
                body = path.read_text(encoding="utf-8")
                for needle in needles:
                    assert needle not in body, f"{path}: {needle}"


def test_migration_head_unchanged() -> None:
    versions = sorted(p.name for p in MIGRATIONS.glob("*.py") if p.name != "__init__.py")
    assert "pedi090001_security_authority.py" in versions
    assert "pedi10b2001_asset_authority_sor.py" in versions
    assert versions[-1] == "tosd030002_generation_run_work_fence.py"
    for path in MIGRATIONS.rglob("*.py"):
        assert "pedi030001" not in path.read_text(encoding="utf-8")


def test_docs_and_changelog() -> None:
    doc = BOUNDARY_DOC.read_text(encoding="utf-8")
    assert "ACTIVATION FAILURE" in doc.upper() or "activation failure" in doc.lower()
    assert "READ-ONLY" in doc.upper() or "read-only" in doc.lower()
    assert "NOT AUTHORIZED" in doc
    assert "production ready" not in doc.lower()
    assert "safe to deploy" not in doc.lower()
    assert "production mutations enabled" not in doc.lower()
    ped_i02 = PED_I02_DOC.read_text(encoding="utf-8")
    assert "mutation activation" in ped_i02.lower()
    changelog = CHANGELOG.read_text(encoding="utf-8")
    assert "PED-I03" in changelog
    assert "fail-closed API mutation activation safety interlock" in changelog
    assert "production-ready" not in changelog.lower()
    assert "safe to deploy" not in changelog.lower()
