"""HTTP header-contract errors. Not application/domain types."""


class PreconditionRequiredError(Exception):
    """If-Match is required for this mutation."""


class InvalidIfMatchError(Exception):
    """If-Match is not one strong AIEOS revision validator."""


class IdempotencyKeyRequiredError(Exception):
    """Idempotency-Key is required for this mutation."""


class InvalidIdempotencyKeyError(Exception):
    """Idempotency-Key failed the bounded contract."""
