"""Fetching URLs from the internet safely.

Every feed URL (and every redirect it leads to) must be http(s) and resolve only to public IP addresses,
so a bad provider entry can't make the server talk to itself, the local network or a cloud metadata
service. Responses are size- and time-limited.

Known limit: the address is checked, then httpx resolves the name again to connect. A DNS server that
answers differently the second time (DNS rebinding) could slip past; run the bot on a host whose
private services need authentication anyway.
"""
import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

import config

MAX_REDIRECTS = 5
ALLOWED_PORTS = {None, 80, 443, 8080, 8443}


class UnsafeURL(ValueError):
    pass


class TooLarge(ValueError):
    pass


@dataclass
class FetchResponse:
    status_code: int
    url: str
    headers: dict
    content: bytes


def check_url_syntax(url: str, https_only: bool = False) -> str:
    """Basic checks that need no network: scheme, host, port, no credentials, no IP-literal private hosts.
    Returns the URL, or raises UnsafeURL."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError as e:
        raise UnsafeURL(f"bad URL: {e}") from None
    if parts.scheme not in (("https",) if https_only else ("http", "https")):
        raise UnsafeURL(f"scheme not allowed: {parts.scheme or '(none)'}")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise UnsafeURL("no host")
    if parts.username or parts.password:
        raise UnsafeURL("credentials in URL")
    if port not in ALLOWED_PORTS:
        raise UnsafeURL(f"port not allowed: {port}")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan", ".home.arpa")):
        raise UnsafeURL(f"local host name: {host}")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host:
            raise UnsafeURL(f"single-label host: {host}") from None
        return url.strip()
    if not _is_public(ip):
        raise UnsafeURL(f"non-public address: {ip}")
    return url.strip()


def _is_public(ip: ipaddress._BaseAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


async def resolve_public(host: str, port: int) -> None:
    """Raise UnsafeURL unless every address the host resolves to is public."""
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise UnsafeURL(f"cannot resolve {host}: {e}") from None
    addresses = {info[4][0] for info in infos}
    if not addresses:
        raise UnsafeURL(f"no addresses for {host}")
    for address in addresses:
        if not _is_public(ipaddress.ip_address(address.split("%")[0])):
            raise UnsafeURL(f"{host} resolves to a non-public address ({address})")


async def safe_get(url: str, *, headers: dict | None = None, timeout: float | None = None,
                   max_bytes: int | None = None, client: httpx.AsyncClient | None = None) -> FetchResponse:
    """GET a URL, re-checking every redirect. Raises UnsafeURL, TooLarge or httpx errors."""
    timeout = timeout or config.FETCH_TIMEOUT_SECONDS
    max_bytes = max_bytes or config.FETCH_MAX_BYTES
    headers = {"User-Agent": config.USER_AGENT, **(headers or {})}
    own_client = client is None
    client = client or httpx.AsyncClient(follow_redirects=False, timeout=timeout)
    try:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            check_url_syntax(current)
            parts = urlsplit(current)
            await resolve_public(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
            async with client.stream("GET", current, headers=headers, timeout=timeout) as response:
                if response.status_code in (301, 302, 303, 307, 308) and "location" in response.headers:
                    current = urljoin(current, response.headers["location"])
                    continue
                declared = response.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > max_bytes:
                    raise TooLarge(f"response is {declared} bytes (limit {max_bytes})")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) > max_bytes:
                        raise TooLarge(f"response is over {max_bytes} bytes")
                return FetchResponse(response.status_code, str(response.url), dict(response.headers), bytes(body))
        raise UnsafeURL(f"more than {MAX_REDIRECTS} redirects")
    finally:
        if own_client:
            await client.aclose()
