#!/usr/bin/env bash
# WPI-OCI-I01R1 credential-free Backend production OCI validation.
# Builds a LOCAL image only. No registry authentication. No push. No DigitalOcean calls.
set -euo pipefail

# Prevent tools/release/__pycache__ from appearing as dirty source.
# (.gitignore un-ignores tools/release/**, so bytecode would fail porcelain.)
export PYTHONDONTWRITEBYTECODE=1

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT}"

BACKEND_SHA="${AIEOS_BACKEND_GIT_SHA:?AIEOS_BACKEND_GIT_SHA required}"
ARCHITECTURE_SHA="${AIEOS_ARCHITECTURE_GIT_SHA:?AIEOS_ARCHITECTURE_GIT_SHA required}"
INFRASTRUCTURE_SHA="${AIEOS_INFRASTRUCTURE_GIT_SHA:?AIEOS_INFRASTRUCTURE_GIT_SHA required}"
APP_VERSION="$(tr -d '[:space:]' < VERSION)"
SHORT_SHA="$(printf '%s' "${BACKEND_SHA}" | cut -c1-12)"
IMAGE_TAG="${AIEOS_BACKEND_OCI_IMAGE:-aieos-backend:wpi-oci-i01r1-${SHORT_SHA}}"
RECEIPT_DIR="$(mktemp -d "${TMPDIR:-/tmp}/aieos-wpi-oci-i01r1.XXXXXX")"
RECEIPT_PATH="${RECEIPT_DIR}/prepublication-receipt.json"

cleanup() {
  rm -rf "${RECEIPT_DIR}" >/dev/null 2>&1 || true
  docker image rm -f "${IMAGE_TAG}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "==> Source identity"
HEAD_SHA="$(git rev-parse HEAD)"
if [[ "${HEAD_SHA}" != "${BACKEND_SHA}" ]]; then
  echo "HEAD (${HEAD_SHA}) != backend_git_sha (${BACKEND_SHA})" >&2
  exit 1
fi
PORCELAIN="$(git status --porcelain)"
if [[ -n "${PORCELAIN}" ]]; then
  echo "dirty source rejected in authoritative mode:" >&2
  echo "${PORCELAIN}" >&2
  exit 1
fi
echo "backend_git_sha=${BACKEND_SHA}"
echo "checked_out_sha=${HEAD_SHA}"
echo "architecture_git_sha=${ARCHITECTURE_SHA}"
echo "infrastructure_git_sha=${INFRASTRUCTURE_SHA}"

echo "==> Building LOCAL production OCI candidate (${IMAGE_TAG}) for linux/amd64"
docker build \
  --platform=linux/amd64 \
  -f deploy/oci/Dockerfile.backend-runtime \
  --build-arg "AIEOS_GIT_REVISION=${BACKEND_SHA}" \
  --build-arg "AIEOS_ARCHITECTURE_REVISION=${ARCHITECTURE_SHA}" \
  --build-arg "AIEOS_INFRASTRUCTURE_REVISION=${INFRASTRUCTURE_SHA}" \
  --build-arg "AIEOS_APPLICATION_VERSION=${APP_VERSION}" \
  -t "${IMAGE_TAG}" \
  .

echo "==> Platform"
OS_NAME="$(docker image inspect "${IMAGE_TAG}" --format '{{.Os}}')"
ARCH_NAME="$(docker image inspect "${IMAGE_TAG}" --format '{{.Architecture}}')"
echo "Os=${OS_NAME} Architecture=${ARCH_NAME}"
test "${OS_NAME}" = "linux"
test "${ARCH_NAME}" = "amd64"

echo "==> Python version"
PY_OUT="$(docker run --rm --read-only --cap-drop=ALL --security-opt=no-new-privileges "${IMAGE_TAG}" python --version)"
echo "${PY_OUT}"
test "${PY_OUT}" = "Python 3.14.7"

echo "==> uv version"
UV_OUT="$(docker run --rm --read-only --cap-drop=ALL --security-opt=no-new-privileges "${IMAGE_TAG}" uv --version)"
echo "${UV_OUT}"
echo "${UV_OUT}" | grep -E '(^| )0\.12\.4( |$)' >/dev/null

echo "==> Effective UID/GID"
UID_OUT="$(docker run --rm --read-only --cap-drop=ALL --security-opt=no-new-privileges "${IMAGE_TAG}" id -u)"
GID_OUT="$(docker run --rm --read-only --cap-drop=ALL --security-opt=no-new-privileges "${IMAGE_TAG}" id -g)"
echo "uid=${UID_OUT} gid=${GID_OUT}"
test "${UID_OUT}" = "10001"
test "${GID_OUT}" = "10001"

USER_CFG="$(docker image inspect "${IMAGE_TAG}" --format '{{.Config.User}}')"
echo "Config.User=${USER_CFG}"
test "${USER_CFG}" = "10001:10001"

echo "==> Worker module imports"
docker run --rm --read-only --cap-drop=ALL --security-opt=no-new-privileges "${IMAGE_TAG}" \
  python -c "import aieos.platform.runtime.entrypoints.workflow_dispatcher_main as m; print('dispatcher-import-ok')"
docker run --rm --read-only --cap-drop=ALL --security-opt=no-new-privileges "${IMAGE_TAG}" \
  python -c "import aieos.platform.runtime.entrypoints.temporal_worker_main as m; print('worker-import-ok')"

echo "==> OCI labels"
LABELS_JSON="$(docker image inspect "${IMAGE_TAG}" --format '{{json .Config.Labels}}')"
python - "${BACKEND_SHA}" "${ARCHITECTURE_SHA}" "${INFRASTRUCTURE_SHA}" "${APP_VERSION}" "${LABELS_JSON}" <<'PY'
import json, sys
backend, arch, infra, app_ver, raw = sys.argv[1:6]
labels = json.loads(raw)
required = {
    "org.opencontainers.image.title": None,
    "org.opencontainers.image.description": None,
    "org.opencontainers.image.version": app_ver,
    "org.opencontainers.image.source": "https://github.com/eduvijna/eduvijna-aieos-backend",
    "org.opencontainers.image.revision": backend,
    "io.eduvijna.aieos.classification": "PRODUCTION_BACKEND_RUNTIME",
    "io.eduvijna.aieos.application_version": app_ver,
    "io.eduvijna.aieos.git_revision": backend,
    "io.eduvijna.aieos.architecture_revision": arch,
    "io.eduvijna.aieos.infrastructure_revision": infra,
}
for key, expected in required.items():
    assert key in labels and labels[key], key
    if expected is not None:
        assert labels[key] == expected, (key, labels[key], expected)
print("labels-ok")
PY

echo "==> Fail-closed default command"
set +e
DEFAULT_OUT="$(docker run --rm --read-only --cap-drop=ALL --security-opt=no-new-privileges "${IMAGE_TAG}" 2>&1)"
DEFAULT_RC=$?
set -e
echo "default_rc=${DEFAULT_RC}"
echo "${DEFAULT_OUT}"
test "${DEFAULT_RC}" = "64"
echo "${DEFAULT_OUT}" | grep -F "AIEOS_BACKEND_RUNTIME_COMMAND_REQUIRED" >/dev/null

echo "==> No exposed ports"
PORTS="$(docker image inspect "${IMAGE_TAG}" --format '{{json .Config.ExposedPorts}}')"
echo "ExposedPorts=${PORTS}"
if [[ "${PORTS}" != "null" && "${PORTS}" != "{}" && -n "${PORTS}" ]]; then
  echo "unexpected ExposedPorts" >&2
  exit 1
fi

echo "==> No secret-like image ENV / config (fail-closed)"
CFG_JSON="$(docker image inspect "${IMAGE_TAG}" --format '{{json .Config}}')"
python - "${CFG_JSON}" <<'PY'
import json, re, sys

cfg = json.loads(sys.argv[1])
if not isinstance(cfg, dict):
    raise SystemExit("config must be object")

# Never print raw config on failure — only marker names.
def fail(msg: str) -> None:
    raise SystemExit(msg)

env = cfg.get("Env") or []
if not isinstance(env, list):
    fail("Config.Env must be list")

forbidden_exact = {
    "AIEOS_WORKFLOW_DISPATCHER_TEMPORAL_API_KEY",
    "AIEOS_TEMPORAL_API_KEY",
}
key_pat = re.compile(r"(SECRET|TOKEN|PASSWORD|CREDENTIAL)", re.I)
value_needles = ("dop_v1_", "EV[", "Authorization:", "BEGIN PRIVATE KEY")

for item in env:
    if not isinstance(item, str) or "=" not in item:
        fail("malformed env entry")
    key, value = item.split("=", 1)
    if key in forbidden_exact:
        fail(f"forbidden env key: {key}")
    if key_pat.search(key):
        fail(f"secret-like env key: {key}")
    for needle in value_needles:
        if needle.lower() in value.lower() or needle in value:
            fail("forbidden credential-like env value")

# Fail-closed registry/auth material scan over serialized config text.
serialized = json.dumps(cfg, sort_keys=True)
lowered = serialized.lower()
for needle in ("dop_v1_", "auths", "dockercfg", "authorization", "x-registry"):
    if needle.lower() in lowered:
        fail(f"forbidden config material: {needle}")

print("env-config-ok")
PY

echo "==> Provenance receipt + verify"
export PYTHONPATH="${ROOT}/tools/release${PYTHONPATH:+:${PYTHONPATH}}"
python -B tools/release/build_backend_oci_provenance.py \
  --image "${IMAGE_TAG}" \
  --backend-git-sha "${BACKEND_SHA}" \
  --architecture-git-sha "${ARCHITECTURE_SHA}" \
  --infrastructure-git-sha "${INFRASTRUCTURE_SHA}" \
  --output "${RECEIPT_PATH}"
python -B tools/release/verify_backend_oci_provenance.py --receipt "${RECEIPT_PATH}"
RECEIPT_SHA="$(python -B - <<PY
import hashlib
from pathlib import Path
p = Path(r"${RECEIPT_PATH}")
print(hashlib.sha256(p.read_bytes()).hexdigest())
PY
)"

DOCKERFILE_SHA="$(python -B - <<PY
import hashlib
from pathlib import Path
p = Path("deploy/oci/Dockerfile.backend-runtime")
print(hashlib.sha256(p.read_bytes()).hexdigest())
PY
)"
UV_LOCK_SHA="$(python -B - <<PY
import hashlib
from pathlib import Path
p = Path("uv.lock")
print(hashlib.sha256(p.read_bytes()).hexdigest())
PY
)"

echo "RECEIPT_SHA256=${RECEIPT_SHA}"
echo "DOCKERFILE_SHA256=${DOCKERFILE_SHA}"
echo "UV_LOCK_SHA256=${UV_LOCK_SHA}"
echo "PR_OR_PUSH_SOURCE_SHA=${BACKEND_SHA}"
echo "CHECKED_OUT_SHA=${HEAD_SHA}"

if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  {
    echo "## WPI-OCI-I01R1 Backend production OCI"
    echo ""
    echo "- classification: PRODUCTION_BACKEND_RUNTIME (candidate; not published)"
    echo "- pr_or_push_source_sha: \`${BACKEND_SHA}\`"
    echo "- checked_out_sha: \`${HEAD_SHA}\`"
    echo "- source_sha_identity_match: true"
    echo "- backend_git_sha: \`${BACKEND_SHA}\`"
    echo "- architecture_git_sha: \`${ARCHITECTURE_SHA}\`"
    echo "- infrastructure_git_sha: \`${INFRASTRUCTURE_SHA}\`"
    echo "- application_version: ${APP_VERSION}"
    echo "- python_version_observed: 3.14.7"
    echo "- uv_version_observed: 0.12.4"
    echo "- build_platform: linux/amd64"
    echo "- dockerfile_sha256: \`${DOCKERFILE_SHA}\`"
    echo "- uv_lock_sha256: \`${UV_LOCK_SHA}\`"
    echo "- base_image_digest: \`sha256:8d033111899301598e33bd321b85f33f86e3ba2953ce00ff70a9cac020246a7c\`"
    echo "- receipt_sha256: \`${RECEIPT_SHA}\`"
    echo "- registry_publication: false"
    echo "- registry_credentials: false"
    echo "- digitalocean_calls: false"
    echo "- production_deployment: false"
    echo "- result: PASSED"
  } >> "${GITHUB_STEP_SUMMARY}"
fi

echo "==> WPI-OCI-I01R1 LOCAL/CI VALIDATION PASSED (no registry publication)"
