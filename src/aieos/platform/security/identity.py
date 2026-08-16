"""Trusted request identity established by the authentication boundary.

This is not an Authorization Kernel and does not select an IdP, JWT, OIDC,
session, or capability model.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class TrustedRequestIdentity:
    """Immutable identity material established by authentication only.

    Contains ONLY principal_id. Roles, permissions, capabilities, tenant lists,
    authorization snapshots, tokens, cookies, and credentials are forbidden.
    """

    principal_id: UUID
