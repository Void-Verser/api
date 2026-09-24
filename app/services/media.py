"""Small media helpers: id parsing, provider hints."""

from __future__ import annotations


def split_media_id(media_id: str) -> tuple[str | None, str]:
    """Split 'provider:rest' hints. Returns (provider_hint|None, remainder)."""
    if ":" in media_id:
        hint, rest = media_id.split(":", 1)
        hint = hint.strip().lower()
        if hint and rest:
            return hint, rest
    return None, media_id
