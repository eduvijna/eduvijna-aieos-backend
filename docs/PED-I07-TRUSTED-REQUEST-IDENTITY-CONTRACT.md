---
id: PED-I07-TRUSTED-REQUEST-IDENTITY-CONTRACT
title: Trusted request identity and current-tenant SecurityContext foundation
status: draft
version: 1.0.0
---

# PED-I07 trusted request identity contract

ADR-AIEOS-029 is frozen. PED-I01–PED-I06 remain in force.

PED-I07 establishes the provider-neutral **trusted HTTP identity boundary** and
**current-tenant TrustedSecurityContext** foundation required before a real
production API composition can eventually exist.

This slice is a production-readiness **foundation** only.

## Prominent invariants

```
REQUESTED TENANT ≠ TENANT AUTHORITY

AUTHENTICATION ≠ AUTHORIZATION

TENANT MEMBERSHIP ≠ CONTENT CAPABILITY

TOKEN/IDENTITY ASSERTION ≠ CAPABILITY SNAPSHOT

AUTHENTICATION FAILURE → FAIL CLOSED

CURRENT AUTHORITY RECHECKED EACH REQUEST
```

## Required flow

```
HTTP request
        ↓
injected RequestIdentityAuthenticator
        ↓
TrustedRequestIdentity (principal_id only)
        ↓
requested X-AIEOS-Tenant-ID
        ↓
CurrentTenantAccessAuthority (current)
        ↓
TrustedSecurityContext (tenant_id, principal_id)
        ↓
existing application/domain services
```

Critical rule: client tenant, client principal header, client role, client
capability, event metadata, and workflow history are **not** authority.

## Contracts

| Contract | Role |
|----------|------|
| `TrustedRequestIdentity` | Immutable authenticated principal only |
| `RequestIdentityAuthenticator` | Explicit HTTP/runtime authentication port |
| `CurrentTenantAccessAuthority` | Current principal→tenant access check |
| `CurrentAuthoritySecurityContextResolver` | Builds TrustedSecurityContext after authority success |
| `TrustedSecurityContext` | Minimal execution context: tenant_id + principal_id |

`create_app(...)` and `ApiRuntimeDependencies` require an explicit
`request_identity_authenticator`. No default. No anonymous fallback.
No `AlwaysAuthenticated` in `src/aieos`.

## Fail-closed HTTP mapping

| Condition | HTTP | Problem code |
|-----------|------|--------------|
| Missing/invalid authentication | 401 | `unauthenticated` |
| Authenticated but no current tenant access | 403 | `forbidden` |
| Authentication authority unavailable / unexpected defect | 503 | `authentication_unavailable` |
| Tenant-access authority unavailable / unexpected defect | 503 | `authorization_unavailable` |
| Missing tenant on tenant-scoped Content APIs | 401 | `unauthenticated` |

Responses must not expose provider, token, credential, membership internals,
stack traces, or secret exception text. Problem Details retain `request_id`,
`correlation_id`, and `instance`.

## What PED-I07 does **not** do

- No IdP selected (no OIDC / OAuth / JWT issuer / Entra / Cognito / Auth0 /
  Keycloak / Firebase / Clerk / Supabase / SAML / API keys / sessions)
- No JWT/OIDC library or Authorization Bearer OpenAPI scheme frozen
- No policy engine selected (OPA / Cerbos / OpenFGA / Casbin / etc.)
- No production authenticator implementation in `src/aieos`
- No production product ASGI entrypoint / module-level app singleton
- No conversion of the PED-I06 OCI probe into a product image
- No SecurityContext persistence / identity tables / migrations
- No audit records for 401/403/auth failure/tenant denial
- No capability/role snapshot on identity or SecurityContext
- Does not replace `ReviewAuthorizationPort` or `PublicationAuthorizationPort`
- Does not authorize production deployment, mutation, or migration

PED-I08 implements the ADR-AIEOS-030 JWT Bearer production authenticator behind
these ports (see `docs/PED-I08-PRODUCTION-REQUEST-AUTHENTICATOR.md`).

## Health independence

`/livez` and `/readyz` remain PED-I02 governed and must not require request
authentication, tenant membership, or an authorization kernel.

## Authorization status

- production deployment remains **NOT AUTHORIZED**
- production mutation remains **NOT AUTHORIZED**
- production migration remains **NOT AUTHORIZED**

PED-I09+ remains **NOT AUTHORIZED** until separately gated.
