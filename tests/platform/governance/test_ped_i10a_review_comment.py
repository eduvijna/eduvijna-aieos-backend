"""PED-I10A DeterministicReviewCommentPolicyV1 unit tests."""

from __future__ import annotations

from uuid import uuid7

import pytest

from aieos.domains.content.application.errors import ReviewCommentRejected
from aieos.platform.governance.review_comment import (
    REVIEW_COMMENT_POLICY_V1,
    DeterministicReviewCommentPolicyV1,
)

pytestmark = pytest.mark.ped_i10a

POLICY = DeterministicReviewCommentPolicyV1()

# Classic Visa test PAN with valid Luhn; used only as rejection fixture.
_VALID_LUHN_CARD = "4111 1111 1111 1111"
_INVALID_LUHN_CARD = "4111 1111 1111 1112"
_SECRET = "sk_live_SUPERSECRETVALUE99"


def _assert_rejected(comment: str) -> None:
    with pytest.raises(ReviewCommentRejected) as caught:
        POLICY.evaluate(comment)
    message = str(caught.value)
    assert message == "review comment rejected"
    assert _SECRET not in message
    assert "PRIVATE KEY" not in message
    assert "4111" not in message
    assert "example.com" not in message
    assert "reviewer@" not in message


class TestReviewCommentPolicyAllowed:
    def test_none_and_normal(self) -> None:
        POLICY.evaluate(None)
        POLICY.evaluate("Please clarify the worked example on page 3.")
        POLICY.evaluate("the password field is unclear")
        POLICY.evaluate("tokenization is incorrect")
        POLICY.evaluate("explain API key rotation")
        POLICY.evaluate(str(uuid7()))
        POLICY.evaluate(f"answer equals {_INVALID_LUHN_CARD}")
        POLICY.evaluate("the bearer of this certificate must sign")

    def test_policy_identity_and_determinism(self) -> None:
        assert POLICY.policy_id == REVIEW_COMMENT_POLICY_V1
        assert POLICY.policy_id == "review_comment_policy.v1"
        comment = f"Authorization: Bearer {_SECRET}"
        with pytest.raises(ReviewCommentRejected):
            POLICY.evaluate(comment)
        with pytest.raises(ReviewCommentRejected):
            POLICY.evaluate(comment)


class TestReviewCommentPolicyRejected:
    @pytest.mark.parametrize(
        "comment",
        [
            "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA",
            "-----BEGIN EC PRIVATE KEY-----\nabc",
            "-----BEGIN OPENSSH PRIVATE KEY-----\nabc",
            "-----BEGIN PRIVATE KEY-----\nabc",
            f"Authorization: Bearer {_SECRET}",
            f"Bearer {_SECRET}",
            f"api_key={_SECRET}",
            f"api-key: {_SECRET}",
            f"password: {_SECRET}",
            f"passwd={_SECRET}",
            f"client_secret={_SECRET}",
            f"client-secret: {_SECRET}",
            f"access_token={_SECRET}",
            f"secret={_SECRET}",
            f"https://user:{_SECRET}@example.com/path",
            f"card {_VALID_LUHN_CARD}",
            "Aadhaar: 2345 6789 0123",
            "Aadhar = 234567890123",
            "SSN: 123-45-6789",
            "Social Security: 123-45-6789",
            "passport: AB1234567",
            "national id: X9Y8Z7W6",
            "tax id: 12-3456789",
            "PAN: ABCDE1234F",
            "Contact me at reviewer@example.com please",
            "phone: +1 555-123-4567",
            "mobile: 9876543210",
            "contact number: (555) 111-2222",
        ],
    )
    def test_rejects_sensitive(self, comment: str) -> None:
        _assert_rejected(comment)

    def test_no_redaction_or_rewrite(self) -> None:
        original = f"password: {_SECRET}"
        with pytest.raises(ReviewCommentRejected):
            POLICY.evaluate(original)
        assert original == f"password: {_SECRET}"
