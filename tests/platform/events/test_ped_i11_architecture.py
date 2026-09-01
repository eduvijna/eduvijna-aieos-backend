"""PED-I11 architecture abuse static checks."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from aieos.platform.events.constants import (
    PRODUCTION_EVENT_PUBLISH_PREFIXES,
    PRODUCTION_EVENT_STREAM_NAME,
    PRODUCTION_EVENT_STREAM_SUBJECTS,
    TEST_STREAM_NAME,
)

pytestmark = pytest.mark.ped_i11

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src" / "aieos"


def _iter_prod_runtime_files() -> list[Path]:
    files: list[Path] = []
    for rel in (
        "platform/runtime/config_event_dispatcher.py",
        "platform/runtime/entrypoints/event_dispatcher_main.py",
        "platform/runtime/event_dispatcher_database.py",
        "platform/runtime/event_dispatcher_authority.py",
        "platform/events/nats/connection.py",
        "platform/events/nats/credentials.py",
        "platform/events/nats/daemon.py",
        "platform/events/nats/publisher.py",
        "platform/events/persistence/candidates.py",
    ):
        path = SRC / rel
        assert path.is_file(), path
        files.append(path)
    return files


def test_forbidden_patterns_absent_from_production_event_runtime() -> None:
    forbidden = (
        "user_credentials=",
        "aieos.event.v1.",
        "ssl.CERT_NONE",
        "check_hostname = False",
        "check_hostname=False",
        "verify = False",
        "verify=False",
        "NamedTemporaryFile",
        "stream.add",
        "add_stream",
        "delete_stream",
        "update_stream",
        "$JS.API",
    )
    for path in _iter_prod_runtime_files():
        text = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in text, f"{path}: forbidden {needle!r}"
        if "AIEOS_EVENT_DISPATCHER_NATS_CREDENTIALS_FILE" in text:
            assert path.name == "config_event_dispatcher.py"
            assert "must not be set" in text or "not production authority" in text
        # Role elevation / bypass must not be enabled (NOBYPASSRLS checks are allowed).
        assert "SET ROLE" not in text, f"{path}: forbidden SET ROLE"
        assert "ALTER ROLE" not in text, f"{path}: forbidden ALTER ROLE"
        for match in re.finditer(r"(?<!NO)BYPASSRLS", text):
            pytest.fail(f"{path}: bare BYPASSRLS at {match.start()}")


def test_production_runtime_does_not_embed_test_only_stream_literal() -> None:
    for path in _iter_prod_runtime_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == "AIEOS_EVENTS":
                pytest.fail(f"{path} embeds TEST-ONLY stream name AIEOS_EVENTS")


def test_no_security_tenants_candidate_scan_in_event_runtime() -> None:
    for path in _iter_prod_runtime_files():
        text = path.read_text(encoding="utf-8")
        assert "security.tenants" not in text
        assert "SELECT DISTINCT tenant_id" not in text


def test_stream_constants() -> None:
    assert PRODUCTION_EVENT_STREAM_NAME == "AIEOS_EVENTS_PROD"
    assert TEST_STREAM_NAME == "AIEOS_EVENTS"
    assert PRODUCTION_EVENT_STREAM_NAME != TEST_STREAM_NAME
    assert PRODUCTION_EVENT_STREAM_SUBJECTS == ("io.eduvijna.aieos.>",)
    assert PRODUCTION_EVENT_PUBLISH_PREFIXES == (
        "io.eduvijna.aieos.content.",
        "io.eduvijna.aieos.teaching.",
    )


def test_candidate_and_daemon_modules_do_not_query_tenant_directory() -> None:
    """Committed outbox delivery is not suppressed by tenant ACTIVE/SUSPENDED/DISABLED."""
    candidates = (SRC / "platform/events/persistence/candidates.py").read_text(
        encoding="utf-8"
    )
    daemon = (SRC / "platform/events/nats/daemon.py").read_text(encoding="utf-8")
    for text in (candidates, daemon):
        assert "security.tenants" not in text
        assert "tenant_status" not in text
        assert "SUSPENDED" not in text
        assert "DISABLED" not in text
        assert "ACTIVE" not in text
