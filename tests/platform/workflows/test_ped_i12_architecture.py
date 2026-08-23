"""PED-I12 architecture abuse static checks."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from aieos.platform.runtime.config_workflow_dispatcher import (
    ENV_WORKER_TEMPORAL_API_KEY,
    ENV_WORKER_TEMPORAL_NAMESPACE,
    ENV_WORKER_TEMPORAL_TARGET_HOST,
    ENV_WORKFLOW_DISPATCHER_TEMPORAL_API_KEY,
    ENV_WORKFLOW_DISPATCHER_TEMPORAL_CONNECT_TIMEOUT_SECONDS,
    ENV_WORKFLOW_DISPATCHER_TEMPORAL_NAMESPACE,
    ENV_WORKFLOW_DISPATCHER_TEMPORAL_TARGET_HOST,
)
from aieos.platform.workflows.constants import (
    CONTENT_REVIEW_TASK_QUEUE,
    SIGNAL_REVIEW_DECISION_RECORDED,
)

pytestmark = pytest.mark.ped_i12

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src" / "aieos"


def _iter_prod_runtime_files() -> list[Path]:
    files: list[Path] = []
    for rel in (
        "platform/runtime/config_workflow_dispatcher.py",
        "platform/runtime/entrypoints/workflow_dispatcher_main.py",
        "platform/runtime/workflow_dispatcher_database.py",
        "platform/runtime/workflow_dispatcher_authority.py",
        "platform/workflows/persistence/candidates.py",
        "platform/workflows/temporal/connection.py",
        "platform/workflows/temporal/daemon.py",
        "platform/workflows/temporal/gateway.py",
    ):
        path = SRC / rel
        assert path.is_file(), path
        files.append(path)
    return files


def test_architecture_frozen_env_names_present() -> None:
    config = (SRC / "platform/runtime/config_workflow_dispatcher.py").read_text(
        encoding="utf-8"
    )
    for name in (
        ENV_WORKFLOW_DISPATCHER_TEMPORAL_TARGET_HOST,
        ENV_WORKFLOW_DISPATCHER_TEMPORAL_NAMESPACE,
        ENV_WORKFLOW_DISPATCHER_TEMPORAL_API_KEY,
        ENV_WORKFLOW_DISPATCHER_TEMPORAL_CONNECT_TIMEOUT_SECONDS,
    ):
        assert name in config


def test_forbidden_patterns_absent_from_workflow_dispatcher_runtime() -> None:
    forbidden = (
        "tls=False",
        "tls = False",
        "verify=False",
        "verify = False",
        "ssl.CERT_NONE",
        "check_hostname=False",
        "check_hostname = False",
        "CloudOps",
        "cloud_ops",
        "TEMPORAL_CLOUD_OPS",
        "terminate_workflow",
        "cancel_workflow",
        "reset_workflow",
        "create_schedule",
        "update_search_attributes",
        "workflow_dispatcher production",
    )
    for path in _iter_prod_runtime_files():
        text = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in text, f"{path}: forbidden {needle!r}"
        assert "SET ROLE" not in text, f"{path}: forbidden SET ROLE"
        assert "ALTER ROLE" not in text, f"{path}: forbidden ALTER ROLE"
        for match in re.finditer(r"(?<!NO)BYPASSRLS", text):
            pytest.fail(f"{path}: bare BYPASSRLS at {match.start()}")


def test_worker_api_key_not_reused_as_dispatcher_credential() -> None:
    config = (SRC / "platform/runtime/config_workflow_dispatcher.py").read_text(
        encoding="utf-8"
    )
    connection = (SRC / "platform/workflows/temporal/connection.py").read_text(
        encoding="utf-8"
    )
    # Worker env names may appear only as rejected/documentation constants.
    assert ENV_WORKER_TEMPORAL_API_KEY in config
    assert "never accepted" in config.lower() or "never" in config.lower()
    assert ENV_WORKER_TEMPORAL_API_KEY not in connection
    assert ENV_WORKER_TEMPORAL_TARGET_HOST not in connection
    assert ENV_WORKER_TEMPORAL_NAMESPACE not in connection
    assert "api_key=config.temporal_api_key" in connection or (
        "api_key=config.temporal_api_key," in connection
    )


def test_no_security_tenants_candidate_scan() -> None:
    for path in _iter_prod_runtime_files():
        text = path.read_text(encoding="utf-8")
        assert "security.tenants" not in text
        assert "SELECT DISTINCT tenant_id" not in text


def test_candidate_repository_no_payload_or_tenant_context() -> None:
    candidates = (SRC / "platform/workflows/persistence/candidates.py").read_text(
        encoding="utf-8"
    )
    assert "set_config" not in candidates
    assert "aieos.tenant_id" not in candidates
    assert "SET ROLE" not in candidates
    assert "input" not in candidates.lower() or "WorkflowDispatchCandidate" in candidates
    assert "SELECT tenant_id, eligible_at" in candidates
    assert "list_start_intent_candidates(:limit, :as_of)" in candidates
    assert "list_command_intent_candidates(:limit, :as_of)" in candidates
    # Payload / business columns must not be selected.
    for needle in (
        "business_key",
        "command_payload",
        "workflow_id",
        "temporal_workflow_id",
        "SELECT *",
    ):
        assert needle not in candidates


def test_no_production_endpoint_or_credential_literals() -> None:
    productionish = (
        "tmprl.cloud",
        "api.temporal.io",
        "digitalocean.com",
        "sk_live_",
        "password=",
    )
    for path in _iter_prod_runtime_files():
        text = path.read_text(encoding="utf-8")
        for needle in productionish:
            assert needle not in text, f"{path}: production-ish literal {needle!r}"


def test_daemon_does_not_suppress_committed_intents_by_tenant_status() -> None:
    daemon = (SRC / "platform/workflows/temporal/daemon.py").read_text(encoding="utf-8")
    candidates = (SRC / "platform/workflows/persistence/candidates.py").read_text(
        encoding="utf-8"
    )
    for text in (daemon, candidates):
        assert "security.tenants" not in text
        assert "tenant_status" not in text
        assert "SUSPENDED" not in text
        assert "DISABLED" not in text


def test_operation_fence_constants() -> None:
    assert CONTENT_REVIEW_TASK_QUEUE == "aieos.content.review"
    assert SIGNAL_REVIEW_DECISION_RECORDED == "review_decision_recorded"
    gateway = (SRC / "platform/workflows/temporal/gateway.py").read_text(encoding="utf-8")
    assert "task_queue != CONTENT_REVIEW_TASK_QUEUE" in gateway
    assert "task_queue or CONTENT_REVIEW_TASK_QUEUE" not in gateway
    assert "ContentReviewWorkflowV1.run" in gateway
    assert "SIGNAL_REVIEW_DECISION_RECORDED" in gateway


def test_no_workflow_dispatcher_deployment_config_introduced() -> None:
    infra_globs = list((ROOT / "deploy").glob("**/*")) if (ROOT / "deploy").exists() else []
    for path in infra_globs:
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert "workflow_dispatcher" not in text.lower() or "NOT AUTHORIZED" in text


def test_gateway_ast_has_no_admin_defs() -> None:
    tree = ast.parse(
        (SRC / "platform/workflows/temporal/gateway.py").read_text(encoding="utf-8")
    )
    method_names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for forbidden in (
        "terminate",
        "cancel",
        "reset",
        "create_schedule",
        "delete_namespace",
    ):
        assert forbidden not in method_names
