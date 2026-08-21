"""AIStor BlobStore adapter (infrastructure only).

NON_PRODUCTION. Implements the provider-neutral BlobStore Protocol against
AIStor via boto3/botocore low-level PutObject / HeadObject / GetBucketLocation
only.

Governing ADRs: ADR-AIEOS-033, 039, 040R1, 042, 043.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any

import boto3
from botocore.client import BaseClient
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from aieos.domains.asset.application.blob_store import (
    BlobObjectInfo,
    ReadableBinary,
    require_byte_size,
    require_opaque_storage_key,
    require_sha256,
)
from aieos.domains.asset.application.errors import (
    BlobAlreadyExistsError,
    BlobStoreContractError,
    BlobStoreUnavailableError,
)
from aieos.domains.asset.infrastructure.blobstore.config import AiStorBlobStoreConfig

_MISSING_BUCKET_CODES = frozenset({"NoSuchBucket", "404 NoSuchBucket"})
_PRECONDITION_CODES = frozenset(
    {"412", "PreconditionFailed", "412 PreconditionFailed"}
)
_AMBIGUOUS_404_CODES = frozenset(
    {
        "",
        "404",
        "NotFound",
        "404 Not Found",
    }
)


class _ReadOnlyStreamingBody:
    """Infrastructure-private facade: expose read() only to Botocore.

    Enforces the provider-neutral ReadableBinary boundary against SDK
    introspection. Does not fake seekability, buffer, spool, or prehash.
    """

    __slots__ = ("_source",)

    def __init__(self, source: ReadableBinary) -> None:
        self._source = source

    def read(self, size: int = -1) -> bytes:
        return self._source.read(size)


def _botocore_config(*, connect_timeout: float, read_timeout: float) -> Config:
    """Exact PED-I10B8 request configuration (preflight-proven semantics)."""
    return Config(
        signature_version="s3v4",
        s3={
            "addressing_style": "path",
            "payload_signing_enabled": False,
        },
        retries={
            "mode": "standard",
            "total_max_attempts": 1,
        },
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
        request_checksum_calculation="when_required",
        response_checksum_validation="when_required",
        disable_request_compression=True,
    )


def build_aistor_client(config: AiStorBlobStoreConfig) -> BaseClient:
    """Build a production-capable AIStor client. TLS verification is mandatory."""
    verify: bool | str = True
    if config.ca_bundle_path is not None:
        verify = config.ca_bundle_path
    return boto3.client(
        "s3",
        endpoint_url=config.endpoint_url,
        aws_access_key_id=config.access_key_id,
        aws_secret_access_key=config.secret_access_key,
        region_name=config.region,
        verify=verify,
        config=_botocore_config(
            connect_timeout=config.connect_timeout_seconds,
            read_timeout=config.read_timeout_seconds,
        ),
    )


def decode_provider_sha256_checksum(value: object) -> str:
    """Convert boto3/botocore Base64 ChecksumSHA256 to AIEOS lowercase hex."""
    if not isinstance(value, str) or not value.strip():
        raise BlobStoreContractError("provider ChecksumSHA256 must be a non-empty string")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise BlobStoreContractError(
            "provider ChecksumSHA256 is not valid Base64"
        ) from exc
    if len(decoded) != 32:
        raise BlobStoreContractError(
            "provider ChecksumSHA256 must decode to exactly 32 bytes"
        )
    return require_sha256(decoded.hex(), error=BlobStoreContractError)


def _error_code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", "") or "")


def _http_status(exc: ClientError) -> int | None:
    status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return int(status) if isinstance(status, int) else None


def _map_create_client_error(exc: ClientError) -> Exception:
    code = _error_code(exc)
    status = _http_status(exc)
    if code in _PRECONDITION_CODES or status == 412:
        return BlobAlreadyExistsError(
            "physical object already exists for this storage_key"
        )
    return BlobStoreUnavailableError("BlobStore create failed")


def _is_explicit_nosuchkey(exc: ClientError) -> bool:
    return _error_code(exc) == "NoSuchKey"


def _is_ambiguous_head_404(exc: ClientError) -> bool:
    """True only for AIStor-style generic HeadObject 404 candidates.

    Explicit NoSuchKey is deterministic missing and is not ambiguous.
    NoSuchBucket and other 404-class bucket failures are unavailable, not
    ambiguous object absence.
    """
    if _is_explicit_nosuchkey(exc):
        return False
    code = _error_code(exc)
    status = _http_status(exc)
    if code in _MISSING_BUCKET_CODES:
        return False
    if status != 404:
        return False
    return code in _AMBIGUOUS_404_CODES


def _map_inspect_unavailable(exc: ClientError) -> BlobStoreUnavailableError:
    code = _error_code(exc)
    if code in _MISSING_BUCKET_CODES:
        return BlobStoreUnavailableError("BlobStore bucket is unavailable")
    status = _http_status(exc)
    if status == 404:
        return BlobStoreUnavailableError(
            "BlobStore inspect failed with ambiguous 404"
        )
    return BlobStoreUnavailableError("BlobStore inspect failed")


def _map_post_put_inspect_client_error(exc: ClientError) -> Exception:
    """Post-write HEAD must never treat absence as success."""
    if _is_explicit_nosuchkey(exc):
        return BlobStoreUnavailableError(
            "BlobStore post-write inspect reported object absence"
        )
    return _map_inspect_unavailable(exc)


def _head_observation(
    *, client: BaseClient, bucket: str, storage_key: str
) -> dict[str, Any]:
    try:
        return client.head_object(
            Bucket=bucket,
            Key=storage_key,
            ChecksumMode="ENABLED",
        )
    except ClientError as exc:
        raise _map_post_put_inspect_client_error(exc) from exc
    except BotoCoreError as exc:
        raise BlobStoreUnavailableError("BlobStore inspect transport failed") from exc


def _blob_info_from_head(*, storage_key: str, response: dict[str, Any]) -> BlobObjectInfo:
    content_length = response.get("ContentLength")
    if isinstance(content_length, bool) or not isinstance(content_length, int):
        raise BlobStoreContractError("provider ContentLength must be an integer")
    if content_length < 0:
        raise BlobStoreContractError("provider ContentLength must be >= 0")
    checksum = response.get("ChecksumSHA256")
    if not isinstance(checksum, str) or not checksum.strip():
        raise BlobStoreContractError(
            "provider ChecksumSHA256 is required; ETag is not integrity authority"
        )
    sha256 = decode_provider_sha256_checksum(checksum)
    return BlobObjectInfo(
        storage_key=storage_key,
        byte_size=content_length,
        sha256=sha256,
    )


def _confirm_bucket_accessible(*, client: BaseClient, bucket: str) -> None:
    """Positive bucket observation via GetBucketLocation only.

    LocationConstraint value is not used as business data. API success alone
    proves the configured bucket is accessible/existing for discrimination.
    """
    try:
        client.get_bucket_location(Bucket=bucket)
    except ClientError as exc:
        raise BlobStoreUnavailableError(
            "BlobStore bucket location observation failed"
        ) from exc
    except BotoCoreError as exc:
        raise BlobStoreUnavailableError(
            "BlobStore bucket location transport failed"
        ) from exc


class AiStorBlobStore:
    """Concrete AIStor BlobStore. Not composed into production runtime by PED-I10B8."""

    def __init__(self, *, client: BaseClient, bucket: str) -> None:
        if not isinstance(bucket, str) or not bucket.strip():
            raise ValueError("bucket must be a non-empty string")
        self._client = client
        self._bucket = bucket

    @classmethod
    def from_config(cls, config: AiStorBlobStoreConfig) -> AiStorBlobStore:
        return cls(client=build_aistor_client(config), bucket=config.bucket)

    def create(
        self, *, storage_key: str, source: ReadableBinary, byte_size: int
    ) -> BlobObjectInfo:
        key = require_opaque_storage_key(storage_key, error=BlobStoreContractError)
        declared = require_byte_size(byte_size, error=BlobStoreContractError)
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=_ReadOnlyStreamingBody(source),
                ContentLength=declared,
                IfNoneMatch="*",
                ChecksumAlgorithm="SHA256",
            )
        except ClientError as exc:
            raise _map_create_client_error(exc) from exc
        except BotoCoreError as exc:
            raise BlobStoreUnavailableError("BlobStore create transport failed") from exc

        response = _head_observation(
            client=self._client, bucket=self._bucket, storage_key=key
        )
        observed = _blob_info_from_head(storage_key=key, response=response)
        if observed.byte_size != declared:
            raise BlobStoreContractError(
                "provider ContentLength does not match declared byte_size"
            )
        if observed.storage_key != key:
            raise BlobStoreContractError(
                "inspect returned a storage_key that does not match create"
            )
        return observed

    def inspect(self, *, storage_key: str) -> BlobObjectInfo | None:
        key = require_opaque_storage_key(storage_key, error=BlobStoreContractError)
        try:
            response = self._client.head_object(
                Bucket=self._bucket,
                Key=key,
                ChecksumMode="ENABLED",
            )
        except ClientError as first_exc:
            if _is_explicit_nosuchkey(first_exc):
                return None
            if not _is_ambiguous_head_404(first_exc):
                raise _map_inspect_unavailable(first_exc) from first_exc

            # Ambiguous HEAD 404: discriminate via GetBucketLocation, then
            # one bounded stability HEAD. Not a general retry policy.
            _confirm_bucket_accessible(client=self._client, bucket=self._bucket)

            try:
                response = self._client.head_object(
                    Bucket=self._bucket,
                    Key=key,
                    ChecksumMode="ENABLED",
                )
            except ClientError as second_exc:
                if _is_explicit_nosuchkey(second_exc):
                    return None
                if _is_ambiguous_head_404(second_exc):
                    return None
                raise _map_inspect_unavailable(second_exc) from second_exc
            except BotoCoreError as exc:
                raise BlobStoreUnavailableError(
                    "BlobStore inspect transport failed"
                ) from exc
            return _blob_info_from_head(storage_key=key, response=response)
        except BotoCoreError as exc:
            raise BlobStoreUnavailableError("BlobStore inspect transport failed") from exc
        return _blob_info_from_head(storage_key=key, response=response)

    def delete(self, *, storage_key: str) -> None:
        """Physical-only delete. Not wired to application commands in PED-I10B8.

        Ordinary production runtime credentials must not have DeleteObject
        authority (ADR-AIEOS-043). Existence of this method is not authorization.
        """
        key = require_opaque_storage_key(storage_key, error=BlobStoreContractError)
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            raise BlobStoreUnavailableError("BlobStore delete failed") from exc
        except BotoCoreError as exc:
            raise BlobStoreUnavailableError("BlobStore delete transport failed") from exc
