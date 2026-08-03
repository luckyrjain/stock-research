"""Symmetric encryption for credentials this codebase must store and later
read back (broker API access tokens) — distinct from the hash-only
storage `auth.py` uses for magic links/sessions/API keys, which never need
to be recovered, only compared. No prior encryption-at-rest precedent
exists anywhere else in this codebase; this is the first.

Requires PORTFOLIO_ENCRYPTION_KEY (a Fernet key — generate one with
`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`).
Unset is a hard failure at the call site, not a silent plaintext fallback:
a broker access token is credential material that can place real trades,
so "misconfigured" must not degrade to "stored in the clear."
"""

import os

from cryptography.fernet import Fernet, InvalidToken


class EncryptionNotConfigured(RuntimeError):
    pass


def _get_fernet() -> Fernet:
    key = os.environ.get("PORTFOLIO_ENCRYPTION_KEY")
    if not key:
        raise EncryptionNotConfigured(
            "PORTFOLIO_ENCRYPTION_KEY is not set. Broker credentials are never stored in "
            "plaintext — generate a key with `python -c \"from cryptography.fernet import "
            "Fernet; print(Fernet.generate_key().decode())\"` and set it before connecting a "
            "broker account."
        )
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise EncryptionNotConfigured(
            f"PORTFOLIO_ENCRYPTION_KEY is set but not a valid Fernet key: {exc}"
        ) from exc


def encrypt(plaintext: str) -> str:
    """Returns an opaque, ASCII-safe ciphertext string for storage."""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Raises EncryptionNotConfigured (bad/missing key) or InvalidToken
    (ciphertext doesn't match the current key, e.g. rotated) — callers
    should treat either as "this stored credential can no longer be read,
    the connection needs to be re-established," never guess or degrade."""
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        raise
