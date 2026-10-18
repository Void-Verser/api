"""Shared upstream HTTP helper: timeouts + status -> provider error mapping."""

from __future__ import annotations

from typing import Any

import httpx

from app.providers.base import (
    ProviderBadResponseError,
    ProviderInvalidQueryError,
    ProviderNotFoundError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


async def request_json(
    provider: str,
    method: str,
    url: str,
    *,
    timeout: float,
    transport: httpx.AsyncBaseTransport | None = None,
    **kwargs: Any,
) -> Any:
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
            resp = await client.request(method, url, **kwargs)
    except httpx.TimeoutException as exc:
        raise ProviderTimeoutError(f"{provider} timed out") from exc
    except httpx.HTTPError as exc:
        raise ProviderUnavailableError(f"{provider} unreachable: {type(exc).__name__}") from exc
    if resp.status_code == 404:
        raise ProviderNotFoundError(f"Media not found on {provider}.")
    if resp.status_code == 400:
        raise ProviderInvalidQueryError(f"{provider} rejected the request as invalid.")
    if resp.status_code in (401, 403):
        raise ProviderUnavailableError(f"{provider} rejected our credentials (HTTP {resp.status_code}).")
    if resp.status_code == 429:
        raise ProviderUnavailableError(f"{provider} rate limit reached; try again later.")
    if resp.status_code >= 500:
        raise ProviderUnavailableError(f"{provider} upstream error {resp.status_code}.")
    if resp.status_code >= 300:
        raise ProviderBadResponseError(f"{provider} unexpected status {resp.status_code}.")
    try:
        return resp.json()
    except ValueError as exc:
        raise ProviderBadResponseError(f"{provider} returned malformed JSON.") from exc


def as_int(value: Any) -> int | None:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None
