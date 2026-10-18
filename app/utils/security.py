"""Security helpers: key generation, hashing, masking, redaction."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets

API_KEY_PREFIX = "mapi_"


def generate_api_key() -> str:
    """Generate a cryptographically random client API key: mapi_<secret>."""
    return f"{API_KEY_PREFIX}{secrets.token_urlsafe(32)}"


def hash_api_key(plaintext: str) -> str:
    """One-way hash for storage/lookup. High-entropy input -> SHA-256 is fine."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def mask_api_key(plaintext_or_hint: str) -> str:
    """Masked representation for list endpoints, e.g. mapi_****************7K2."""
    s = plaintext_or_hint or ""
    if s.startswith(API_KEY_PREFIX):
        tail = s[-3:] if len(s) >= 3 else "***"
    else:  # already just a tail hint
        tail = s[-3:] if len(s) >= 3 else "***"
    return f"{API_KEY_PREFIX}****************{tail}"


def constant_time_compare(a: str, b: str) -> bool:
    return hmac.compare_digest(a or "", b or "")


_SECRET_PATTERNS = [
    re.compile(r"(mapi_[A-Za-z0-9_\-]{6,})"),
    re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"(?i)(bearer\s+)([^\s,;]+)"),
    re.compile(r"(?i)(token\s*[:=]\s*)([^\s,;]+)"),
]


def redact_secrets(text: str) -> str:
    """Redact key-like material before logging."""
    if not text:
        return text
    redacted = text
    redacted = re.sub(r"mapi_[A-Za-z0-9_\-]+", "mapi_***REDACTED***", redacted)
    redacted = re.sub(r"(?i)(api[_-]?key\s*[:=]\s*)[^\s,;]+", r"\1***REDACTED***", redacted)
    redacted = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1***REDACTED***", redacted)
    redacted = re.sub(r"(?i)(oauth\s+)[^\s,;]+", r"\1***REDACTED***", redacted)
    redacted = re.sub(r"(?i)([?&](?:key|sig|token|api_key)=)[^&\s]+", r"\1***REDACTED***", redacted)
    return redacted


# --- Owner passwords (stdlib scrypt; no extra dependency) -------------------
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_hex, digest_hex = stored.split("$")
        if algo != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(digest_hex) // 2,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def generate_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# --- Signed, expiring links ---------------------------------------------------
_ephemeral_signing_secret = secrets.token_hex(32)


def signing_key() -> bytes:
    """SIGNING_SECRET, else derived from MASTER_API_SECRET, else per-process
    random (links then stop working after a restart — set one in prod)."""
    from app.config import get_settings

    s = get_settings()
    if s.signing_secret:
        return s.signing_secret.encode("utf-8")
    if s.master_api_secret:
        return hmac.new(s.master_api_secret.encode(), b"media-api-link-signing", hashlib.sha256).digest()
    return _ephemeral_signing_secret.encode()


def sign_value(value: str) -> str:
    return hmac.new(signing_key(), value.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_signature(value: str, signature: str) -> bool:
    return hmac.compare_digest(sign_value(value), signature or "")
