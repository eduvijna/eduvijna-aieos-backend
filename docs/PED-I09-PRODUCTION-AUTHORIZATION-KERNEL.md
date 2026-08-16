# PED-I09 Production Authorization Kernel

**Status:** IMPLEMENTED (source) — production migration / mutation / deployment remain
**NOT AUTHORIZED**.

**Governing ADR:** ADR-AIEOS-031 — Production Authorization Kernel (FROZEN / APPROVED).

**Authorized base (implementation):** `d8cc8ae9b394bb58fd1b74b41bb51f27e13b573e`

## Decision model

- Vocabulary: **ALLOW** / **DENY**
- Default: **DENY**
- Exact capability strings only (no roles, no wildcards, no hierarchical inference)
- Current authority is revalidated on every evaluation (no authorization decision cache)

## Authority mechanism

Embedded AIEOS Authorization Kernel + Python + SQLAlchemy + PostgreSQL security
authority System of Record.

Explicitly **not** used:

- OPA / Rego / Cerbos / Cedar / Casbin / OpenFGA
- Amazon Verified Permissions / Auth0 / Entra / Keycloak authorization services
- external policy SaaS
- JWT business-authorization mapping (`roles` / `groups` / `permissions` / `scope`)

No new authorization dependency/library is introduced.

## Trust boundary (unchanged from PED-I07 / PED-I08)

```text
Bearer JWT
  → TrustedRequestIdentity(principal_id only)
  → requested X-AIEOS-Tenant-ID
  → CurrentTenantAccessAuthority
  → TrustedSecurityContext(tenant_id, principal_id)
  → current capability authorization
  → application/domain command
```

JWT authentication claims are never AIEOS current business authority.

## Security authority SoR

Schema: `security` (existing schema from SAI-I02; not recreated).

Authoritative **current-state** tables (PED-I09):

| Table | Purpose |
|-------|---------|
| `security.principals` | Global principal status |
| `security.tenants` | Tenant status |
| `security.tenant_memberships` | Current tenant membership |
| `security.capability_grants` | Exact capability grants |

Status vocabularies:

- principals / tenants: `ACTIVE` | `SUSPENDED` | `DISABLED`
- memberships: `ACTIVE` | `SUSPENDED` | `REVOKED`
- grants: `ACTIVE` | `REVOKED`

No roles / role_capabilities / permissions / policy documents / delegations /
break-glass tables.

## Migration

- Revision: `pedi090001`
- `down_revision = "saii020001"`
- Expected Alembic head after PED-I09: `pedi090001`

Production / staging / shared EduVijna migration execution remains **NOT AUTHORIZED**.
Disposable local test PostgreSQL execution is allowed for verification.

## Exact capability model

Code-governed Content capability catalog (injected into the kernel; not a DB
catalog). Canonical string constants are owned by
`aieos.domains.content.application.ports`. The Content adapter layer composes
`AIEOS_CONTENT_CAPABILITIES` from those constants. Generic `decisions.py` /
`kernel.py` do **not** redefine Content capability strings.

- `content.review.submit`
- `content.review.decide`
- `content.publish`
- `content.version.create`
- `content.migrate.import`

Unknown capability → **DENY**.

### Wildcard capability identifiers are invalid

Any capability containing `*` is **never** valid authority — including `*`,
`content.*`, `*.publish`, and `content.review.*`.

Hardening:

- DB CHECK on `security.capability_grants` rejects capability values containing `*`
- `AuthorizationKernel` rejects known-capability catalogs that include `*`
- `decide_capability` returns **DENY** before authority lookup when the
  requested capability contains `*`

No prefix/glob/wildcard semantics are implemented.

## Embedded kernel modules

Under `src/aieos/platform/security/authorization/`:

- `AuthorizationKernel` — ALLOW/DENY evaluator
- `SqlAlchemySecurityAuthorityRepository` — current-authority reads
- `security_authority_read` — short-lived authority read transaction
- `KernelCurrentTenantAccessAuthority` — production `CurrentTenantAccessAuthority`
- Content adapters: Review / Publication / AI generation / Migration import

SQLAlchemy stays out of Content domain code. Composition injects the known
capability set and wires adapters explicitly. PED-I09 does **not** finalize
product deployment composition.

## Authority read transaction + RLS query scope

Before `TrustedSecurityContext` exists, the requested tenant may be installed
only as an RLS **query scope** inside a short security DB transaction:

```text
requested tenant
  → short security DB transaction
  → set_config('aieos.tenant_id', requested tenant, true)
  → read current authority rows
  → ALLOW / DENY
  → transaction ends (rollback)
  → only on ALLOW create TrustedSecurityContext
```

Key: transaction-local `aieos.tenant_id` (same GUC as Content RLS).
Helper: `security.current_tenant_id()` (owned by SAI-I02).

FORCE RLS applies to `security.tenants`, `security.tenant_memberships`, and
`security.capability_grants`. `security.principals` is global (no tenant RLS).

RLS is defense-in-depth, not the business capability engine.
Connection-pool reuse must not leak tenant scope across transactions.

## Failure semantics

| Condition | Outcome |
|-----------|---------|
| Missing/invalid authentication | 401 `unauthenticated` (PED-I08) |
| Authenticated but tenant/member/capability denied | 403 `forbidden` |
| Authority cannot be evaluated safely | 503 `authorization_unavailable` |

Infrastructure failure is **not** DENY. Never turn an exception into ALLOW.
Driver/SQL details must not leak.

## Zero-business-UoW boundary

Tenant denial, capability denial, and authorization-unavailable paths must not
open a Content business Unit of Work, mutate Content/outbox/workflow state, or
create ReviewDecision / Publication / ContentVersion rows.

The dedicated security authority read transaction is permitted and is **not** a
Content UoW.

## Delegation / break-glass / control-plane

- Live delegation is **not** part of PED-I09 (`effective_actor_id = principal_id`,
  `delegation_id = None` where provenance requires those fields) — **no delegation**
  control-plane or live delegated authority
- Break-glass / admin bypass / `*` universal permission are **not** part of PED-I09 —
  **no break-glass**
- **No roles**, **no wildcard** authorization, no external policy engine
- No security control-plane APIs to mutate principals/tenants/memberships/grants
- Tests may seed authority rows in disposable databases only

## API contract

Product OpenAPI surface remains **UNCHANGED**.
`AIEOSBearerAuth` remains the security scheme.
Expected OpenAPI SHA256:

`D847C7BC21227072DC2627426A1B61774F33DEB78F65397C7C584BCC38C0BCAF`

No roles / permissions / membership / authorization-decision endpoints.

## Health independence

`/livez` and `/readyz` remain unauthenticated operational endpoints and must not
require Bearer token, tenant header, membership, or capability grant.

## Explicit non-authorization

PED-I09 does **not** authorize:

- production migration execution
- production mutation enablement
- production deployment / OCI promotion / cloud provisioning
- final production API composition
- PED-I10+
