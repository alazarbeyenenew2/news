"""SSRF protection for feed fetching."""
import asyncio
import socket

import httpx
import pytest

import netsafe
from netsafe import TooLarge, UnsafeURL, check_url_syntax, safe_get


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "ftp://example.com/x", "gopher://example.com",
    "http://127.0.0.1/", "http://10.0.0.5/feed", "http://192.168.1.1/", "http://169.254.169.254/latest",
    "http://[::1]/", "http://[::ffff:127.0.0.1]/", "http://0.0.0.0/", "http://localhost/feed",
    "http://printer.local/", "http://intranet/", "https://user:pass@example.com/", "http://example.com:22/",
])
def test_unsafe_urls_rejected(url):
    with pytest.raises(UnsafeURL):
        check_url_syntax(url)


@pytest.mark.parametrize("url", ["https://feeds.bbci.co.uk/news/rss.xml", "http://example.com:8080/feed",
                                 "https://93.184.216.34/feed"])
def test_public_urls_allowed(url):
    assert check_url_syntax(url) == url


def _fake_dns(mapping):
    async def getaddrinfo(host, port, type=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (mapping[host], port))]
    return getaddrinfo


def test_hostname_resolving_to_private_address_rejected(monkeypatch):
    loop = asyncio.new_event_loop()
    monkeypatch.setattr(loop, "getaddrinfo", _fake_dns({"evil.example.com": "10.1.2.3"}))
    with pytest.raises(UnsafeURL):
        loop.run_until_complete(netsafe.resolve_public("evil.example.com", 443))
    loop.close()


def test_redirect_to_private_address_rejected(monkeypatch):
    async def run():
        asyncio.get_running_loop().getaddrinfo = _fake_dns(
            {"feeds.example.com": "93.184.216.34", "internal.example.com": "192.168.0.10"})

        def handler(request: httpx.Request):
            if request.url.host == "feeds.example.com":
                return httpx.Response(302, headers={"location": "http://internal.example.com/admin"})
            return httpx.Response(200, content=b"secret")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(UnsafeURL):
                await safe_get("https://feeds.example.com/rss", client=client)
    asyncio.run(run())


def test_size_limit(monkeypatch):
    async def run():
        asyncio.get_running_loop().getaddrinfo = _fake_dns({"feeds.example.com": "93.184.216.34"})
        handler = lambda request: httpx.Response(200, content=b"x" * 5000)  # noqa: E731
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(TooLarge):
                await safe_get("https://feeds.example.com/rss", client=client, max_bytes=1000)
            ok = await safe_get("https://feeds.example.com/rss", client=client, max_bytes=10000)
            assert ok.status_code == 200 and len(ok.content) == 5000
    asyncio.run(run())
