"""An OpenAI-compatible Chat Completions client for model gateways.

Many gateways and self-hosted servers implement the Chat Completions protocol
as it was before OpenAI renamed ``max_tokens`` to ``max_completion_tokens``.
LangChain's ``ChatOpenAI`` always sends the new name: a lenient gateway then
ignores the output cap (a real run wrote 7,400 tokens against a 4,096 cap), and
a strict one rejects every request. This client sends ``max_tokens`` and is
otherwise the engine's normal OpenAI client, so tool calling, streaming and
usage reporting behave the same.
"""

from __future__ import annotations

from typing import Any

from langchain_openai import ChatOpenAI


class ChatCompletionsModel(ChatOpenAI):
    # Only the Chat Completions endpoint; never the Responses API.
    use_responses_api: bool | None = False

    def _get_request_payload(self, input_: Any, *, stop: list[str] | None = None, **kwargs: Any) -> dict:
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        if "max_completion_tokens" in payload:
            payload["max_tokens"] = payload.pop("max_completion_tokens")
        return payload
