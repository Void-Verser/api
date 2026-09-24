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
    return redacted
