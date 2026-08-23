"""In-memory NATS JWT + NKey .creds material (ADR-AIEOS-046).

Parses credential material from memory only. Never writes .creds/.jwt/.nk/.seed
to the filesystem. Never embeds credential contents in exceptions or repr.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass

import nkeys

from aieos.platform.runtime.errors import RuntimeConfigurationError

_JWT_BEGIN = "-----BEGIN NATS USER JWT-----"
_JWT_END = "------END NATS USER JWT------"
_SEED_BEGIN = "-----BEGIN USER NKEY SEED-----"
_SEED_END = "------END USER NKEY SEED------"

_JWT_BLOCK = re.compile(
    re.escape(_JWT_BEGIN) + r"\s*(.*?)\s*" + re.escape(_JWT_END),
    re.DOTALL,
)
_SEED_BLOCK = re.compile(
    re.escape(_SEED_BEGIN) + r"\s*(.*?)\s*" + re.escape(_SEED_END),
    re.DOTALL,
)


class NatsCredentialError(RuntimeConfigurationError):
    """Secret-safe NATS credential configuration failure."""


def _extract_single(pattern: re.Pattern[str], material: str, label: str) -> str:
    matches = pattern.findall(material)
    if len(matches) == 0:
        raise NatsCredentialError(f"NATS credentials missing required {label} section")
    if len(matches) > 1:
        raise NatsCredentialError(f"NATS credentials contain duplicate {label} sections")
    value = matches[0].strip()
    if not value:
        raise NatsCredentialError(f"NATS credentials {label} section is empty")
    return value


@dataclass(slots=True)
class InMemoryNatsCredentials:
    """Parsed JWT + NKey seed held only in process memory."""

    _user_jwt: str
    _seed: bytearray

    @classmethod
    def parse(cls, material: str) -> InMemoryNatsCredentials:
        if material is None or not str(material).strip():
            raise NatsCredentialError("NATS credentials material is required and must be non-empty")
        text = str(material)
        jwt = _extract_single(_JWT_BLOCK, text, "user JWT")
        seed_text = _extract_single(_SEED_BLOCK, text, "user NKEY seed")
        # Seeds are ASCII; reject non-printable / whitespace-only noise after strip.
        if any(ord(ch) < 32 or ord(ch) > 126 for ch in seed_text):
            raise NatsCredentialError("NATS credentials user NKEY seed is malformed")
        seed = bytearray(seed_text.encode("ascii"))
        try:
            kp = nkeys.from_seed(bytes(seed))
            kp.wipe()
        except Exception:
            for i in range(len(seed)):
                seed[i] = 0
            raise NatsCredentialError("NATS credentials user NKEY seed is malformed") from None
        return cls(_user_jwt=jwt, _seed=seed)

    def user_jwt_cb(self) -> bytes:
        # nats-py 2.15 invokes this synchronously during CONNECT.
        return self._user_jwt.encode("utf-8")

    def signature_cb(self, nonce: str | bytes) -> bytes:
        # nats-py 2.15: Callable[[str], bytes] — sync, base64-encoded signature.
        challenge = nonce.encode("utf-8") if isinstance(nonce, str) else nonce
        kp = nkeys.from_seed(bytes(self._seed))
        try:
            signature = kp.sign(challenge)
        finally:
            kp.wipe()
        return base64.b64encode(signature)

    def wipe(self) -> None:
        for i in range(len(self._seed)):
            self._seed[i] = 0

    def __repr__(self) -> str:
        return "InMemoryNatsCredentials(<redacted>)"

    def __str__(self) -> str:
        return self.__repr__()

    def __del__(self) -> None:
        try:
            self.wipe()
        except Exception:
            pass
