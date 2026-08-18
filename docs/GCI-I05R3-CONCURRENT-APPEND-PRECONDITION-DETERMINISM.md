# GCI-I05R3 Concurrent Append Precondition Determinism

**Status:** IMPLEMENTED (source)

**Classification:** Generic Content correction — not PED-I10B5.

Two concurrent ContentVersion appends against the same Content, with distinct
`Idempotency-Key` values and the same valid `If-Match: "r0"`, must produce:

- exactly one `201`
- exactly one `412 resource_revision_conflict`

Never `409`, `422`, `500`, or `503` for the ordinary expected-revision loser.

Durable outcome: one ContentVersion, one `version_created` outbox event,
`aggregate_revision == 1`. The losing transaction leaves zero
business/outbox/audit/idempotency mutation.

## Pre-fix evidence

Observed loser:

- HTTP status: `422`
- `ProblemDetails.code`: `persistence_invariant_violation`

Cause: `SqlAlchemyContentRepository.get_head_for_update()` used a single
`SELECT contents LEFT JOIN content_versions ... FOR UPDATE OF contents`.

Under READ COMMITTED, a waiter that blocked while another transaction
committed the first ContentVersion could resume with a mixed projection:

- `contents.current_version_id` = committed V1
- joined `content_versions.version_number` = NULL

HTTP append then raised `PersistenceInvariantViolation` (`locked head is
missing current version_number`) **before** the existing
`AggregateRevisionConflict` check in `append_version_in_uow`.

## Correction

Lock-then-read in the same UoW transaction:

1. `SELECT` authoritative `content.contents` columns `FOR UPDATE` (no join).
2. After the lock: if `current_version_id` is NULL, `current_version_number`
   is NULL; otherwise a separate `SELECT` of `version_number` from
   `content.content_versions` by exact `tenant_id` / `content_id` /
   `current_version_id`.
3. Missing child row with non-NULL `current_version_id` fails closed as
   `PersistenceInvariantViolation`.

HTTP append additionally compares `head.aggregate_revision` to the expected
If-Match revision immediately after the locked head is returned, raising
existing `AggregateRevisionConflict` (`412 resource_revision_conflict`).
This does not replace repository consistency.

412 arises because the authoritative expected aggregate revision is stale.
Unrelated errors are not remapped to 412.
