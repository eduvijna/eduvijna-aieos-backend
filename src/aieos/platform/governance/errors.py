"""Platform governance errors (ADR-AIEOS-032). Not authorization."""

from __future__ import annotations


class GovernanceUnavailableError(Exception):
    """REQUIRED governance evaluation could not safely produce an answer.

    Not a business rejection. Maps to HTTP 503 governance_unavailable.
    """
