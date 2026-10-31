from __future__ import annotations

import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()  # Argon2id with library defaults
_DUMMY_HASH = _hasher.hash("clearpocket-dummy-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str | None, password: str) -> bool:
    """Verifies against a dummy hash when the user doesn't exist (timing parity)."""
    try:
        return _hasher.verify(stored_hash or _DUMMY_HASH, password) and stored_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    return _hasher.check_needs_rehash(stored_hash)


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def csrf_token_for(session_token: str, secret_key: str) -> str:
    """Stateless per-session CSRF token: HMAC(secret, session token)."""
    return hmac.new(secret_key.encode(), b"csrf:" + session_token.encode(), hashlib.sha256).hexdigest()


def tokens_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def identifier_hash(value: str, secret_key: str) -> str:
    """Keyed hash for rate-limit buckets so raw emails/IPs aren't stored."""
    return hmac.new(secret_key.encode(), value.encode(), hashlib.sha256).hexdigest()[:32]
