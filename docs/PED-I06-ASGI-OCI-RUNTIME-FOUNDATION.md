---
id: PED-I06-ASGI-OCI-RUNTIME-FOUNDATION
title: ASGI server and NON_PRODUCTION OCI runtime viability foundation
status: draft
version: 1.0.0
---

# PED-I06 ASGI / OCI runtime foundation

**Classification: NON_PRODUCTION**

ADR-AIEOS-029 is frozen. PED-I01–PED-I05 remain in force.

PED-I06 establishes a provider-neutral **NON_PRODUCTION** runtime viability
foundation for packaging and serving through the selected ASGI technology.

It does **not** create the final production AIEOS API executable, does **not**
compose product dependencies, and does **not** authorize production deployment,
mutation, or migration.

## What this slice proves

- Python **3.14.7** inside a Linux OCI image
- Uvicorn **0.51.0** (constraint `uvicorn>=0.51,<0.52`; minimal pure-Python set)
- Deterministic ASGI server configuration
- Non-root container execution (`10001:10001`)
- Locked dependency installation (`uv sync --locked --no-dev --no-editable`)
- Digest-pinned OCI build base
- Container runtime smoke validation + CI `oci-runtime-probe`

## ASGI baseline

Module: `src/aieos/platform/runtime/asgi.py`

| Setting | Value |
|---------|-------|
| host | `0.0.0.0` |
| port | `8080` |
| workers | `1` |
| loop | `asyncio` |
| http | `h11` |
| proxy_headers | `false` |
| server_header | `false` |
| reload | `false` |
| lifespan | on |

One container = one Uvicorn process. No gunicorn, supervisor, systemd, or
multi-worker process manager.

Forwarded headers (`X-Forwarded-For`, `X-Forwarded-Proto`, `Forwarded`) are
**not** trusted until ingress / trusted-proxy topology is frozen.
`forwarded_allow_ips="*"` is forbidden.

Importing `aieos.platform.runtime.asgi` must not start a server, open a DB
connection, load environment config, construct the API app, or perform network
I/O. No module-level FastAPI singleton.

Do **not** install `uvicorn[standard]`, gunicorn, uvicorn-worker, or hypercorn.

## Why this is not the product image

`compose_api_application(...)` still requires trusted production ports
(explicit request identity authenticator, SecurityContextResolver wired to a
current tenant-access authority, authorization/governance ports, readiness,
mutation gate, etc.). PED-I06 must **not** fill those gaps with test fakes,
allow-all adapters, or header-trusting identity.

PED-I07 establishes the request-identity / SecurityContext foundation ports and
resolver, but the PED-I06 OCI runtime probe remains **NON_PRODUCTION** and must
not become the product image.

Therefore:

- no production RequestIdentityAuthenticator implementation in the probe image
- no permissive bootstrap application
- no product API default CMD in the probe image
- default CMD may only perform a non-serving probe (e.g. `uvicorn --version`)

## OCI probe image

Path: `deploy/oci/Dockerfile.api-runtime-probe`

| Concern | Contract |
|---------|----------|
| Classification label | `io.eduvijna.aieos.classification=NON_PRODUCTION_RUNTIME_PROBE` |
| Base | `ghcr.io/astral-sh/uv:0.12.4-trixie-slim@sha256:…` (immutable digest) |
| Python | managed CPython **3.14.7** under `/opt/python` |
| Venv | `/opt/venv` via locked non-dev install |
| User | non-root `10001:10001` |
| Secrets | none baked |
| Migrations | never at startup |
| Mutation activation | never `ENABLED` |
| Registry | **no push** |
| Cloud target | **none** (provider-neutral) |

The probe image is a technical runtime compatibility artefact only. It must not
be treated as a production image, release image, or authorized deployment
artefact.

CI proves HTTP serving by mounting a **test-only** ASGI probe from
`tools/runtime/` (e.g. `GET /livez` → 200). That probe is not the product
application and does not reuse production `/livez` semantics.

Hardened smoke uses at least `--read-only`, `--cap-drop=ALL`, and
`--security-opt=no-new-privileges` (tmpfs `/tmp` only if required).

## CI

Job: `oci-runtime-probe`

- triggers: `pull_request`, `push` to `main`
- `needs: quality-gate`
- permissions remain `contents: read`
- no registry/deploy secrets or write permissions

`quality-gate` remains the current required branch check. Whether
`oci-runtime-probe` becomes an additional required check is a later governance
decision. PED-I06 does not mutate branch protection.

## Explicit non-goals

- final production API composition / trusted identity integration
- production Dockerfile at repository root
- OCI registry publication / promotion
- target cloud manifests (ECS/EKS/ACA/AKS/Cloud Run/GKE/Helm/Terraform)
- mapping local probe digest to `AIEOS_ARTIFACT_DIGEST`
- production mutation activation
- Alembic at image start
- product OpenAPI / route contract changes

## Authorization status

- production deployment remains **NOT AUTHORIZED**
- production mutation remains **NOT AUTHORIZED**
- production migration remains **NOT AUTHORIZED**

PED-I07 establishes the trusted request-identity / current-tenant
SecurityContext foundation, but this OCI probe remains NON_PRODUCTION.
PED-I08+ remains **NOT AUTHORIZED** until separately gated.
