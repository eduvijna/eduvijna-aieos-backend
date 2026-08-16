"""GCI-I14 adversarial: architecture abuse and deferred boundaries.

GCI-G10 (archive) is deferred: this suite asserts archive is not implemented
and does not authorize production archive behavior.
"""

from __future__ import annotations

import ast
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.platform.api.app import create_app
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

pytestmark = pytest.mark.gci_i14

SRC_ROOT = REPO_ROOT / "src" / "aieos"
CONTENT_ROOT = SRC_ROOT / "domains" / "content"
DOMAIN_ROOT = CONTENT_ROOT / "domain"
APPLICATION_ROOT = CONTENT_ROOT / "application"
API_ROOT = CONTENT_ROOT / "api"
MIGRATIONS = REPO_ROOT / "migrations" / "versions"

FORBIDDEN_APP_DOMAIN = (
    "temporalio",
    "nats",
    "openai",
    "anthropic",
    "fastapi",
    "sqlalchemy",
    "eduvijna",
    "postgrest",
)

_EXPECTED_MIGRATIONS = [
    "gcii020001_content_schema.py",
    "gcii050001_api_idempotency.py",
    "gcii060001_review_decisions.py",
    "gcii070001_workflow_intents.py",
    "gcii080001_outbox_messages.py",
    "gcii090001_publications.py",
    "gcii100001_version_asset_refs.py",
    "gcii110001_ai_provenance.py",
    "gcii130001_migration_import.py",
    "saii020001_security_audit_ledger.py",
]


def _import_violations(root: Path, forbidden: tuple[str, ...]) -> list[str]:
    violations: list[str] = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in forbidden:
                        violations.append(f"{path.name}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] in forbidden:
                    violations.append(f"{path.name}: from {node.module}")
    return violations


class TestArchitectureAbuse:
    def test_domain_application_forbid_infrastructure_sdks(self) -> None:
        assert _import_violations(DOMAIN_ROOT, FORBIDDEN_APP_DOMAIN) == []
        assert _import_violations(APPLICATION_ROOT, FORBIDDEN_APP_DOMAIN) == []

    def test_src_does_not_import_tests_fakes_or_allow_star(self) -> None:
        hits: list[str] = []
        for path in (REPO_ROOT / "src").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "tests.fakes" in text or "from tests." in text:
                hits.append(str(path.relative_to(REPO_ROOT)))
            for needle in (
                "AllowReviewAuthorization",
                "AllowPublicationAuthorization",
                "AllowMigrationAuthorization",
                "AllowAIGenerationAuthorization",
                "StubSecurityContextResolver",
            ):
                if needle in text:
                    hits.append(f"{path.relative_to(REPO_ROOT)}:{needle}")
        assert hits == []

    def test_no_archive_purge_migrate_http_routes(self) -> None:
        routes = (API_ROOT / "v1" / "routes.py").read_text(encoding="utf-8")
        for needle in ("/archive", "/purge", "/migrate", "/imports", "/legacy"):
            assert needle not in routes

    def test_no_gcii140001_or_gcii120001_migration_chain_includes_sai_i02(self) -> None:
        versions = sorted(
            path.name
            for path in MIGRATIONS.glob("*.py")
            if path.name != "__init__.py"
        )
        assert versions == _EXPECTED_MIGRATIONS
        assert not (MIGRATIONS / "gcii140001_adversarial.py").exists()
        assert not (MIGRATIONS / "gcii120001_review_queue.py").exists()
        for path in MIGRATIONS.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "gcii140001" not in text
            assert "gcii120001" not in text

    def test_no_worksheet_or_lesson_versions_tables(self) -> None:
        hits: list[str] = []
        for path in (REPO_ROOT / "migrations").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for needle in ("worksheet_versions", "lesson_versions"):
                if needle in text:
                    hits.append(f"{path.name}:{needle}")
        assert hits == []

    def test_review_queue_source_has_no_approve_reject_enqueue(self) -> None:
        text = (APPLICATION_ROOT / "review_queue.py").read_text(encoding="utf-8").lower()
        for needle in ("approve", "reject", "enqueue", "dequeue"):
            assert needle not in text

    def test_gci_g10_archive_not_implemented(self) -> None:
        """GCI-G10 deferred: archive HTTP and content.archived emission absent."""
        routes = (API_ROOT / "v1" / "routes.py").read_text(encoding="utf-8")
        assert "/archive" not in routes
        assert "content.archived" not in routes
        for path in (REPO_ROOT / "migrations").rglob("*.py"):
            assert "content.archived" not in path.read_text(encoding="utf-8")

    def test_security_audit_ledger_exists_api_ai_migration_wired(self) -> None:
        hits: list[str] = []
        for path in (REPO_ROOT / "migrations").rglob("*.py"):
            if "audit_events" in path.read_text(encoding="utf-8"):
                hits.append(path.name)
        for path in (REPO_ROOT / "src").rglob("*.py"):
            if "audit_events" in path.read_text(encoding="utf-8"):
                hits.append(str(path.relative_to(REPO_ROOT)))
        assert hits == []
        assert (MIGRATIONS / "saii020001_security_audit_ledger.py").is_file()
        assert not (MIGRATIONS / "saii030001_security_audit_content.py").exists()
        assert not any(MIGRATIONS.glob("saii040001*"))
        create = (CONTENT_ROOT / "application" / "create.py").read_text(encoding="utf-8")
        assert "insert_required_content_audit" in create
        ai = (CONTENT_ROOT / "application" / "ai_materialization.py").read_text(
            encoding="utf-8"
        )
        migration = (CONTENT_ROOT / "application" / "migration_import.py").read_text(
            encoding="utf-8"
        )
        assert "insert_required_content_audit" in ai
        assert "insert_required_content_audit" in migration
        ports = (CONTENT_ROOT / "application" / "ports.py").read_text(encoding="utf-8")
        assert "SqlAlchemySecurityMutationAuditRepository" not in ports
        assert "SecurityMutationAuditRepository" in ports

    def test_openapi_has_no_migrate_import_adversarial_ops(self, runtime_engine) -> None:
        tenant_id = uuid.uuid7()
        app = create_app(
            uow_factory=SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
            request_identity_authenticator=FixedPrincipalAuthenticator(tenant_id),
            security_resolver=StubSecurityContextResolver(tenant_id, tenant_id),
            content_types=StaticContentTypeCatalog({"test.generic"}),
            cursor_signing_key=b"gci-i14-openapi",
            schema_registry=make_test_schema_registry(),
            idempotency_retention=IDEMPOTENCY_RETENTION,
            review_authorization=AllowReviewAuthorization(),
            review_comment_policy=AllowReviewCommentPolicy(),
            publication_authorization=AllowPublicationAuthorization(),
            publication_governance=AllowPublicationGovernance(),
            asset_reference_validation=AllowAssetReferenceValidation(),
            asset_current_governance=AllowAssetCurrentGovernance(),
        )
        schema = TestClient(app).get("/openapi.json").json()
        for path in schema.get("paths", {}):
            lowered = path.lower()
            assert "/migrate" not in lowered
            assert "/imports" not in lowered
            assert "/archive" not in lowered
            assert "/purge" not in lowered
            assert "adversarial" not in lowered
        ops = " ".join(
            str(op.get("operationId", "")).lower()
            for path_item in schema.get("paths", {}).values()
            for op in path_item.values()
            if isinstance(op, dict)
        )
        assert "migrate" not in ops
        assert "adversarial" not in ops
        assert "importmigrated" not in ops.replace("_", "")
        assert "legacyimport" not in ops.replace("_", "")
