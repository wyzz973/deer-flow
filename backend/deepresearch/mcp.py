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
from uuid import uuid4

from .evidence import digest
from .secrets import SECRETS

DISCOVERY_TTL_SECONDS = 600


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
        self._tools = {}
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
        if cached and not refresh and time.monotonic() - cached[0] < DISCOVERY_TTL_SECONDS:
            return cached[1]
        client = MultiServerMCPClient({name: params})
        tools = await asyncio.wait_for(client.get_tools(server_name=name), spec.timeout_seconds)
        with self._lock:
            self._tools[key] = (time.monotonic(), tools)
        return tools

    async def tool(self, name, spec, tool_name, *, request=None):
        from .providers import ProviderError

        try:
            tools = await self.tools(name, spec, request=request)
        except TimeoutError:
            raise ProviderError("timeout", f"MCP server {name} did not list its tools in time") from None
        except Exception as exc:
            raise ProviderError("network", f"MCP server {name} is unavailable ({type(exc).__name__})") from None
        found = next((tool for tool in tools if tool.name == tool_name), None)
        if found is None:
            raise ProviderError("config", f"MCP server {name} has no tool named {tool_name}")
        return found

    async def call(self, tool, arguments):
        """Invoke one MCP tool and return (content, artifact, status)."""
        from langchain_core.tools import ToolException

        try:
            message = await tool.ainvoke({"type": "tool_call", "name": tool.name, "args": arguments, "id": "dr-" + uuid4().hex})
        except ToolException as exc:
            return str(exc), None, "error"
        return getattr(message, "content", message), getattr(message, "artifact", None), getattr(message, "status", "success")


MANAGER = McpManager()


async def source_tool(source, servers, request_secrets=None):
    """Expose a DeepResearch MCP server tool under the source's tool name.

    The model sees the MCP tool's own description and input schema; results
    are returned unchanged and observed after execution like any source.
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
        content, artifact, status = await MANAGER.call(remote, arguments)
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
