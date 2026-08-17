# PED-I10B5 Asset Mutation & Revision Activation Foundation

**Status:** IMPLEMENTED (source) — production migration / mutation / deployment remain
**NOT AUTHORIZED**.

**Classification:** NON_PRODUCTION

**Governing ADR:**

- ADR-AIEOS-035 — AIEOS Asset Mutation & Revision Activation Semantics
  (**FROZEN / APPROVED**)

PED-I10B5 implements the NON_PRODUCTION Asset mutation foundation over the
existing PED-I10B2 tables. It does not select a production BlobStore provider,
does not add Asset HTTP/OpenAPI, does not compose into runtime, and does not
implement purge/retention/hold.

The Asset schema-owner readiness guard remains open:
`AIEOS_ASSET_SCHEMA_OWNER_ROLE` is still absent from API runtime config and
readiness (`asset` is not in `_ALL_APP_SCHEMAS`).

## Implementation boundary

Authorized application capabilities:

- create Asset aggregate
- register immutable AssetRevision and initialize AssetRevisionState
- activate an exact revision as `current_revision`
- lifecycle: ACTIVE ↔ WITHDRAWN; ACTIVE/WITHDRAWN → DELETED
- quarantine: clear ↔ quarantined
- safety: pending → passed; pending → failed; passed → failed

PostgreSQL write repositories and an Asset Unit of Work perform these operations
through `asset.assets`, `asset.asset_revisions`, and `asset.asset_revision_states`.
Existing domain objects and SQLAlchemy Core mappings are reused. The Asset domain
is not redesigned.

## Lifecycle transitions

| From | To | Allowed |
| --- | --- | --- |
| active | withdrawn | yes |
| withdrawn | active | yes |
| active | deleted | yes |
| withdrawn | deleted | yes |
| deleted | active | no |
| deleted | withdrawn | no |
| deleted | deleted | no (deletion is terminal) |

Withdrawal/deletion must not automatically clear `current_revision`.
Logical deletion must not call `BlobStore.delete`, mutate `bytes_purged`, or
write `deletion_evidence`.

## Safety transitions

| From | To | Allowed |
| --- | --- | --- |
| pending | passed | yes |
| pending | failed | yes |
| passed | failed | yes |
| passed | pending | no |
| failed | pending | no |
| failed | passed | no |

`failed` is terminal for that immutable revision. Safety mutations operate on an
exact AssetRevision; a historical revision safety change still advances the
parent Asset `aggregate_revision`. A pending revision may receive its terminal
safety result after the Asset is logically deleted. That does not reactivate the
Asset and does not make it usable. Immutable revision byte facts are never
rewritten.

## Aggregate revision rules

`AssetRevisionNumber` is the business ResourceRef revision.
`AssetAggregateRevision` is optimistic-concurrency authority only. They are
distinct types.

Successful commands that increment `Asset.aggregate_revision` exactly once:

- activate revision
- active → withdrawn / withdrawn → active
- active → deleted / withdrawn → deleted
- clear → quarantined / quarantined → clear
- pending → passed / pending → failed / passed → failed

These do **not** increment `aggregate_revision`:

- initial Asset creation
- revision registration
- BlobStore prepare
- reconciliation
- reads
- rejected or failed commands

All mutable governance/selection commands use `expected aggregate_revision` with
atomic compare-and-set / locked-authority protection. Stale expected revision is
a conflict with zero mutation. Last-write-wins and silent retry against a newer
database revision are forbidden.

## Revision registration is not activation

Registration accepts authoritative physical facts already produced by BlobStore /
`PreparedBlob`. Sequence:

1. BEGIN Asset write UoW
2. establish transaction-local `aieos.tenant_id`
3. lock the exact tenant-visible Asset aggregate
4. reject `lifecycle == deleted`
5. allocate `max(existing revision_number) + 1` under that lock
6. insert immutable AssetRevision
7. insert AssetRevisionState (`safety_state = pending`, `bytes_purged = false`)
8. COMMIT

Registration must not change `current_revision`, lifecycle, quarantine_state, or
`aggregate_revision`. A withdrawn Asset may receive a new revision. A deleted
Asset must not.

`AssetRevisionId` is the replay identity. Identical immutable facts recover the
existing row; conflicting facts fail closed with zero mutation. There is no
generic Asset idempotency table and no HTTP Idempotency-Key concept.

If physical bytes exist but revision registration does not commit, bytes are
left untouched. B3 reconciliation may identify an orphan candidate. Compensation
must **never** call `BlobStore.delete`.

## Cross-store activation algorithm

Activation is a separate explicit command. Required input includes tenant_id,
principal_id, asset_id, exact revision_number, and expected aggregate_revision.

The write lock is **not** held across Blob I/O:

```text
read authoritative candidate facts
    ↓
require candidate.aggregate_revision == expected_aggregate_revision
    (else AssetConflict; zero inspect; zero mutation)
    ↓
BlobStore.inspect(exact opaque storage_key)
    ↓
verify physical size/hash
    ↓
BEGIN write transaction
    establish tenant context
    lock exact Asset aggregate
    re-read authoritative Asset/revision/state
    verify expected aggregate_revision
    verify candidate governing facts did not change
    including locked.aggregate_revision == candidate.aggregate_revision
    update current_revision
    aggregate_revision = aggregate_revision + 1
COMMIT
```

pending and failed revisions cannot be newly activated. A withdrawn or
quarantined Asset may receive an activated revision; PED-I10B4 remains
authoritative for current-use evaluation and will still report WITHDRAWN or
QUARANTINED.

`storage_key` remains completely opaque.

## Crash-window behavior

PostgreSQL and BlobStore are not one ACID transaction.

- Create/register/activate/lifecycle/quarantine/safety either commit wholly or
  roll back to zero partial mutation.
- If bytes exist and revision registration does not commit, bytes remain;
  reconciliation may later classify an orphan. Never compensate with
  `BlobStore.delete`.
- If the caller supplies an `expected_aggregate_revision` that does not match
  the candidate Asset at read time, activation is an `AssetConflict` with zero
  BlobStore inspection and zero mutation.
- If `BlobStore.inspect` succeeds but governing facts change before the locked
  reread, including any Asset `aggregate_revision` mutation, activation is a
  concurrency conflict with zero mutation.
- If BlobStore is unavailable, activation fails closed with zero mutation.
- Physical TOCTOU after inspect and before commit is the accepted cross-store
  window; activation does not retain a DB write lock while inspecting.

Arbitrary `Exception` / `RuntimeError` is not relabeled as storage or governance
unavailability.

## RLS / Unit of Work

The Asset write Unit of Work owns one SQLAlchemy 2 Core / psycopg3 transaction.
Repositories do not independently commit or rollback. Each transaction installs
transaction-local `set_config('aieos.tenant_id', tenant_id, true)`. Pooled
connections must not retain tenant context. Existing FORCE RLS remains active.
There is no BYPASSRLS, no SET ROLE bypass, and no schema-owner identity used as
product runtime identity.

All Asset read/write operations are scoped through current tenant RLS. There is
no privileged cross-tenant existence probe. For existing-object commands,
globally absent and cross-tenant-hidden remain indistinguishable at the
application authority boundary.

## Explicit non-goals

No production BlobStore provider (AWS S3, Azure Blob Storage, GCS, MinIO,
production filesystem). No new cloud/storage SDK. No Asset HTTP routes, OpenAPI
paths, upload/download API, or signed URLs. No purge orchestration. No
`BlobStore.delete` as governed deletion. No `bytes_purged` mutation to true. No
`deletion_evidence` writes. No retention, legal/governance hold, or Content
reference analysis. No Asset ACL, role model, or capability vocabulary. No JWT
role/scope authorization. No `SecurityAuditAction` `asset.*` values. No Asset
CloudEvents, outbox events, or Temporal workflows. No runtime production
composition of these mutation services. No `PostgresAssetUseAuthority`
production wiring. No readiness changes. No `AIEOS_ASSET_SCHEMA_OWNER_ROLE`
config. No migrations, tables, columns, indexes, or cross-domain foreign keys.
No new runtime dependency family.

PED-I10B6 and PED-I11+ are not started.

## No purge

There is no B5 application command capable of `bytes_purged = true`,
`BlobStore.delete(...)`, or insertion into `asset.deletion_evidence`. Those
operations remain architecture-blocked pending retention/hold/reference and
purge-order decisions.

## No provider / no API / no production composition

B5 mutation services are implementation foundations only. Tests instantiate them
explicitly. They are not wired into FastAPI, runtime composition, Temporal
activities, event consumers, NATS handlers, or CLI production entry points.
There is no production call site exposing B5 mutations.

Alembic head remains `pedi10b2001`. Canonical OpenAPI SHA256 remains
`D847C7BC21227072DC2627426A1B61774F33DEB78F65397C7C584BCC38C0BCAF`.
