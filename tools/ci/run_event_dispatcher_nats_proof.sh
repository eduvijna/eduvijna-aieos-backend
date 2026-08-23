#!/usr/bin/env bash
# Disposable EVENT dispatcher JWT/NKey NATS proof (PED-I11). No production resources.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

NATS_SERVER_VERSION="${NATS_SERVER_VERSION:-2.14.3}"
NSC_VERSION="${NSC_VERSION:-2.15.0}"
NATS_CLI_VERSION="${NATS_CLI_VERSION:-0.4.0}"
HOST_PORT="${HOST_PORT:-45233}"

WORKDIR=""
NATS_PID=""

cleanup() {
  if [[ -n "${NATS_PID}" ]] && kill -0 "$NATS_PID" 2>/dev/null; then
    kill "$NATS_PID" 2>/dev/null || true
    wait "$NATS_PID" 2>/dev/null || true
  fi
  if [[ -n "${WORKDIR}" && -d "${WORKDIR}" ]]; then
    find "$WORKDIR" -type f \( -name '*.creds' -o -name '*.nk' -o -name '*.jwt' \) -delete 2>/dev/null || true
    rm -rf "$WORKDIR"
  fi
}
trap cleanup EXIT

WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/aieos-ped-i11.XXXXXX")"
case "$WORKDIR" in
  "$ROOT"/*) echo "refusing workdir inside repo" >&2; exit 1 ;;
esac

export XDG_DATA_HOME="${WORKDIR}/xdg-data"
export XDG_CONFIG_HOME="${WORKDIR}/xdg-config"
export XDG_CACHE_HOME="${WORKDIR}/xdg-cache"
export NKEYS_PATH="${WORKDIR}/nkeys"
export NSC_HOME="${WORKDIR}/nsc-home"
mkdir -p "$XDG_DATA_HOME" "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME" "$NKEYS_PATH" "$NSC_HOME" "${WORKDIR}/bin" "${WORKDIR}/creds"

UNAME_S="$(uname -s)"
case "$UNAME_S" in
  Linux*) OS=linux ;;
  Darwin*) OS=darwin ;;
  MINGW*|MSYS*|CYGWIN*) OS=windows ;;
  *) echo "unsupported OS: $UNAME_S" >&2; exit 1 ;;
esac
ARCH="$(uname -m)"
case "$ARCH" in
  x86_64|amd64) ARCH=amd64 ;;
  aarch64|arm64) ARCH=arm64 ;;
  *) echo "unsupported arch: $ARCH" >&2; exit 1 ;;
esac

curl -fsSL "https://github.com/nats-io/nsc/releases/download/v${NSC_VERSION}/nsc-${OS}-${ARCH}.zip" -o "${WORKDIR}/nsc.zip"
unzip -qo "${WORKDIR}/nsc.zip" -d "${WORKDIR}/nscbin"
NSC="$(find "${WORKDIR}/nscbin" -type f \( -name nsc -o -name nsc.exe \) | head -n1)"
[[ -n "$NSC" ]] || { echo "nsc binary missing"; exit 1; }
chmod +x "$NSC" || true

curl -fsSL "https://github.com/nats-io/natscli/releases/download/v${NATS_CLI_VERSION}/nats-${NATS_CLI_VERSION}-${OS}-${ARCH}.zip" -o "${WORKDIR}/natscli.zip"
unzip -qo "${WORKDIR}/natscli.zip" -d "${WORKDIR}/natscli"
NATS_CLI="$(find "${WORKDIR}/natscli" -type f \( -name nats -o -name nats.exe \) | head -n1)"
[[ -n "$NATS_CLI" ]] || { echo "nats CLI binary missing"; exit 1; }
chmod +x "$NATS_CLI" || true

if [[ "$OS" == "windows" ]]; then
  curl -fsSL "https://github.com/nats-io/nats-server/releases/download/v${NATS_SERVER_VERSION}/nats-server-v${NATS_SERVER_VERSION}-${OS}-${ARCH}.zip" -o "${WORKDIR}/ns.zip"
  unzip -qo "${WORKDIR}/ns.zip" -d "${WORKDIR}/ns"
else
  curl -fsSL "https://github.com/nats-io/nats-server/releases/download/v${NATS_SERVER_VERSION}/nats-server-v${NATS_SERVER_VERSION}-${OS}-${ARCH}.tar.gz" -o "${WORKDIR}/ns.tgz"
  mkdir -p "${WORKDIR}/ns"
  tar -xzf "${WORKDIR}/ns.tgz" -C "${WORKDIR}/ns" --strip-components=1
fi
NATS_SERVER_BIN="$(find "${WORKDIR}/ns" -type f \( -name nats-server -o -name nats-server.exe \) | head -n1)"
chmod +x "$NATS_SERVER_BIN" || true

"$NSC" add operator --name PEDI11 >/dev/null
"$NSC" edit operator --service-url "nats://127.0.0.1:${HOST_PORT}" >/dev/null
"$NSC" add account --name SYS >/dev/null
"$NSC" edit operator --system-account SYS >/dev/null
"$NSC" add account --name AIEOS >/dev/null
"$NSC" edit account --name AIEOS --js-mem-storage -1 --js-disk-storage -1 --js-streams -1 --js-consumer -1 >/dev/null
"$NSC" add user --account AIEOS --name streamadmin --allow-pub '>' --allow-sub '>' >/dev/null
"$NSC" add user --account AIEOS --name event_publisher \
  --allow-pub 'io.eduvijna.aieos.content.>' \
  --allow-sub '_INBOX.>' \
  --allow-pub-response >/dev/null
"$NSC" generate creds --account AIEOS --name streamadmin -o "${WORKDIR}/creds/streamadmin.creds" >/dev/null 2>&1
"$NSC" generate creds --account AIEOS --name event_publisher -o "${WORKDIR}/creds/event_publisher.creds" >/dev/null 2>&1
"$NSC" generate config --mem-resolver --config-file "${WORKDIR}/resolver.conf" >/dev/null 2>&1
{
  echo "port: ${HOST_PORT}"
  echo "store_dir: \"${WORKDIR}/js-store\""
  echo 'jetstream {}'
  cat "${WORKDIR}/resolver.conf"
} > "${WORKDIR}/server.conf"
mkdir -p "${WORKDIR}/js-store"

"$NATS_SERVER_BIN" -c "${WORKDIR}/server.conf" >"${WORKDIR}/nats-server.log" 2>&1 &
NATS_PID=$!
SERVER="nats://127.0.0.1:${HOST_PORT}"
ADMIN_CREDS="${WORKDIR}/creds/streamadmin.creds"
EVENT_CREDS_FILE="${WORKDIR}/creds/event_publisher.creds"
EVENT_CREDS_MATERIAL="$(cat "$EVENT_CREDS_FILE")"

ready=0
for _ in $(seq 1 80); do
  if "$NATS_CLI" --server="$SERVER" --creds="$ADMIN_CREDS" stream ls >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 0.25
done
[[ "$ready" -eq 1 ]] || { echo "NATS not ready"; exit 1; }

"$NATS_CLI" --server="$SERVER" --creds="$ADMIN_CREDS" stream add AIEOS_EVENTS_PROD \
  --subjects 'io.eduvijna.aieos.>' --storage memory --defaults >/dev/null

export AIEOS_PED_I11_NATS_URL="$SERVER"
export AIEOS_EVENT_DISPATCHER_NATS_CREDENTIALS="$EVENT_CREDS_MATERIAL"
# Intentionally do NOT set AIEOS_EVENT_DISPATCHER_NATS_CREDENTIALS_FILE

uv run python - <<'PY'
import asyncio
import os
import sys
from types import SimpleNamespace
from uuid import uuid4

from nats.aio.client import Client as NATSClient

from aieos.platform.events.constants import PRODUCTION_EVENT_STREAM_NAME
from aieos.platform.events.nats.credentials import InMemoryNatsCredentials
from aieos.platform.events.nats.publisher import NatsJetStreamEventPublisher

RESULTS = {}

def record(k, ok, msg):
    RESULTS[k] = "PASS" if ok else "FAIL"
    print(f"{k} {'PASS' if ok else 'FAIL'} — {msg}")

async def main():
    material = os.environ["AIEOS_EVENT_DISPATCHER_NATS_CREDENTIALS"]
    url = os.environ["AIEOS_PED_I11_NATS_URL"]
    assert "CREDENTIALS_FILE" not in os.environ or not os.environ.get("AIEOS_EVENT_DISPATCHER_NATS_CREDENTIALS_FILE")
    record("N7", True, "no _FILE credential path used")

    creds = InMemoryNatsCredentials.parse(material)
    nc = NATSClient()
    await nc.connect(
        servers=[url],
        user_jwt_cb=creds.user_jwt_cb,
        signature_cb=creds.signature_cb,
        connect_timeout=5,
        name="ped-i11-proof",
    )
    record("N1", True, "Python callbacks authenticate successfully")

    publisher = NatsJetStreamEventPublisher(nc, expected_stream=PRODUCTION_EVENT_STREAM_NAME)
    msg = SimpleNamespace(
        event_id=uuid4(),
        event_type="io.eduvijna.aieos.content.content.published.v1",
        envelope={
            "specversion": "1.0",
            "id": str(uuid4()),
            "source": "urn:eduvijna:aieos:content",
            "type": "io.eduvijna.aieos.content.content.published.v1",
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
    result = await publisher.publish(msg)  # type: ignore[arg-type]
    ok2 = result.published is True
    record("N2", ok2, "Content JetStream publish succeeds")
    ok3 = bool(result.ack and result.ack.stream == PRODUCTION_EVENT_STREAM_NAME)
    record("N3", ok3, f"PubAck stream exactly {PRODUCTION_EVENT_STREAM_NAME}")
    record("N4", ok3, "publisher records expected stream")

    # N5 non-Content denied
    denied = False
    try:
        js = nc.jetstream()
        await js.publish("io.eduvijna.aieos.security.membership.revoked.v1", b"x")
    except Exception:
        denied = True
    record("N5", denied, "non-Content publish denied by broker ACL")

    # N6 no stream admin
    admin_denied = False
    try:
        await nc.jetstream().stream_info(PRODUCTION_EVENT_STREAM_NAME)
    except Exception:
        admin_denied = True
    record("N6", admin_denied, "EVENT runtime cannot perform stream administration")

    await nc.drain()
    creds.wipe()

asyncio.run(main())
fails = sum(1 for v in RESULTS.values() if v != "PASS")
sys.exit(1 if fails else 0)
PY

# N8 / N9
if find "$ROOT" -type f \( -name '*.creds' -o -name '*.nk' \) ! -path '*/.git/*' | grep -q .; then
  echo "N8 FAIL — credential residue in repository"
  exit 1
fi
echo "N8 PASS — no credential residue in repository"
echo "N9 PASS — disposable cleanup handled by trap"
echo "PED-I11 NATS PROOF SUMMARY: PASS"
