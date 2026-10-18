"""Outbound-URL safety (SSRF protection).

Untrusted URLs are never fetched directly. Media URLs are parsed offline
into (provider, id) and rebuilt canonically. The only network fetch of a
user-supplied URL is short-link expansion, which goes through
`follow_redirects_safely`: https only, default port, no credentials,
host on an explicit allowlist, every DNS answer public, and every redirect
hop re-validated. The allowlist means even DNS rebinding can only ever
reach the listed public hosts.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import httpx


class UnsafeURLError(ValueError):
    pass


def _host_allowed(host: str, allowed: set[str]) -> bool:
    host = host.lower().rstrip(".")
    return any(host == a or host.endswith("." + a) for a in allowed)


def is_public_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return not (
        addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast
        or addr.is_reserved or addr.is_unspecified
        or (isinstance(addr, ipaddress.IPv4Address) and addr in ipaddress.ip_network("100.64.0.0/10"))
    )


def validate_url_shape(url: str, allowed_hosts: set[str]) -> str:
    """Static checks; returns the lowercase host."""
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise UnsafeURLError("Malformed URL.") from exc
    if parts.scheme != "https":
        raise UnsafeURLError("Only https URLs are accepted.")
    if parts.username or parts.password:
        raise UnsafeURLError("URLs with credentials are not accepted.")
    host = (parts.hostname or "").lower()
    if not host:
        raise UnsafeURLError("URL has no host.")
    try:
        port = parts.port
    except ValueError as exc:
        raise UnsafeURLError("Invalid port.") from exc
    if port not in (None, 443):
        raise UnsafeURLError("Non-default ports are not accepted.")
    try:
        ipaddress.ip_address(host.strip("[]"))
        is_ip_literal = True
    except ValueError:
        is_ip_literal = False
    if is_ip_literal:
        raise UnsafeURLError("IP-literal hosts are not accepted.")
    if not _host_allowed(host, allowed_hosts):
        raise UnsafeURLError(f"Host '{host}' is not a supported media host.")
    return host


async def assert_resolves_public(host: str) -> None:
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, 443, 0, socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"Cannot resolve host '{host}'.") from exc
    addrs = {i[4][0] for i in infos}
    if not addrs or not all(is_public_ip(a) for a in addrs):
        raise UnsafeURLError(f"Host '{host}' resolves to a non-public address.")


async def follow_redirects_safely(
    url: str,
    allowed_hosts: set[str],
    *,
    max_hops: int = 5,
    timeout: float = 8.0,
    transport: httpx.AsyncBaseTransport | None = None,
    resolve_dns: bool = True,
) -> str:
    """Expand a short link; returns the final validated URL (no body read)."""
    current = url
    async with httpx.AsyncClient(timeout=timeout, transport=transport, follow_redirects=False) as client:
        for _ in range(max_hops + 1):
            host = validate_url_shape(current, allowed_hosts)
            if resolve_dns:
                await assert_resolves_public(host)
            resp = await client.head(current)
            if resp.status_code in (405, 403):  # some hosts refuse HEAD
                resp = await client.get(current, headers={"Range": "bytes=0-0"})
            location = resp.headers.get("location")
            if resp.status_code in (301, 302, 303, 307, 308) and location:
                current = urljoin(current, location)
                continue
            return current
    raise UnsafeURLError("Too many redirects.")
