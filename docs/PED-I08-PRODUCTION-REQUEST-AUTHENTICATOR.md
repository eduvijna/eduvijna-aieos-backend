---
id: PED-I08-PRODUCTION-REQUEST-AUTHENTICATOR
title: Concrete production JWT Bearer RequestIdentityAuthenticator
status: draft
version: 1.0.0
---

# PED-I08 production request authenticator

ADR-AIEOS-029 and ADR-AIEOS-030 are frozen. PED-I01–PED-I07 remain in force.

PED-I08 implements the **concrete production-facing**
`RequestIdentityAuthenticator` adapter authorized by ADR-AIEOS-030 behind the
PED-I07 trust ports.

This slice is a production-readiness **foundation** only.

## Approved authentication mechanism

| Concern | Contract |
|---------|----------|
| Credential | `Authorization: Bearer <JWT access token>` only |
| Profile | Compact JWS access-token JWT; header `typ` **mandatory** and exactly `at+jwt` |
| Algorithm | **RS256 only** (reject `none` and all other algs) |
| Keys | Configured HTTPS JWKS URI only (token cannot choose JWKS/issuer/alg) |
| Issuer | Exact `AIEOS_AUTH_ISSUER` |
| Audience | Exact `AIEOS_AUTH_AUDIENCE` |
| Time claims | `exp` required; `iat` required; `nbf` enforced when present |
| Identity claims | non-empty `sub`, `client_id`, `jti` |
| Canonical principal | `https://eduvijna.com/claims/aieos/principal_id` = UUID |
| Library family | **PyJWT 2.x** + **cryptography** only |

Trusted result remains exactly:

```text
TrustedRequestIdentity(principal_id=<validated AIEOS principal UUID>)
```

## Credential trust boundary

```text
Bearer JWT (verified)
        ↓
TrustedRequestIdentity(principal_id ONLY)
        ↓
X-AIEOS-Tenant-ID (requested tenant ONLY)
        ↓
CurrentTenantAccessAuthority (current)
        ↓
TrustedSecurityContext
        ↓
later Authorization Kernel (not PED-I08)
```

## Principal derivation / mapping

ADR-AIEOS-030 requires the trusted issuer to emit the canonical AIEOS
`principal_id` claim. Runtime issuer+`sub` mapping, principal provisioning, and
identity SoR tables are **out of scope** for PED-I08.

## Intentionally NOT trusted

- Token `roles` / `groups` / `scope` / `permissions` / admin flags
- Tenant IDs or memberships asserted in the token
- Client headers: `X-AIEOS-Principal-ID`, `X-User-ID`, `X-Admin`, `X-Role(s)`,
  `X-Permissions`, `X-Capabilities`, etc.
- `X-AIEOS-Tenant-ID` as authentication or authorization proof
- Cookies, browser sessions, refresh tokens, opaque IdP SDK sessions

## Fail-closed HTTP mapping

| Condition | HTTP | Problem code |
|-----------|------|--------------|
| Missing/invalid/malformed/untrusted Bearer JWT | 401 | `unauthenticated` |
| JWKS/verifier dependency unavailable | 503 | `authentication_unavailable` |
| Authenticated but current tenant access denied | 403 | `forbidden` |
| Tenant-access authority unavailable | 503 | `authorization_unavailable` |

Provider/JWT/parser exception text, key material, tokens, and stack traces must
not appear in RFC 9457 Problem Details.

## Zero-UoW guarantee

Authentication completes **before** any business Unit of Work. Authentication
failure or authentication-unavailable ⇒ zero-UoW, 0 persistence, 0 outbox,
0 workflow, 0 domain mutation.

## Health independence

`/livez` and `/readyz` remain operationally independent of request
authentication and JWKS availability.

## Configuration (no defaults)

| Variable | Purpose |
|----------|---------|
| `AIEOS_AUTH_ISSUER` | Exact JWT issuer |
| `AIEOS_AUTH_AUDIENCE` | Exact JWT audience |
| `AIEOS_AUTH_JWKS_URI` | Absolute `https` JWKS URI (no embedded credentials) |

No allow-all mode. No anonymous production authenticator. No header-principal
fallback. No legacy JWT fallback.

## OpenAPI (ADR-AIEOS-030 authorized additive change)

Product OpenAPI gains security scheme `AIEOSBearerAuth`:

- `type: http`
- `scheme: bearer`
- `bearerFormat: JWT`

This is an **authorized additive security-contract change**, not uncontrolled
API drift. No OAuth authorization-code, PKCE, or login UI is introduced.
Health endpoints remain excluded from product OpenAPI.

## Remaining production-enablement gaps (explicit)

PED-I08 does **not** provide:

- Full Authorization Kernel / policy engine
- Review or publication authorization adapters
- Final production API composition / deployment
- Secret-manager integration / target environment / TLS topology
- User provisioning / MFA / social login / browser sessions
- Principal mapping tables or migrations
- Production mutation enablement

## Authorization status

- production deployment remains **NOT AUTHORIZED**
- production mutation remains **NOT AUTHORIZED**
- production migration remains **NOT AUTHORIZED**

PED-I09+ remains **NOT AUTHORIZED** until separately gated.
