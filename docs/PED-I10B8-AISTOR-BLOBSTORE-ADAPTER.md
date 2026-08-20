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
spool; no ordinary runtime physical-delete authority.

The Bootstrap 32 MiB / media-profile admission boundary is **not** hard-coded
into the generic BlobStore port. That rule belongs to a later governed binary
HTTP/composition path. PED-I10B8 does not implement HTTP-level admission.

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

Default implementation dependency (exact preflight-proven pair):

- `boto3 == 1.40.21`
- `botocore == 1.40.76`

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
- `Body` = one-shot `ReadableBinary` stream
- `ContentLength` = exact `byte_size`
- `IfNoneMatch` = `"*"`

No multipart. No overwrite fallback. No alternative key generation. No
full-object pre-read, hashing, temporary spool, or RAM buffering. No upload
helper. No `ChecksumAlgorithm=SHA256` on PutObject.

Botocore request configuration:

```python
retries = {
    "mode": "standard",
    "total_max_attempts": 1,
}
addressing_style = "path"
payload_signing_enabled = False
request_checksum_calculation = "when_required"
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

## Checksum decoding

boto3/botocore `ChecksumSHA256` is Base64-encoded. Canonical AIEOS `sha256`:

1. require non-empty string
2. strict Base64 decode
3. require decoded length == 32 bytes
4. convert to lowercase hexadecimal
5. validate via `require_sha256` (exactly 64 lowercase hex characters)

Do not store Base64 in `BlobObjectInfo.sha256`. Do not use ETag, user metadata,
or caller-provided digests.

## Inspect

`inspect` uses HEAD only. Returns `BlobObjectInfo` when complete and valid.
Only genuine object-not-found may return `None`. Missing bucket, permission
denial, TLS failure, timeout, ambiguous 404, and malformed responses fail
closed (`BlobStoreUnavailableError` or `BlobStoreContractError`). ETag-only
responses are not sufficient.

## Error mapping

| Outcome | Application error |
| --- | --- |
| HTTP 412 / PreconditionFailed | `BlobAlreadyExistsError` |
| Permission / TLS / network / timeout / 5xx / unknown | `BlobStoreUnavailableError` |
| Malformed response / missing/invalid checksum / size contradiction | `BlobStoreContractError` |
| Genuine object absence (inspect) | `None` |

No boto/botocore exception types escape above the infrastructure boundary.

## Delete method vs runtime delete authority

Delete option A: `BlobStore.delete` remains on the provider-neutral Protocol.
The AIStor adapter may implement `DeleteObject`.

PED-I10B8 does **not** add an application delete call site, Asset purge,
retention execution, admin endpoint, API route, production runtime wiring,
production credential, or "delete enabled" flag.

ADR-AIEOS-043 remains binding: ordinary production runtime credentials MUST NOT
have DeleteObject authority. Existence of the adapter method is not
authorization to use it.

## Deferred

- `BlobInventory` provider implementation (LIST→HEAD)
- Live production AIStor conformance gate
- Asset HTTP / upload / download
- Production composition and credentials
- Spaces backup / RFC 8785 / Ed25519 backup
- Cloud provisioning / DNS / certificates / OpenTofu

## Production validation still required

PED-I10B8 proves adapter implementation + local/stubbed contract behavior +
frozen-provider semantics encoded in source/tests.

It does **not** prove live production AIStor compatibility. A later controlled
non-production AIStor adapter conformance gate must prove against the selected
AIStor version: single conditional PUT, checksum HEAD, missing-object vs
missing-bucket mapping, TLS validation, runtime permission model, and no
DeleteObject for normal runtime credentials.
