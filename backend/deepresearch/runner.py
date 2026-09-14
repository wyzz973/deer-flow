"""Agent execution adapters. Demo is explicit; real failures never use demo data."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Protocol

from pydantic import Field

from .contracts import Contract, Finding, RawEvidence, ResearchError, ResearchPlan, ResearchResult, ResearchUnit, StructuredReport
from .evidence import digest, ordered_sources
from .trace import current_context


class ResearchAnalysis(Contract):
    findings: list[Finding] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


class AgentRunner(Protocol):
    async def plan(self, run, proposed=None) -> ResearchPlan: ...
    async def research(self, run, unit, dependencies) -> ResearchResult: ...
    async def synthesize(self, run, plan, findings, pool, errors=()) -> StructuredReport: ...


class DemoRunner:
    """Synthetic, deterministic integration fixture, not a search implementation."""

    def __init__(self, settings, store):
        self.settings, self.store = settings, store

    async def plan(self, run, proposed=None):
        if proposed:
            return ResearchPlan.model_validate(proposed)
        names = [name for name in self.settings.skills if name not in {"deepresearch", "report-synthesis"}]
        return ResearchPlan(
            goal=run["query"],
            constraints=run["constraints"],
            research_units=[ResearchUnit(id=f"R{i + 1}", skill=name, objective=f"{run['query']}：{self.settings.skills[name].description}") for i, name in enumerate(names[: min(2, run["budget"]["max_units"])])],
        )

    async def research(self, run, unit, dependencies):
        evidences = []
        for origin in ("internal", "external"):
            await self.store.reserve(run["run_id"], tool_calls=1)
            evidences.append(
                RawEvidence(
                    raw_id=f"{unit.id}-{origin}",
                    title=f"演示来源：{unit.skill} / {origin}",
                    url=f"https://example.invalid/demo/{unit.skill}" if origin == "external" else None,
                    source_uri=f"demo://internal/{unit.skill}" if origin == "internal" else None,
                    origin=origin,
                    source_name=f"demo-{origin}",
                    source_level="L2",
                    publisher=f"demo-{origin}",
                    snippet=f"合成测试证据，仅用于验证 {unit.objective} 的流程和引用，不代表真实研究结论。",
                )
            )
        return ResearchResult(
            unit_id=unit.id,
            raw_evidences=evidences,
            confidence=0.5,
            findings=[Finding(claim=f"演示发现：{unit.objective}。该内容只用于功能测试。", raw_evidence_refs=[e.raw_id for e in evidences], confidence=0.5)],
            searched_origins=["internal", "external"],
        )

    async def synthesize(self, run, plan, findings, pool, errors=()):
        def seg(f):
            return {"text": f["claim"], "evidence_ids": f["evidence_ids"], "segment_type": "fact"}

        first = next(iter(findings), None)
        sections = []
        for unit in plan.research_units:
            selected = [f for f in findings if f["unit_id"] == unit.id]
            sections.append({"heading": self.settings.skills[unit.skill].description, "unit_ids": [unit.id], "segments": [seg(f) for f in selected] or [{"text": "该维度暂无有效证据。", "evidence_ids": [], "segment_type": "analysis"}]})
        return StructuredReport.model_validate(
            {
                "title": "演示研究报告：" + run["query"][:180],
                "executive_summary": [seg(first)] if first else [{"text": "暂无有效证据。", "evidence_ids": [], "segment_type": "analysis"}],
                "sections": sections,
                "conclusion": [{"text": "请切换真实 DeerFlow/MCP 适配器后再开展业务研究。", "evidence_ids": [], "segment_type": "recommendation"}],
            }
        )


class DeerFlowRunner:
    """Uses this fork's Custom Agent config, SDK factory and MCP interceptors.

    All credentials travel on Runtime.context only. SDK children are ephemeral;
    only sanitized structured results enter the parent checkpoint/store.
    """

    def __init__(self, settings, store):
        self.settings, self.store = settings, store

    async def _agent_config(self, skill_name):
        from deerflow.subagents.registry import get_subagent_config

        spec = self.settings.skills[skill_name]
        agent = await asyncio.to_thread(get_subagent_config, spec.agent)
        if agent is None:
            raise ResearchError("AGENT_NOT_CONFIGURED", f"未配置 Custom Agent: {spec.agent}", recoverable=False)
        if agent.skills is not None and skill_name not in agent.skills:
            raise ResearchError("SKILL_DENIED", f"Agent 未授权 Skill: {skill_name}", recoverable=False)
        return spec, agent

    async def _json(self, run, skill_name, payload, schema, tools=None, agent=None):
        # Keep the workflow contract separate from native agent execution.
        from .structured import run_structured

        return await run_structured(self, run, skill_name, payload, schema, tools, agent)

    async def plan(self, run, proposed=None):
        names = {k: v.description for k, v in self.settings.skills.items() if k not in {"deepresearch", "report-synthesis"}}
        return await self._json(
            run,
            "deepresearch",
            {
                "goal": run["query"],
                "constraints": run["constraints"],
                "available_skills": names,
                "available_sources": [{"name": s.name, "origin": s.origin} for s in self.settings.sources],
                "require_dual_source": self.settings.require_dual_source,
                "budget": run["budget"],
                "proposed_plan_to_normalize": proposed,
            },
            ResearchPlan,
        )

    @asynccontextmanager
    async def _source_tools(self, run, sources):
        """Use the host's MCP cache/session pool, credentials and transport.

        Research configuration selects tools, not another MCP implementation.
        Original argument schemas, descriptions, interceptors and metadata are
        preserved. Host-wide discovery is owned by DeerFlow startup/cache.
        """
        from deerflow.mcp.cache import get_cached_mcp_tools
        from deerflow.tools.mcp_metadata import get_mcp_source

        loaded = await asyncio.to_thread(get_cached_mcp_tools)
        selected = {}
        for source in sources:
            tool = next((tool for tool in loaded if tool.name == source.tool and (get_mcp_source(tool) or {}).get("server_name") == source.server), None)
            if tool is None:
                raise ResearchError("MCP_TOOL_MISSING", f"Host MCP tool is unavailable: {source.tool}", recoverable=False)
            selected[source.name] = tool
        yield selected

    async def research(self, run, unit, dependencies):
        from dataclasses import asdict

        from .native import execute_role
        from .observations import NativeExecution, research_observations
        from .structured import convert_answer

        sources, priority_origin = await asyncio.to_thread(ordered_sources, self.settings, unit.source_strategy, run["source_names"])
        _, agent = await self._agent_config(unit.skill)
        allowed = set(agent.tools) if agent.tools is not None else {s.tool for s in sources}
        allowed -= set(agent.disallowed_tools or [])
        sources = [s for s in sources if s.tool in allowed]
        if not set(unit.source_strategy.required_origins).issubset({s.origin for s in sources}):
            raise ResearchError("TOOL_DENIED", "Agent tool policy does not cover the required research sources", recoverable=False)
        context = current_context()
        payload = {
            "unit": unit.model_dump(mode="json"),
            "dependencies": dependencies,
            "sources": [{"name": s.name, "tool": s.tool, "origin": s.origin} for s in sources],
            "instructions": "Research the assigned objective using the available native tools. "
            "Consult every required source origin; independent tool calls may run in parallel. "
            "Read each tool's own schema and its original response. Cite native receipt IDs/tool call IDs "
            "for findings. Do not assume a search result schema. Explain missing information honestly.",
        }
        await self.store.event(run["run_id"], "research.source_policy", {"unit_id": unit.id, "policy": priority_origin, "sources": [s.name for s in sources]})
        # Cache a completed native execution before output conversion. A format
        # repair/retry therefore does not repeat a successful research session.
        # Individual tools are not replay-cached here: arbitrary MCP tools can
        # have side effects and their native semantics must remain intact.
        key = "native-unit:" + digest([unit.model_dump(mode="json"), dependencies])
        cached = await self.store.cached(run["run_id"], key)
        if cached is not None:
            execution = NativeExecution(**cached)
        else:
            async with self._source_tools(run, sources) as tool_map:
                execution = await execute_role(self.settings, self.store, run, unit.skill, payload, list(tool_map.values()), agent, context)
            await self.store.cache(run["run_id"], key, asdict(execution))
        evidences, catalog = research_observations(execution, sources)
        analysis = await convert_answer(
            self,
            run,
            unit.skill,
            {
                **payload,
                "observed_calls": catalog,
                "instructions": "Associate findings with raw_id values from observed_calls. "
                "Native receipt IDs are aliases in that catalog, not external document citations. "
                "Do not invent source URLs or claim that a tool receipt proves semantic support.",
            },
            ResearchAnalysis,
            execution.answer,
            context,
        )
        if execution.stop_reason:
            analysis.open_questions.append(f"Native agent stopped before normal completion: {execution.stop_reason}")
        try:
            return ResearchResult(unit_id=unit.id, raw_evidences=evidences, searched_origins=sorted({e.origin for e in evidences if e.origin != "runtime"}), **analysis.model_dump())
        except ValueError:
            raise ResearchError("EVIDENCE_REFERENCE", "Findings refer to an unobserved native tool call") from None

    async def synthesize(self, run, plan, findings, pool, errors=()):
        return await self._json(
            run,
            "report-synthesis",
            {
                "plan": plan.model_dump(mode="json"),
                "findings": findings,
                "evidence_pool": pool,
                "validation_errors_to_fix": list(errors),
                "instructions": "No search. Cover original unit_ids. Facts require evidence_ids. Separate analysis from facts. Never produce numeric citations, source URLs, or Markdown.",
            },
            StructuredReport,
        )
