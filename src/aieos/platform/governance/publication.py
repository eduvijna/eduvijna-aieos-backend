"""Baseline Publication Governance V1 (ADR-AIEOS-032).

Explicit versioned production baseline with no additional stateful
publication-specific restrictions beyond first-class gates elsewhere.
"""

from __future__ import annotations

from uuid import UUID

from aieos.domains.content.domain.identities import ContentId, ContentVersionId

PUBLICATION_GOVERNANCE_V1 = "publication_governance.v1"


class BaselinePublicationGovernanceV1:
    """Production PublicationGovernancePort V1 — not a permissive test fake."""

    policy_id = PUBLICATION_GOVERNANCE_V1

    def evaluate(
        self,
        *,
        tenant_id: UUID,
        content_id: ContentId,
        version_id: ContentVersionId,
    ) -> None:
        _ = (tenant_id, content_id, version_id)
        return None
