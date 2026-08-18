# PED-I10B6 Asset Authorization & Transactional Security Audit Foundation

**Status:** IMPLEMENTED (source) — production migration / mutation / deployment remain
**NOT AUTHORIZED**.

**Classification:** NON_PRODUCTION

**Governing ADRs:**

- ADR-AIEOS-036 — Asset Authorization & Transactional Security Audit Baseline
  (**FROZEN / APPROVED**)
- ADR-AIEOS-036R1 — Asset Security-Audit Resource Revision Semantics
  (**FROZEN / APPROVED**)

PED-I10B6 implements the NON_PRODUCTION Asset cross-cutting security foundation:
exact capability authorization through the Authorization Kernel, authorization
before the first Asset Unit of Work (UoW), and same-transaction
`security.audit_records` evidence for committed Asset mutations.

This slice remains uncomposed and externally unreachable. Architecture catalogue
synchronization is still required before any production promotion.

The Asset schema-owner readiness guard remains open:
`AIEOS_ASSET_SCHEMA_OWNER_ROLE` is still absent from API runtime config and
readiness (`asset` is not in `_ALL_APP_SCHEMAS`).

## Exact six capabilities

Canonical constants live in `src/aieos/domains/asset/application/ports.py`.
The platform adapter imports those constants and must not redefine the strings.

| Constant | Value |
| --- | --- |
| `ASSET_CREATE` | `asset.create` |
| `ASSET_REVISION_REGISTER` | `asset.revision.register` |
| `ASSET_REVISION_ACTIVATE` | `asset.revision.activate` |
| `ASSET_LIFECYCLE_MANAGE` | `asset.lifecycle.manage` |
| `ASSET_QUARANTINE_MANAGE` | `asset.quarantine.manage` |
| `ASSET_SAFETY_DECIDE` | `asset.safety.decide` |

No wildcard. No `asset.*`, `*`, `asset.read`, `asset.purge`, `asset.bytes.read`,
`asset.download`, or `asset.upload`.

Authorization remains principal + tenant + exact capability. Resource context is
contextual only. Ownership is not an allow rule. There are no Asset ACLs,
resource-scoped grants, roles, or JWT role/group/scope claims.

## Exact ten audit actions

| `SecurityAuditAction` | Value |
| --- | --- |
| `ASSET_CREATE` | `asset.create` |
| `ASSET_REVISION_REGISTER` | `asset.revision.register` |
| `ASSET_REVISION_ACTIVATE` | `asset.revision.activate` |
| `ASSET_LIFECYCLE_WITHDRAW` | `asset.lifecycle.withdraw` |
| `ASSET_LIFECYCLE_RESTORE` | `asset.lifecycle.restore` |
| `ASSET_LIFECYCLE_DELETE` | `asset.lifecycle.delete` |
| `ASSET_QUARANTINE_SET` | `asset.quarantine.set` |
| `ASSET_QUARANTINE_CLEAR` | `asset.quarantine.clear` |
| `ASSET_SAFETY_PASS` | `asset.safety.pass` |
| `ASSET_SAFETY_FAIL` | `asset.safety.fail` |

No `asset.purge`, `asset.download`, `asset.upload`, `asset.read`, or event-like
values.

## Command → capability mapping

The service chooses the required capability. Callers cannot select a weaker one.

| Command | Capability |
| --- | --- |
| `create_asset` | `asset.create` |
| `register_revision` | `asset.revision.register` |
| `activate_revision` | `asset.revision.activate` |
| `withdraw_asset` | `asset.lifecycle.manage` |
| `restore_asset` | `asset.lifecycle.manage` |
| `delete_asset` | `asset.lifecycle.manage` |
| `quarantine_asset` | `asset.quarantine.manage` |
| `clear_quarantine` | `asset.quarantine.manage` |
| `mark_safety_passed` | `asset.safety.decide` |
| `mark_safety_failed` | `asset.safety.decide` |

## Command → audit action mapping

A successful-mutation audit row exists only when a new authoritative mutation
commits.

| Command | Audit action |
| --- | --- |
| `create_asset` | `asset.create` |
| `register_revision` | `asset.revision.register` |
| `activate_revision` | `asset.revision.activate` |
| `withdraw_asset` | `asset.lifecycle.withdraw` |
| `restore_asset` | `asset.lifecycle.restore` |
| `delete_asset` | `asset.lifecycle.delete` |
| `quarantine_asset` | `asset.quarantine.set` |
| `clear_quarantine` | `asset.quarantine.clear` |
| `mark_safety_passed` | `asset.safety.pass` |
| `mark_safety_failed` | `asset.safety.fail` |

Replay, stale CAS, invalid transition, identity conflict, activation byte
failure, BlobStore unavailability, authorization DENY, and authorization
unavailability insert **no** new audit row.

## Authorization-before-UoW

Authorization MUST occur before the first Asset Unit of Work begins.

For `activate_revision`:

authorization → candidate DB read → `BlobStore.inspect` → write UoW

DENY or authorization-unavailable therefore produces zero Asset UoW opens,
zero Asset DB reads/writes, zero `BlobStore.inspect` calls, and zero mutations.

## Audit ResourceRef strategy (ADR-AIEOS-036R1)

Do **not** reuse Asset `aggregate_revision` as `ResourceRef.resource_revision`.
Those are different concepts. Do **not** place `AssetRevisionId` in
`ResourceRef.resource_id`.

**Primary ResourceRef** (stable Asset):

- `resource_type` = canonical typed Asset resource type already stored on the
  aggregate (`asset.image` / `asset.document` / `asset.audio` / `asset.video`)
- `resource_id` = `AssetId`
- `resource_revision` = `None`

**`resource_revision_before` / `resource_revision_after`** represent Asset
`aggregate_revision`, not `AssetRevisionNumber`.

**Related pinned ResourceRef** (same type + same `AssetId` + exact
`AssetRevisionNumber`) is required for:

- `asset.revision.register`
- `asset.revision.activate`
- `asset.safety.pass`
- `asset.safety.fail`

Lifecycle and quarantine audits have empty related refs.

## Aggregate revision semantics

| Action | before | after |
| --- | --- | --- |
| `asset.create` | `None` | `0` |
| `asset.revision.register` | current aggregate N | N (N→N; registration does not advance aggregate_revision) |
| eight Asset increment actions | N | N+1 |

Asset increment actions: activate, lifecycle withdraw/restore/delete,
quarantine set/clear, safety pass/fail.

Content audit semantics are unchanged: Content primary `ResourceRef.resource_revision`
equals `resource_revision_after`.

## Audit rollback invariant

Sequence for a successful mutation:

authoritative mutation → construct exact audit record → `uow.audit.insert(record)`
→ `uow.commit()`

If audit insertion fails, the transaction rolls back and the Asset mutation must
not commit. The same Asset UoW transaction owns the Asset table mutation and the
`security.audit_records` insert.

## Alembic

- revision: `pedi10b6001`
- down_revision: `pedi10b2001`

The migration extends the existing `security.audit_records` contract only. It
does not create tables, modify `asset.*` / `content.*` tables, weaken
immutability, grant UPDATE/DELETE, or add BYPASSRLS.

If Asset audit evidence exists, downgrade fails closed and does not delete or
rewrite immutable history.

## Explicit non-goals

- no Asset HTTP / OpenAPI / binary upload or download / presigned URLs
- no Asset events, CloudEvents, or outbox rows
- no NATS or Temporal changes
- no production BlobStore provider
- no purge, `BlobStore.delete` call sites, `bytes_purged=true`, retention, hold,
  or `deletion_evidence` writes
- no production composition (`compose_api_application` / `ApiRuntimeDependencies`
  Asset wiring)
- no PED-I03 operation inventory expansion
- no Asset schema-owner readiness closure
- no production mutation activation, cloud provisioning, or deployment
