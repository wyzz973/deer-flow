"""Choices the research settings page offers, all owned by DeepResearch.

Model providers, source provider presets, engine tools and every prompt with its
purpose. Nothing here reads the host's model, tool or MCP lists: research
configuration is independent of them.
"""

from __future__ import annotations

from .config import ENGINE_TOOLS, FIXED_ROLES, MODEL_PROVIDERS
from .prompts import PROMPT_INFO, PromptSet
from .providers import PRESETS

MODEL_PROVIDER_INFO = {
    "openai": {"label": "OpenAI 兼容接口", "hint": "OpenAI、Xinference、LM Studio、Ollama 的 /v1、公司模型网关", "base_url": "https://api.openai.com/v1"},
    "deepseek": {"label": "DeepSeek", "hint": "保留多轮推理内容；base_url 可留空", "base_url": "https://api.deepseek.com"},
    "vllm": {"label": "vLLM", "hint": "本地或内网 vLLM；Qwen3 等模型可按开关关闭思考", "base_url": "http://127.0.0.1:8000/v1"},
    "anthropic": {"label": "Anthropic", "hint": "需要安装 langchain-anthropic", "base_url": None},
    "custom": {"label": "自定义类", "hint": "填写模型类路径 use，例如 langchain_openai:ChatOpenAI", "base_url": None},
}
ENGINE_TOOL_INFO = {
    "read_file": "读取引擎外置到文件的长工具结果（建议保留）",
    "ls": "列出沙箱目录",
    "glob": "按通配符查找沙箱文件",
    "grep": "在沙箱文件中搜索文本",
}


def settings_catalog():
    defaults = PromptSet()
    return {
        "model_providers": [{"id": key, "use": MODEL_PROVIDERS.get(key), **MODEL_PROVIDER_INFO[key]} for key in MODEL_PROVIDER_INFO],
        "source_providers": [{"type": name, **meta} for name, meta in PRESETS.items()],
        "engine_tools": [{"name": name, "description": ENGINE_TOOL_INFO[name]} for name in ENGINE_TOOLS],
        "fixed_roles": list(FIXED_ROLES),
        "prompts": [{"key": key, "stage": stage, "label": label, "description": description, "default": getattr(defaults, key)} for key, stage, label, description in PROMPT_INFO],
    }
