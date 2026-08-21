# PED-I10B8 — AIStor BlobStore Adapter & Exact-Length Streaming Contract

**Classification: NON_PRODUCTION**

This slice implements the MinIO AIStor BlobStore adapter and the approved
provider-neutral exact-length streaming port refinement. It does **not**
provision cloud resources, create buckets, ship credentials, compose production
runtime, expose Asset HTTP, change OpenAPI, migrate schema, activate PED-I03,
or deploy.

## Governing architecture

Frozen / Approved:

- ADR-AIEOS-033
- ADR-AIEOS-034
- ADR-AIEOS-035
- ADR-AIEOS-036
- ADR-AIEOS-036R1
- ADR-AIEOS-039
- ADR-AIEOS-040R1
- ADR-AIEOS-041
- ADR-AIEOS-041R1
- ADR-AIEOS-042
- ADR-AIEOS-043

Preserved semantics: MinIO AIStor primary BlobStore; create-new-only; atomic
`If-None-Match: *`; no overwrite; single PutObject only; no multipart;
provider-authoritative whole-object SHA-256; exact byte size; no ETag-as-digest;
inspect without ordinary GET; opaque `storage_key`; fail closed; no presigned
URLs; no public byte path; no CDN; streaming without full-object application
spool; ReadableBinary guarantees `read()` only (no seek/tell/fileno/path/replay
requirement); no ordinary runtime physical-delete authority.

No ADR amendment is required for the live-conformance correction below.

The Bootstrap 32 MiB / media-profile admission boundary is **not** hard-coded
into the generic BlobStore port. That rule belongs to a later governed binary
HTTP/composition path. PED-I10B8 does not implement HTTP-level admission.

## Live-conformance correction (local AIStor)

Tested provider (stable at correction time):

- Image: `quay.io/minio/aistor/minio:RELEASE.2026-08-07T18-34-35Z`
- Immutable digest:
  `sha256:d1eb0f79ced75d6c024fc6a2ab6a7b3629ff54c798d967d9c6f89951237480a7`

Findings encoded by this correction:

1. Default AIStor PutObject without explicit SHA-256 returned `ChecksumCRC64NVME`,
   not `ChecksumSHA256`. Explicit `ChecksumAlgorithm="SHA256"` is therefore
   required on PutObject so post-write HEAD can observe provider SHA-256.
2. botocore `1.40.76` required `tell()`/`seek()` on the checksum-trailer path for
   non-seekable bodies. That violates the ReadableBinary contract.
3. botocore `1.43.57` plus an infrastructure read-only body facade preserves
   non-seekable streaming: caller `ContentLength` supplies decoded length for
   aws-chunked SHA-256 trailers. Underlying tell/seek are not required and must
   not be invoked.
4. AIStor HeadObject for genuine missing objects returns ambiguous
   `404` / Error.Code `"404"` / `Not Found` (not always explicit `NoSuchKey`).
5. Deterministic absence uses fail-closed discrimination:
   ambiguous HEAD 404 → successful `GetBucketLocation` on the configured bucket
   → one stability HeadObject recheck. Second ambiguous 404 or explicit
   `NoSuchKey` → `None`. Location failure or other second-HEAD errors →
   unavailable.
6. HTTP aws-chunked framing is still **one** S3 PutObject. It is not multipart.

### Future ordinary runtime credential semantics (documentation only)

May include on the exact configured primary bucket:

- Object: `s3:PutObject`, `s3:GetObject`
- Bucket: `s3:GetBucketLocation`

Must **not** grant ordinary runtime:

- `s3:ListBucket`
- `s3:ListAllMyBuckets`
- `s3:DeleteObject`
- admin authority

PED-I10B8 does not create production credentials or IAM policies.
`s3:GetBucketLocation` is infrastructure-level provider observation permission,
not business authorization or object-namespace enumeration.

### Forbidden list APIs in the adapter

The adapter must not call:

- `ListObjects` / `ListObjectsV2` / `ListObjectVersions` / `ListBuckets`

Exact-key ListObjectsV2 is rejected for ordinary runtime because it requires
`s3:ListBucket` and expands object-namespace enumeration.

### Observed ListBuckets residual (non-blocking)

During local live testing of `RELEASE.2026-08-07T18-34-35Z`, `ListBuckets`
returned the identity's accessible bucket name even without
`s3:ListAllMyBuckets` and despite an attempted explicit deny.

This does **not** authorize calling `ListBuckets`. It is a provider-specific
residual to re-check at production credential conformance. It is non-blocking
for Bootstrap because: the adapter does not call `ListBuckets`; production
credentials must be dedicated to the AIEOS primary bucket; runtime already
requires that configured bucket name; object-namespace enumeration remains
denied by absence of `s3:ListBucket`; `DeleteObject` remains denied.

Do not claim MinIO ListBuckets deny semantics were proven correct.

## Approved port refinement

Provider-neutral signatures:

```python
BlobStore.create(
    *,
    storage_key: str,
    source: ReadableBinary,
    byte_size: int,
) -> BlobObjectInfo

BlobIngestPreparer.prepare(
    source: ReadableBinary,
    *,
    byte_size: int,
) -> PreparedBlob
```

`byte_size` is the exact declared transport length. Validation uses existing
provider-neutral rules (`bool` is invalid; negatives are invalid). The
refinement does not reopen ADR-AIEOS-033/039/042: it does not add file/path
semantics, does not require `seek`/`tell`/`fileno`/replayability, does not add
provider types to application, does not take SHA-256 as create input, and does
not prehash or spool the complete source.

`BlobIngestPreparer` forwards `byte_size` unchanged to `BlobStore.create`.
`PreparedBlob` remains a provider observation (`storage_key`, `byte_size`,
`sha256`).

## Client family

Default implementation dependency (exact live-conformance-proven pair):

- `boto3 == 1.43.57`
- `botocore == 1.43.57`

Proven transitive family at correction time included:

- `s3transfer == 0.19.2`
- `urllib3 == 2.7.0`
- `python-dateutil == 2.9.0.post0`
- `jmespath == 1.1.0`
- `six` as resolved transitively

Uses the **boto3 / botocore low-level S3 client** only.

Does **not** use: MinIO Python SDK; `upload_file` / `upload_fileobj`;
S3Transfer / TransferManager; multipart upload helpers
(`CreateMultipartUpload` / `UploadPart` / `CompleteMultipartUpload`).

Presence of transitive `s3transfer` does not authorize using its transfer APIs.

Provider SDK imports are permitted **only** under:

`src/aieos/domains/asset/infrastructure/blobstore/`

They remain forbidden from Asset domain/application, Content, platform
security, and unrelated infrastructure.

## Create request semantics

Exactly one write operation per `create()`:

- `PutObject`
- `Bucket` = configured primary bucket
- `Key` = exact opaque `storage_key`
- `Body` = infrastructure `_ReadOnlyStreamingBody(source)` (read()-only facade)
- `ContentLength` = exact `byte_size`
- `IfNoneMatch` = `"*"`
- `ChecksumAlgorithm` = `"SHA256"`

Do **not** pass precomputed `ChecksumSHA256`. Do not calculate a complete
application SHA-256 before upload. No multipart. No overwrite fallback. No
alternative key generation. No full-object pre-read, hashing, temporary spool,
or RAM buffering. No upload helper.

The read-only facade exposes only `read()`. It does not expose tell/seek/
fileno/path/reset/rewind/replay and does not fake seekability.

Botocore request configuration:

```python
retries = {
    "mode": "standard",
    "total_max_attempts": 1,
}
addressing_style = "path"
payload_signing_enabled = False
request_checksum_calculation = "when_required"
response_checksum_validation = "when_required"
disable_request_compression = True
```

`total_max_attempts=1` disables body retries for the one-shot stream.
Do **not** use `max_attempts=1` as the no-retry setting (that still allows one
retry after the initial attempt).

Production-capable factory semantics require HTTPS, path-style addressing, and
mandatory TLS verification. `verify=False` and plaintext fallback are forbidden.
There is no production "allow insecure" toggle. Tests may inject stubbed
clients without making insecure HTTP a production option.

## Post-write observation

Successful PutObject alone is insufficient. After PutObject, the adapter issues
`HeadObject` with `ChecksumMode="ENABLED"` on the same key and establishes:

- exact provider `ContentLength` (must equal declared `byte_size`)
- provider whole-object SHA-256

Mismatch or uncertain inspection → `BlobStoreContractError` / unavailable as
mapped. The adapter does **not** delete as compensation. Orphan/reconciliation
architecture remains binding.

Post-write create observation remains fail closed. Missing-object absence
discrimination must **not** convert a successful PUT into silent absence.

## Checksum decoding

boto3/botocore `ChecksumSHA256` is Base64-encoded. Canonical AIEOS `sha256`:

1. require non-empty string
2. strict Base64 decode
3. require decoded length == 32 bytes
4. convert to lowercase hexadecimal
5. validate via `require_sha256` (exactly 64 lowercase hex characters)

Do not store Base64 in `BlobObjectInfo.sha256`. Do not use ETag, user metadata,
CRC64, or caller-provided digests.

## Inspect

`inspect` uses HeadObject (and, only for ambiguous HEAD 404,
`GetBucketLocation` plus one stability HeadObject). Ordinary GET is not used.

Algorithm:

1. HeadObject with `ChecksumMode="ENABLED"`.
2. Success → validate and return `BlobObjectInfo` (no location call).
3. Explicit `NoSuchKey` → `None` (no location call).
4. Non-ambiguous failure (403/AccessDenied, NoSuchBucket, 5xx, TLS, timeout,
   unknown) → `BlobStoreUnavailableError` (no location call).
5. Ambiguous HEAD 404 only (`404` / `NotFound` / bare 404 class, not
   `NoSuchKey`, not `NoSuchBucket`) → `GetBucketLocation(Bucket=configured)`.
6. Location failure for any reason → `BlobStoreUnavailableError` (never infer
   absence). LocationConstraint value is not business data; API success is the
   positive bucket observation.
7. After successful location: exactly one second HeadObject.
   - success → `BlobObjectInfo`
   - explicit `NoSuchKey` → `None`
   - same ambiguous 404 class → `None`
   - any other error → `BlobStoreUnavailableError`

Maximum calls per inspect invocation: **2** HeadObject, **1** GetBucketLocation.
No loops, sleeps, or general retries.

Do **not** globally map `404 → None`. The first ambiguous 404 must never return
`None` directly.

## Error mapping

| Outcome | Application error |
| --- | --- |
| HTTP 412 / PreconditionFailed | `BlobAlreadyExistsError` |
| Permission / TLS / network / timeout / 5xx / unknown | `BlobStoreUnavailableError` |
| Malformed response / missing/invalid checksum / size contradiction | `BlobStoreContractError` |
| Explicit `NoSuchKey` (inspect) | `None` |
| Ambiguous HEAD 404 + successful GetBucketLocation + second missing HEAD | `None` |
| Ambiguous HEAD 404 + GetBucketLocation failure | `BlobStoreUnavailableError` |
| Ambiguous HEAD 404 alone (without location success) | never `None` |

No boto/botocore exception types escape above the infrastructure boundary.

## Delete method vs runtime delete authority

Delete option A: `BlobStore.delete` remains on the provider-neutral Protocol.
The AIStor adapter may implement `DeleteObject`.

PED-I10B8 does **not** add an application delete call site, Asset purge,
retention execution, admin endpoint, API route, production runtime wiring,
production credential, or "delete enabled" flag.

ADR-AIEOS-043 remains binding: ordinary production runtime credentials MUST NOT
have DeleteObject authority. Existence of the adapter method is not
authorization to use it. DeleteObject remains unavailable to ordinary runtime.

## Deferred

- `BlobInventory` provider implementation (LIST→HEAD)
- Production AIStor credential / composition gate
- Asset HTTP / upload / download
- Production composition and credentials
- Spaces backup / RFC 8785 / Ed25519 backup
- Cloud provisioning / DNS / certificates / OpenTofu

## Production validation still required

PED-I10B8 proves adapter implementation + local/stubbed contract behavior +
local live AIStor conformance against the pinned Free Tier disposable provider.

It does **not** create cloud resources, production credentials, or production
runtime composition. A later production credential conformance gate must still
prove the dedicated primary-bucket identity shape, including re-checking the
ListBuckets residual and DeleteObject denial under production policy.
