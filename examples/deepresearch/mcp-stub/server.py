"""A stand-in MCP search server for DeepResearch acceptance runs. Not for production.

- Streamable HTTP, protected by BOTH a bearer token and a session cookie.
- Search only: nothing here returns a full original document.
- Every tool answers in a different shape, the way real internal tools do.
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
