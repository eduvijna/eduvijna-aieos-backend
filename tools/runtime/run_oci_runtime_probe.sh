#!/usr/bin/env bash
# PED-I06 OCI runtime probe CI helper.
# Builds the NON_PRODUCTION probe image and runs hardened smoke checks.
# Does not push to any registry. Does not deploy.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT}"

IMAGE_TAG="${AIEOS_OCI_PROBE_IMAGE:-aieos-api-runtime-probe:local}"
GIT_REVISION="$(git rev-parse HEAD)"
APP_VERSION="$(tr -d '[:space:]' < VERSION)"
PROBE_MOUNT="${ROOT}/tools/runtime/asgi_http_probe.py"
CONTAINER_NAME="aieos-oci-runtime-probe-$$"

cleanup() {
  docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "==> Building NON_PRODUCTION OCI runtime probe (${IMAGE_TAG})"
docker build \
  -f deploy/oci/Dockerfile.api-runtime-probe \
  --build-arg "AIEOS_GIT_REVISION=${GIT_REVISION}" \
  --build-arg "AIEOS_APPLICATION_VERSION=${APP_VERSION}" \
  -t "${IMAGE_TAG}" \
  .

echo "==> Inspect non-root Config.User"
USER_CFG="$(docker image inspect "${IMAGE_TAG}" --format '{{.Config.User}}')"
echo "Config.User=${USER_CFG}"
test -n "${USER_CFG}"
test "${USER_CFG}" != "root"
test "${USER_CFG}" != "0"
test "${USER_CFG}" != "0:0"

echo "==> Python version"
PY_OUT="$(docker run --rm --read-only --cap-drop=ALL --security-opt=no-new-privileges "${IMAGE_TAG}" python --version)"
echo "${PY_OUT}"
test "${PY_OUT}" = "Python 3.14.7"

echo "==> Uvicorn version"
UV_OUT="$(docker run --rm --read-only --cap-drop=ALL --security-opt=no-new-privileges "${IMAGE_TAG}" uvicorn --version)"
echo "${UV_OUT}"
echo "${UV_OUT}" | grep -F "Running uvicorn 0.51.0" >/dev/null
echo "${UV_OUT}" | grep -F "with CPython 3.14.7" >/dev/null

echo "==> Non-root id"
ID_OUT="$(docker run --rm --read-only --cap-drop=ALL --security-opt=no-new-privileges "${IMAGE_TAG}" id -u)"
echo "uid=${ID_OUT}"
test "${ID_OUT}" != "0"

echo "==> Runtime package imports"
docker run --rm --read-only --cap-drop=ALL --security-opt=no-new-privileges "${IMAGE_TAG}" \
  python -c "import aieos, fastapi, sqlalchemy, psycopg, uvicorn; print('imports-ok')"

echo "==> Hardened HTTP ASGI probe"
docker run -d --name "${CONTAINER_NAME}" \
  --read-only \
  --cap-drop=ALL \
  --security-opt=no-new-privileges \
  --tmpfs /tmp \
  -p 18080:8080 \
  -v "${PROBE_MOUNT}:/probe/asgi_http_probe.py:ro" \
  -w /probe \
  "${IMAGE_TAG}" \
  uvicorn asgi_http_probe:app \
    --host 0.0.0.0 \
    --port 8080 \
    --loop asyncio \
    --http h11 \
    --no-proxy-headers \
    --no-server-header \
    --no-access-log

READY=0
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:18080/livez" >/dev/null 2>&1; then
    READY=1
    break
  fi
  sleep 1
done
test "${READY}" = "1"

HTTP_CODE="$(curl -sS -o /tmp/aieos-oci-livez.out -w '%{http_code}' http://127.0.0.1:18080/livez)"
BODY="$(cat /tmp/aieos-oci-livez.out)"
echo "HTTP ${HTTP_CODE} body=${BODY}"
test "${HTTP_CODE}" = "200"
test "${BODY}" = "ok"

echo "==> Image identity (local only; not published)"
docker image inspect "${IMAGE_TAG}" --format 'Id={{.Id}} RepoDigests={{json .RepoDigests}}'

echo "==> OCI runtime probe PASSED (NON_PRODUCTION; not pushed)"
