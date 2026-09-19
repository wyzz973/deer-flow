"""Choices the research settings page offers, all owned by DeepResearch.

Model providers, source provider presets, engine tools and every prompt with its
purpose. Nothing here reads the host's model, tool or MCP lists: research
configuration is independent of them.
"""

from __future__ import annotations

from .config import ENGINE_TOOLS, FIXED_ROLES, GAP_CODES, MODEL_PROVIDERS, NODE_NAMES, OPTIONAL_NODES
from .prompts import PROMPT_INFO, PromptSet
from .providers import PRESETS

MODEL_PROVIDER_INFO = {
    "openai": {"label": "OpenAI 兼容接口", "hint": "OpenAI、Xinference、LM Studio、Ollama 的 /v1、模型网关", "base_url": "https://api.openai.com/v1"},
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
# Workflow nodes in the order a research passes through them: (label, what it
# does, which model it falls back to, tuning advice for a slow or weak model).
NODE_INFO = {
    "rewrite": ("请求改写", "把对话改写成完整的研究请求；修改计划时合并改动并写确认话术。一次直接调用，不带工具。", "rewrite_model → 规划角色模型 → 默认模型", "输出 JSON：温度 0–0.3；模型很慢时可以关闭，直接用用户原话规划。"),
    "plan": ("研究计划", "把研究请求拆成研究步骤，输出计划 JSON。", "规划角色模型 → 默认模型", "输出 JSON：温度 0–0.2，max_tokens 2048–4096 足够。"),
    "research": ("检索研究", "每个研究步骤的 Agent 循环：检索、读取、写研究笔记。对所有研究员生效，研究员自己的模型优先。", "研究员角色模型 → 本节点模型 → 默认模型", "工具调用要稳定：温度 0.2–0.5；超时按“步数 × 单轮耗时”估算。"),
    "conversion": ("笔记整理", "把研究笔记整理成结构化发现并关联证据 ID。一次直接调用，失败按重试次数修复。", "extraction_model → 研究员模型", "输出 JSON：温度 0；可以单独指定一个格式遵循好的小模型；网关支持时可开 JSON 模式。"),
    "outline": ("报告大纲", "规划章节、关键结论、假设与局限，输出大纲 JSON。", "写作角色模型 → 默认模型", "输出 JSON：温度 0–0.3。"),
    "section": ("章节写作", "并行写各章正文（Markdown，带证据标记）。", "写作角色模型 → 默认模型", "长文本：温度 0.4–0.7，max_tokens 按章节目标长度留足；并发用“写作并发”控制。"),
    "summary": ("执行摘要", "基于各章草稿写执行摘要。", "写作角色模型 → 默认模型", "可以关闭：关闭后用大纲的关键结论作为摘要，省一次长生成。"),
    "revision": ("报告改写", "报告完成后按用户要求改写现有报告。", "写作角色模型 → 默认模型", "整篇重写，max_tokens 要大于报告长度。"),
    "follow_up": ("追问分流", "判断追问是直接回答、改写报告还是开启新研究，并直接给出回答。", "规划角色模型 → 默认模型", "输出 JSON：温度 0–0.3。"),
}
GAP_INFO = {
    "coverage": "某个研究目标完全没有可引用的发现",
    "unsupported": "存在没有绑定证据的结论",
    "missing-internal": "要求内部来源但没有取得内部证据",
    "missing-external": "要求外部来源但没有取得外部证据",
    "date": "没有满足时效要求的来源",
    "open-questions": "研究员认为还有可以继续查的问题（弱模型几乎总会提，最耗时）",
}


def settings_catalog():
    defaults = PromptSet()
    return {
        "model_providers": [{"id": key, "use": MODEL_PROVIDERS.get(key), **MODEL_PROVIDER_INFO[key]} for key in MODEL_PROVIDER_INFO],
        "source_providers": [{"type": name, **meta} for name, meta in PRESETS.items()],
        "engine_tools": [{"name": name, "description": ENGINE_TOOL_INFO[name]} for name in ENGINE_TOOLS],
        "fixed_roles": list(FIXED_ROLES),
        "nodes": [{"name": name, "label": NODE_INFO[name][0], "description": NODE_INFO[name][1], "model_fallback": NODE_INFO[name][2], "advice": NODE_INFO[name][3], "optional": name in OPTIONAL_NODES} for name in NODE_NAMES],
        "gap_codes": [{"code": code, "description": GAP_INFO[code]} for code in GAP_CODES],
        "prompts": [{"key": key, "stage": stage, "label": label, "description": description, "default": getattr(defaults, key)} for key, stage, label, description in PROMPT_INFO],
    }
