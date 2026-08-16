"""Deterministic Review Comment Policy V1 (ADR-AIEOS-032)."""

from __future__ import annotations

import re

from aieos.domains.content.application.errors import ReviewCommentRejected

REVIEW_COMMENT_POLICY_V1 = "review_comment_policy.v1"

_GENERIC_REJECTION = "review comment rejected"

# A. Private key PEM / OpenSSH / PKCS8 material
_PRIVATE_KEY = re.compile(
    r"-----BEGIN(?:\s+[A-Z0-9][A-Z0-9\s-]*)?\s+PRIVATE KEY-----",
    re.IGNORECASE,
)

# B. Bearer / access token material (not bare prose "bearer")
_AUTH_BEARER = re.compile(
    r"(?i)(?:Authorization\s*:\s*)?Bearer\s+[A-Za-z0-9._\-+/=]{8,}"
)

# C. Labeled secret / credential assignments
_SECRET_LABEL = (
    r"(?:api[\s_-]?key|access[\s_-]?token|password|passwd|"
    r"client[\s_-]?secret|(?<![A-Za-z0-9_])secret(?![A-Za-z0-9_]))"
)
_LABELED_SECRET = re.compile(
    rf"(?i)\b{_SECRET_LABEL}\b\s*[:=]\s*\S+"
)

# D. Credential-bearing URI userinfo
_CREDENTIAL_URI = re.compile(
    r"(?i)[a-z][a-z0-9+.-]*://[^\s/@:]+:[^\s/@]+@"
)

# E. Payment card-like digit runs (spaces/hyphens allowed); Luhn applied later
_CARD_CANDIDATE = re.compile(r"(?<!\d)(?:\d[ -]*?){13,19}(?!\d)")

# F. Label-anchored government / national identifiers
_GOV_LABEL = (
    r"(?:aadhaa?r|ssn|social\s+security(?:\s+number)?|passport|"
    r"national\s+id|tax\s+id|\bpan\b)"
)
_GOV_ID = re.compile(
    rf"(?i)\b{_GOV_LABEL}\b\s*[:=#-]?\s*[A-Za-z0-9][A-Za-z0-9\s-]{{3,}}"
)

# G. Direct contact data
_EMAIL = re.compile(
    r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"
)
_PHONE_LABELED = re.compile(
    r"(?i)\b(?:phone|mobile|contact(?:\s+number)?)\b\s*[:=]?\s*"
    r"(?:\+?\(?\d[\d\s\-()]{5,}\d)"
)


def _luhn_valid(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = ord(char) - 48
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _contains_luhn_card(text: str) -> bool:
    for match in _CARD_CANDIDATE.finditer(text):
        digits = re.sub(r"\D", "", match.group(0))
        if 13 <= len(digits) <= 19 and _luhn_valid(digits):
            return True
    return False


def _is_prohibited(comment: str) -> bool:
    if _PRIVATE_KEY.search(comment):
        return True
    if _AUTH_BEARER.search(comment):
        return True
    if _LABELED_SECRET.search(comment):
        return True
    if _CREDENTIAL_URI.search(comment):
        return True
    if _contains_luhn_card(comment):
        return True
    if _GOV_ID.search(comment):
        return True
    if _EMAIL.search(comment):
        return True
    if _PHONE_LABELED.search(comment):
        return True
    return False


class DeterministicReviewCommentPolicyV1:
    """Embedded, deterministic, synchronous review-comment governance V1."""

    policy_id = REVIEW_COMMENT_POLICY_V1

    def evaluate(self, comment: str | None) -> None:
        if comment is None:
            return
        if not isinstance(comment, str):
            raise ReviewCommentRejected(_GENERIC_REJECTION)
        if _is_prohibited(comment):
            raise ReviewCommentRejected(_GENERIC_REJECTION)
