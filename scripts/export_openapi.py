"""Export the deterministic AIEOS OpenAPI 3.1 snapshot. No production runtime."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.domain.schema import ContentSchemaRegistry
from aieos.platform.api.app import create_app
from tests.fakes import AllowClassroomAssessmentAuthorization
from aieos.platform.api.openapi import build_openapi, canonical_openapi_json
from aieos.platform.security.context import TrustedSecurityContext
from aieos.platform.security.identity import TrustedRequestIdentity

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "contracts" / "openapi" / "aieos-v1.json"


class _UnusedUowFactory:
    def __call__(self, execution_tenant_id):
        raise AssertionError("OpenAPI export must not touch persistence")


class _ExportOnlyRequestIdentityAuthenticator:
    """Deterministic export/test-only adapter. Not production authentication."""

    def authenticate(self, request) -> TrustedRequestIdentity:
        return TrustedRequestIdentity(principal_id=uuid4())


class _ExportResolver:
    def resolve(self, *, identity, requested_tenant_id):
        return TrustedSecurityContext(tenant_id=uuid4(), principal_id=identity.principal_id)


class _ExportReviewAuthorization:
    def authorize(self, **kwargs) -> None:
        return None


class _ExportReviewCommentPolicy:
    def evaluate(self, comment: str | None) -> None:
        return None


class _ExportPublicationAuthorization:
    def authorize(self, **kwargs) -> None:
        return None


class _ExportPublicationGovernance:
    def evaluate(self, **kwargs) -> None:
        return None


class _ExportAssetReferenceValidation:
    def validate_binding(self, **kwargs) -> None:
        return None


class _ExportAssetCurrentGovernance:
    def validate_current_use(self, **kwargs) -> None:
        return None


def main() -> None:
    app = create_app(
        uow_factory=_UnusedUowFactory(),
        teaching_uow_factory=_UnusedUowFactory(),
        assessment_uow_factory=_UnusedUowFactory(),
        assessment_authorization=AllowClassroomAssessmentAuthorization(),
        request_identity_authenticator=_ExportOnlyRequestIdentityAuthenticator(),
        security_resolver=_ExportResolver(),
        content_types=StaticContentTypeCatalog({"test.generic"}),
        cursor_signing_key=b"gci-i04-openapi-export-key",
        schema_registry=ContentSchemaRegistry(),
        idempotency_retention=timedelta(hours=24),
        review_authorization=_ExportReviewAuthorization(),
        review_comment_policy=_ExportReviewCommentPolicy(),
        publication_authorization=_ExportPublicationAuthorization(),
        publication_governance=_ExportPublicationGovernance(),
        asset_reference_validation=_ExportAssetReferenceValidation(),
        asset_current_governance=_ExportAssetCurrentGovernance(),
    )
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(
        canonical_openapi_json(build_openapi(app)),
        encoding="utf-8",
        newline="\n",
    )
    print(SNAPSHOT)


if __name__ == "__main__":
    main()
