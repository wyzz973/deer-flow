"""Site icons come through the gateway: public hosts only, raster bytes only, cached."""

import sys
import types

import httpx
import pytest
from fastapi import FastAPI

from deepresearch.api import build_router
from deepresearch.favicons import Favicons, image_type, normalize_domain
from deepresearch.service import ResearchService
from deepresearch.store import Store

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
ICO = b"\x00\x00\x01\x00" + b"\x01" * 32


def test_domains_are_hostnames_and_icons_are_recognised_by_their_bytes():
    assert normalize_domain("www.Docs.GitLab.com.") == "docs.gitlab.com"
    for value in ["127.0.0.1", "localhost", "exa mple.com", "a/b.com", "-bad.com", "http://x.com", "", "x" * 300 + ".com"]:
        assert normalize_domain(value) is None, value
    assert image_type(PNG) == "image/png" and image_type(ICO) == "image/x-icon"
    assert image_type(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert image_type(b"<svg xmlns='http://www.w3.org/2000/svg'><script/></svg>") is None
    assert image_type(b"<!doctype html><title>Not an icon</title>") is None


@pytest.mark.asyncio
async def test_icons_follow_checked_redirects_fall_back_to_the_site_and_are_cached(tmp_path):
    store = Store(tmp_path / "icons.sqlite")
    await store.start()
    requested, checked = [], []

    def handler(request):
        requested.append(str(request.url))
        if request.url.host == "docs.example.com":
            return httpx.Response(404)
        if request.url.path == "/favicon.ico":
            return httpx.Response(301, headers={"location": "https://cdn.example.com/icon.png"})
        # A wrong Content-Type does not matter; the bytes decide.
        return httpx.Response(200, content=PNG, headers={"content-type": "text/html"})

    def validate(url):
        checked.append(url)

    icons = Favicons(store, transport=httpx.MockTransport(handler), validate=validate)
    icon = await icons.get("docs.example.com")
    assert icon["content_type"] == "image/png" and icon["body"] == PNG
    assert requested == ["https://docs.example.com/favicon.ico", "https://docs.example.com/", "https://example.com/favicon.ico", "https://cdn.example.com/icon.png"]
    assert checked == requested  # every hop is screened before it is requested
    assert (await icons.get("docs.example.com"))["body"] == PNG
    assert len(requested) == 4


@pytest.mark.asyncio
async def test_icons_declared_in_the_home_page_are_used_but_never_svg(tmp_path):
    store = Store(tmp_path / "icons.sqlite")
    await store.start()
    requested = []
    page = '<html><head><link rel=icon href=/favicon.svg type=image/svg+xml><link rel=\'shortcut icon\' href=\'data:image/png;base64,AAAA\'><link rel="icon" type="image/x-icon" href="/favicon.png"></head></html>'

    def handler(request):
        requested.append(request.url.path)
        if request.url.path == "/":
            return httpx.Response(200, text=page, headers={"content-type": "text/html"})
        if request.url.path == "/favicon.png":
            return httpx.Response(200, content=PNG)
        return httpx.Response(404)

    icons = Favicons(store, transport=httpx.MockTransport(handler), validate=lambda url: None)
    icon = await icons.get("typesense.org")
    assert icon["content_type"] == "image/png"
    assert requested == ["/favicon.ico", "/", "/favicon.png"]


@pytest.mark.asyncio
async def test_a_slow_site_does_not_hold_the_request_and_finishes_in_the_background(tmp_path):
    import asyncio

    store = Store(tmp_path / "icons.sqlite")
    await store.start()
    release = asyncio.Event()

    async def slow(request):
        await release.wait()
        return httpx.Response(200, content=PNG)

    icons = Favicons(store, transport=httpx.MockTransport(slow), validate=lambda url: None, wait_seconds=0.05)
    pending = await icons.get("slow.example.com")
    assert pending["body"] is None and pending["pending"] is True
    release.set()
    for _ in range(100):
        cached = await store.favicon("slow.example.com")
        if cached:
            break
        await asyncio.sleep(0.01)
    assert (await icons.get("slow.example.com"))["body"] == PNG


@pytest.mark.asyncio
async def test_private_hosts_scripts_and_oversized_bodies_are_cached_misses(tmp_path):
    store = Store(tmp_path / "icons.sqlite")
    await store.start()
    requested = []

    def handler(request):
        requested.append(request.url.host)
        if request.url.host == "svg.example.com":
            return httpx.Response(200, content=b"<svg onload='alert(1)'></svg>")
        if request.url.host == "big.example.com":
            return httpx.Response(200, content=PNG + b"\x00" * 300_000)
        return httpx.Response(302, headers={"location": "http://10.0.0.5/favicon.ico"})

    def validate(url):
        return "Error: private address" if "10.0.0.5" in url else None

    icons = Favicons(store, transport=httpx.MockTransport(handler), validate=validate)
    for domain in ["svg.example.com", "big.example.com", "redirect.example.com"]:
        assert (await icons.get(domain))["body"] is None, domain
    assert "10.0.0.5" not in requested
    count = len(requested)
    assert (await icons.get("svg.example.com"))["body"] is None and len(requested) == count

    def offline(request):
        raise AssertionError("disabled icons must not reach the network")

    disabled = Favicons(store, enabled=False, transport=httpx.MockTransport(offline))
    assert (await disabled.get("new.example.com"))["body"] is None


@pytest.mark.asyncio
async def test_favicon_route_requires_a_user_and_serves_images_with_safe_headers(settings, monkeypatch):
    service = ResearchService(settings)
    await service.store.start()

    def resolver(request):
        return types.SimpleNamespace(user_id="alice") if request.headers.get("x-test-user") else None

    monkeypatch.setitem(sys.modules, "deerflow_extension_api", types.SimpleNamespace(resolve_principal=resolver))
    looked_up = []

    async def get(domain):
        looked_up.append(domain)
        hit = domain == "docs.example.com"
        return {"domain": domain, "body": PNG if hit else None, "content_type": "image/png" if hit else None, "pending": domain == "slow.example.com"}

    monkeypatch.setattr(service.favicons, "get", get)
    app = FastAPI()
    app.include_router(build_router(service))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
        user = {"x-test-user": "alice"}
        assert (await client.get("/api/deepresearch/favicon?domain=docs.example.com")).status_code == 401
        hit = await client.get("/api/deepresearch/favicon?domain=www.docs.example.com", headers=user)
        assert hit.status_code == 200 and hit.content == PNG and hit.headers["content-type"] == "image/png"
        assert hit.headers["x-content-type-options"] == "nosniff"
        assert "default-src 'none'" in hit.headers["content-security-policy"]
        assert "max-age=" in hit.headers["cache-control"]
        miss = await client.get("/api/deepresearch/favicon?domain=missing.example.com", headers=user)
        assert miss.status_code == 404 and "max-age=" in miss.headers["cache-control"]
        # Still being fetched: the browser must ask again rather than cache a miss.
        pending = await client.get("/api/deepresearch/favicon?domain=slow.example.com", headers=user)
        assert pending.status_code == 404 and pending.headers["cache-control"] == "no-store"
        assert (await client.get("/api/deepresearch/favicon?domain=127.0.0.1", headers=user)).status_code == 422
    assert looked_up == ["docs.example.com", "missing.example.com", "slow.example.com"]
