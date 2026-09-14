"""Project native execution records into research references after execution.

Nothing here sits between a tool and the research model. The only structures
we inspect are DeerFlow/LangChain's own ToolMessage and tool receipt envelopes;
the tool's business payload stays opaque. A receipt proves a call occurred,
not that the model's interpretation of its result is correct.
"""

from dataclasses import dataclass, field
from typing import Any

from .contracts import RawEvidence
from .evidence import digest


@dataclass
class NativeExecution:
    answer: str
    execution_id: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    receipts: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str | None = None


def research_observations(execution: NativeExecution, sources):
    from deerflow.utils.messages import message_content_to_text

    by_tool = {source.tool: source for source in sources}
    receipts = {r["tool_call_id"]: r for r in execution.receipts if r.get("tool_call_id")}
    calls = {call["id"]: call for message in execution.messages for call in message.get("tool_calls", []) if call.get("id")}
    evidence, catalog = {}, []
    for message in execution.messages:
        if message.get("type") != "tool":
            continue
        call_id = message.get("tool_call_id")
        call = calls.get(call_id, {})
        name = message.get("name") or call.get("name")
        source = by_tool.get(name)
        receipt = receipts.get(call_id, {})
        status = receipt.get("status") or message.get("status", "success")
        if not name or not call_id or status != "success" or message.get("status") == "error":
            continue
        text = message_content_to_text(message.get("content") or "")
        if not text.strip():
            continue
        raw_id = "raw_" + digest([execution.execution_id, call_id])[:24]
        item = RawEvidence(
            raw_id=raw_id,
            title=f"{source.name if source else name} / {receipt.get('id', call_id)}",
            source_uri=f"{'mcp-result' if source else 'tool-result'}://{execution.execution_id}/{raw_id}",
            # Files, sandbox and other native tools can support findings too.
            # Runtime provenance must not masquerade as a configured internal
            # or external MCP source and satisfy that source's coverage gate.
            origin=source.origin if source else "runtime",
            source_name=source.name if source else "native-" + digest(name)[:16],
            source_level=source.level if source else "L4",
            publisher=source.publisher if source else "runtime",
            snippet=text[:20000],
            provenance="tool_output",
            raw_content_ref=f"execution:{execution.execution_id}:{call_id}",
        )
        evidence[raw_id] = item
        catalog.append({"raw_id": raw_id, "receipt_id": receipt.get("id"), "tool_call_id": call_id, "tool_name": name, "origin": item.origin, "excerpt": item.snippet})
    return list(evidence.values()), catalog
