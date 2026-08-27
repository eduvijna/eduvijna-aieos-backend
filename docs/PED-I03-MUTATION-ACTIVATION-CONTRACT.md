---
id: PED-I03-MUTATION-ACTIVATION-CONTRACT
title: Fail-closed API mutation activation safety interlock
status: draft
version: 1.0.0
---

# PED-I03 mutation activation contract

ADR-AIEOS-029 is frozen. PED-I01/R1 and PED-I02 remain in force.

PED-I03 implements the **application-layer fail-closed API mutation activation
safety interlock** only.

## Primary safety invariant

```
ACTIVATION FAILURE = READ-ONLY / NO MUTATION
```

Activation missing, invalid, release-mismatched, or gate failure means:

- mutation denied (HTTP 503 `mutations_not_activated`)
- no UoW / business transaction
- no Content / ContentVersion / ReviewDecision / Publication change
- no idempotency write, workflow intent, outbox intent, or security audit row

It does **not** mean:

- API process failure
- `/livez` or `/readyz` failure
- a data rollback requirement
- production authorization to mutate

## Deployment ≠ mutation activation

```
DEPLOYED + LIVE + READY ≠ MUTATION ENABLED
```

Mutation activation is a distinct explicit control.

## Environment contract

| Variable | Role |
|----------|------|
| `AIEOS_API_MUTATION_ACTIVATION` | Exact `ENABLED` or `DISABLED` only |
| `AIEOS_API_MUTATION_AUTHORIZED_GIT_SHA` | 40 lowercase hex; must equal release Git SHA |
| `AIEOS_API_MUTATION_AUTHORIZED_ARTIFACT_DIGEST` | `sha256:<64 lowercase hex>`; must equal release digest |

Only exact `ENABLED` plus exact release binding may permit mutations.
Missing/empty/`enabled`/`true`/`1`/`yes`/unknown → fail closed (disabled).
Malformed SHA/digest → disabled (no uppercase normalization).

Ordinary activation configuration problems **must not** prevent application
composition, `/livez`, `/readyz`, or read-only API operation.

## Release binding

A stale activation for release A must not activate release B.

## Gate

- Protocol: `ApiMutationActivationGate.check() -> MutationActivationDecision`
- Production loader: `load_api_mutation_activation_gate(environ, release_identity)`
- Local, deterministic, non-networked, non-persistent
- No DB / NATS / Temporal / remote feature-flag provider
- Unexpected `check()` exceptions → treat as mutation disabled (no `/readyz` impact)

## Frozen mutation inventory

`content_create`, `content_version_append`, `content_review_submit`,
`content_review_approve`, `content_review_request_changes`,
`content_review_reject`, `content_publish`, `teaching_work_create`,
`teaching_work_refine`

GET/HEAD/OPTIONS product routes are not gated. `/livez` and `/readyz` are not
gated. Future write-capable `/api/v1` routes without explicit classification
fail closed at composition.

## Composition boundary

Installed by `compose_api_application(...)` only.
Raw `create_app(...)` remains a lower-level factory without this production
interlock. PED-I03 does not add an ASGI entrypoint.

Activation does **not** replace SecurityContext, review/publication
authorization, or governance.

## Boundaries

- AI materialization / controlled migration services are **not** gated here
- No workflow-origin Content mutation added
- No activation HTTP API, client override, or tenant-specific activation
- No DB activation table/migration
- No gateway implementation (application gate only)
- No feature-flag SaaS

## Authorization status

- production deployment remains **NOT AUTHORIZED**
- production mutation remains **NOT AUTHORIZED**
- production migration remains **NOT AUTHORIZED**

PED-I04+ remains **NOT AUTHORIZED** until separately gated.
