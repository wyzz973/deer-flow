"""MCP servers configured for DeepResearch, separate from the host's MCP servers.

The engine's MCP adapter library connects to them. Tools are discovered once per
server configuration and cached; each call opens its own session inside the
calling event loop, so native subagent loops can use them without sharing a
session across loops. Credentials in headers or environment are references
resolved at connection time.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import OrderedDict
from datetime import timedelta
from uuid import uuid4

from . import wire
from .evidence import digest
from .secrets import SECRETS

DISCOVERY_TTL_SECONDS = 600
# Discovered tool lists kept at once. Per-request credentials give every user
# (and every rotated token) an entry of its own, so the cache must be bounded.
DISCOVERY_ENTRIES = 64


def connection_failure(name, error):
    """A typed failure for an MCP server that could not be reached or refused us.

    The MCP client raises from a task group, so the useful cause (an HTTP 401
    for an expired cookie or token, a refused connection) sits inside an
    ``ExceptionGroup``. Only the status and exception type are reported: the
    text of a transport error can echo request headers.
    """
    from .providers import ProviderError, classify

    pending, causes = [error], []
    while pending:
        item = pending.pop()
        nested = getattr(item, "exceptions", None)
        if nested:
            pending.extend(nested)
        else:
            causes.append(item)
    for cause in causes:
        status = getattr(getattr(cause, "response", None), "status_code", None)
        if isinstance(status, int):
            kind = classify(status)
            hint = {"auth": "the server rejected the credentials; check its headers, cookie or token", "quota": "the account behind these credentials is out of quota", "rate_limit": "the server is rate limiting"}.get(
                kind, "the server answered with an error"
            )
            return ProviderError(kind if kind in {"auth", "quota", "rate_limit", "timeout", "server"} else "server", f"MCP server {name}: HTTP {status}, {hint}", status=status)
    if any(isinstance(cause, TimeoutError) for cause in causes):
        return ProviderError("timeout", f"MCP server {name} did not answer in time")
    names = ", ".join(sorted({type(cause).__name__ for cause in causes})[:3]) or type(error).__name__
    return ProviderError("network", f"MCP server {name} is unavailable ({names})")


def described(tools):
    """What a server said it offers: names, descriptions and argument shapes.

    A tool that stops being offered, or quietly changes its schema, explains a
    research that suddenly found nothing; without this the tool list existed
    only in memory for ten minutes and was never written down.
    """
    found = []
    for tool in tools or []:
        schema = getattr(tool, "args", None)
        shape = schema if isinstance(schema, dict) else None
        found.append({"name": getattr(tool, "name", None), "description": (getattr(tool, "description", "") or "")[:2000], "arguments": sorted(shape) if shape else None, "schema": shape})
    return found


def connection(spec, request=None):
    """Connection parameters with credentials resolved for this request.

    The transport keeps deadlines of its own, and its defaults are far shorter
    than what an internal server may take over a search: 30 seconds for a whole
    streamable-HTTP request, 5 seconds to open an SSE stream. Left unstated
    they, not our own wait, are what cuts a slow answer off, so the server's
    call timeout is passed down here. One set of parameters serves both
    listing tools and calling them, so listing stays bounded by the shorter
    wait its caller applies.
    """
    if spec.transport == "stdio":
        params = {"transport": "stdio", "command": spec.command, "args": list(spec.args)}
        if spec.env:
            params["env"] = SECRETS.expand(dict(spec.env), request)
        return params
    params = {"transport": "http" if spec.transport == "streamable_http" else spec.transport, "url": spec.url}
    if spec.headers:
        params["headers"] = SECRETS.expand(dict(spec.headers), request)
    if spec.transport == "sse":
        # SSE splits the two: ``timeout`` opens the stream, ``sse_read_timeout``
        # waits for the answer to arrive on it. Both take plain seconds.
        params["timeout"] = spec.timeout_seconds
        params["sse_read_timeout"] = spec.call_timeout_seconds
    else:
        # Streamable HTTP bounds every operation with ``timeout``, so the call
        # timeout governs it; both take a timedelta.
        params["timeout"] = timedelta(seconds=spec.call_timeout_seconds)
        params["sse_read_timeout"] = timedelta(seconds=spec.call_timeout_seconds)
    return params


class McpManager:
    def __init__(self):
        self._tools = OrderedDict()
        self._lock = threading.Lock()

    def forget(self):
        with self._lock:
            self._tools.clear()

    async def tools(self, name, spec, *, request=None, refresh=False):
        from langchain_mcp_adapters.client import MultiServerMCPClient

        if not spec.enabled:
            raise RuntimeError(f"MCP server {name} is disabled")
        params = connection(spec, request)
        # One cache entry per resolved connection: a per-request credential must
        # never reuse another user's discovered tools. The key holds a digest,
        # never the credential itself.
        key = digest([name, spec.model_dump(mode="json"), params])
        with self._lock:
            cached = self._tools.get(key)
            if cached:
                self._tools.move_to_end(key)
        fresh = cached and not refresh and time.monotonic() - cached[0] < DISCOVERY_TTL_SECONDS
        # Header and env values are credentials; which headers were sent is the
        # useful part and the only part recorded.
        details = {"server": name, "transport": spec.transport, "url": spec.url, "cache": "hit" if fresh else "expired" if cached else "miss"}
        asked = {"header_names": sorted(spec.headers or {}), "env_names": sorted(spec.env or {}), "timeout_seconds": spec.timeout_seconds}
        if fresh:
            async with wire.outbound("mcp_discovery", details, request=asked) as sent:
                sent.responded(status="success", discovered_tools=described(cached[1]))
            return cached[1]
        client = MultiServerMCPClient({name: params})
        async with wire.outbound("mcp_discovery", details, request=asked) as sent:
            tools = await asyncio.wait_for(client.get_tools(server_name=name), spec.timeout_seconds)
            sent.responded(status="success", discovered_tools=described(tools))
        with self._lock:
            self._tools[key] = (time.monotonic(), tools)
            self._tools.move_to_end(key)
            while len(self._tools) > DISCOVERY_ENTRIES:
                self._tools.popitem(last=False)
        return tools

    async def tool(self, name, spec, tool_name, *, request=None):
        from .providers import ProviderError

        # The allowlist holds even for a tool name that reaches here from a
        # snapshot or an override written before the list was tightened.
        if spec.allowed_tools is not None and tool_name not in spec.allowed_tools:
            raise ProviderError("config", f"MCP tool {tool_name} is not in the allowed_tools of server {name}")
        try:
            tools = await self.tools(name, spec, request=request)
        except TimeoutError:
            raise ProviderError("timeout", f"MCP server {name} did not list its tools in time") from None
        except BaseException as exc:
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise
            raise connection_failure(name, exc) from None
        found = next((tool for tool in tools if tool.name == tool_name), None)
        if found is None:
            raise ProviderError("config", f"MCP server {name} has no tool named {tool_name}")
        return found

    async def call(self, tool, arguments, *, server=None, provider=None, source=None, argument_name=None):
        """Invoke one MCP tool and return (content, artifact, status).

        ``server`` and the rest only describe the call for the audit: the tool a
        researcher sees may be an alias (``mcp_tool``) of a differently named
        remote tool on a server neither the model nor any record ever mentioned.
        """
        from langchain_core.tools import ToolException

        inner = "dr-" + uuid4().hex
        details = {"server": server, "provider": provider, "remote_tool": tool.name, "inner_call_id": inner}
        if argument_name:
            details["argument_name"] = argument_name
        if source:
            details["source"] = source
        async with wire.outbound("mcp", details, request={"arguments": arguments}) as sent:
            try:
                # The research callbacks of the surrounding source tool must not see
                # this inner call: they would record it as a second tool call and
                # charge the run's tool budget twice for one search (found in a real
                # run: 25 searches were billed as 41). An empty callback list
                # replaces the inherited one.
                message = await tool.ainvoke({"type": "tool_call", "name": tool.name, "args": arguments, "id": inner}, config={"callbacks": []})
            except ToolException as exc:
                sent.responded(status="error", body=str(exc))
                return str(exc), None, "error"
            content, artifact = getattr(message, "content", message), getattr(message, "artifact", None)
            status = getattr(message, "status", "success")
            # A server that answered "isError" is not our timeout and not an
            # adapter exception; flattening them into one ToolException lost that.
            sent.responded(status=status, body=content, artifact=artifact)
            return content, artifact, status


MANAGER = McpManager()


async def source_tool(source, servers, request_secrets=None, budget=None, recorder=None):
    """Expose a DeepResearch MCP server tool under the source's tool name.

    The model sees the MCP tool's own description and input schema; results
    are returned unchanged and observed after execution like any source.
    ``budget`` accounts every call like a provider-based source does: without
    it a directly exposed MCP tool was exempt from the run's tool ceiling and
    from the step's search allowance, because the model callback leaves
    research-owned sources to account for themselves.
    """
    from langchain_core.tools import StructuredTool, ToolException

    from .providers import ProviderError

    spec = servers[source.server]
    try:
        remote = await MANAGER.tool(source.server, spec, source.mcp_tool or source.tool, request=request_secrets)
    except ProviderError as exc:
        from .contracts import ResearchError

        raise ResearchError("MCP_TOOL_MISSING", f"研究数据源 {source.name} 的 MCP 工具不可用：{exc}", recoverable=False) from None

    async def run(**arguments):
        if budget is not None and (stop := await budget.reserve("read" if source.role == "read" else "search")):
            return stop.text, stop.artifact
        # A tool call gets the server's call timeout, not the shorter one that
        # bounds connecting and listing: the answer is the work, and an
        # internal service often takes minutes over it.
        limits = [value for value in (spec.call_timeout_seconds, budget.call_timeout() if budget is not None else None) if value is not None]
        try:
            content, artifact, status = await asyncio.wait_for(MANAGER.call(remote, arguments, server=source.server, source=source.name), min(limits))
        except TimeoutError:
            raise ToolException(f"Error: {source.tool} did not answer within {min(limits):g}s. Do not retry the same call; continue with what you have.") from None
        if status == "error":
            raise ToolException(content if isinstance(content, str) else "MCP tool returned an error")
        return content, artifact

    coroutine = run
    if recorder is not None:
        # A server whose schema has a field called `callbacks` would have the
        # model's value overwritten by LangChain's injected manager; leave that
        # call unwrapped (no correlation id) rather than corrupt it.
        if "callbacks" in (getattr(remote, "args", None) or {}):
            recorder = None
        else:
            coroutine = wire.recorded(run, recorder, tool=source.tool, source=source.name)
    return StructuredTool(
        name=source.tool,
        description=source.description or remote.description or source.name,
        args_schema=remote.args_schema,
        coroutine=coroutine,
        response_format="content_and_artifact",
        handle_tool_error=True,
    )
