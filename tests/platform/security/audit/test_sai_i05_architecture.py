"""SAI-I05 architecture gate: mutation inventory, fatality, boundaries, privileges."""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import fields
from pathlib import Path

import pytest
from sqlalchemy import text

from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.domain.schema import ContentSchemaRegistry
from aieos.platform.api.app import create_app
from aieos.platform.api.openapi import build_openapi, canonical_openapi_json
from aieos.platform.security.audit import SecurityAuditAction, SecurityAuditExecutionChannel
from aieos.platform.security.audit.persistence.models import audit_records_table
from tests.conftest import (
    SCHEMA_OWNER_ROLE,
    SECURITY_SCHEMA_OWNER_ROLE,
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
)

pytestmark = pytest.mark.sai_i05

SRC_ROOT = REPO_ROOT / "src"
CONTENT_ROOT = SRC_ROOT / "aieos" / "domains" / "content"
APP_ROOT = CONTENT_ROOT / "application"
AUDIT_CONTRACT = SRC_ROOT / "aieos" / "platform" / "security" / "audit"
AUDIT_PERSISTENCE = AUDIT_CONTRACT / "persistence"
TEMPORAL_ROOT = SRC_ROOT / "aieos" / "platform" / "workflows" / "temporal"
MIGRATIONS = REPO_ROOT / "migrations" / "versions"
BOUNDARY_DOC = REPO_ROOT / "docs" / "GCI-I04-NON-PRODUCTION-MUTATION-BOUNDARY.md"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
SNAPSHOT = REPO_ROOT / "contracts" / "openapi" / "aieos-v1.json"
EXPECTED_OPENAPI_SHA256 = (
    "BBE357612BFF091F7EAF54A4C5F1065B248BB0212A3F0DDF4AFF0685C759C4C7"
)

_EXPECTED_MIGRATIONS = [
    "adra045001_dispatcher_candidate_authority.py",
    "gcii020001_content_schema.py",
    "gcii050001_api_idempotency.py",
    "gcii060001_review_decisions.py",
    "gcii070001_workflow_intents.py",
    "gcii080001_outbox_messages.py",
    "gcii090001_publications.py",
    "gcii100001_version_asset_refs.py",
    "gcii110001_ai_provenance.py",
    "gcii130001_migration_import.py",
    "pedi090001_security_authority.py",
    "pedi10b2001_asset_authority_sor.py",
    "pedi10b6001_asset_security_audit.py",
    "saii020001_security_audit_ledger.py",
    "tosd020001_teaching_work.py",
    "tosd030001_generation_runs.py",
    "tosd030002_generation_run_work_fence.py",
    "tosd040001_multi_artifact_provenance_and_generation_fences.py",
]

# Frozen SAI-I05 mutation inventory classification.
# A = wired to required security audit
# B = non-product/internal foundation (no production src call site)
# C = unimplemented / N/A
# R = read-only / non-committed-mutation
_MUTATION_INVENTORY: dict[str, str] = {
    "CreateContentService": "A",
    "HttpAppendContentVersionService": "A",
    "ReviewCommandService": "A",
    "PublishContentService": "A",
    "MaterializeAIGeneratedContentVersionService": "A",
    "CreateAIGeneratedContentForReviewService": "A",
    "ImportMigratedContentService": "A",
    "AppendContentVersionService": "B",
    "GetContentService": "R",
    "ListContentsService": "R",
    "GetContentVersionService": "R",
    "ListTeacherReviewQueueService": "R",
    "GetTeacherReviewQueueItemService": "R",
    "ValidateVersionAssetGovernanceService": "R",
}

_WIRED_AUDIT_FILES = {
    "CreateContentService": "in_uow.py",
    "HttpAppendContentVersionService": "http_append.py",
    "ReviewCommandService": "review.py",
    "PublishContentService": "publish.py",
    "MaterializeAIGeneratedContentVersionService": "in_uow.py",
    "CreateAIGeneratedContentForReviewService": "ai_for_review.py",
    "ImportMigratedContentService": "migration_import.py",
}

_FROZEN_ACTIONS = frozenset(a.value for a in SecurityAuditAction)
_FORBIDDEN_AUDIT_FIELDS = (
    "payload",
    "metadata",
    "details",
    "request",
    "response",
    "headers",
    "cookies",
    "jwt",
    "claims",
    "roles",
    "permissions",
    "token",
    "api_key",
    "secret",
    "prompt",
    "ai_response",
    "review_comment",
    "comment",
)
_CRYPTO_NEEDLES = (
    "hash_chain",
    "hashchain",
    "merkle",
    "kms_seal",
    "blockchain",
    "cryptographic_non_repudiation",
)
_BYPASS_PATTERNS = (
    r"best_effort_audit",
    r"optional_audit",
    r"audit_enabled",
    r"if\s+audit\s+is\s+not\s+None",
    r"except\s*:\s*\n\s*pass",
)
_SIEM_CALL_PATTERNS = (
    r"siem",
    r"splunk",
    r"datadog\.api",
    r"export_audit",
    r"audit_sink",
)


def _py_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.py") if p.is_file()]


def _service_classes(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name.endswith("Service")
    ]


class TestMutationInventory:
    def test_production_content_mutation_inventory_complete(self) -> None:
        discovered: set[str] = set()
        for path in _py_files(APP_ROOT):
            discovered.update(_service_classes(path))
        unknown = discovered - set(_MUTATION_INVENTORY)
        assert unknown == set(), f"unclassified Content *Service classes: {unknown}"
        missing = set(_MUTATION_INVENTORY) - discovered
        assert missing == set(), f"inventory lists missing services: {missing}"

        for name, cls in _MUTATION_INVENTORY.items():
            if cls != "A":
                continue
            fname = _WIRED_AUDIT_FILES[name]
            text = (APP_ROOT / fname).read_text(encoding="utf-8")
            assert "insert_required_content_audit" in text, name

        # B: AppendContentVersionService has no production src instantiation
        pattern = re.compile(r"(?<![A-Za-z])AppendContentVersionService\(")
        hits = []
        for path in _py_files(SRC_ROOT):
            if path.name == "services.py":
                continue
            if pattern.search(path.read_text(encoding="utf-8")):
                hits.append(str(path.relative_to(REPO_ROOT)))
        assert hits == []

        # C: archive / purge action strings / workflow-origin mutation unimplemented
        for path in _py_files(CONTENT_ROOT):
            body = path.read_text(encoding="utf-8")
            assert '"content.archive"' not in body
            assert "'content.archive'" not in body
            assert "content.purge" not in body
            assert "WORKFLOW_ACTIVITY" not in body
            assert "SecurityAuditAction.CONTENT_ARCHIVE" not in body

    def test_public_api_routes_map_only_to_wired_mutations(self) -> None:
        routes = (CONTENT_ROOT / "api" / "v1" / "routes.py").read_text(encoding="utf-8")
        assert "CreateContentService" in routes or "create_content_service" in routes
        assert "HttpAppendContentVersionService" in routes or "append" in routes.lower()
        assert "submit-for-review" in routes
        assert "actions/approve" in routes
        assert "actions/request-changes" in routes
        assert "actions/reject" in routes
        assert "actions/publish" in routes
        for forbidden in (
            "/audit",
            "actions/materialize",
            "actions/import",
            "/migrate",
            "actions/archive",
            "actions/purge",
        ):
            assert forbidden not in routes
        assert "api_mutation_audit_provenance" in routes

    def test_internal_ai_and_migration_map_to_actions(self) -> None:
        ai = (APP_ROOT / "ai_materialization.py").read_text(encoding="utf-8")
        ai_uow = (APP_ROOT / "in_uow.py").read_text(encoding="utf-8")
        ai_for_review = (APP_ROOT / "ai_for_review.py").read_text(encoding="utf-8")
        mig = (APP_ROOT / "migration_import.py").read_text(encoding="utf-8")
        helper = (APP_ROOT / "audit.py").read_text(encoding="utf-8")
        assert "materialize_ai_version_in_uow" in ai
        assert "CONTENT_AI_MATERIALIZE" in ai_uow
        assert "CreateAIGeneratedContentForReviewService" in ai_for_review
        assert "CONTENT_REVIEW_SUBMIT" in ai_for_review
        assert "AI_MATERIALIZATION" in helper
        assert "ai_materialization_audit_provenance" in helper
        assert "CONTENT_MIGRATION_IMPORT" in mig
        assert "SecurityAuditExecutionChannel.MIGRATION" in helper or "MIGRATION" in helper
        assert "migration_audit_provenance" in helper

    def test_exact_frozen_action_vocabulary(self) -> None:
        expected = {
            "content.create",
            "content.version.create",
            "content.review.submit",
            "content.review.approve",
            "content.review.request_changes",
            "content.review.reject",
            "content.publish",
            "content.ai.materialize",
            "content.migration.import",
            "asset.create",
            "asset.revision.register",
            "asset.revision.activate",
            "asset.lifecycle.withdraw",
            "asset.lifecycle.restore",
            "asset.lifecycle.delete",
            "asset.quarantine.set",
            "asset.quarantine.clear",
            "asset.safety.pass",
            "asset.safety.fail",
        }
        assert _FROZEN_ACTIONS == expected
        assert SecurityAuditExecutionChannel.WORKFLOW_ACTIVITY.value == "WORKFLOW_ACTIVITY"


class TestAuditFatalityAndBypassScan:
    def test_no_best_effort_or_optional_audit_bypass(self) -> None:
        hits: list[str] = []
        for path in _py_files(CONTENT_ROOT):
            text = path.read_text(encoding="utf-8")
            for pattern in _BYPASS_PATTERNS:
                if re.search(pattern, text, flags=re.MULTILINE | re.IGNORECASE):
                    hits.append(f"{path.relative_to(REPO_ROOT)}:{pattern}")
            if "try:" in text and "insert_required_content_audit" in text:
                # ensure insert is not inside a swallowed except
                if re.search(
                    r"try:\s*\n(?:.*\n)*?\s*insert_required_content_audit[\s\S]*?except[\s\S]*?pass",
                    text,
                ):
                    hits.append(f"{path.relative_to(REPO_ROOT)}:swallowed_audit")
        assert hits == []

        helper = (APP_ROOT / "audit.py").read_text(encoding="utf-8")
        assert "insert_required_content_audit" in helper
        assert "best_effort" not in helper.lower()
        assert "optional" not in helper.lower()

    def test_no_runtime_audit_correction_or_delete_path(self) -> None:
        needles = (
            "update audit",
            "delete audit",
            "correct audit",
            "rewrite audit",
            "purge audit",
            "UPDATE security.audit_records",
            "DELETE FROM security.audit_records",
        )
        for path in _py_files(SRC_ROOT / "aieos"):
            lower = path.read_text(encoding="utf-8").lower()
            for needle in needles:
                assert needle.lower() not in lower, f"{path}: {needle}"
        adapter = (
            CONTENT_ROOT / "infrastructure" / "persistence" / "audit_repository.py"
        ).read_text(encoding="utf-8")
        for method in ("def update", "def delete", "def get", "def list", "def search"):
            assert method not in adapter

    def test_audit_never_consulted_for_authorization_or_business_truth(self) -> None:
        for path in _py_files(CONTENT_ROOT / "application"):
            if path.name == "audit.py":
                continue
            text = path.read_text(encoding="utf-8")
            assert "FROM security.audit_records" not in text
            assert "security.audit_records" not in text
        # Authorization capability ports must not authorize from audit rows
        ports = (APP_ROOT / "ports.py").read_text(encoding="utf-8")
        assert "FROM security.audit_records" not in ports
        assert "authorize" not in ports.lower() or "SecurityMutationAuditRepository" in ports
        # UoW exposes insert-only audit port; authorization remains separate ports
        assert "SecurityMutationAuditRepository" in ports
        assert "ReviewAuthorizationPort" in ports or "authorize" in ports.lower()


class TestWorkflowAndTemporalBoundary:
    def test_no_content_mutating_activity_or_workflow_audit(self) -> None:
        for path in _py_files(TEMPORAL_ROOT):
            text = path.read_text(encoding="utf-8")
            assert "@activity.defn" not in text
            assert "workflow.execute_activity" not in text
            assert "insert_required_content_audit" not in text
            assert "ContentUnitOfWork" not in text
            assert "WORKFLOW_ACTIVITY" not in text

    def test_content_review_workflow_observation_only(self) -> None:
        text = (TEMPORAL_ROOT / "content_review.py").read_text(encoding="utf-8")
        assert "@workflow.run" in text
        assert "@workflow.signal" in text
        assert "@workflow.query" in text
        assert "security.audit" not in text


class TestFrameworkAndDomainBoundaries:
    def test_audit_contract_framework_neutral(self) -> None:
        forbidden = (
            "fastapi",
            "pydantic",
            "sqlalchemy",
            "alembic",
            "temporalio",
            "nats",
            "openai",
            "anthropic",
        )
        for path in _py_files(AUDIT_CONTRACT):
            if "persistence" in path.parts:
                continue
            text = path.read_text(encoding="utf-8").lower()
            for needle in forbidden:
                assert needle not in text, f"{path}: {needle}"

    def test_content_domain_keeps_framework_out(self) -> None:
        domain = CONTENT_ROOT / "domain"
        for path in _py_files(domain):
            text = path.read_text(encoding="utf-8")
            assert "import sqlalchemy" not in text
            assert "from sqlalchemy" not in text
            assert "import fastapi" not in text
            assert "from fastapi" not in text
            assert "import temporalio" not in text
            assert "from temporalio" not in text
            assert "import nats" not in text
            assert "from nats" not in text
            assert "postgrest" not in text.lower()
            assert "import postgrest" not in text.lower()

    def test_no_runtime_schema_creation(self) -> None:
        for path in _py_files(SRC_ROOT / "aieos"):
            text = path.read_text(encoding="utf-8")
            assert "metadata.create_all" not in text
            assert ".create_all(" not in text
            assert "CREATE TABLE" not in text
            assert "CREATE SCHEMA" not in text

    def test_no_postgrest_or_legacy_edu_content_sor(self) -> None:
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8").lower()
        assert "postgrest" not in pyproject
        for path in _py_files(SRC_ROOT / "aieos"):
            text = path.read_text(encoding="utf-8")
            assert "postgrest" not in text.lower()
            assert "edu.content" not in text

    def test_no_hash_chain_or_crypto_seal_claim(self) -> None:
        for path in list(_py_files(AUDIT_CONTRACT)) + list(_py_files(CONTENT_ROOT)):
            lower = path.read_text(encoding="utf-8").lower()
            for needle in _CRYPTO_NEEDLES:
                assert needle not in lower, f"{path}: {needle}"
        doc = BOUNDARY_DOC.read_text(encoding="utf-8").lower()
        assert "crypto sealing" in doc or "cryptographic" in doc
        assert "dba-independent" not in doc or "not" in doc

    def test_no_synchronous_siem_or_export_dependency(self) -> None:
        for path in _py_files(CONTENT_ROOT / "application"):
            text = path.read_text(encoding="utf-8").lower()
            for pattern in _SIEM_CALL_PATTERNS:
                assert re.search(pattern, text) is None, f"{path}:{pattern}"
        assert "siem" in BOUNDARY_DOC.read_text(encoding="utf-8").lower()


class TestDataMinimizationAndAuthoritySeparation:
    def test_physical_audit_columns_minimized(self) -> None:
        cols = {c.name.lower() for c in audit_records_table.columns}
        for forbidden in _FORBIDDEN_AUDIT_FIELDS:
            assert forbidden not in cols
            assert not any(forbidden in c for c in cols)

    def test_mutation_event_and_security_context_unchanged(self) -> None:
        from aieos.platform.events.models import MutationEventContext
        from aieos.platform.security.context import TrustedSecurityContext

        assert {f.name for f in fields(MutationEventContext)} == {
            "correlation_id",
            "causation_id",
            "actor_principal_id",
            "effective_actor_id",
        }
        assert {f.name for f in fields(TrustedSecurityContext)} == {
            "tenant_id",
            "principal_id",
        }

    def test_security_owner_not_used_as_app_runtime_identity(self) -> None:
        for path in _py_files(SRC_ROOT / "aieos" / "platform" / "api"):
            text = path.read_text(encoding="utf-8")
            assert "AIEOS_SECURITY_SCHEMA_OWNER_ROLE" not in text
            assert "aieos_security_owner" not in text
        for path in _py_files(CONTENT_ROOT):
            text = path.read_text(encoding="utf-8")
            assert "AIEOS_SECURITY_SCHEMA_OWNER_ROLE" not in text
            assert "aieos_security_owner" not in text

    def test_migrator_not_composed_as_product_runtime(self) -> None:
        """Migrator credentials must not be product runtime login.

        PED-I01 may validate the migrator *role name* env
        (``AIEOS_MIGRATOR_ROLE``) for separation checks, and must reject
        ``AIEOS_DATABASE_URL`` in the API runtime environment loader.

        PED-I02 may construct a runtime Engine via ``create_engine`` only in
        ``platform/runtime/database.py`` (never from migrator DSN).

        PED-I11 may construct an EVENT dispatcher Engine via ``create_engine``
        only in ``platform/runtime/event_dispatcher_database.py``.

        PED-I12 may construct a WORKFLOW dispatcher Engine via ``create_engine``
        only in ``platform/runtime/workflow_dispatcher_database.py``.
        """
        _engine_allowed = frozenset(
            {
                "database.py",
                "event_dispatcher_database.py",
                "workflow_dispatcher_database.py",
            }
        )
        for path in _py_files(SRC_ROOT / "aieos"):
            text = path.read_text(encoding="utf-8")
            if "alembic" in path.parts or "migrations" in str(path):
                continue
            if "platform" in path.parts and "runtime" in path.parts:
                assert "aieos_migrator" not in text
                assert "MIGRATOR_USER" not in text
                if path.name not in _engine_allowed:
                    assert "create_engine" not in text
                continue
            assert "aieos_migrator" not in text
            assert "MIGRATOR_USER" not in text
            assert "AIEOS_MIGRATOR" not in text
        # Loader must explicitly reject migrator DSN injection into API env
        config_src = (
            SRC_ROOT / "aieos" / "platform" / "runtime" / "config.py"
        ).read_text(encoding="utf-8")
        assert "AIEOS_DATABASE_URL" in config_src
        assert "AIEOS_MIGRATOR_ROLE" in config_src
        assert "must not be present in the API runtime environment" in config_src
        database_src = (
            SRC_ROOT / "aieos" / "platform" / "runtime" / "database.py"
        ).read_text(encoding="utf-8")
        assert "create_engine" in database_src
        assert "AIEOS_DATABASE_URL" not in database_src
        assert "runtime_database_url" in database_src
        event_db_src = (
            SRC_ROOT
            / "aieos"
            / "platform"
            / "runtime"
            / "event_dispatcher_database.py"
        ).read_text(encoding="utf-8")
        assert "create_engine" in event_db_src
        assert "AIEOS_DATABASE_URL" not in event_db_src
        workflow_db_src = (
            SRC_ROOT
            / "aieos"
            / "platform"
            / "runtime"
            / "workflow_dispatcher_database.py"
        ).read_text(encoding="utf-8")
        assert "create_engine" in workflow_db_src
        assert "AIEOS_DATABASE_URL" not in workflow_db_src


class TestMigrationChainAndOpenApi:
    def test_exact_migration_chain_no_saii050001(self) -> None:
        versions = sorted(p.name for p in MIGRATIONS.glob("*.py") if p.name != "__init__.py")
        assert versions == _EXPECTED_MIGRATIONS
        for path in MIGRATIONS.rglob("*.py"):
            body = path.read_text(encoding="utf-8")
            for needle in ("saii030001", "saii040001", "saii050001"):
                assert needle not in body

    def test_openapi_semantic_unchanged_and_hash(self) -> None:
        from uuid import uuid4

        class _UnusedUowFactory:
            def __call__(self, execution_tenant_id):
                raise AssertionError("OpenAPI export must not touch persistence")

        def schema() -> dict:
            app = create_app(
                uow_factory=_UnusedUowFactory(),
                teaching_uow_factory=_UnusedUowFactory(),
                request_identity_authenticator=FixedPrincipalAuthenticator(uuid4()),
                security_resolver=StubSecurityContextResolver(uuid4(), uuid4()),
                content_types=StaticContentTypeCatalog({"test.generic"}),
                cursor_signing_key=b"gci-i04-openapi-export-key",
                schema_registry=ContentSchemaRegistry(),
                idempotency_retention=IDEMPOTENCY_RETENTION,
                review_authorization=AllowReviewAuthorization(),
                review_comment_policy=AllowReviewCommentPolicy(),
                publication_authorization=AllowPublicationAuthorization(),
                publication_governance=AllowPublicationGovernance(),
                asset_reference_validation=AllowAssetReferenceValidation(),
                asset_current_governance=AllowAssetCurrentGovernance(),
            )
            return build_openapi(app)

        first = canonical_openapi_json(schema())
        second = canonical_openapi_json(schema())
        assert first == second
        snapshot = SNAPSHOT.read_text(encoding="utf-8")
        assert first == snapshot
        digest = hashlib.sha256(SNAPSHOT.read_bytes()).hexdigest().upper()
        assert digest == EXPECTED_OPENAPI_SHA256


class TestPrivilegesAndRlsCatalog:
    def test_owner_separation_and_runtime_attributes(self, postgres18, bootstrap_engine) -> None:
        with bootstrap_engine.connect() as conn:
            security_owner = conn.execute(
                text(
                    "SELECT r.rolname FROM pg_namespace n "
                    "JOIN pg_roles r ON r.oid = n.nspowner "
                    "WHERE n.nspname = 'security'"
                )
            ).scalar_one()
            content_owner = conn.execute(
                text(
                    "SELECT r.rolname FROM pg_namespace n "
                    "JOIN pg_roles r ON r.oid = n.nspowner "
                    "WHERE n.nspname = 'content'"
                )
            ).scalar_one()
            assert security_owner == SECURITY_SCHEMA_OWNER_ROLE
            assert content_owner == SCHEMA_OWNER_ROLE
            identities = {
                security_owner,
                content_owner,
                postgres18["migrator_user"],
                postgres18["runtime_user"],
                postgres18["migration_runtime_user"],
                postgres18["event_dispatcher_user"],
                postgres18["workflow_dispatcher_user"],
            }
            assert len(identities) == 7

            for role in (
                postgres18["runtime_user"],
                postgres18["migration_runtime_user"],
                postgres18["event_dispatcher_user"],
                postgres18["workflow_dispatcher_user"],
            ):
                row = conn.execute(
                    text(
                        "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = :r"
                    ),
                    {"r": role},
                ).one()
                assert row == (False, False)

            migrator = conn.execute(
                text(
                    "SELECT rolsuper, rolbypassrls, rolinherit "
                    "FROM pg_roles WHERE rolname = :r"
                ),
                {"r": postgres18["migrator_user"]},
            ).one()
            assert migrator == (False, False, False)

    def test_rls_enable_force_insert_only_policy(self, bootstrap_engine) -> None:
        with bootstrap_engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT c.relrowsecurity, c.relforcerowsecurity
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'security' AND c.relname = 'audit_records'
                    """
                )
            ).one()
            assert row == (True, True)
            policies = list(
                conn.execute(
                    text(
                        """
                        SELECT polname, polcmd, pg_get_expr(polqual, polrelid) AS qual
                        FROM pg_policy
                        WHERE polrelid = 'security.audit_records'::regclass
                        """
                    )
                ).mappings()
            )
            assert len(policies) == 1
            assert policies[0]["polcmd"] == "a"  # INSERT
            assert "FOR ALL" not in str(policies)
            cmds = {p["polcmd"] for p in policies}
            assert "r" not in cmds  # SELECT
            assert "w" not in cmds  # UPDATE
            assert "d" not in cmds  # DELETE

    def test_runtime_and_migration_runtime_audit_privileges(
        self, bootstrap_engine, postgres18
    ) -> None:
        with bootstrap_engine.connect() as conn:
            for role in (
                postgres18["runtime_user"],
                postgres18["migration_runtime_user"],
            ):
                assert conn.execute(
                    text("SELECT has_schema_privilege(:r, 'security', 'USAGE')"),
                    {"r": role},
                ).scalar_one()
                assert conn.execute(
                    text(
                        "SELECT has_table_privilege(:r, 'security.audit_records', 'INSERT')"
                    ),
                    {"r": role},
                ).scalar_one()
                for priv in ("SELECT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
                    assert not conn.execute(
                        text(
                            "SELECT has_table_privilege(:r, 'security.audit_records', :p)"
                        ),
                        {"r": role, "p": priv},
                    ).scalar_one(), f"{role}:{priv}"

    def test_dispatchers_denied_audit_ledger(
        self, bootstrap_engine, postgres18
    ) -> None:
        with bootstrap_engine.connect() as conn:
            for role in (
                postgres18["event_dispatcher_user"],
                postgres18["workflow_dispatcher_user"],
            ):
                for priv in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                    assert not conn.execute(
                        text(
                            "SELECT has_table_privilege(:r, 'security.audit_records', :p)"
                        ),
                        {"r": role, "p": priv},
                    ).scalar_one(), f"{role}:{priv}"

    def test_public_has_no_accidental_security_acl(self, bootstrap_engine) -> None:
        with bootstrap_engine.connect() as conn:
            assert not conn.execute(
                text("SELECT has_schema_privilege('public', 'security', 'CREATE')")
            ).scalar_one()
            for priv in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                assert not conn.execute(
                    text(
                        "SELECT has_table_privilege('public', 'security.audit_records', :p)"
                    ),
                    {"p": priv},
                ).scalar_one(), priv


class TestImplementationBaselineDocs:
    def test_boundary_doc_states_baseline_and_non_production(self) -> None:
        doc = BOUNDARY_DOC.read_text(encoding="utf-8")
        assert "SAI-I05" in doc
        assert "NOT AUTHORIZED" in doc
        assert "IMPLEMENTATION-BASELINE COMPLETE" in doc
        assert "NON-PRODUCTION" in doc or "non-production" in doc.lower()
        assert "provisioning" in doc.lower()
        lowered = doc.lower()
        assert "production ready" not in lowered
        assert "safe to deploy" not in lowered
        assert 'as "production approved"' not in lowered
        changelog = CHANGELOG.read_text(encoding="utf-8")
        assert "SAI-I05" in changelog
        assert "implementation baseline" in changelog.lower()
        assert "NOT AUTHORIZED" in changelog or "not authorized" in changelog.lower()
        assert "production ready" not in changelog.lower()
        assert "safe to deploy" not in changelog.lower()
