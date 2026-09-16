import asyncio
import hashlib
import re
from typing import Annotated

from langchain.tools import tool
from langchain_core.tools import ToolException
from pydantic import Field

from deerflow.community.jina_ai.jina_client import JinaClient
from deerflow.config import get_app_config
from deerflow.utils.readability import ReadabilityExtractor

readability_extractor = ReadabilityExtractor()


def _coerce_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _coerce_timeout(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _coerce_proxy(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    proxy = value.strip()
    return proxy or None


@tool("web_fetch", parse_docstring=True, response_format="content_and_artifact")
async def web_fetch_tool(
    url: str,
    start_index: Annotated[int, Field(ge=0)] = 0,
    max_length: Annotated[int, Field(ge=1000, le=16000)] = 16000,
    query: Annotated[str, Field(max_length=200)] | None = None,
) -> tuple[str, dict]:
    """Fetch the contents of a web page at a given URL.
    Only fetch EXACT URLs that have been provided directly by the user or have been returned in results from the web_search and web_fetch tools.
    This tool can NOT access content that requires authentication, such as private Google Docs or pages behind login walls.
    Do NOT add www. to URLs that do NOT have them.
    URLs must include the schema: https://example.com is a valid URL while example.com is an invalid URL.
    Long pages are returned in readable excerpts with explicit continuation positions.
    Use query to locate a section instead of repeatedly reading the page beginning.
    A fragment in the URL does not guarantee that the relevant section was read.

    Args:
        url: The URL to fetch the contents of.
        start_index: Character position to resume reading from, as returned by this tool.
        max_length: Maximum characters in this excerpt, from 1000 to 16000.
        query: Optional literal text to locate within the page; returned positions can skip a table-of-contents match.
    """
    jina_client = JinaClient()
    timeout = 10
    proxy = None
    trust_env = True
    config = get_app_config().get_tool_config("web_fetch")
    if config is not None:
        timeout = _coerce_timeout(config.model_extra.get("timeout"), timeout)
        proxy = _coerce_proxy(config.model_extra.get("proxy"))
        trust_env = _coerce_bool(config.model_extra.get("trust_env"), trust_env)
    html_content = await jina_client.crawl(url, return_format="html", timeout=timeout, proxy=proxy, trust_env=trust_env)
    if isinstance(html_content, str) and html_content.startswith("Error:"):
        raise ToolException(html_content)
    article = await asyncio.to_thread(readability_extractor.extract_article, html_content)
    markdown = article.to_markdown()
    if article.html_content == "No content could be extracted from this page":
        raise ToolException("Error: No readable page content was extracted; do not cite this response as original text.")
    matches = [match.start() for match in re.finditer(re.escape(query), markdown, re.I)][:30] if query else []
    position = next((max(start_index, match - 250) for match in matches if match >= start_index), start_index)
    position = min(position, len(markdown))
    end = min(position + max_length, len(markdown))
    excerpt = markdown[position:end]
    next_position = end if end < len(markdown) else None
    title = str(article.title)[:1000]
    artifact = {
        "schema": "deerflow.web_page.v1",
        "url": url,
        "title": title,
        "document_hash": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        "start_index": position,
        "end_index": end,
        "total_chars": len(markdown),
        "next_start_index": next_position,
        "excerpt": excerpt[:1200],
    }
    header = f"Source: {url}\nTitle: {title}\nExcerpt: characters {position}-{end} of {len(markdown)}."
    if query:
        header += f"\nLiteral matches: {matches}." if matches else "\nNo literal match found; do not infer absence of the concept from this alone."
    footer = f"\n\n[More content: call web_fetch with start_index={next_position}, or query a section title.]" if next_position is not None else "\n\n[End of extracted page.]"
    return header + "\n\n" + excerpt + footer, artifact


# Preserve readable errors for callers while native ToolMessages carry error
# status. Failed fetches must not be registered as successfully read sources.
web_fetch_tool.handle_tool_error = True
