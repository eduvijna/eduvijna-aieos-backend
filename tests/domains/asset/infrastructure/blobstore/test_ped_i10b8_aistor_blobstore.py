"""PED-I10B8 AIStor BlobStore adapter and exact-length streaming contract tests."""

from __future__ import annotations

import base64
import hashlib
import inspect
from io import BytesIO
from typing import Any

import pytest
from botocore.exceptions import (
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
    SSLError,
)

from aieos.domains.asset.application.blob_store import BlobStore
from aieos.domains.asset.application.errors import (
    BlobAlreadyExistsError,
    BlobStoreContractError,
    BlobStoreUnavailableError,
)
from aieos.domains.asset.application.ingest import BlobIngestPreparer, PreparedBlob
from aieos.domains.asset.infrastructure.blobstore.aistor import (
    AiStorBlobStore,
    _botocore_config,
    decode_provider_sha256_checksum,
)
from aieos.domains.asset.infrastructure.blobstore.config import AiStorBlobStoreConfig
from aieos.platform.runtime.readiness import EXPECTED_ALEMBIC_HEAD
from tests.dbutil import REPO_ROOT
from tests.domains.asset.application.fakes import InMemoryBlobStore

pytestmark = pytest.mark.ped_i10b8

ASSET_ROOT = REPO_ROOT / "src" / "aieos" / "domains" / "asset"
APPLICATION = ASSET_ROOT / "application"
DOMAIN = ASSET_ROOT / "domain"
BLOBSTORE = ASSET_ROOT / "infrastructure" / "blobstore"
COMPOSITION = REPO_ROOT / "src" / "aieos" / "platform" / "runtime" / "composition.py"
OPENAPI = REPO_ROOT / "contracts" / "openapi" / "aieos-v1.json"
EXPECTED_OPENAPI_SHA256 = (
    "D847C7BC21227072DC2627426A1B61774F33DEB78F65397C7C584BCC38C0BCAF"
)
MIGRATIONS = REPO_ROOT / "migrations" / "versions"
DOC = REPO_ROOT / "docs" / "PED-I10B8-AISTOR-BLOBSTORE-ADAPTER.md"
PYPROJECT = REPO_ROOT / "pyproject.toml"
SRC_ROOT = REPO_ROOT / "src"

_PAYLOAD = b"hello-aistor-b8"
_SHA256_HEX = hashlib.sha256(_PAYLOAD).hexdigest()
_SHA256_B64 = base64.b64encode(bytes.fromhex(_SHA256_HEX)).decode("ascii")


def _client_error(
    *, code: str, status: int, operation: str = "PutObject"
) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": code},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        operation,
    )


class _RecordingClient:
    """Stub S3 client that records PutObject / HeadObject / DeleteObject."""

    def __init__(self) -> None:
        self.put_calls: list[dict[str, Any]] = []
        self.head_calls: list[dict[str, Any]] = []
        self.delete_calls: list[dict[str, Any]] = []
        self.create_multipart_calls: list[dict[str, Any]] = []
        self.upload_part_calls: list[dict[str, Any]] = []
        self.complete_multipart_calls: list[dict[str, Any]] = []
        self.put_error: Exception | None = None
        self.head_error: Exception | None = None
        self.head_response: dict[str, Any] = {
            "ContentLength": len(_PAYLOAD),
            "ChecksumSHA256": _SHA256_B64,
            "ETag": '"ignored"',
        }
        self.put_side_effects: list[Exception | None] = []

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.put_calls.append(kwargs)
        if self.put_side_effects:
            effect = self.put_side_effects.pop(0)
            if effect is not None:
                raise effect
        if self.put_error is not None:
            raise self.put_error
        assert "ChecksumAlgorithm" not in kwargs
        return {}

    def head_object(self, **kwargs: Any) -> dict[str, Any]:
        self.head_calls.append(kwargs)
        if self.head_error is not None:
            raise self.head_error
        return dict(self.head_response)

    def delete_object(self, **kwargs: Any) -> dict[str, Any]:
        self.delete_calls.append(kwargs)
        return {}

    def create_multipart_upload(self, **kwargs: Any) -> dict[str, Any]:
        self.create_multipart_calls.append(kwargs)
        raise AssertionError("multipart must not be used")

    def upload_part(self, **kwargs: Any) -> dict[str, Any]:
        self.upload_part_calls.append(kwargs)
        raise AssertionError("multipart must not be used")

    def complete_multipart_upload(self, **kwargs: Any) -> dict[str, Any]:
        self.complete_multipart_calls.append(kwargs)
        raise AssertionError("multipart must not be used")


def _store(client: _RecordingClient | None = None) -> tuple[AiStorBlobStore, _RecordingClient]:
    recording = client or _RecordingClient()
    return AiStorBlobStore(client=recording, bucket="primary-bucket"), recording


class TestPortRefinement:
    def test_blobstore_create_requires_byte_size(self) -> None:
        params = inspect.signature(BlobStore.create).parameters
        assert "byte_size" in params
        assert params["byte_size"].kind is inspect.Parameter.KEYWORD_ONLY

    def test_preparer_prepare_keyword_only_byte_size(self) -> None:
        params = inspect.signature(BlobIngestPreparer.prepare).parameters
        assert list(params) == ["self", "source", "byte_size"]
        assert params["byte_size"].kind is inspect.Parameter.KEYWORD_ONLY

    def test_invalid_byte_size_fails_before_provider_write(self) -> None:
        store, client = _store()
        for bad in (True, False, -1, 1.5, "8"):
            with pytest.raises(BlobStoreContractError):
                store.create(
                    storage_key="k", source=BytesIO(_PAYLOAD), byte_size=bad  # type: ignore[arg-type]
                )
        assert client.put_calls == []

    def test_preparer_forwards_byte_size_exactly(self) -> None:
        store = InMemoryBlobStore()

        class _Factory:
            def generate(self) -> str:
                return "generated-key"

        preparer = BlobIngestPreparer(blob_store=store, storage_key_factory=_Factory())
        prepared = preparer.prepare(BytesIO(_PAYLOAD), byte_size=len(_PAYLOAD))
        assert isinstance(prepared, PreparedBlob)
        assert prepared.storage_key == "generated-key"
        assert prepared.byte_size == len(_PAYLOAD)
        assert prepared.sha256 == _SHA256_HEX


class TestCreate:
    def test_single_put_exact_params_and_stream_body(self) -> None:
        store, client = _store()
        body = BytesIO(_PAYLOAD)
        info = store.create(
            storage_key="opaque-key", source=body, byte_size=len(_PAYLOAD)
        )
        assert len(client.put_calls) == 1
        put = client.put_calls[0]
        assert put["Bucket"] == "primary-bucket"
        assert put["Key"] == "opaque-key"
        assert put["ContentLength"] == len(_PAYLOAD)
        assert put["IfNoneMatch"] == "*"
        assert put["Body"] is body
        assert "ChecksumAlgorithm" not in put
        assert client.create_multipart_calls == []
        assert client.upload_part_calls == []
        assert client.complete_multipart_calls == []
        assert info.storage_key == "opaque-key"
        assert info.byte_size == len(_PAYLOAD)
        assert info.sha256 == _SHA256_HEX

    def test_412_maps_to_already_exists(self) -> None:
        store, client = _store()
        client.put_error = _client_error(code="PreconditionFailed", status=412)
        with pytest.raises(BlobAlreadyExistsError):
            store.create(
                storage_key="k", source=BytesIO(_PAYLOAD), byte_size=len(_PAYLOAD)
            )
        assert client.head_calls == []

    @pytest.mark.parametrize(
        "exc",
        [
            _client_error(code="AccessDenied", status=403),
            _client_error(code="InternalError", status=500),
            EndpointConnectionError(endpoint_url="https://example.test"),
            ConnectTimeoutError(endpoint_url="https://example.test"),
            ReadTimeoutError(endpoint_url="https://example.test"),
            SSLError(endpoint_url="https://example.test", error="tls"),
        ],
    )
    def test_infra_failures_map_unavailable(self, exc: Exception) -> None:
        store, client = _store()
        client.put_error = exc
        with pytest.raises(BlobStoreUnavailableError):
            store.create(
                storage_key="k", source=BytesIO(_PAYLOAD), byte_size=len(_PAYLOAD)
            )


class TestPostPutInspect:
    def test_create_heads_with_checksum_mode(self) -> None:
        store, client = _store()
        store.create(
            storage_key="k", source=BytesIO(_PAYLOAD), byte_size=len(_PAYLOAD)
        )
        assert len(client.head_calls) == 1
        assert client.head_calls[0]["ChecksumMode"] == "ENABLED"
        assert client.head_calls[0]["Key"] == "k"
        assert client.delete_calls == []

    def test_size_mismatch_fails_closed_without_delete(self) -> None:
        store, client = _store()
        client.head_response["ContentLength"] = len(_PAYLOAD) + 1
        with pytest.raises(BlobStoreContractError):
            store.create(
                storage_key="k", source=BytesIO(_PAYLOAD), byte_size=len(_PAYLOAD)
            )
        assert client.delete_calls == []

    def test_missing_checksum_fails_closed(self) -> None:
        store, client = _store()
        client.head_response = {
            "ContentLength": len(_PAYLOAD),
            "ETag": '"abc"',
        }
        with pytest.raises(BlobStoreContractError):
            store.create(
                storage_key="k", source=BytesIO(_PAYLOAD), byte_size=len(_PAYLOAD)
            )
        assert client.delete_calls == []

    def test_malformed_base64_fails_closed(self) -> None:
        store, client = _store()
        client.head_response["ChecksumSHA256"] = "!!!not-base64!!!"
        with pytest.raises(BlobStoreContractError):
            store.create(
                storage_key="k", source=BytesIO(_PAYLOAD), byte_size=len(_PAYLOAD)
            )

    def test_wrong_decoded_digest_length_fails_closed(self) -> None:
        store, client = _store()
        client.head_response["ChecksumSHA256"] = base64.b64encode(b"short").decode()
        with pytest.raises(BlobStoreContractError):
            store.create(
                storage_key="k", source=BytesIO(_PAYLOAD), byte_size=len(_PAYLOAD)
            )

    def test_post_put_head_failure_does_not_delete(self) -> None:
        store, client = _store()
        client.head_error = _client_error(code="InternalError", status=500, operation="HeadObject")
        with pytest.raises(BlobStoreUnavailableError):
            store.create(
                storage_key="k", source=BytesIO(_PAYLOAD), byte_size=len(_PAYLOAD)
            )
        assert client.delete_calls == []


class TestChecksumDecode:
    def test_base64_to_lowercase_hex(self) -> None:
        assert decode_provider_sha256_checksum(_SHA256_B64) == _SHA256_HEX


class TestInspect:
    def test_valid_head_returns_info(self) -> None:
        store, client = _store()
        info = store.inspect(storage_key="opaque-key")
        assert info is not None
        assert info.storage_key == "opaque-key"
        assert info.byte_size == len(_PAYLOAD)
        assert info.sha256 == _SHA256_HEX
        assert client.head_calls[0]["ChecksumMode"] == "ENABLED"

    def test_nosuchkey_returns_none(self) -> None:
        store, client = _store()
        client.head_error = _client_error(
            code="NoSuchKey", status=404, operation="HeadObject"
        )
        assert store.inspect(storage_key="missing") is None

    def test_generic_notfound_404_unavailable(self) -> None:
        store, client = _store()
        client.head_error = _client_error(
            code="NotFound", status=404, operation="HeadObject"
        )
        with pytest.raises(BlobStoreUnavailableError):
            store.inspect(storage_key="k")

    def test_nosuchbucket_unavailable(self) -> None:
        store, client = _store()
        client.head_error = _client_error(
            code="NoSuchBucket", status=404, operation="HeadObject"
        )
        with pytest.raises(BlobStoreUnavailableError):
            store.inspect(storage_key="k")

    def test_access_denied_unavailable(self) -> None:
        store, client = _store()
        client.head_error = _client_error(
            code="AccessDenied", status=403, operation="HeadObject"
        )
        with pytest.raises(BlobStoreUnavailableError):
            store.inspect(storage_key="k")

    def test_timeout_unavailable(self) -> None:
        store, client = _store()
        client.head_error = ReadTimeoutError(endpoint_url="https://example.test")
        with pytest.raises(BlobStoreUnavailableError):
            store.inspect(storage_key="k")

    def test_ambiguous_404_unavailable(self) -> None:
        store, client = _store()
        client.head_error = _client_error(code="", status=404, operation="HeadObject")
        with pytest.raises(BlobStoreUnavailableError):
            store.inspect(storage_key="k")

    def test_etag_only_contract_error(self) -> None:
        store, client = _store()
        client.head_response = {
            "ContentLength": len(_PAYLOAD),
            "ETag": '"abc"',
        }
        with pytest.raises(BlobStoreContractError):
            store.inspect(storage_key="k")

    def test_malformed_checksum_contract_error(self) -> None:
        store, client = _store()
        client.head_response["ChecksumSHA256"] = "not@@valid"
        with pytest.raises(BlobStoreContractError):
            store.inspect(storage_key="k")


class TestRetries:
    def test_botocore_config_total_max_attempts_one(self) -> None:
        cfg = _botocore_config(connect_timeout=1.0, read_timeout=1.0)
        assert cfg.retries["mode"] == "standard"
        assert cfg.retries["total_max_attempts"] == 1
        assert cfg.s3["addressing_style"] == "path"
        assert cfg.s3["payload_signing_enabled"] is False
        assert cfg.request_checksum_calculation == "when_required"
        assert cfg.disable_request_compression is True

    def test_no_second_put_on_retryable_error(self) -> None:
        store, client = _store()
        client.put_error = _client_error(code="InternalError", status=500)
        with pytest.raises(BlobStoreUnavailableError):
            store.create(
                storage_key="k", source=BytesIO(_PAYLOAD), byte_size=len(_PAYLOAD)
            )
        assert len(client.put_calls) == 1


class TestDeleteBoundary:
    def test_adapter_delete_may_exist_without_application_call_sites(self) -> None:
        store, client = _store()
        store.delete(storage_key="k")
        assert client.delete_calls == [{"Bucket": "primary-bucket", "Key": "k"}]
        hits: list[str] = []
        for path in APPLICATION.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            if "self._blob_store.delete" in source:
                hits.append(path.name)
            if "AiStorBlobStore" in source and path.name != "__init__.py":
                hits.append(path.name)
        assert hits == []
        config_source = (BLOBSTORE / "config.py").read_text(encoding="utf-8")
        assert "delete_enabled" not in config_source
        assert "allow_delete" not in config_source
        assert "verify=False" not in (BLOBSTORE / "aistor.py").read_text(encoding="utf-8")


class TestConfig:
    def test_https_and_tls_required(self) -> None:
        with pytest.raises(ValueError, match="HTTPS"):
            AiStorBlobStoreConfig(
                endpoint_url="http://insecure.example",
                bucket="b",
                region="us-east-1",
                access_key_id="ak",
                secret_access_key="sk",
            )


class TestBoundaries:
    def test_no_boto_in_application_or_domain(self) -> None:
        for root in (APPLICATION, DOMAIN):
            for path in root.rglob("*.py"):
                source = path.read_text(encoding="utf-8")
                assert "import boto3" not in source
                assert "import botocore" not in source
                assert "from botocore" not in source

    def test_no_http_openapi_migration_composition_or_pedi03(self) -> None:
        assert not (ASSET_ROOT / "api").exists()
        assert EXPECTED_ALEMBIC_HEAD == "pedi10b6001"
        assert not any(p.name.startswith("pedi10b8") for p in MIGRATIONS.glob("*.py"))
        digest = hashlib.sha256(OPENAPI.read_bytes()).hexdigest().upper()
        assert digest == EXPECTED_OPENAPI_SHA256
        composition = COMPOSITION.read_text(encoding="utf-8")
        assert "AiStorBlobStore" not in composition
        assert "domains.asset" not in composition
        assert "BlobInventory" not in (BLOBSTORE / "aistor.py").read_text(encoding="utf-8")
        pyproject = PYPROJECT.read_text(encoding="utf-8")
        assert "ped_i10b8" in pyproject
        assert "boto3==1.40.21" in pyproject
        assert "botocore==1.40.76" in pyproject

    def test_provider_sdk_only_under_blobstore_package(self) -> None:
        approved = "src/aieos/domains/asset/infrastructure/blobstore"
        hits: list[str] = []
        for path in SRC_ROOT.rglob("*.py"):
            rel = path.relative_to(REPO_ROOT).as_posix()
            source = path.read_text(encoding="utf-8")
            if "import boto3" in source or "from botocore" in source or "import botocore" in source:
                if not rel.startswith(approved + "/"):
                    hits.append(rel)
        assert hits == []

    def test_docs_exist(self) -> None:
        text = DOC.read_text(encoding="utf-8")
        assert "NON_PRODUCTION" in text
        assert "ADR-AIEOS-033" in text
        assert "byte_size" in text
        assert "total_max_attempts" in text
        assert "ChecksumSHA256" in text
        assert "BlobInventory" in text
