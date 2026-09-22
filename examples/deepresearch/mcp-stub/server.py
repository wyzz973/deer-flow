"""A stand-in MCP search server for DeepResearch acceptance runs. Not for production.

- Streamable HTTP, protected by BOTH a bearer token and a session cookie.
- Search only: nothing here returns a full original document.
- Every tool answers in a different shape, the way real internal tools do.
- ``find_pages`` + ``open_page`` rehearse a search and a fetch tool exposed to
  research directly (``kind: mcp``): results under unusual field names, the page
  in a JSON envelope with escaped text, and nothing that says "this is a page".
- One dangerous tool that research must never be able to call.
"""

import asyncio
import json
import os
import re
import sys
import time

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

TOKEN = os.environ.get("MCP_STUB_TOKEN", "tok-acceptance-12345")
COOKIE = os.environ.get("MCP_STUB_COOKIE", "sess-acceptance-67890")
LATENCY = float(os.environ.get("MCP_STUB_LATENCY", "0.2"))  # seconds per call; raise it to rehearse slow tools
LOG = open(os.environ.get("MCP_STUB_LOG", os.devnull), "a", encoding="utf-8")  # one JSON line per call

CORPUS = json.load(open(os.path.join(os.path.dirname(__file__), "corpus.json"), encoding="utf-8"))


def log(**record):
    LOG.write(json.dumps({"at": time.time(), **record}, ensure_ascii=False) + "\n")
    LOG.flush()


def score(text, query):
    terms = [t for t in re.split(r"[\s,，、/]+", query.lower()) if t]
    grams = set()
    for term in terms:
        grams.add(term)
        if re.search(r"[一-鿿]", term):
            grams.update(term[i : i + 2] for i in range(len(term) - 1))
    lowered = text.lower()
    return sum(lowered.count(gram) for gram in grams)


def search(kind, query, limit):
    ranked = sorted(((score(item["title"] + " " + item["text"], query), item) for item in CORPUS if item["kind"] == kind), key=lambda pair: -pair[0])
    return [item for points, item in ranked if points > 0][:limit]


mcp = FastMCP("knowledge-stub", stateless_http=True, json_response=True)


@mcp.tool()
async def search_docs(query: str, top_k: int = 6) -> str:
    """Search the engineering knowledge base. Returns the best matching passages."""
    await asyncio.sleep(LATENCY)
    found = search("doc", query, top_k)
    log(tool="search_docs", query=query, results=len(found))
    # Chunks without titles or links: {content, score, doc_id}.
    return json.dumps({"code": 0, "data": {"chunks": [{"content": item["text"], "score": 0.9 - index * 0.05, "doc_id": item["id"], "updated": item["date"]} for index, item in enumerate(found)]}}, ensure_ascii=False)


@mcp.tool()
async def search_wiki(keyword: str, limit: int = 5) -> str:
    """Search the wiki. Returns page titles, links and summaries as text."""
    await asyncio.sleep(LATENCY)
    found = search("wiki", keyword, limit)
    log(tool="search_wiki", query=keyword, results=len(found))
    if not found:
        return "没有找到相关页面。"
    return "\n\n".join(f"Title: {item['title']}\nURL: https://wiki.corp.example/{item['id']}\n更新时间: {item['date']}\n{item['text']}" for item in found)


@mcp.tool()
async def search_tickets(q: str, size: int = 5) -> dict:
    """Search incident and change tickets. Returns structured records."""
    await asyncio.sleep(LATENCY)
    found = search("ticket", q, size)
    log(tool="search_tickets", query=q, results=len(found))
    return {"name": "ticket_search", "description": "工单检索结果", "total": len(found), "items": [{"headline": item["title"], "summary": item["text"], "ticket_id": item["id"], "date": item["date"]} for item in found]}


PAGE_BASE = "https://wiki.corp.example/"
# The logo this wiki states for itself. Internal sites rarely have a
# /favicon.ico the gateway can find, so the search tool names it; research shows
# it next to the site and only ever fetches an icon on the site's own host.
SITE_LOGO = PAGE_BASE + "static/logo/platform-team.png"


@mcp.tool()
async def find_pages(query: str, count: int = 5) -> str:
    """Search pages. Returns links with a one-line summary; open a page to read it."""
    await asyncio.sleep(LATENCY)
    found = search("wiki", query, count) + search("doc", query, count)
    log(tool="find_pages", query=query, results=len(found))
    return json.dumps({"code": 0, "data": {"hits": [{"headline": item["title"], "link": PAGE_BASE + item["id"], "desc": item["text"][:36] + "…", "published_at": item["date"], "logo_url": SITE_LOGO} for item in found[:count]]}})


@mcp.tool()
async def open_page(url: str, max_chars: int = 4000) -> str:
    """Open one page by its link and return its text."""
    await asyncio.sleep(LATENCY)
    item = next((item for item in CORPUS if url.rstrip("/") == PAGE_BASE + item["id"]), None)
    log(tool="open_page", query=url, results=int(item is not None))
    if item is None:
        return "404 not found"
    related = [other for other in CORPUS if other is not item and other["kind"] == item["kind"]][:2]
    body = f"# {item['title']}\n\n更新时间：{item['date']}\n\n{item['text']}\n\n## 相关页面\n" + "\n".join(f"- [{other['title']}]({PAGE_BASE}{other['id']})" for other in related)
    # ensure_ascii on purpose: many servers escape non-ASCII text.
    return json.dumps({"code": 0, "data": {"title": item["title"], "content": body[:max_chars], "published_at": item["date"], "truncated": len(body) > max_chars}})


@mcp.tool()
def delete_page(page_id: str) -> str:
    """Delete a wiki page. Research must never reach this tool."""
    log(tool="delete_page", query=page_id, results=0, DANGER=True)
    return "deleted " + page_id


app = mcp.streamable_http_app()


class Authenticate(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        bearer = request.headers.get("authorization", "")
        cookie = request.headers.get("cookie", "")
        if bearer != f"Bearer {TOKEN}" or f"sid={COOKIE}" not in cookie:
            log(tool="<auth>", query=request.url.path, results=0, rejected=True, has_bearer=bool(bearer), has_cookie=bool(cookie))
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


app.add_middleware(Authenticate)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 9731, log_level="warning")
