"""Export the deterministic AIEOS OpenAPI 3.1 snapshot. No production runtime."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.platform.api.app import create_app
from aieos.platform.api.openapi import build_openapi, canonical_openapi_json
from aieos.platform.security.context import TrustedSecurityContext

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "contracts" / "openapi" / "aieos-v1.json"


class _UnusedUowFactory:
    def __call__(self, execution_tenant_id):
        raise AssertionError("OpenAPI export must not touch persistence")


class _ExportResolver:
    def resolve(self, requested_tenant_id):
        return TrustedSecurityContext(tenant_id=uuid4(), principal_id=uuid4())


def main() -> None:
    app = create_app(
        uow_factory=_UnusedUowFactory(),
        security_resolver=_ExportResolver(),
        content_types=StaticContentTypeCatalog({"test.generic"}),
        cursor_signing_key=b"gci-i04-openapi-export-key",
    )
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(canonical_openapi_json(build_openapi(app)), encoding="utf-8")
    print(SNAPSHOT)


if __name__ == "__main__":
    main()
