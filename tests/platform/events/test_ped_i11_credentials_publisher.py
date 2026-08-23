"""PED-I11 in-memory NATS credentials + expected-stream publisher tests."""

from __future__ import annotations

import asyncio
import base64
import os
import platform
import subprocess
import zipfile
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from aieos.platform.events.constants import (
    ERROR_NATS_STREAM_MISMATCH,
    PRODUCTION_EVENT_STREAM_NAME,
    TEST_STREAM_NAME,
)
from aieos.platform.events.nats.connection import build_verifying_ssl_context
from aieos.platform.events.nats.credentials import (
    InMemoryNatsCredentials,
    NatsCredentialError,
)
from aieos.platform.events.nats.publisher import NatsJetStreamEventPublisher

pytestmark = pytest.mark.ped_i11

_NSC_VERSION = "2.15.0"


def _generate_disposable_creds(workdir: Path) -> str:
    os_name = platform.system().lower()
    if os_name.startswith("win"):
        os_key = "windows"
    elif os_name == "darwin":
        os_key = "darwin"
    else:
        os_key = "linux"
    arch = platform.machine().lower()
    arch_key = "arm64" if arch in {"arm64", "aarch64"} else "amd64"
    nsc_zip = workdir / "nsc.zip"
    url = (
        f"https://github.com/nats-io/nsc/releases/download/v{_NSC_VERSION}/"
        f"nsc-{os_key}-{arch_key}.zip"
    )
    urllib.request.urlretrieve(url, nsc_zip)
    with zipfile.ZipFile(nsc_zip) as zf:
        zf.extractall(workdir / "bin")
    nsc_candidates = list((workdir / "bin").rglob("nsc*"))
    nsc = next(p for p in nsc_candidates if p.is_file())
    env = os.environ.copy()
    env["XDG_DATA_HOME"] = str(workdir / "xdg-data")
    env["XDG_CONFIG_HOME"] = str(workdir / "xdg-config")
    env["NKEYS_PATH"] = str(workdir / "nkeys")
    env["NSC_HOME"] = str(workdir / "nsc-home")
    for d in ("xdg-data", "xdg-config", "nkeys", "nsc-home"):
        (workdir / d).mkdir(parents=True, exist_ok=True)

    def run(args: list[str]) -> None:
        subprocess.run([str(nsc), *args], check=True, env=env, capture_output=True)

    run(["add", "operator", "--name", "PEDI11"])
    run(["add", "account", "--name", "AIEOS"])
    run(
        [
            "add",
            "user",
            "--account",
            "AIEOS",
            "--name",
            "event_publisher",
            "--allow-pub",
            "io.eduvijna.aieos.content.>",
            "--allow-sub",
            "_INBOX.>",
            "--allow-pub-response",
        ]
    )
    creds_path = workdir / "event.creds"
    run(
        [
            "generate",
            "creds",
            "--account",
            "AIEOS",
            "--name",
            "event_publisher",
            "-o",
            str(creds_path),
        ]
    )
    return creds_path.read_text(encoding="utf-8")


def test_production_and_test_stream_constants_distinct() -> None:
    assert PRODUCTION_EVENT_STREAM_NAME != TEST_STREAM_NAME
    assert PRODUCTION_EVENT_STREAM_NAME == "AIEOS_EVENTS_PROD"
    assert TEST_STREAM_NAME == "AIEOS_EVENTS"


def test_ssl_context_requires_verification() -> None:
    import ssl

    ctx = build_verifying_ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_parse_valid_disposable_creds(tmp_path: Path) -> None:
    material = _generate_disposable_creds(tmp_path)
    creds = InMemoryNatsCredentials.parse(material)
    assert "BEGIN" not in repr(creds)
    assert "SU" not in repr(creds)
    jwt = creds.user_jwt_cb()
    jwt_s = jwt.decode() if isinstance(jwt, bytes) else jwt
    assert jwt_s.count(".") == 2
    sig = creds.signature_cb(b"nonce-challenge")
    assert base64.b64decode(sig)
    creds.wipe()


def test_missing_jwt_rejected() -> None:
    material = (
        "-----BEGIN USER NKEY SEED-----\n"
        "SUAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n"
        "------END USER NKEY SEED------\n"
    )
    with pytest.raises(NatsCredentialError):
        InMemoryNatsCredentials.parse(material)


def test_duplicate_jwt_rejected(tmp_path: Path) -> None:
    material = _generate_disposable_creds(tmp_path)
    with pytest.raises(NatsCredentialError, match="duplicate"):
        InMemoryNatsCredentials.parse(material + "\n" + material)


def test_malformed_seed_rejected() -> None:
    material = (
        "-----BEGIN NATS USER JWT-----\n"
        "eyJhbGciOiJub25lIn0.e30.e30\n"
        "------END NATS USER JWT------\n"
        "-----BEGIN USER NKEY SEED-----\n"
        "NOTASEED\n"
        "------END USER NKEY SEED------\n"
    )
    with pytest.raises(NatsCredentialError):
        InMemoryNatsCredentials.parse(material)


def test_expected_stream_mismatch_not_published() -> None:
    class _JS:
        async def publish(self, *args, **kwargs):
            return SimpleNamespace(stream="WRONG_STREAM", seq=9)

    class _Client:
        def jetstream(self):
            return _JS()

    publisher = NatsJetStreamEventPublisher(
        _Client(),  # type: ignore[arg-type]
        expected_stream=PRODUCTION_EVENT_STREAM_NAME,
    )
    msg = SimpleNamespace(
        event_id=uuid4(),
        event_type="io.eduvijna.aieos.content.content.created.v1",
        envelope={
            "specversion": "1.0",
            "id": str(uuid4()),
            "source": "urn:eduvijna:aieos:content",
            "type": "io.eduvijna.aieos.content.content.created.v1",
            "subject": "content/x",
            "time": "2026-08-23T00:00:00Z",
            "datacontenttype": "application/json",
            "data": {},
            "tenantid": str(uuid4()),
            "correlationid": str(uuid4()),
            "causationid": str(uuid4()),
            "actorid": str(uuid4()),
            "effectiveactorid": str(uuid4()),
            "aggregaterevision": 1,
        },
    )

    async def _run():
        return await publisher.publish(msg)  # type: ignore[arg-type]

    result = asyncio.run(_run())
    assert result.published is False
    assert result.error_code == ERROR_NATS_STREAM_MISMATCH
    assert result.permanent is True
    assert result.ack is not None
    assert result.ack.stream == "WRONG_STREAM"


def test_no_creds_file_written_by_parser(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    nsc_out = tmp_path / "nsc-out"
    nsc_out.mkdir(parents=True, exist_ok=True)
    material = _generate_disposable_creds(nsc_out)
    InMemoryNatsCredentials.parse(material)
    assert not list(Path.cwd().glob("*.creds"))
    assert not list(Path.cwd().glob("*.nk"))
    assert not list(Path.cwd().glob("*.jwt"))
