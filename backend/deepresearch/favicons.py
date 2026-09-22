"""Site icons for cited domains, fetched by the gateway.

The browser only asks this service, so opening a report does not contact every
cited site. Outbound requests use the same public-address screening as native
web tools on every redirect hop, accept only raster images recognised by their
bytes (never SVG, HTML or scripts, whatever the Content-Type says), bound size
and time, and cache hits and misses. DNS is resolved again by the HTTP client,
so this carries the same rebinding caveat as the native fetch tools.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from urllib.parse import urljoin

logger = logging.getLogger(__name__)

HOSTNAME = re.compile(r"^(?=.{4,253}$)(?:(?!-)[a-z0-9-]{1,63}(?<!-)\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]$")
SIGNATURES = ((b"\x00\x00\x01\x00", "image/x-icon"), (b"\x89PNG\r\n\x1a\n", "image/png"), (b"GIF87a", "image/gif"), (b"GIF89a", "image/gif"), (b"\xff\xd8\xff", "image/jpeg"))
MAX_BYTES = 256_000
MAX_REDIRECTS = 3
MAX_DECLARED = 3
LINK = re.compile(r"<link\b[^>]*>", re.I)
ATTRIBUTE = re.compile(r"""([a-zA-Z_:-]+)\s*=\s*("[^"]*"|'[^']*'|[^\s"'>]+)""")
HIT_SECONDS = 7 * 86400
MISS_SECONDS = 86400


def normalize_domain(value):
    host = (value or "").strip().lower().rstrip(".").removeprefix("www.")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    return host if HOSTNAME.fullmatch(host) else None


def image_type(body):
    for signature, kind in SIGNATURES:
        if body.startswith(signature):
            return kind
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "image/webp"
    return None


def declared_icons(page, base):
    """Raster icon URLs a home page declares, in order; SVG and data URIs are skipped."""
    found = []
    for tag in LINK.findall(page or ""):
        attributes = {name.lower(): value.strip("\"'") for name, value in ATTRIBUTE.findall(tag)}
        href = attributes.get("href", "").strip()
        if "icon" not in attributes.get("rel", "").lower() or not href or href.lower().startswith("data:"):
            continue
        if attributes.get("type", "").lower() == "image/svg+xml" or href.lower().split("?")[0].endswith(".svg"):
            continue
        url = urljoin(base, href)
        if url.startswith(("https://", "http://")) and url not in found:
            found.append(url)
    return found


def _screen(url, **allowance):
    from deerflow.community.url_safety import validate_public_http_url

    return validate_public_http_url(url, action="fetch a site icon", **allowance)


def _same_site(url, domain):
    """A site may declare an icon for itself, and for nobody else."""
    from urllib.parse import urlsplit

    host = (urlsplit(url).hostname or "").removeprefix("www.").lower()
    return bool(host) and host == domain and url.startswith(("https://", "http://"))


class Favicons:
    def __init__(self, store, *, enabled=True, transport=None, validate=None, concurrency=6, wait_seconds=2.0, private_network=False):
        self.store, self.enabled = store, enabled
        # A site on a private address is only reachable where the operator said
        # so, and only for an icon that site declared for itself.
        self._private = private_network
        self._transport, self._validate = transport, validate or _screen
        self._limit = asyncio.Semaphore(concurrency)
        self._wait = wait_seconds
        self._inflight = {}

    async def get(self, domain):
        """Return ``{domain, body, content_type}``; ``body`` is None for a miss.

        A slow site does not hold the request (browsers share a few connections
        with the research API): after ``wait_seconds`` the result is marked
        ``pending`` and the fetch finishes in the background for the next ask.
        """
        if not self.enabled:
            return {"domain": domain, "body": None, "content_type": None}
        cached = await self.store.favicon(domain)
        if cached and time.time() - cached["fetched_at"] < (HIT_SECONDS if cached["body"] else MISS_SECONDS):
            return cached
        # Many icons of one report load at once; share one refresh per domain.
        task = self._inflight.get(domain)
        if task is None:
            task = self._inflight[domain] = asyncio.ensure_future(self._refresh(domain))
            task.add_done_callback(lambda done: self._settled(domain, done))
        try:
            return await asyncio.wait_for(asyncio.shield(task), self._wait)
        except TimeoutError:
            return {"domain": domain, "body": None, "content_type": None, "pending": True}

    def _screened(self, url, private):
        """Screen one hop. Private addresses stay refused unless allowed here."""
        return self._validate(url, allow_private_addresses=True) if private else self._validate(url)

    def _settled(self, domain, task):
        self._inflight.pop(domain, None)
        if not task.cancelled() and task.exception() is not None:
            logger.warning("Site icon refresh failed for %s: %s", domain, type(task.exception()).__name__)

    async def _refresh(self, domain):
        async with self._limit:
            try:
                icon = await self._fetch(domain)
            except Exception as exc:  # A broken site must not break the source list.
                logger.debug("Site icon fetch failed for %s: %s", domain, type(exc).__name__)
                icon = None
        value = {"domain": domain, "body": icon[1] if icon else None, "content_type": icon[0] if icon else None, "fetched_at": time.time()}
        await self.store.save_favicon(value)
        return value

    async def _fetch(self, domain):
        import httpx

        # What the site itself said its icon is, learned from a source that
        # cited it. Tried first: an internal wiki often has no /favicon.ico.
        declared = await self.store.icon_hint(domain)
        labels = domain.split(".")
        # docs.example.com often has no icon of its own; its site does.
        hosts = [domain, ".".join(labels[-2:])] if len(labels) > 2 else [domain]
        headers = {"User-Agent": "Mozilla/5.0 (compatible; DeerFlow-DeepResearch site icons)"}
        async with httpx.AsyncClient(transport=self._transport, headers=headers, timeout=httpx.Timeout(5.0), follow_redirects=False) as client:
            if declared and _same_site(declared, domain):
                if icon := await self._image(client, declared, private=self._private):
                    return icon
            for host in hosts:
                icon = await self._image(client, f"https://{host}/favicon.ico")
                if icon:
                    return icon
                # Many sites declare their icon only in the home page.
                page = await self._get(client, f"https://{host}/", truncate=True)
                if page is None:
                    continue
                final_url, body = page
                for url in declared_icons(body.decode("utf-8", "replace"), final_url)[:MAX_DECLARED]:
                    if icon := await self._image(client, url):
                        return icon
        return None

    async def _image(self, client, url, *, private=False):
        response = await self._get(client, url, private=private)
        if response is None:
            return None
        body = response[1]
        kind = image_type(body)
        return (kind, body) if kind else None

    async def _get(self, client, url, *, truncate=False, private=False):
        """GET with screened redirects and a byte bound; returns (final URL, body)."""
        import httpx

        for _ in range(MAX_REDIRECTS + 1):
            if await asyncio.to_thread(self._screened, url, private):
                return None
            try:
                async with client.stream("GET", url) as response:
                    if response.is_redirect and response.headers.get("location"):
                        url = urljoin(url, response.headers["location"])
                        continue
                    if response.status_code != 200:
                        return None
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_BYTES:
                            if not truncate:
                                return None
                            break
                    return url, bytes(body[:MAX_BYTES])
            except (httpx.HTTPError, ValueError):
                return None
        return None
