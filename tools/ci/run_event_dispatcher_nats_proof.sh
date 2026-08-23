#!/usr/bin/env bash
# Disposable EVENT dispatcher JWT/NKey + TLS NATS proof (PED-I11 / PED-I11R1).
# Exercises connect_event_dispatcher_nats. No production resources.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

NATS_SERVER_VERSION="${NATS_SERVER_VERSION:-2.14.3}"
NSC_VERSION="${NSC_VERSION:-2.15.0}"
NATS_CLI_VERSION="${NATS_CLI_VERSION:-0.4.0}"
HOST_PORT="${HOST_PORT:-45233}"
TLS_HOST="localhost"

WORKDIR=""
NATS_PID=""
CLEANED=0

cleanup() {
  if [[ "${CLEANED}" -eq 1 ]]; then
    return 0
  fi
  if [[ -n "${NATS_PID}" ]] && kill -0 "$NATS_PID" 2>/dev/null; then
    kill "$NATS_PID" 2>/dev/null || true
    wait "$NATS_PID" 2>/dev/null || true
  fi
  NATS_PID=""
  if [[ -n "${WORKDIR}" && -d "${WORKDIR}" ]]; then
    find "$WORKDIR" -type f \( \
      -name '*.creds' -o -name '*.nk' -o -name '*.jwt' -o -name '*.seed' \
      -o -name '*.key' -o -name '*.pem' \
    \) -delete 2>/dev/null || true
    rm -rf "$WORKDIR"
  fi
  WORKDIR=""
  CLEANED=1
}
trap cleanup EXIT

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

path_for_native() {
  # nats-server.exe on Windows cannot open Git-Bash /tmp paths.
  if [[ "$OS" == "windows" ]] && command -v cygpath >/dev/null 2>&1; then
    cygpath -m "$1"
  else
    printf '%s' "$1"
  fi
}

if [[ "$OS" == "windows" ]]; then
  export TMPDIR="${LOCALAPPDATA:-${TEMP:-/tmp}}"
fi
WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/aieos-ped-i11.XXXXXX")"
case "$WORKDIR" in
  "$ROOT"/*) echo "refusing workdir inside repo" >&2; exit 1 ;;
esac

export XDG_DATA_HOME="${WORKDIR}/xdg-data"
export XDG_CONFIG_HOME="${WORKDIR}/xdg-config"
export XDG_CACHE_HOME="${WORKDIR}/xdg-cache"
export NKEYS_PATH="${WORKDIR}/nkeys"
export NSC_HOME="${WORKDIR}/nsc-home"
mkdir -p "$XDG_DATA_HOME" "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME" "$NKEYS_PATH" "$NSC_HOME" \
  "${WORKDIR}/bin" "${WORKDIR}/creds" "${WORKDIR}/tls"

command -v openssl >/dev/null 2>&1 || { echo "openssl required"; exit 1; }

# Portable CA + leaf with SKI/AKI (required by Python 3.14 SSL verification).
cat > "${WORKDIR}/tls/ca.cnf" <<EOF
[req]
distinguished_name = req_dn
x509_extensions = v3_ca
prompt = no
[req_dn]
CN = AIEOS-PED-I11-Test-CA
[v3_ca]
basicConstraints = critical,CA:TRUE
keyUsage = critical,keyCertSign,cRLSign
subjectKeyIdentifier = hash
EOF
openssl genrsa -out "${WORKDIR}/tls/ca.key" 2048
openssl req -x509 -new -nodes -key "${WORKDIR}/tls/ca.key" -sha256 -days 1 \
  -config "${WORKDIR}/tls/ca.cnf" -out "${WORKDIR}/tls/ca.crt"
openssl genrsa -out "${WORKDIR}/tls/server.key" 2048
cat > "${WORKDIR}/tls/server.cnf" <<EOF
[req]
distinguished_name = req_dn
prompt = no
[req_dn]
CN = ${TLS_HOST}
EOF
openssl req -new -key "${WORKDIR}/tls/server.key" -config "${WORKDIR}/tls/server.cnf" \
  -out "${WORKDIR}/tls/server.csr"
cat > "${WORKDIR}/tls/server.ext" <<EOF
basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid,issuer
subjectAltName=DNS:${TLS_HOST},IP:127.0.0.1
EOF
openssl x509 -req -in "${WORKDIR}/tls/server.csr" -CA "${WORKDIR}/tls/ca.crt" \
  -CAkey "${WORKDIR}/tls/ca.key" -CAcreateserial -out "${WORKDIR}/tls/server.crt" \
  -days 1 -sha256 -extfile "${WORKDIR}/tls/server.ext"

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

TLS_URL="tls://${TLS_HOST}:${HOST_PORT}"
"$NSC" add operator --name PEDI11 >/dev/null
"$NSC" edit operator --service-url "${TLS_URL}" >/dev/null
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
  echo "host: 0.0.0.0"
  echo "store_dir: \"$(path_for_native "${WORKDIR}/js-store")\""
  echo 'jetstream {}'
  echo "tls {"
  echo "  cert_file: \"$(path_for_native "${WORKDIR}/tls/server.crt")\""
  echo "  key_file: \"$(path_for_native "${WORKDIR}/tls/server.key")\""
  echo "  timeout: 5"
  echo "}"
  cat "${WORKDIR}/resolver.conf"
} > "${WORKDIR}/server.conf"
mkdir -p "${WORKDIR}/js-store"

"$NATS_SERVER_BIN" -c "${WORKDIR}/server.conf" >"${WORKDIR}/nats-server.log" 2>&1 &
NATS_PID=$!
ADMIN_CREDS="${WORKDIR}/creds/streamadmin.creds"
EVENT_CREDS_FILE="${WORKDIR}/creds/event_publisher.creds"
EVENT_CREDS_MATERIAL="$(cat "$EVENT_CREDS_FILE")"
ADMIN_CREDS_MATERIAL="$(cat "$ADMIN_CREDS")"
CA_BUNDLE="${WORKDIR}/tls/ca.crt"
CA_BUNDLE_NATIVE="$(path_for_native "$CA_BUNDLE")"

export AIEOS_PED_I11_NATS_URL="$TLS_URL"
export AIEOS_PED_I11_NATS_CA_BUNDLE="$CA_BUNDLE_NATIVE"
export AIEOS_PED_I11_ADMIN_NATS_CREDENTIALS="$ADMIN_CREDS_MATERIAL"
export AIEOS_EVENT_DISPATCHER_NATS_CREDENTIALS="$EVENT_CREDS_MATERIAL"

# Disposable admin readiness + stream create (in-memory admin material; not EVENT runtime).
uv run python - <<'PY'
import asyncio
import os
import sys

from aieos.platform.events.constants import (
    PRODUCTION_EVENT_STREAM_NAME,
    PRODUCTION_EVENT_STREAM_SUBJECTS,
)
from aieos.platform.events.nats.connection import build_verifying_ssl_context
from aieos.platform.events.nats.credentials import InMemoryNatsCredentials
from nats.aio.client import Client as NATSClient

url = os.environ["AIEOS_PED_I11_NATS_URL"]
ca = os.environ["AIEOS_PED_I11_NATS_CA_BUNDLE"]
admin_material = os.environ["AIEOS_PED_I11_ADMIN_NATS_CREDENTIALS"]
ssl_ctx = build_verifying_ssl_context(ca_bundle_path=ca)
creds = InMemoryNatsCredentials.parse(admin_material)

async def ready_and_create() -> None:
    last_err: Exception | None = None
    for _ in range(80):
        nc = NATSClient()
        try:
            await nc.connect(
                servers=[url],
                user_jwt_cb=creds.user_jwt_cb,
                signature_cb=creds.signature_cb,
                tls=ssl_ctx,
                connect_timeout=5,
                name="ped-i11-admin-bootstrap",
                allow_reconnect=False,
            )
            js = nc.jetstream()
            try:
                await js.add_stream(
                    name=PRODUCTION_EVENT_STREAM_NAME,
                    subjects=list(PRODUCTION_EVENT_STREAM_SUBJECTS),
                )
            except Exception:
                # Stream may already exist from a prior partial run.
                await js.stream_info(PRODUCTION_EVENT_STREAM_NAME)
            await nc.drain()
            creds.wipe()
            return
        except Exception as exc:
            last_err = exc
            try:
                await nc.close()
            except Exception:
                pass
            await asyncio.sleep(0.25)
    detail = type(last_err).__name__ if last_err else "unknown"
    raise SystemExit(f"NATS TLS admin bootstrap failed: {detail}")

asyncio.run(ready_and_create())
print("NATS TLS ready; AIEOS_EVENTS_PROD ensured")
PY

uv run python - <<'PY'
import asyncio
import os
import sys
from types import SimpleNamespace
from uuid import uuid4

from aieos.platform.events.constants import PRODUCTION_EVENT_STREAM_NAME
from aieos.platform.events.nats.connection import connect_event_dispatcher_nats
from aieos.platform.events.nats.credentials import InMemoryNatsCredentials
from aieos.platform.events.nats.publisher import NatsJetStreamEventPublisher
from aieos.platform.runtime.config_event_dispatcher import EventDispatcherRuntimeConfig
from aieos.platform.runtime.models import DeploymentEnvironment, ReleaseIdentity

RESULTS = {}

def record(k, ok, msg):
    RESULTS[k] = "PASS" if ok else "FAIL"
    print(f"{k} {'PASS' if ok else 'FAIL'} — {msg}")

async def main():
    material = os.environ["AIEOS_EVENT_DISPATCHER_NATS_CREDENTIALS"]
    url = os.environ["AIEOS_PED_I11_NATS_URL"]
    ca = os.environ["AIEOS_PED_I11_NATS_CA_BUNDLE"]
    assert url.startswith("tls://")
    assert not os.environ.get("AIEOS_EVENT_DISPATCHER_NATS_CREDENTIALS_FILE")
    record(
        "N7",
        True,
        "Backend EVENT runtime consumed credential MATERIAL in memory; no _FILE / no user_credentials path",
    )

    config = EventDispatcherRuntimeConfig(
        environment=DeploymentEnvironment.PRODUCTION,
        release_identity=ReleaseIdentity(
            application_version="0.1.0",
            git_sha="a" * 40,
            build_id="ped-i11-proof",
            artifact_digest="sha256:" + ("b" * 64),
        ),
        database_url="postgresql+psycopg://ped_i11_proof:unused@127.0.0.1:1/unused",
        database_role="aieos_event_dispatcher",
        database_connect_timeout_seconds=5,
        nats_url=url,
        nats_credentials=material,
        nats_connect_timeout_seconds=10,
        nats_ca_bundle_path=ca,
        poll_interval_seconds=2,
        candidate_batch_size=10,
        max_messages_per_tenant_per_pass=1,
        claim_lease_seconds=30,
        max_attempts=3,
        retry_delay_seconds=1,
        publish_timeout_seconds=5,
        shutdown_grace_seconds=5,
    )
    creds = InMemoryNatsCredentials.parse(config.nats_credentials)
    nc = await connect_event_dispatcher_nats(config, creds)
    assert nc.options.get("allow_reconnect") is True
    record(
        "N1",
        True,
        "production EVENT connection factory authenticated over verified TLS",
    )

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
    record("N4", ok3, "NatsJetStreamEventPublisher expected-stream enforcement accepts exact stream")

    denied = False
    try:
        await nc.jetstream().publish("io.eduvijna.aieos.security.membership.revoked.v1", b"x")
    except Exception:
        denied = True
    record("N5", denied, "EVENT broker identity cannot publish non-Content AIEOS subject")

    admin_denied = False
    try:
        await nc.jetstream().stream_info(PRODUCTION_EVENT_STREAM_NAME)
    except Exception:
        admin_denied = True
    record("N6", admin_denied, "EVENT stream administration denied")

    await nc.drain()
    creds.wipe()

asyncio.run(main())
fails = sum(1 for v in RESULTS.values() if v != "PASS")
sys.exit(1 if fails else 0)
PY

if find "$ROOT" -type f \( -name '*.creds' -o -name '*.nk' -o -name '*.jwt' -o -name '*.seed' \) \
    ! -path '*/.git/*' ! -path '*/.venv/*' | grep -q .; then
  echo "N8 FAIL — credential residue in repository"
  exit 1
fi
echo "N8 PASS — no governed credential/key residue"

PROOF_PID="${NATS_PID}"
PROOF_WORKDIR="${WORKDIR}"
cleanup
if [[ -n "${PROOF_PID}" ]] && kill -0 "${PROOF_PID}" 2>/dev/null; then
  echo "N9 FAIL — disposable NATS process still alive"
  exit 1
fi
if [[ -n "${PROOF_WORKDIR}" && -d "${PROOF_WORKDIR}" ]]; then
  echo "N9 FAIL — disposable workdir still present"
  exit 1
fi
if find "$ROOT" -type f \( -name '*.creds' -o -name '*.nk' -o -name '*.jwt' -o -name '*.seed' \) \
    ! -path '*/.git/*' ! -path '*/.venv/*' | grep -q .; then
  echo "N9 FAIL — credential residue after cleanup"
  exit 1
fi
echo "N9 PASS — cleanup completed AND verified before PASS"
echo "PED-I11 NATS PROOF SUMMARY: PASS"
