# PED-I10A Production Governance Adapter Foundation

**Status:** IMPLEMENTED (source) — production migration / mutation / deployment remain
**NOT AUTHORIZED**.

**Governing ADR:** ADR-AIEOS-032 — AIEOS Production Governance Adapters Baseline
(**FROZEN / APPROVED**).

**Authorized base (implementation):** `095e02d25617aae45f9cdc96cb5a67c8aaa9d6a1`

## Governance != Authorization

| Concern | Meaning | Baseline |
|---------|---------|----------|
| **Authorization** | May this principal perform this capability? | ADR-AIEOS-031 / PED-I09 |
| **Governance** | Is this proposed/current resource use compliant with governed rules for this resource and purpose? | ADR-AIEOS-032 / PED-I10A |

Approval != Governance. Schema validation != Governance. `ResourceRef` != proof of
current usability. Asset existence != permanent Asset governance approval.

## Scope of PED-I10A

Closes the **adapter / contracts** layer only:

- `GovernanceUnavailableError` + HTTP 503 `governance_unavailable`
- Deterministic Review Comment Policy V1 (`review_comment_policy.v1`)
- Baseline Publication Governance V1 (`publication_governance.v1`)
- Provider-neutral `AssetUseAuthority` + typed `AssetUseAssessment`
- Content production adapters that consume `AssetUseAuthority` for binding and
  current-use validation

**Explicitly deferred to PED-I10B:** concrete Asset/File production authority
provider, Asset/File SoR tables, Asset HTTP APIs, production composition of a
real Asset provider.

PED-I10B, PED-I11+, production migration, production mutation, and production
deployment remain **NOT AUTHORIZED**.

## Failure semantics

| Condition | Outcome |
|-----------|---------|
| Authoritative governance says NO | Existing business rejection (422/409 family) |
| Required governance cannot safely evaluate | `GovernanceUnavailableError` → 503 `governance_unavailable` |
| Unexpected programming defect | Existing sanitized 500 `internal_error` |

HTTP for unavailable governance:

- status: `503`
- code: `governance_unavailable`
- title: `Governance unavailable`
- detail: `Governance is temporarily unavailable`
- type: `urn:aieos:problem:governance_unavailable`

Malformed `AssetUseAuthority` results fail closed as `GovernanceUnavailableError`.
Unexpected adapter/provider `RuntimeError` is **not** translated into governance
unavailable.

## Review Comment Policy V1

Class: `DeterministicReviewCommentPolicyV1`

- Embedded, deterministic, synchronous, code-governed, versioned
- No network I/O, AI/LLM, external DLP, database, or environment bypass
- Input: `evaluate(comment: str | None) -> None`
- Rejects with generic `ReviewCommentRejected` only (no matched secret in
  exception/HTTP/logs; no automatic redaction/rewrite)

Detection floor covers: private-key PEM material; Bearer credential forms;
labeled secret assignments; credential-bearing URI userinfo; Luhn-valid payment
card candidates; label-anchored government identifiers; explicit email and
label-aware phone/contact numbers.

Ordinary prose mentioning “password”, “tokenization”, or “API key” without a
credential value remains allowed.

Idempotent review-decision replay of an already-established success is not
invalidated by later policy evaluation.

## Publication Governance V1

Class: `BaselinePublicationGovernanceV1` (`publication_governance.v1`)

V1 has **no additional stateful publication-specific restrictions** beyond the
already separate first-class gates (authorization, schema, Asset current-use,
approval). It is an explicit versioned production baseline — not an `Allow*` /
`Stub*` / `Fake*` test fake.

It must not grant `content.publish`, inspect roles/JWT claims, duplicate the
Authorization Kernel, manufacture approval, or become an RBAC dumping ground.
Future publication rules require a later reviewed policy/ADR version.

## AssetUseAuthority (contract only)

Location: `aieos.platform.resources.asset_use`

```text
assess_use(*, tenant_id, principal_id, resource_ref: ResourceRef) -> AssetUseAssessment
```

- No SQLAlchemy, HTTP client, or Content persistence in the contract
- Closed V1 rejection reasons: `NOT_FOUND`, `TENANT_INACCESSIBLE`,
  `REVISION_NOT_FOUND`, `WITHDRAWN`, `DELETED`, `QUARANTINED`, `SAFETY_PENDING`,
  `SAFETY_FAILED`
- Invariants: usable ⇒ `reason_code is None`; unusable ⇒ exact closed reason

PED-I10A does **not** implement the owning Asset/File production provider.

## Content Asset adapters

- `AssetAuthorityReferenceValidationAdapter` → `AssetReferenceValidationPort`
- `AssetAuthorityCurrentGovernanceAdapter` → `AssetCurrentGovernancePort`

Both require an **explicit immutable handled resource-type set** (no `*`,
`asset.*`, glob, or regex wildcards). Unknown types fail binding/publication
validation without guessing a provider.

`resource_revision` is passed unchanged. Pinned revision existence is owned by
the future Asset provider; revision pin ≠ governance freshness — current
quarantine/withdrawal can still invalidate use.

Optional (`required=False`) VersionAssetRefs are still evaluated for current use.
Per-operation memoization of identical `ResourceRef`s is allowed; no
cross-request positive cache. Governance status is not persisted into Content as
current Asset truth.

## Post-publication boundary

Publication history remains immutable. Later Asset quarantine/deletion/withdrawal
does not delete or rewrite the Publication. Current Asset use/delivery must
revalidate governance.

`ValidateVersionAssetGovernanceService` remains the reusable Content-side seam.
Future binary delivery, render-time Asset resolution, teacher preview,
student/parent delivery, export, and derived materialization that embeds an Asset
must invoke current Asset governance. Plain historical metadata reads that only
expose `ResourceRef` history do not need Asset authority merely to prove the
historical association existed.

Concrete delivery integration is **not** part of PED-I10A.

## What PED-I10A does not introduce

- Asset/File DB tables, migrations, HTTP APIs, or network clients
- Cross-domain SQL/FKs from Content into Asset
- Governance policy database / generic DSL
- OPA / Rego / Cerbos / Cedar / Casbin / OpenFGA / AVP / Auth0 / Entra policy
- External DLP vendor or AI comment classifier
- Roles, authorization capabilities, delegation, break-glass, wildcards
- Production credentials, deployment manifests, or environment bypasses
- New Alembic migration (head remains `pedi090001`)
- New runtime dependency family (`uv.lock` unchanged; pytest marker only)

## Production composition

`ApiRuntimeDependencies` continues to require explicit:

- `ReviewCommentPolicy`
- `PublicationGovernancePort`
- `AssetReferenceValidationPort`
- `AssetCurrentGovernancePort`

PED-I10A supplies concrete classes that **can** satisfy those ports when a real
`AssetUseAuthority` is later provided. It does **not** fabricate a production
Asset provider, auto-allow missing authority, default to test fakes, or wire
`tests.fakes` into production.

## OpenAPI / database

- Canonical OpenAPI SHA256 must remain
  `D847C7BC21227072DC2627426A1B61774F33DEB78F65397C7C584BCC38C0BCAF`
- Alembic head remains `pedi090001`
- No migration authorized in this slice
