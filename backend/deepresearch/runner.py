"""Agent execution adapters. Demo is explicit; real failures never use demo data."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Protocol

from pydantic import Field

from .contracts import Contract, Finding, FollowupResult, RawEvidence, ResearchError, ResearchPlan, ResearchResult, ResearchUnit, SourceAnnotation, StructuredReport, utcnow
from .evidence import digest, ordered_sources
from .report_policy import report_character_limit, report_schema, source_allowed
from .trace import current_context


class ResearchAnalysis(Contract):
    findings: list[Finding] = Field(default_factory=list, max_length=100)
    open_questions: list[str] = Field(default_factory=list, max_length=50)
    limitations: list[str] = Field(default_factory=list, max_length=30)
    confidence: float = Field(ge=0, le=1)
    source_annotations: list[SourceAnnotation] = Field(default_factory=list, max_length=100)


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
            if "revision" in proposed:
                revised = ResearchPlan.model_validate(proposed["plan"])
                revised.constraints.append(proposed["revision"])
                revised.clarification_questions = []
                return revised
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
        import os

        from .sources import observed_sources

        delay = float(os.getenv("DEEPRESEARCH_DEMO_STEP_DELAY", "0"))
        for evidence in evidences:
            call_id = f"demo-{run.get('cycle', 0)}-{unit.id}-{evidence.origin}"
            details = {"tool_name": "demo_search", "unit_id": unit.id, "agent_name": self.settings.skills[unit.skill].agent, "started_at": evidence.retrieved_at, "status": "running", "demo": True}
            await self.store.record_call(run["run_id"], call_id, details)
            await self.store.event(run["run_id"], "activity.tool.started", {"call_id": call_id, **details})
            if delay:
                await asyncio.sleep(delay)
            found = observed_sources(evidence.url or "", connector=evidence.source_name, origin=evidence.origin)
            if found:
                evidence.source_id = found[0]["id"]
            call = await self.store.record_call(run["run_id"], call_id, {"status": "success", "ended_at": utcnow(), "duration_ms": int(delay * 1000)}, found)
            await self.store.event(run["run_id"], "activity.tool.completed", call)
        return ResearchResult(
            unit_id=unit.id,
            raw_evidences=evidences,
            confidence=0.5,
            findings=[Finding(claim=f"演示发现：{unit.objective}。该内容只用于功能测试。", raw_evidence_refs=[e.raw_id for e in evidences], confidence=0.5)],
            searched_origins=["internal", "external"],
        )

    async def respond(self, run, text):
        # Synthetic fixture behavior only; the real runner classifies intent.
        action = "research" if "补充研究" in text else "revise" if "改写报告" in text else "answer"
        return FollowupResult(action=action, text="演示回复：" + text + "。此内容仅验证对话流程。")

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
                "conversation": [{"role": m["role"], "text": m["text"]} for m in run.get("conversation", [])[-20:]],
                "instructions": "Infer intent and constraints from conversation. "
                "Keep edits within this research plan, never produce a report instead. "
                "Ask at most three concise clarification_questions only if essential; "
                "otherwise leave that list empty. Do not invent hard requirements. "
                "Choose report_style=brief for concise/简洁/简明 requests. "
                "Translate explicit source restrictions into source_policy: only the requested official domains, "
                "exclude user forums or mailing-list URL prefixes when official documentation is required, "
                "and require_original=true when the user asks to read original pages. Leave domains empty when unrestricted.",
            },
            ResearchPlan,
        )

    @asynccontextmanager
    async def _source_tools(self, run, sources):
        """Select original host tools, whether built-in or MCP-backed.

        Research configuration selects tools, not another MCP implementation.
        Original argument schemas, descriptions, interceptors and metadata are
        preserved. Host-wide discovery is owned by DeerFlow startup/cache.
        """
        from deerflow.mcp.cache import get_cached_mcp_tools
        from deerflow.tools.mcp_metadata import get_mcp_source

        loaded = await asyncio.to_thread(get_cached_mcp_tools) if any(s.kind == "mcp" for s in sources) else []
        native = []
        if any(s.kind == "native" for s in sources):
            from deerflow.config import get_app_config
            from deerflow.tools import get_available_tools

            app_config = await asyncio.to_thread(get_app_config)
            native = await asyncio.to_thread(get_available_tools, include_mcp=False, include_upload_tool=False, app_config=app_config)
        selected = {}
        for source in sources:
            if source.kind == "native":
                if self.settings.native_tools is not None and source.tool not in self.settings.native_tools:
                    raise ResearchError("TOOL_DENIED", f"Native tool is outside the configured ceiling: {source.tool}", recoverable=False)
                tool = next((tool for tool in native if tool.name == source.tool), None)
            else:
                tool = next((tool for tool in loaded if tool.name == source.tool and (get_mcp_source(tool) or {}).get("server_name") == source.server), None)
            if tool is None:
                code = "NATIVE_TOOL_MISSING" if source.kind == "native" else "MCP_TOOL_MISSING"
                raise ResearchError(code, f"Host research tool is unavailable: {source.tool}", recoverable=False)
            selected[source.name] = tool
        yield selected

    async def research(self, run, unit, dependencies):
        from dataclasses import asdict

        from .native import execute_role
        from .observations import NativeExecution, ground_source_annotations, research_observations
        from .structured import convert_answer

        sources, priority_origin = await asyncio.to_thread(ordered_sources, self.settings, unit.source_strategy, run["source_names"])
        _, agent = await self._agent_config(unit.skill)
        allowed = set(agent.tools) if agent.tools is not None else {s.tool for s in sources}
        allowed -= set(agent.disallowed_tools or [])
        sources = [s for s in sources if s.tool in allowed]
        if not set(unit.source_strategy.required_origins).issubset({s.origin for s in sources}):
            raise ResearchError("TOOL_DENIED", "Agent tool policy does not cover the required research sources", recoverable=False)
        context = current_context()
        current = await self.store.get(run["run_id"])
        plan_context = (current or run).get("plan") or {}
        source_policy = plan_context.get("source_policy", {})
        payload = {
            "unit": unit.model_dump(mode="json"),
            "dependencies": dependencies,
            "research_goal": (current or run).get("query", ""),
            "constraints": plan_context.get("constraints", []),
            "source_policy": source_policy,
            "shared_run_budget": run["budget"],
            "sources": [{"name": s.name, "tool": s.tool, "origin": s.origin} for s in sources],
            "instructions": "Research the assigned objective using the available native tools. "
            "Consult every required source origin; independent tool calls may run in parallel. "
            "Read each tool's own schema and its original response. Cite native receipt IDs/tool call IDs "
            "for findings. Do not assume a search result schema. Explain missing information honestly. "
            "The run budget is shared by all researchers and report synthesis. max_results is a ceiling, not a collection quota. "
            "Stop gathering once the assigned questions have adequate evidence; return concise findings and explicit remaining gaps. "
            "Do not repeat searches merely to fill the available budget. "
            "Follow source_policy: search may discover other sites, but do not use them as report evidence. "
            "When original text is required, fetch the relevant section; use native web_fetch query/start_index to read past truncation. "
            "Do not use search snippets as a substitute for the original page. "
            "Keep only decision-relevant findings (typically 4-8), not an encyclopedia of tool results. "
            "Separate blocking, researchable open_questions from unavoidable limitations such as missing scenario inputs or unavailable benchmarks. "
            "Do not expand the task into unrelated versions, HA, or third-party extensions unless requested.",
        }
        await self.store.event(run["run_id"], "research.source_policy", {"unit_id": unit.id, "policy": priority_origin, "sources": [s.name for s in sources]})
        # Cache a completed native execution before output conversion. A format
        # repair/retry therefore does not repeat a successful research session.
        # Individual tools are not replay-cached here: arbitrary MCP tools can
        # have side effects and their native semantics must remain intact.
        key = "native-unit:" + digest([run.get("cycle", 0), unit.model_dump(mode="json"), dependencies])
        cached = await self.store.cached(run["run_id"], key)
        if cached is not None:
            execution = NativeExecution(**cached)
        else:
            async with self._source_tools(run, sources) as tool_map:
                execution = await execute_role(self.settings, self.store, run, unit.skill, payload, list(tool_map.values()), agent, context)
            await self.store.cache(run["run_id"], key, asdict(execution))
        evidences, catalog = research_observations(execution, sources)
        # A supplement receives its parent's findings. Their references must
        # travel with them, otherwise valid parent IDs look fabricated when
        # the child result is validated against only its own tool calls.
        by_id = {e.raw_id: e for e in evidences}
        for dependency, findings in dependencies.items():
            if dependency not in unit.depends_on:
                raise ResearchError("DEPENDENCY_EVIDENCE", "Undeclared research dependency", recoverable=False)
            saved = await self.store.completed_unit(run["run_id"], f"{run.get('cycle', 0)}:{dependency}")
            if saved is None or saved["findings"] != findings:
                raise ResearchError("DEPENDENCY_EVIDENCE", "Dependency findings do not match the saved research result", recoverable=False)
            referenced = {ref for finding in findings for ref in finding["raw_evidence_refs"]}
            for raw in saved["raw_evidences"]:
                if raw["raw_id"] in referenced and raw["raw_id"] not in by_id:
                    item = RawEvidence.model_validate(raw)
                    by_id[item.raw_id] = item
                    catalog.append({"raw_id": item.raw_id, "dependency_unit_id": dependency, "origin": item.origin, "title": item.title, "url": item.url, "source_uri": item.source_uri})
        evidences = list(by_id.values())
        eligible = {eid for eid, item in by_id.items() if source_allowed(item, source_policy)}
        catalog = [item for item in catalog if item["raw_id"] in eligible]

        def validate_references(value):
            referenced = {ref for finding in value.findings for ref in finding.raw_evidence_refs}
            referenced.update(annotation.raw_id for annotation in value.source_annotations)
            unknown = referenced - eligible
            if unknown:
                raise ValueError("Unknown evidence IDs: " + ", ".join(sorted(unknown)[:20]) + ". Use exact raw_id values from observed_calls, including declared dependency evidence.")

        analysis = await convert_answer(
            self,
            run,
            unit.skill,
            {
                **payload,
                # The researcher has already read the original responses.
                # Conversion needs identities/locators, not a second copy of
                # every tool body. Full evidence remains in the local store.
                "observed_calls": [{k: v for k, v in call.items() if k != "excerpt"} for call in catalog],
                "instructions": "Associate findings with raw_id values from observed_calls. "
                "Native receipt IDs are aliases in that catalog, not external document citations. "
                "Declared dependency evidence is valid too; preserve its exact raw_id rather than inventing a new one. "
                "Prefer a document raw_id when referring to a specific URL. "
                "Optional source_annotations may extract exact titles and quotes already present in the research answer; "
                "the server checks them against original recorded output. "
                "Do not invent source URLs or claim that a tool receipt proves semantic support.",
                "source_policy": source_policy,
                "editorial_guidance": "Use only the eligible observed_calls catalog. Omit unsupported claims rather than relabeling them as facts. "
                "Put unavailable benchmarks, unknown user deployment details and out-of-scope questions in limitations, not blocking open_questions. "
                "Keep a concise set of distinct, decision-relevant findings; preserve exact supporting excerpts in source_annotations when available.",
            },
            ResearchAnalysis,
            execution.answer,
            context,
            validator=validate_references,
        )
        ground_source_annotations(evidences, analysis.source_annotations)
        if execution.stop_reason:
            analysis.open_questions.append(f"Native agent stopped before normal completion: {execution.stop_reason}")
        try:
            return ResearchResult(unit_id=unit.id, raw_evidences=evidences, searched_origins=sorted({e.origin for e in evidences if e.origin != "runtime"}), **analysis.model_dump(exclude={"source_annotations"}))
        except ValueError:
            raise ResearchError("EVIDENCE_REFERENCE", "Findings refer to an unobserved native tool call") from None

    async def respond(self, run, text):
        return await self._json(
            run,
            "deepresearch",
            {
                "message": text,
                "current_plan": run.get("plan"),
                "report": (run.get("report") or {}).get("report"),
                "conversation": [{"role": m["role"], "text": m["text"]} for m in run.get("conversation", [])[-20:]],
                "instructions": "Classify this follow-up: answer for explanations grounded in the existing report; "
                "revise for rewriting/reformatting the existing report without new research; "
                "research only when new evidence is needed. Do not start tools or research here. "
                "For answer, provide the actual concise response in text. "
                "Never treat a request to edit a plan as a completed research report.",
            },
            FollowupResult,
        )

    async def synthesize(self, run, plan, findings, pool, errors=()):
        # The writer consumes evidence supporting the findings, not every raw
        # tool response and incidental URL collected by parallel researchers.
        # The complete pool remains persisted for audit and citation binding.
        referenced = {identifier for finding in findings for identifier in finding.get("evidence_ids", [])}
        selected_pool = {identifier: evidence for identifier, evidence in pool.items() if identifier in referenced and source_allowed(evidence, plan.source_policy)}
        return await self._json(
            run,
            "report-synthesis",
            {
                "plan": plan.model_dump(mode="json"),
                "previous_report": run.get("revision_report"),
                "findings": findings,
                "evidence_pool": selected_pool,
                "limitations": run.get("limitations", []),
                "editorial_brief": {
                    "style": plan.report_style,
                    "maximum_body_characters": report_character_limit(plan.report_style),
                    "target_body_characters": 3000 if plan.report_style == "brief" else 6500,
                    "structure": "Direct recommendation first; one compact comparison_table for comparison tasks; original research dimensions; short actionable conclusion.",
                    "rules": "Synthesize, do not concatenate researcher notes. Cite the strongest 1-3 supporting pages per paragraph, not every receipt. "
                    "Each table cell should be short and retain evidence_ids for factual claims. Use precise technical names rather than spelling identifiers out in prose. "
                    "No repetitive 'official fact' prefixes, no chapter proving research coverage, no implementation audit/self-report in report fields. "
                    "If the runtime requires an execution self-report, place it outside the report JSON, never inside the public report. "
                    "Distinguish inference naturally. Explain conditions that change the recommendation; do not copy another report's conclusion. "
                    "State limits briefly without repeating the entire investigation history. "
                    "The user's original request is authoritative: planner/researcher assumptions are NOT confirmed user facts. "
                    "State unprovided workload/deployment assumptions explicitly as conditions; never infer workload from team size alone. "
                    "Do not claim an option is sufficient or a feature is superior merely because of its name. Explain the decision-relevant consequence. "
                    "Check internal consistency: a capability missing from both options cannot be a reason to prefer one; "
                    "a shared capability cannot be presented as exclusive. Distinguish supported deployment patterns from actual workload limits. "
                    "Preserve source conditions and exceptions; do not generalize one isolation level or failure case into a universal rule. "
                    "Use at most two short executive-summary paragraphs. Give each original research dimension its own short section. "
                    "Keep the conclusion actionable rather than repeating the summary. "
                    "Discuss only limitations that could change the decision; omit routine retry/receipt bookkeeping and deliberate scope exclusions from the public narrative. "
                    "The complete audit limitations remain available separately.",
                    "revision_mode": "When previous_report is supplied, edit that report according to the latest user message. "
                    "Preserve its unaffected sections, table cells and evidence IDs. Do not replace a targeted edit with a new report from research notes. "
                    "Any changed claim must remain supported by the supplied evidence pool and source policy.",
                },
                "validation_errors_to_fix": list(errors),
                "conversation": [{"role": m["role"], "text": m["text"]} for m in run.get("conversation", [])[-20:]],
                "instructions": "No search. Cover original unit_ids. Facts require evidence_ids. Separate analysis from facts. "
                "Respect the stated limitations; never present an unanswered question as an established fact. "
                "Never produce numeric citations, source URLs, or Markdown.",
            },
            report_schema(plan),
        )
