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
from uuid import uuid4

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


def connection(spec, request=None):
    """Connection parameters with credentials resolved for this request."""
    if spec.transport == "stdio":
        params = {"transport": "stdio", "command": spec.command, "args": list(spec.args)}
        if spec.env:
            params["env"] = SECRETS.expand(dict(spec.env), request)
        return params
    params = {"transport": "http" if spec.transport == "streamable_http" else spec.transport, "url": spec.url}
    if spec.headers:
        params["headers"] = SECRETS.expand(dict(spec.headers), request)
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
        if cached and not refresh and time.monotonic() - cached[0] < DISCOVERY_TTL_SECONDS:
            return cached[1]
        client = MultiServerMCPClient({name: params})
        tools = await asyncio.wait_for(client.get_tools(server_name=name), spec.timeout_seconds)
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

    async def call(self, tool, arguments):
        """Invoke one MCP tool and return (content, artifact, status)."""
        from langchain_core.tools import ToolException

        try:
            # The research callbacks of the surrounding source tool must not see
            # this inner call: they would record it as a second tool call and
            # charge the run's tool budget twice for one search (found in a real
            # run: 25 searches were billed as 41). An empty callback list
            # replaces the inherited one.
            message = await tool.ainvoke({"type": "tool_call", "name": tool.name, "args": arguments, "id": "dr-" + uuid4().hex}, config={"callbacks": []})
        except ToolException as exc:
            return str(exc), None, "error"
        return getattr(message, "content", message), getattr(message, "artifact", None), getattr(message, "status", "success")


MANAGER = McpManager()


async def source_tool(source, servers, request_secrets=None, budget=None):
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
        limits = [value for value in (spec.timeout_seconds, budget.call_timeout() if budget is not None else None) if value is not None]
        try:
            content, artifact, status = await asyncio.wait_for(MANAGER.call(remote, arguments), min(limits))
        except TimeoutError:
            raise ToolException(f"Error: {source.tool} did not answer within {min(limits):g}s. Do not retry the same call; continue with what you have.") from None
        if status == "error":
            raise ToolException(content if isinstance(content, str) else "MCP tool returned an error")
        return content, artifact

    return StructuredTool(
        name=source.tool,
        description=source.description or remote.description or source.name,
        args_schema=remote.args_schema,
        coroutine=run,
        response_format="content_and_artifact",
        handle_tool_error=True,
    )
