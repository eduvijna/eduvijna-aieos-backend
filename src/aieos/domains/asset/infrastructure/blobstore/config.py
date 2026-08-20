"""AIStor BlobStore configuration (infrastructure only).

NON_PRODUCTION implementation configuration. No production credentials,
endpoints, or insecure toggles are defined here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AiStorBlobStoreConfig:
    """Private HTTPS AIStor endpoint configuration for a future composition gate.

    Production factory semantics (from_config) require HTTPS and TLS verification.
    Tests inject a stubbed client and do not use insecure HTTP as a production option.
    """

    endpoint_url: str
    bucket: str
    region: str
    access_key_id: str
    secret_access_key: str
    connect_timeout_seconds: float = 5.0
    read_timeout_seconds: float = 60.0
    ca_bundle_path: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint_url, str) or not self.endpoint_url.strip():
            raise ValueError("endpoint_url must be a non-empty string")
        if not self.endpoint_url.lower().startswith("https://"):
            raise ValueError("endpoint_url must use HTTPS")
        if not isinstance(self.bucket, str) or not self.bucket.strip():
            raise ValueError("bucket must be a non-empty string")
        if not isinstance(self.region, str) or not self.region.strip():
            raise ValueError("region must be a non-empty string")
        if not isinstance(self.access_key_id, str) or not self.access_key_id.strip():
            raise ValueError("access_key_id must be a non-empty string")
        if (
            not isinstance(self.secret_access_key, str)
            or not self.secret_access_key.strip()
        ):
            raise ValueError("secret_access_key must be a non-empty string")
        if self.connect_timeout_seconds <= 0 or self.read_timeout_seconds <= 0:
            raise ValueError("timeouts must be positive")
        if self.ca_bundle_path is not None and (
            not isinstance(self.ca_bundle_path, str) or not self.ca_bundle_path.strip()
        ):
            raise ValueError("ca_bundle_path must be None or a non-empty string")
