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

# B. Bearer / access token material
# Explicit Authorization header: any nontrivial non-whitespace credential token.
_AUTH_HEADER_BEARER = re.compile(
    r"(?i)\bAuthorization\s*:\s*Bearer\s+\S+"
)
# Bare Bearer: require credential-like evidence (digit or token punctuation).
_BARE_BEARER_CREDENTIAL = re.compile(
    r"(?i)(?<![A-Za-z0-9_])Bearer\s+"
    r"(?=[A-Za-z0-9._\-+/=]*[0-9._\-+/=])"
    r"[A-Za-z0-9._\-+/=]{8,}"
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

# F. Label-anchored government / national identifiers (bounded, label-specific)
_AADHAAR = re.compile(
    r"(?i)\baadhaa?r\b\s*[:=#-]?\s*(?:\d[\s-]*){11}\d(?!\d)"
)
_SSN = re.compile(
    r"(?i)\b(?:ssn|social\s+security(?:\s+number)?)\b\s*[:=#-]?\s*"
    r"\d{3}[\s-]?\d{2}[\s-]?\d{4}\b"
)
_PASSPORT = re.compile(
    r"(?i)\bpassport\b\s*[:=#-]?\s*(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{6,}\b"
)
_PAN = re.compile(
    r"(?i)\bpan\b\s*[:=#-]?\s*[A-Za-z]{5}\d{4}[A-Za-z]\b"
)
_NATIONAL_ID = re.compile(
    r"(?i)\bnational\s+id\b\s*[:=#-]?\s*"
    r"(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{4,}\b"
)
_TAX_ID = re.compile(
    r"(?i)\btax\s+id\b\s*[:=#-]?\s*"
    r"(?=\d)(?:\d[\d\s-]*){2,}\d\b"
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


def _contains_gov_id(text: str) -> bool:
    return bool(
        _AADHAAR.search(text)
        or _SSN.search(text)
        or _PASSPORT.search(text)
        or _PAN.search(text)
        or _NATIONAL_ID.search(text)
        or _TAX_ID.search(text)
    )


def _is_prohibited(comment: str) -> bool:
    if _PRIVATE_KEY.search(comment):
        return True
    if _AUTH_HEADER_BEARER.search(comment):
        return True
    if _BARE_BEARER_CREDENTIAL.search(comment):
        return True
    if _LABELED_SECRET.search(comment):
        return True
    if _CREDENTIAL_URI.search(comment):
        return True
    if _contains_luhn_card(comment):
        return True
    if _contains_gov_id(comment):
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
