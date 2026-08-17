# PED-I10B4 Asset Current-Use Authority

**Status:** IMPLEMENTED (source) — production migration / mutation / deployment remain
**NOT AUTHORIZED**.

**Classification:** NON_PRODUCTION

**Governing ADRs:**

- ADR-AIEOS-032 — Production Governance Adapter Foundation
- ADR-AIEOS-033 — Asset/File Architecture
- ADR-AIEOS-034 — AIEOS Asset Current-Use Authority Decision Semantics
  (**FROZEN / APPROVED**)

PED-I10B4 implements the concrete Asset-domain provider behind the existing
`AssetUseAuthority.assess_use(...)` contract. ADR-AIEOS-034 supersedes the
former eight-value AssetUseRejectionReason V1 vocabulary where necessary so
definitive physical-byte governance failures are not overloaded onto unrelated
meanings.

No production BlobStore / cloud storage provider is selected.

## Integration direction

```text
Content
  -> AssetUseAuthority protocol
  -> Asset concrete current-use authority
  -> Asset-owned persistence / BlobStore abstractions
```

Forbidden: Content → Asset SQL / SQLAlchemy models / BlobStore / persistence
repository internals.

## Eleven-value current-use rejection vocabulary

Exact frozen `AssetUseRejectionReason` values:

- `NOT_FOUND`
- `TENANT_INACCESSIBLE`
- `REVISION_NOT_FOUND`
- `WITHDRAWN`
- `DELETED`
- `QUARANTINED`
- `SAFETY_PENDING`
- `SAFETY_FAILED`
- `BYTES_PURGED`
- `BYTES_MISSING`
- `INTEGRITY_MISMATCH`

Not introduced: `BLOB_MISSING`, `STORAGE_UNAVAILABLE`, `UNKNOWN`, `ERROR`,
`CORRUPT`, `UNAVAILABLE`, `BLOB_ERROR`.

Forbidden overloads:

- missing blob → `NOT_FOUND` / `DELETED` / `SAFETY_FAILED`
- bytes purged → `DELETED` / `SAFETY_FAILED`
- integrity mismatch → `SAFETY_FAILED`
- `BlobStoreUnavailableError` → deterministic `AssetUseAssessment(NO)`

## Physical-byte mappings

| Evidence | Outcome |
| --- | --- |
| `asset_revision_states.bytes_purged == true` | unusable / `BYTES_PURGED` (do not call `BlobStore.inspect`) |
| `BlobStore.inspect(storage_key)` returns `None` | unusable / `BYTES_MISSING` |
| inspect succeeds but `byte_size` or `sha256` disagrees with immutable `AssetRevision` facts | unusable / `INTEGRITY_MISMATCH` |
| `BlobStoreUnavailableError` (and equivalent unsafe DB/infrastructure evaluation failure) | `GovernanceUnavailableError("governance unavailable")` |

`bytes_purged` is authoritative Asset governance evidence. Physical existence
must not override a purge fact. Do not repair metadata, overwrite revision
facts, delete the object, auto-quarantine, or trigger reconciliation mutation.

Do not catch all `RuntimeError` / `Exception` and relabel programming defects as
governance unavailable.

## RLS-safe NOT_FOUND semantics

This PostgreSQL provider preserves B2 FORCE-RLS and transaction-local
`aieos.tenant_id`. It does not use `BYPASSRLS`, disable RLS, query all tenants
then filter in Python, or probe another tenant to determine whether an Asset
exists there.

Asset not visible through the active tenant RLS context → `NOT_FOUND`.

Genuinely nonexistent Assets and Assets belonging to another tenant are
externally indistinguishable. `TENANT_INACCESSIBLE` remains on the
provider-neutral platform contract; this PostgreSQL provider does not emit it.

Missing tenant execution context follows the B2 fail-closed infrastructure
path (`GovernanceUnavailableError`), not `NOT_FOUND`.

Exact `resource_type` participates in identity. UUID visible only as a
different type → `NOT_FOUND`. No wildcard resource types. `storage_key` remains
opaque and is never parsed.

## Deterministic precedence

1. Exact typed Asset identity under current tenant RLS — not visible → `NOT_FOUND`
2. `lifecycle == deleted` → `DELETED`
3. `lifecycle == withdrawn` → `WITHDRAWN`
4. `quarantine_state == quarantined` → `QUARANTINED`
5. Pinned `ResourceRef.resource_revision` if present, else `Asset.current_revision`.
   No current/latest/nearest fallback. No effective revision → `REVISION_NOT_FOUND`
   (`ACTIVE` + clear + `current_revision` NULL is not usable.)
6. Exact `AssetRevision` missing → `REVISION_NOT_FOUND`
7. Missing / uninterpretable authoritative revision-state → `GovernanceUnavailableError`
   (do not manufacture `SAFETY_PENDING`)
8. `safety_state == failed` → `SAFETY_FAILED`
9. `safety_state == pending` → `SAFETY_PENDING`
10. `bytes_purged == true` → `BYTES_PURGED` (no physical inspect)
11. `BlobStore.inspect`: unavailable → `GovernanceUnavailableError`; `None` →
    `BYTES_MISSING`; size or sha256 mismatch → `INTEGRITY_MISMATCH`
12. Only then `usable=True` / `reason_code=None`

`ACTIVE` alone never implies usable.

A pinned historical revision preserves revision identity, not governance
freshness. Current aggregate deleted / withdrawn / quarantined still governs.

## `current_revision` NULL semantics

Unpinned evaluation uses `Asset.current_revision`. If that pointer is NULL,
the outcome is `REVISION_NOT_FOUND` with the observed Asset
`aggregate_revision`. There is no fallback to any historical revision.

## Missing revision-state fail-closed semantics

If the required authoritative `asset_revision_states` row cannot safely be
obtained or interpreted, evaluation fails closed as
`GovernanceUnavailableError`. A missing state row is not `SAFETY_PENDING`.

## `authority_revision` / `observed_at`

- `authority_revision` is `asset.assets.aggregate_revision` for any assessment
  where the Asset aggregate was authoritatively resolved (usable, lifecycle /
  quarantine / safety / revision / physical-byte outcomes including
  `REVISION_NOT_FOUND`).
- `NOT_FOUND` → `authority_revision = None`
- This PostgreSQL provider does not emit `TENANT_INACCESSIBLE`
- `observed_at` is timezone-aware authority observation time (`datetime.now(UTC)`
  or an injected clock). It is not a storage ETag/generation, not
  `AssetRevisionNumber`, and not a Content revision.

## Cross-store positive-result stability

PostgreSQL Asset governance state and BlobStore are not one atomic transaction.
A positive usable result is never returned from `DB read → blob inspect → true`
without confirming that governing Asset state remained stable.

For a candidate positive result:

1. read the authoritative Asset / revision / revision-state governing tuple
2. inspect physical bytes
3. re-read the authoritative governing tuple
4. verify relevant facts are unchanged
5. only then return `usable=True`

Relevant facts include lifecycle, quarantine_state, unpinned `current_revision`,
`aggregate_revision`, selected revision identity/number, immutable
`storage_key` / `byte_size` / `sha256`, `safety_state`, and `bytes_purged`.

If an unpinned Asset changes `current_revision` during physical evaluation, do
not return success for the old revision; re-evaluate using the newly
authoritative revision. Optimistic retry is bounded (three attempts). Persistent
governing-state churn fails closed as `GovernanceUnavailableError`.

No cross-request positive cache. Per-operation memoization remains a Content
adapter concern (PED-I10A) and must not create stale Asset governance.

## Governance vs authorization

`principal_id` is passed through the existing authority contract. PED-I10B4 does
not invent Asset ACL, share grants, ownership authorization, role policy, JWT
scope/role mapping, capability delegation, break-glass, teacher/admin bypass, or
organization membership policy. JWT claims are not used inside this provider.
Authorization remains PED-I09.

## Persistence / BlobStore / runtime

- Reuses PED-I10B2 tables exactly. No new table, column, FK, or migration.
  Alembic head remains `pedi10b2001`.
- SQLAlchemy stays under Asset infrastructure. Asset domain does not import
  SQLAlchemy. Content does not import Asset persistence.
- Uses the provider-neutral BlobStore contract from PED-I10B3. No AWS S3,
  Azure Blob, GCS, MinIO, filesystem production storage, cloud SDK, provider
  credentials, or production storage runtime configuration.
- Test-only fakes remain under `tests/`. No fake imported by production source.
- Canonical OpenAPI is unchanged
  (`D847C7BC21227072DC2627426A1B61774F33DEB78F65397C7C584BCC38C0BCAF`).
- `pyproject.toml` runtime dependencies and `uv.lock` are unchanged.
- Production `composition.py` is not wired to this provider.
- Content adapters continue to consume usable / unusable and surface existing
  generic business errors. `GovernanceUnavailableError` still propagates.

## Open production-readiness guard (unchanged)

Asset schema-owner role must be included in future runtime readiness / role
separation / ownership validation before production activation. PED-I10B4 does
not close that guard.

PED-I10B5 is not authorized by this slice.
