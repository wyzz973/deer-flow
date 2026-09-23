"""Backends of research sources: API presets, custom HTTP APIs and MCP tools.

A provider answers one request for its source's role: ``search`` (query to
result records), ``read`` (URL to one document) or ``data`` (query to knowledge
records). Failures are typed so the source can fail over to the next provider
and cool a failing one down. Classification uses HTTP status and generic wording
(rate limit, quota, balance), never one vendor's response schema.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from . import extract, wire
from .audit import scrub_text
from .config import PROVIDER_TIMEOUT_SECONDS
from .secrets import SECRETS

# Failures that say something about the provider (cool it down) versus about
# this one request (try the next provider, keep this one available).
PROVIDER_FAILURES = {"rate_limit", "quota", "auth", "timeout", "network", "server", "config"}
REQUEST_FAILURES = {"invalid", "not_found", "blocked", "empty", "unsupported"}
QUOTA_WORDS = re.compile(r"(?i)quota|exceed|insufficient|balance|credit|billing|payment|plan limit|usage limit|余额|额度|欠费|套餐")
RATE_WORDS = re.compile(r"(?i)rate.?limit|too many requests|throttl|请求过于频繁|限流|频率")
AUTH_WORDS = re.compile(r"(?i)unauthori[sz]ed|invalid api key|api key|forbidden|permission|鉴权|认证|无效的?密钥")
# API calls identify themselves honestly; only direct page reads look like a browser.
API_USER_AGENT = "DeerFlow-DeepResearch/1.0"
BROWSER_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
HTML_TITLE = re.compile(r"(?is)<title[^>]*>(.*?)</title>")
MAX_DOCUMENT_BYTES = 8 * 1024 * 1024

# Metadata for the settings page. key_env is only a suggested variable name.
PRESETS = {
    "tavily": {"label": "Tavily", "role": "search", "requires_key": True, "key_env": "TAVILY_API_KEY", "base_url": "https://api.tavily.com", "docs": "https://docs.tavily.com"},
    "serper": {"label": "Serper (Google)", "role": "search", "requires_key": True, "key_env": "SERPER_API_KEY", "base_url": "https://google.serper.dev", "docs": "https://serper.dev"},
    "brave": {"label": "Brave Search", "role": "search", "requires_key": True, "key_env": "BRAVE_SEARCH_API_KEY", "base_url": "https://api.search.brave.com", "docs": "https://api-dashboard.search.brave.com"},
    "exa": {"label": "Exa", "role": "search", "requires_key": True, "key_env": "EXA_API_KEY", "base_url": "https://api.exa.ai", "docs": "https://docs.exa.ai"},
    "bocha": {"label": "博查 Bocha", "role": "search", "requires_key": True, "key_env": "BOCHA_API_KEY", "base_url": "https://api.bochaai.com", "docs": "https://open.bochaai.com"},
    "searxng": {"label": "SearXNG（自建）", "role": "search", "requires_key": False, "key_env": None, "base_url": None, "docs": "https://docs.searxng.org"},
    "jina_search": {"label": "Jina Search", "role": "search", "requires_key": True, "key_env": "JINA_API_KEY", "base_url": "https://s.jina.ai", "docs": "https://jina.ai/reader"},
    "duckduckgo": {"label": "DuckDuckGo（免费）", "role": "search", "requires_key": False, "key_env": None, "base_url": None, "docs": "https://pypi.org/project/ddgs/"},
    "jina_reader": {"label": "Jina Reader", "role": "read", "requires_key": False, "key_env": "JINA_API_KEY", "base_url": "https://r.jina.ai", "docs": "https://jina.ai/reader"},
    "tavily_extract": {"label": "Tavily Extract", "role": "read", "requires_key": True, "key_env": "TAVILY_API_KEY", "base_url": "https://api.tavily.com", "docs": "https://docs.tavily.com"},
    "firecrawl": {"label": "Firecrawl", "role": "read", "requires_key": True, "key_env": "FIRECRAWL_API_KEY", "base_url": "https://api.firecrawl.dev", "docs": "https://docs.firecrawl.dev"},
    "direct": {"label": "直接抓取网页", "role": "read", "requires_key": False, "key_env": None, "base_url": None, "docs": None},
    "ragflow": {"label": "RAGFlow 知识库", "role": "data", "requires_key": True, "key_env": "RAGFLOW_API_KEY", "base_url": None, "docs": "https://ragflow.io/docs"},
    "lightrag": {"label": "LightRAG 知识库", "role": "data", "requires_key": False, "key_env": "LIGHTRAG_API_KEY", "base_url": None, "docs": "https://github.com/HKUDS/LightRAG"},
    "http": {"label": "自定义 HTTP 接口", "role": None, "requires_key": False, "key_env": None, "base_url": None, "docs": None},
    "mcp": {"label": "MCP 工具", "role": None, "requires_key": False, "key_env": None, "base_url": None, "docs": None},
}


class ProviderError(Exception):
    def __init__(self, kind, message, *, status=None, retry_after=None):
        super().__init__(message)
        self.kind, self.status, self.retry_after = kind, status, retry_after


@dataclass
class Request:
    role: str
    query: str | None = None
    url: str | None = None
    max_results: int = 8
    time_range: str | None = None


@dataclass
class Outcome:
    records: list[dict] = field(default_factory=list)
    document: dict | None = None
    # A bounded copy of what the provider returned, kept for the tool-call audit.
    raw: str = ""


def classify(status, text=""):
    if status == 429 or RATE_WORDS.search(text or ""):
        return "rate_limit"
    if status in {401, 403} and (text or "").lstrip().startswith("<"):
        # An HTML page (bot challenge, gateway error) is not a key problem.
        return "server"
    if status in {402, 432, 433} or (status in {401, 403} and QUOTA_WORDS.search(text or "")):
        return "quota"
    if status in {401, 403}:
        return "auth"
    if status in {408, 504}:
        return "timeout"
    if status == 404:
        return "not_found"
    if status >= 500:
        return "server"
    return "invalid"


def _retry_after(value):
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            moment = datetime.strptime(value, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=UTC)
        except ValueError:
            return None
        return max(0.0, (moment - datetime.now(UTC)).total_seconds())


def _key(spec, required):
    key = SECRETS.resolve(spec.api_key)
    if required and not key:
        reference = spec.api_key or f"${PRESETS.get(spec.type, {}).get('key_env') or 'API_KEY'}"
        raise ProviderError("config", f"API key is not set ({reference})")
    return key


def _render(template, request, *, encode=False):
    """Fill {query}/{url}/{max_results}/{time_range} in strings, keeping numbers typed.

    ``encode`` is for a URL template: the model writes the query, so a raw
    ``&``, ``#`` or space would add or cut parameters of the operator's API.
    A page address keeps its own structure when it is the path of a reader API
    (``https://reader/{url}``) and is encoded when it is a query value.
    """
    values = {"query": request.query or "", "url": request.url or "", "max_results": request.max_results, "time_range": request.time_range or ""}
    if isinstance(template, str):
        whole = re.fullmatch(r"\{(query|url|max_results|time_range)\}", template.strip())
        if whole and not encode:
            return values[whole.group(1)]

        def fill(match):
            value = str(values[match.group(1)])
            if not encode or (match.group(1) == "url" and "?" not in template[: match.start()]):
                return value
            return quote(value, safe="")

        return re.sub(r"\{(query|url|max_results|time_range)\}", fill, template)
    if isinstance(template, dict):
        return {key: _render(value, request) for key, value in template.items() if not (value == "{time_range}" and not request.time_range)}
    if isinstance(template, list):
        return [_render(value, request) for value in template]
    return template


def _brief(text):
    """A short, readable error body: the title of an HTML page, else collapsed text."""
    if text.lstrip().startswith("<"):
        match = HTML_TITLE.search(text)
        return "HTML page" + (f" “{' '.join(match.group(1).split())[:120]}”" if match else "")
    return " ".join(text.split())[:300]


async def _http(spec, method, url, *, secrets=(), headers=None, params=None, body=None, raw=False, follow_redirects=True, user_agent=API_USER_AGENT, max_bytes=None):
    """One HTTP request to a source, recorded when a wire recorder is bound."""
    async with wire.outbound(
        "http",
        {"provider": spec.id, "provider_type": spec.type, "method": method, "url": url},
        request={"headers": headers, "params": params, "body": body, "timeout_seconds": spec.timeout_seconds},
        secrets=secrets,
    ) as sent:
        return await _request(spec, method, url, sent, secrets=secrets, headers=headers, params=params, body=body, raw=raw, follow_redirects=follow_redirects, user_agent=user_agent, max_bytes=max_bytes)


async def _request(spec, method, url, sent, *, secrets=(), headers=None, params=None, body=None, raw=False, follow_redirects=True, user_agent=API_USER_AGENT, max_bytes=None):
    import httpx

    try:
        async with httpx.AsyncClient(timeout=spec.timeout_seconds, follow_redirects=follow_redirects, trust_env=True, headers={"User-Agent": user_agent}) as client:
            if max_bytes is None:
                response = await client.request(method, url, headers=headers, params=params, json=body)
            else:
                # The address comes from the model or from a web page: stop
                # reading at the cap instead of buffering whatever is served.
                async with client.stream(method, url, headers=headers, params=params, json=body) as streamed:
                    chunks, size = [], 0
                    async for chunk in streamed.aiter_bytes():
                        chunks.append(chunk)
                        size += len(chunk)
                        if size >= max_bytes:
                            break
                    # aiter_bytes already decoded the body; the rebuilt response must not decode it again.
                    kept = [(name, value) for name, value in streamed.headers.items() if name.lower() not in {"content-encoding", "content-length", "transfer-encoding"}]
                    response = httpx.Response(streamed.status_code, headers=kept, content=b"".join(chunks)[:max_bytes], request=streamed.request)
    except httpx.TimeoutException as exc:
        raise ProviderError("timeout", f"{type(exc).__name__} after {spec.timeout_seconds:g}s") from None
    except httpx.HTTPError as exc:
        raise ProviderError("network", type(exc).__name__) from None
    # Recorded before the 4xx raise, so a rate-limited or refused call keeps the
    # provider's own explanation instead of the 300 characters the attempt keeps.
    sent.responded(response)
    if response.status_code >= 400:
        text = scrub_text(response.text[:600], secrets)
        raise ProviderError(classify(response.status_code, text), f"HTTP {response.status_code}: {_brief(text)}", status=response.status_code, retry_after=_retry_after(response.headers.get("retry-after")))
    return response if raw else _json(response, secrets)


def _json(response, secrets=()):
    try:
        data = response.json()
    except ValueError:
        raise ProviderError("invalid", "The provider did not return JSON") from None
    # Many APIs report errors with HTTP 200 and a status field in the body.
    if isinstance(data, dict):
        code = data.get("code")
        message = data.get("msg") or data.get("message") or data.get("error")
        if isinstance(code, int) and code not in {0, 200} and message:
            text = scrub_text(str(message), secrets)[:300]
            raise ProviderError(classify(code if 400 <= code < 600 else 400, text), f"Provider error {code}: {text}")
        if data.get("success") is False and message:
            text = scrub_text(str(message), secrets)[:300]
            raise ProviderError(classify(400, text), f"Provider error: {text}")
    return data


def _raw(value, limit=20000):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text[:limit]


def _found(value, request, path=None):
    data = value
    for part in path or ():
        data = data.get(part) if isinstance(data, dict) else None
    found = extract.records(data if data is not None else value, limit=request.max_results)
    return Outcome(records=found, raw=_raw(value))


FRESHNESS = {
    "brave": {"day": "pd", "week": "pw", "month": "pm", "year": "py"},
    "serper": {"day": "qdr:d", "week": "qdr:w", "month": "qdr:m", "year": "qdr:y"},
    "bocha": {"day": "oneDay", "week": "oneWeek", "month": "oneMonth", "year": "oneYear"},
    "duckduckgo": {"day": "d", "week": "w", "month": "m", "year": "y"},
}


async def tavily(spec, request):
    key = _key(spec, True)
    base = (spec.base_url or PRESETS["tavily"]["base_url"]).rstrip("/")
    body = {"query": request.query, "max_results": request.max_results, "search_depth": spec.options.get("search_depth", "basic"), "topic": spec.options.get("topic", "general"), "include_answer": False}
    if request.time_range:
        body["time_range"] = request.time_range
    return _found(await _http(spec, "POST", base + "/search", secrets=[key], headers={"Authorization": f"Bearer {key}"}, body=body), request, ["results"])


async def serper(spec, request):
    key = _key(spec, True)
    base = (spec.base_url or PRESETS["serper"]["base_url"]).rstrip("/")
    body = {"q": request.query, "num": request.max_results, **{name: spec.options[name] for name in ("gl", "hl", "location") if spec.options.get(name)}}
    if request.time_range:
        body["tbs"] = FRESHNESS["serper"][request.time_range]
    return _found(await _http(spec, "POST", base + "/search", secrets=[key], headers={"X-API-KEY": key}, body=body), request, ["organic"])


async def brave(spec, request):
    key = _key(spec, True)
    base = (spec.base_url or PRESETS["brave"]["base_url"]).rstrip("/")
    params = {"q": request.query, "count": min(request.max_results, 20), **{name: spec.options[name] for name in ("country", "search_lang") if spec.options.get(name)}}
    if request.time_range:
        params["freshness"] = FRESHNESS["brave"][request.time_range]
    return _found(await _http(spec, "GET", base + "/res/v1/web/search", secrets=[key], headers={"X-Subscription-Token": key, "Accept": "application/json"}, params=params), request, ["web", "results"])


async def exa(spec, request):
    key = _key(spec, True)
    base = (spec.base_url or PRESETS["exa"]["base_url"]).rstrip("/")
    body = {"query": request.query, "numResults": request.max_results, "type": spec.options.get("type", "auto"), "contents": {"text": {"maxCharacters": int(spec.options.get("max_characters", 1200))}}}
    if request.time_range:
        days = {"day": 1, "week": 7, "month": 31, "year": 366}[request.time_range]
        body["startPublishedDate"] = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return _found(await _http(spec, "POST", base + "/search", secrets=[key], headers={"x-api-key": key}, body=body), request, ["results"])


async def bocha(spec, request):
    key = _key(spec, True)
    base = (spec.base_url or PRESETS["bocha"]["base_url"]).rstrip("/")
    body = {"query": request.query, "count": request.max_results, "summary": bool(spec.options.get("summary", True)), "freshness": FRESHNESS["bocha"].get(request.time_range or "", "noLimit")}
    return _found(await _http(spec, "POST", base + "/v1/web-search", secrets=[key], headers={"Authorization": f"Bearer {key}"}, body=body), request, ["data", "webPages", "value"])


async def searxng(spec, request):
    key = _key(spec, False)
    params = {"q": request.query, "format": "json", "pageno": 1, **({"language": spec.options["language"]} if spec.options.get("language") else {})}
    if request.time_range:
        params["time_range"] = request.time_range
    headers = {"Authorization": f"Bearer {key}"} if key else None
    return _found(await _http(spec, "GET", spec.base_url.rstrip("/") + "/search", secrets=[key] if key else [], headers=headers, params=params), request, ["results"])


async def jina_search(spec, request):
    key = _key(spec, True)
    base = (spec.base_url or PRESETS["jina_search"]["base_url"]).rstrip("/")
    headers = {"Accept": "application/json", "Authorization": f"Bearer {key}", "X-Respond-With": "no-content"}
    return _found(await _http(spec, "GET", base + "/", secrets=[key], headers=headers, params={"q": request.query, "num": request.max_results}), request, ["data"])


async def duckduckgo(spec, request):
    from ddgs import DDGS
    from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException

    backend = spec.options.get("backend") or ("brave,duckduckgo,yahoo" if request.time_range else "auto")
    region = spec.options.get("region") or ("cn-zh" if re.search(r"[一-鿿]", request.query or "") else "us-en")

    def search():
        kwargs = {"region": region, "safesearch": spec.options.get("safesearch", "moderate"), "max_results": request.max_results, "backend": backend}
        if request.time_range:
            kwargs["timelimit"] = FRESHNESS["duckduckgo"][request.time_range]
        return DDGS(timeout=int(spec.timeout_seconds)).text(request.query, **kwargs)

    try:
        results = await asyncio.wait_for(asyncio.to_thread(search), spec.timeout_seconds + 5)
    except RatelimitException:
        raise ProviderError("rate_limit", "DuckDuckGo rate limit") from None
    except (TimeoutException, TimeoutError):
        raise ProviderError("timeout", "DuckDuckGo timed out") from None
    except DDGSException as exc:
        text = str(exc)
        raise ProviderError("empty" if "no results" in text.lower() else "server", "DuckDuckGo: " + text[:200]) from None
    records = [{"title": item.get("title") or item.get("href"), "url": extract.http_url(item.get("href")), "snippet": item.get("body") or ""} for item in results or [] if extract.http_url(item.get("href"))]
    return Outcome(records=records[: request.max_results], raw=_raw(results))


def _page(title, url, text, raw, published=None):
    if not text or len(text.strip()) < 40:
        raise ProviderError("empty", "No readable content was returned for this page")
    # Whatever the page said about its own date, in the shape the provider had
    # it; the read tool normalizes it once, where the artifact is built.
    return Outcome(document={"title": title or url, "url": url, "text": text, "published_at": published}, raw=_raw(raw, 4000))


async def jina_reader(spec, request):
    key = _key(spec, False)
    base = (spec.base_url or PRESETS["jina_reader"]["base_url"]).rstrip("/")
    headers = {"Accept": "application/json", "X-Return-Format": "markdown", "X-Timeout": str(max(5, int(min(spec.timeout_seconds, 60)) - 5))}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    data = await _http(spec, "POST", base + "/", secrets=[key] if key else [], headers=headers, body={"url": request.url})
    found = extract.document(data, request.url)
    return _page(found["title"], request.url, found["text"], data, found.get("published_at"))


async def tavily_extract(spec, request):
    key = _key(spec, True)
    base = (spec.base_url or PRESETS["tavily_extract"]["base_url"]).rstrip("/")
    body = {"urls": [request.url], "extract_depth": spec.options.get("extract_depth", "basic"), "format": "markdown"}
    data = await _http(spec, "POST", base + "/extract", secrets=[key], headers={"Authorization": f"Bearer {key}"}, body=body)
    results = data.get("results") if isinstance(data, dict) else None
    if not results:
        failed = (data.get("failed_results") or [{}])[0] if isinstance(data, dict) else {}
        raise ProviderError("blocked", "Tavily could not extract the page: " + str(failed.get("error") or "no result")[:200])
    found = extract.document(results[0], request.url)
    return _page(found["title"], request.url, found["text"], data, found.get("published_at"))


async def firecrawl(spec, request):
    key = _key(spec, True)
    base = (spec.base_url or PRESETS["firecrawl"]["base_url"]).rstrip("/")
    body = {"url": request.url, "formats": ["markdown"], "onlyMainContent": bool(spec.options.get("only_main_content", True))}
    data = await _http(spec, "POST", base + "/v1/scrape", secrets=[key], headers={"Authorization": f"Bearer {key}"}, body=body)
    found = extract.document(data, request.url)
    status = ((data.get("data") or {}).get("metadata") or {}).get("statusCode") if isinstance(data, dict) else None
    if isinstance(status, int) and status >= 400:
        raise ProviderError("not_found" if status == 404 else "blocked", f"The page returned HTTP {status}")
    return _page(found["title"], request.url, found["text"], data, found.get("published_at"))


async def direct(spec, request):
    """Fetch a page directly, screening every redirect hop like the engine's fetchers."""
    from deerflow.community.url_safety import validate_public_http_url

    url = request.url
    for _ in range(6):
        problem = validate_public_http_url(url, allow_private_addresses=spec.allow_private_network)
        if problem:
            raise ProviderError("blocked", problem.removeprefix("Error: "))
        try:
            response = await _http(
                spec, "GET", url, raw=True, follow_redirects=False, user_agent=BROWSER_USER_AGENT, max_bytes=MAX_DOCUMENT_BYTES, headers={"Accept": "text/html,application/xhtml+xml,text/plain,application/pdf;q=0.9,*/*;q=0.5"}
            )
        except ProviderError as exc:
            # A site refusing, missing or timing out a page says nothing about
            # direct fetching in general; the next provider may still read it.
            raise ProviderError("not_found" if exc.status == 404 else "blocked", f"{url}: {exc}") from None
        if response.status_code in {301, 302, 303, 307, 308} and response.headers.get("location"):
            url = str(response.url.join(response.headers["location"]))
            continue
        break
    else:
        raise ProviderError("blocked", "Too many redirects")
    if response.status_code != 200:
        raise ProviderError("blocked", f"The page returned HTTP {response.status_code}")
    content = response.content[:MAX_DOCUMENT_BYTES]
    kind = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if kind in {"text/html", "application/xhtml+xml"} or content[:200].lstrip().lower().startswith((b"<!doctype html", b"<html")):
        from deerflow.utils.readability import ReadabilityExtractor

        article = await asyncio.to_thread(ReadabilityExtractor().extract_article, response.text)
        text = article.to_markdown()
        if "No content could be extracted from this page" in text:
            raise ProviderError("empty", "No readable content was extracted")
        # Readability keeps the article, not the head; the date lives in the head.
        return _page(str(article.title or ""), url, text, "", extract.page_date(response.text))
    if kind == "application/pdf" or content[:5] == b"%PDF-":
        text = await asyncio.to_thread(_convert_document, content, ".pdf")
        return _page(extract.document(text, url)["title"], url, text, "")
    if kind.startswith("text/") or kind in {"application/json", "application/xml"}:
        found = extract.document(response.text, url)
        return _page(found["title"], url, found["text"], "", found.get("published_at") or extract.page_date(response.text))
    raise ProviderError("unsupported", f"Unsupported content type: {kind or 'unknown'}")


def _convert_document(content, suffix):
    import tempfile

    from markitdown import MarkItDown

    with tempfile.NamedTemporaryFile(suffix=suffix) as handle:
        handle.write(content)
        handle.flush()
        return MarkItDown().convert(handle.name).text_content


async def ragflow(spec, request):
    from deerflow.community.ragflow.client import RAGFlowAPIError, RAGFlowClient, RAGFlowConnectionError, RAGFlowProtocolError

    key = _key(spec, True)
    client = RAGFlowClient(base_url=spec.base_url.rstrip("/"), api_key=key, timeout=spec.timeout_seconds)
    try:
        dataset_ids = [item for item in spec.options.get("dataset_ids") or [] if isinstance(item, str) and item.strip()]
        if not dataset_ids:
            dataset_ids = [item["id"] for item in await client.list_datasets() if isinstance(item, dict) and item.get("id")]
        if not dataset_ids:
            raise ProviderError("config", "No accessible RAGFlow dataset")
        data = await client.retrieve(
            request.query,
            dataset_ids=dataset_ids,
            page_size=request.max_results,
            similarity_threshold=float(spec.options.get("similarity_threshold", 0.2)),
            vector_similarity_weight=float(spec.options.get("vector_similarity_weight", 0.3)),
            top_k=int(spec.options.get("top_k", 256)),
        )
    except RAGFlowConnectionError as exc:
        raise ProviderError("network", scrub_text(str(exc), [key])[:200]) from None
    except RAGFlowAPIError as exc:
        text = scrub_text(str(exc), [key])[:200]
        raise ProviderError(classify(401 if AUTH_WORDS.search(text) else 400, text), text) from None
    except RAGFlowProtocolError as exc:
        raise ProviderError("server", scrub_text(str(exc), [key])[:200]) from None
    records = []
    for chunk in (data.get("chunks") or [])[: request.max_results]:
        if not isinstance(chunk, dict):
            continue
        title = chunk.get("document_keyword") or chunk.get("docnm_kwd") or chunk.get("document_name") or "RAGFlow document"
        text = chunk.get("content") or chunk.get("content_with_weight") or ""
        records.append({"title": str(title)[:500], "url": None, "snippet": str(text)[:4000], "id": str(chunk.get("document_id") or chunk.get("id") or "")})
    return Outcome(records=records, raw=_raw(data))


async def lightrag(spec, request):
    from deerflow.community.lightrag.client import LightRAGAPIError, LightRAGClient, LightRAGConnectionError, LightRAGProtocolError

    key = _key(spec, False)
    client = LightRAGClient(base_url=spec.base_url.rstrip("/"), api_key=key, timeout=spec.timeout_seconds)
    try:
        data = await client.query_data(request.query, mode=spec.options.get("mode", "mix"), top_k=int(spec.options.get("top_k", 60)))
    except LightRAGConnectionError as exc:
        raise ProviderError("network", scrub_text(str(exc), [key] if key else [])[:200]) from None
    except (LightRAGAPIError, LightRAGProtocolError) as exc:
        text = scrub_text(str(exc), [key] if key else [])[:200]
        raise ProviderError(classify(401 if AUTH_WORDS.search(text) else 400, text), text) from None
    references = {item.get("reference_id"): item.get("file_path") for item in data.get("references") or [] if isinstance(item, dict)}
    records = []
    for chunk in (data.get("chunks") or [])[: request.max_results]:
        if isinstance(chunk, dict) and chunk.get("content"):
            title = chunk.get("file_path") or references.get(chunk.get("reference_id")) or "LightRAG chunk"
            records.append({"title": str(title)[:500], "url": None, "snippet": str(chunk["content"])[:4000], "id": str(chunk.get("chunk_id") or chunk.get("reference_id") or "")})
    return Outcome(records=records, raw=_raw(data))


async def http(spec, request, *, request_secrets=None):
    headers = {key: SECRETS.expand(value, request_secrets) for key, value in spec.headers.items()}
    secrets = [value for value in headers.values() if isinstance(value, str) and len(value) >= 8]
    key = _key(spec, False)
    if key:
        secrets.append(key)
    url = _render(spec.url, request, encode=True)
    params = _render(spec.params, request) or None
    body = _render(spec.body, request) if spec.body is not None else None
    response = await _http(spec, spec.method, url, secrets=secrets, headers=headers, params=params, body=body, raw=True)
    try:
        data = response.json()
    except ValueError:
        data = response.text
    if request.role == "read":
        found = extract.document(data, request.url)
        return _page(found["title"], request.url, found["text"], data, found.get("published_at"))
    records = extract.records(data, limit=request.max_results, text_limit=1200 if request.role == "search" else 4000)
    if not records and request.role == "data":
        text = extract.content_text(data)
        if text.strip():
            records = [{"title": "Result", "url": None, "snippet": text[:8000]}]
    return Outcome(records=records, raw=_raw(data))


async def mcp(spec, request, *, servers, request_secrets=None):
    from .mcp import MANAGER

    tool = await MANAGER.tool(spec.server, servers[spec.server], spec.tool, request=request_secrets)
    arguments = _render(spec.arguments, request) if spec.arguments else infer_arguments(tool, request)
    # Which parameter the heuristic picked: a wrong guess used to be invisible
    # afterwards, leaving a source that always returned nothing unexplained.
    asked = request.url if request.role == "read" else request.query
    guessed = None if spec.arguments else next((name for name, value in arguments.items() if value == asked), None)
    try:
        result = await asyncio.wait_for(MANAGER.call(tool, arguments, server=spec.server, provider=spec.id, argument_name=guessed), spec.timeout_seconds)
    except TimeoutError:
        raise ProviderError("timeout", f"MCP tool timed out after {spec.timeout_seconds:g}s") from None
    content, artifact, status = result
    text = extract.content_text(content)
    structured = (artifact or {}).get("structured_content") if isinstance(artifact, dict) else None
    if status == "error":
        raise ProviderError(classify(400, text), "MCP tool error: " + " ".join(text.split())[:300])
    payload = structured if structured is not None else text
    if request.role == "read":
        found = extract.document(payload if structured is not None else text, request.url)
        # The tool's text is the better copy only when no field of the structured
        # answer held the page; otherwise it is the same page as escaped JSON.
        if structured is not None and not found["readable"] and len(text) > len(found["text"]):
            found["text"] = text
        return _page(found["title"], found.get("url") or request.url, found["text"], payload, found.get("published_at"))
    records = extract.records(payload, limit=request.max_results, text_limit=1200 if request.role == "search" else 4000)
    if not records and text.strip():
        if request.role == "search":
            records = extract.text_records(text, limit=request.max_results)
        if not records:
            # An opaque answer still reaches the model; it just has no record structure.
            return Outcome(records=[{"title": spec.tool, "url": None, "snippet": text[:8000], "opaque": True}], raw=_raw(payload))
    return Outcome(records=records, raw=_raw(payload))


QUERY_NAMES = ("query", "q", "search_query", "keywords", "keyword", "question", "text", "input", "prompt", "search")
URL_NAMES = ("url", "uri", "link", "href", "page_url", "id")
COUNT_NAMES = ("max_results", "maxResults", "num_results", "numResults", "count", "limit", "top_k", "topK", "size", "n")


def infer_arguments(tool, request):
    """Map a request onto an MCP tool's own input schema by common parameter names."""
    schema = getattr(tool, "args", None) or {}
    names = list(schema)
    arguments = {}
    target = URL_NAMES if request.role == "read" else QUERY_NAMES
    name = next((candidate for candidate in target if candidate in names), None)
    if name is None:
        required = [key for key, value in schema.items() if isinstance(value, dict) and value.get("type") in {None, "string"}]
        name = required[0] if required else None
    if name is None:
        raise ProviderError("invalid", "Cannot tell which argument of the MCP tool takes the " + ("URL" if request.role == "read" else "query") + "; set arguments in the provider")
    arguments[name] = request.url if request.role == "read" else request.query
    count = next((candidate for candidate in COUNT_NAMES if candidate in names), None)
    if count and request.role != "read":
        arguments[count] = request.max_results
    return arguments


CALLS = {
    "tavily": tavily,
    "serper": serper,
    "brave": brave,
    "exa": exa,
    "bocha": bocha,
    "searxng": searxng,
    "jina_search": jina_search,
    "duckduckgo": duckduckgo,
    "jina_reader": jina_reader,
    "tavily_extract": tavily_extract,
    "firecrawl": firecrawl,
    "direct": direct,
    "ragflow": ragflow,
    "lightrag": lightrag,
    "http": http,
}


def provider_timeout(spec, servers):
    """Seconds one call of this provider may take.

    A provider that states no timeout takes the default for its kind. An MCP
    provider follows its server's call timeout: an internal MCP service is
    routinely far slower than a web API, and the operator already said on the
    server how long one of its calls may take, so the chain of a source that
    mixes both does not have to repeat it on every provider.
    """
    if spec.timeout_seconds is not None:
        return spec.timeout_seconds
    server = (servers or {}).get(spec.server or "") if spec.type == "mcp" else None
    return server.call_timeout_seconds if server is not None else PROVIDER_TIMEOUT_SECONDS


async def call(spec, request, *, servers=None, request_secrets=None):
    """Run one provider. ``request_secrets`` are this request's own credentials."""
    # Every backend below reads spec.timeout_seconds, so the effective value is
    # resolved once here rather than defaulted again in each of them.
    spec = spec.model_copy(update={"timeout_seconds": provider_timeout(spec, servers)})
    if spec.type == "mcp":
        return await mcp(spec, request, servers=servers or {}, request_secrets=request_secrets)
    if spec.type == "http":
        return await http(spec, request, request_secrets=request_secrets)
    return await CALLS[spec.type](spec, request)


def catalog() -> list[dict[str, Any]]:
    return [{"type": name, **meta} for name, meta in PRESETS.items()]
